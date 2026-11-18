"""Relabel augmented real-part variants: each dimension the reviewer kept maps to exactly ONE
candidate in the variant (closest match, measured from the correct side after mirroring).

python relabel_real.py LABELS.json REAL_PARTS_DIR AUG_DIR
"""
import sys, os, json
import pandas as pd, cadquery as cq
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from features import extract, candidates

AX = "xyz"
POSITIONAL = {"hole_pos", "level_pos"}
L = {k: v for k, v in json.load(open(sys.argv[1])).items() if not k.startswith("_")}
real_dir, aug = sys.argv[2], sys.argv[3]

# original candidates (with their sources) and extents per real part
orig = {}
for pid in L:
    F0 = extract(cq.importers.importStep(os.path.join(real_dir, pid, "model.step")))
    C0 = {tuple(c["key"]): c for c in candidates(F0)}
    orig[pid] = (C0, F0["ext"])

df = pd.read_csv(os.path.join(aug, "table.csv"))
df["key_t"] = df["key"].map(lambda s: tuple(json.loads(s)))
new_label = pd.Series(0, index=df.index)
stats = []
man = [json.loads(l) for l in open(os.path.join(aug, "manifest.jsonl"))]
for m in man:
    lab = json.load(open(os.path.join(aug, m["labels"])))
    pid, s = lab["source_part"], lab["scale"]
    perm = [AX.index(a) for a in lab["orientation"]["perm"]]; signs = lab["orientation"]["signs"]
    C0, ext = orig[pid]
    rows = df.index[df.part == m["id"]]
    cand = {i: df.at[i, "key_t"] for i in rows}
    chosen = []
    for k in L[pid]["keep"]:
        k = tuple(k); c0 = C0[k]
        if k[0] in ("dia", "rad"):
            want_kind, want_axis, want_v = k[0], None, k[-1] * s
        else:
            a = AX.index(k[1]); j = perm.index(a); flipped = signs[j] < 0
            purely_pos = c0["srcs"] <= POSITIONAL
            v = (ext[k[1]] - k[2]) if (flipped and purely_pos) else k[2]
            want_kind, want_axis, want_v = "lin", AX[j], v * s
        best, bd = None, 1e9
        for i, ck in cand.items():
            if ck[0] != want_kind or (want_axis and ck[1] != want_axis) or i in chosen:
                continue
            d = abs(ck[-1] - want_v)
            if d < bd:
                best, bd = i, d
        if best is not None and bd <= 0.002 + 0.001 * s:
            chosen.append(best)
    new_label.loc[chosen] = 1
    lab["keep"] = [list(cand[i]) for i in chosen]; lab["label_rule"] = "one candidate per kept dimension"
    json.dump(lab, open(os.path.join(aug, m["labels"]), "w"))
    m["n_positive"] = len(chosen)
    stats.append(len(chosen) / len(L[pid]["keep"]))
df["label"] = new_label.values
df.drop(columns=["key_t"]).to_csv(os.path.join(aug, "table.csv"), index=False)
with open(os.path.join(aug, "manifest.jsonl"), "w") as f:
    for m in man: f.write(json.dumps(m) + "\n")
print(f"variants {len(stats)}  label recovery mean {sum(stats)/len(stats):.3f}  min {min(stats):.3f}  max {max(stats):.3f}"
      f"  positives {int(df.label.sum())}")
