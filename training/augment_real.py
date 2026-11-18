"""Make training variants of labelled real parts: 48 axis orientations (24 rotations x mirror)
times 'nice' scale factors. Labels are carried through the transform.

python augment_real.py LABELS.json REAL_PARTS_DIR OUT_DIR N_TOTAL [--workers K] [--seed S] [--test]

Writes OUT_DIR/parts/<id>/model.step + labels.json, OUT_DIR/manifest.jsonl, OUT_DIR/table.csv
"""
import argparse, itertools, json, os, random, sys, csv
from multiprocessing import Pool
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

AX = "xyz"
SCALES = [0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0]
POSITIONAL = {"hole_pos", "level_pos", "incl_corner"}
TOL = 0.002


def orientations():
    """All 48 signed permutation matrices as (perm, signs): new axis j = sign[j] * old axis perm[j]."""
    out = []
    for perm in itertools.permutations(range(3)):
        for signs in itertools.product((1, -1), repeat=3):
            out.append((perm, signs))
    out.sort(key=lambda o: (o[0] != (0, 1, 2)) * 2 + (o[1] != (1, 1, 1)))   # identity first
    return out


def transform(shape, perm, signs, s):
    import cadquery as cq
    from OCP.gp import gp_Trsf
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
    M = [[0.0] * 3 for _ in range(3)]
    for j in range(3):
        M[j][perm[j]] = float(signs[j])
    det = (M[0][0] * (M[1][1] * M[2][2] - M[1][2] * M[2][1]) - M[0][1] * (M[1][0] * M[2][2] - M[1][2] * M[2][0])
           + M[0][2] * (M[1][0] * M[2][1] - M[1][1] * M[2][0]))
    t = gp_Trsf()
    # gp_Trsf stores orthogonal matrix * scale; a mirror is a rotation with scale -1
    k = s * det
    R = [[M[i][j] * det for j in range(3)] for i in range(3)]          # det(R) = +1
    t.SetValues(R[0][0] * k, R[0][1] * k, R[0][2] * k, 0.0,
                R[1][0] * k, R[1][1] * k, R[1][2] * k, 0.0,
                R[2][0] * k, R[2][1] * k, R[2][2] * k, 0.0)
    solids = [x for v in shape.vals() for x in v.Solids()]
    comp = cq.Compound.makeCompound(solids)
    return cq.Shape.cast(BRepBuilderAPI_Transform(comp.wrapped, t, True).Shape())


def map_labels(C_new, keep, perm, signs, s, ext_orig):
    """1 if a new candidate is the same dimension as one the reviewer kept on the original part."""
    kd = {(k[0], k[-1]) for k in keep if k[0] in ("dia", "rad")}
    kl = {(k[1], k[2]) for k in keep if k[0] == "lin"}
    def has(pairs, a, v):
        return any(pa == a and abs(pv - v) <= TOL for pa, pv in pairs)
    labels = []
    for c in C_new:
        key = c["key"]; v = key[-1] / s
        if key[0] == "ang":                     # angles do not scale and survive 90-degree rotations
            labels.append(int(any(k[0] == "ang" and abs(k[-1] - key[-1]) <= 0.11 for k in keep)))
            continue
        if key[0] in ("dia", "rad"):
            labels.append(int(any(kk == key[0] and abs(kv - v) <= TOL for kk, kv in kd)))
            continue
        j = AX.index(key[1]); a = AX[perm[j]]; flipped = signs[j] < 0
        positional = bool(c["srcs"] & POSITIONAL)
        other = bool(c["srcs"] - POSITIONAL)
        lab = other and has(kl, a, v)
        if positional:
            vm = ext_orig[a] - v if flipped else v
            lab = lab or has(kl, a, vm)
        labels.append(int(lab))
    return labels


def work(job):
    import cadquery as cq
    from features import extract, candidates, feature_rows
    pid, oi, s, src_step, keep, out_dir, write_step = job
    perm, signs = orientations()[oi]
    vid = f"{pid}__o{oi:02d}_s{str(s).replace('.', 'p')}"
    try:
        orig = cq.importers.importStep(src_step)
        F0 = extract(orig)
        shp = transform(orig, perm, signs, s)
        F = extract(cq.Workplane().add(shp)); C = candidates(F); rows = feature_rows(F, C)
        labs = map_labels(C, keep, perm, signs, s, F0["ext"])
        d = os.path.join(out_dir, "parts", vid); os.makedirs(d, exist_ok=True)
        if write_step:
            cq.exporters.export(cq.Workplane().add(shp), os.path.join(d, "model.step"))
        lab = dict(id=vid, source_part=pid, orientation=dict(index=oi, perm=[AX[p] for p in perm], signs=list(signs)),
                   scale=s, n_keep_original=len(keep), keep=[c["key"] for c, l in zip(C, labs) if l],
                   n_candidates=len(C))
        json.dump(lab, open(os.path.join(d, "labels.json"), "w"))
        for r, c, l in zip(rows, C, labs):
            r.update(part=vid, family=pid, key=json.dumps(c["key"]), label=l)
        return "ok", vid, rows, sum(labs), len(keep), len(C)
    except Exception as e:
        return "fail", vid, f"{type(e).__name__}: {e}"[:200], 0, len(keep), 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("labels"); ap.add_argument("real_dir"); ap.add_argument("out"); ap.add_argument("n", type=int)
    ap.add_argument("--workers", type=int, default=os.cpu_count()); ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--no-step", action="store_true")
    a = ap.parse_args()
    L = {k: v for k, v in json.load(open(a.labels)).items() if not k.startswith("_")}
    parts = sorted(L)
    rng = random.Random(a.seed)
    combos = [(oi, s) for oi in range(48) for s in SCALES]
    per = [a.n // len(parts) + (1 if i < a.n % len(parts) else 0) for i in range(len(parts))]
    jobs = []
    for pid, n in zip(parts, per):
        pick = [(0, 1.0)] + rng.sample([c for c in combos if c != (0, 1.0)], n - 1)
        jobs += [(pid, oi, s, os.path.join(a.real_dir, pid, "model.step"), [tuple(k) for k in L[pid]["keep"]], a.out,
                  not a.no_step) for oi, s in pick]
    os.makedirs(a.out, exist_ok=True)
    rows, man, fails, rec = [], [], [], []
    with Pool(a.workers) as p:
        for i, (st, vid, r, npos, nkeep, nc) in enumerate(p.imap_unordered(work, jobs, chunksize=2)):
            if st == "ok":
                rows += r; man.append(dict(id=vid, source_part=vid.split("__")[0], n_candidates=nc, n_positive=npos,
                                           n_keep_original=nkeep, step=f"parts/{vid}/model.step", labels=f"parts/{vid}/labels.json"))
                rec.append(npos / max(1, nkeep))
            else:
                fails.append((vid, r))
            if (i + 1) % 250 == 0:
                print(f"{i + 1}/{len(jobs)}", flush=True)
    man.sort(key=lambda m: m["id"])
    with open(os.path.join(a.out, "manifest.jsonl"), "w") as f:
        for m in man: f.write(json.dumps(m) + "\n")
    with open(os.path.join(a.out, "table.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
    print(f"variants {len(man)} / {len(jobs)}  failures {len(fails)}  rows {len(rows)}")
    print(f"label recovery (positives / original kept): mean {sum(rec)/len(rec):.3f}  min {min(rec):.3f}  max {max(rec):.3f}")
    for v, e in fails[:10]: print("  FAIL", v, e)


if __name__ == "__main__":
    main()
