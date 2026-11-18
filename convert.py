#!/usr/bin/env python3
"""STEP -> engineering drawings.

    python convert.py part.stp [more.stp ...] [-o output_folder] [--units inch|mm] [--model models/x.joblib]

For each STEP file this writes, in <output>/<file name>/:
    <name>_drawings.pdf     A3 drawing package: assembly sheet (if any) + one sheet per unique part
    <name>_one_sheet.pdf    everything on one A3 sheet (assemblies with 2-8 unique parts)
    <name>_one_sheet.dxf    the same sheet, editable in CAD
    assembly.dxf            assembly drawing (assembled views, balloons, parts list)
    parts/<item>_<name>/    drawing.dxf, drawing.png, part.step, selected.json for each unique part
    bom.csv                 parts list
    report.json             what was found, chosen and skipped
"""
import argparse, collections, csv, json, math, os, re, sys, time, warnings
warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import numpy as np, joblib
import cadquery as cq
from s2d.stepread import read_parts
from s2d.features import extract, candidates, feature_rows, thread_of
from s2d.orient import orient
from s2d.select import to_specs, hole_centers, drop_redundant
from s2d.drawing import make_drawing
from s2d.assembly import make_assembly_drawing
from s2d.compose import compose, render

A3 = (420, 297)


def nice(v):
    return f"{v:.2f}".rstrip("0").rstrip(".")


def safe(s):
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", s).strip("_")[:40] or "part"


def group_parts(named, unit_mm):
    """Unique parts by geometry (+ name). Returns list of dict(name, solids, skipped_reason)."""
    groups = collections.OrderedDict()
    for name, s in named:
        bb = s.BoundingBox()
        v = s.Volume()
        vkey = float(f"{v:.4g}")                          # identical parts differ only by float noise
        key = (name, vkey, tuple(sorted(round(d, 2) for d in (bb.xlen, bb.ylen, bb.zlen))), len(s.Faces()))
        groups.setdefault(key, []).append(s)
    out = []
    for (name, vol, dims, nf), sols in groups.items():
        vol = sum(x.Volume() for x in sols) / len(sols)
        thin = dims[0] < 0.2 / unit_mm                       # glue layers, foils, coatings (<0.2 mm)
        out.append(dict(name=name, solids=sols, volume=vol, dims=dims, faces=nf,
                        skipped="thinner than 0.2 mm (glue layer / foil)" if thin else None))
    out.sort(key=lambda g: -g["volume"])
    # names: if one name covers several different parts (multi-body file), number them
    by_name = collections.Counter(g["name"] for g in out if not g["skipped"])
    single_file_name = len(by_name) == 1 and next(iter(by_name.values())) > 1
    k = 0
    seen = collections.Counter()
    for g in out:
        if g["skipped"]:
            continue
        k += 1
        if not g["name"] or single_file_name:
            g["name"] = f"PART {k:02d}"
        elif by_name[g["name"]] > 1:
            seen[g["name"]] += 1
            g["name"] = f"{g['name']}-{seen[g['name']]}"
    return out


def describe(F, dims):
    """Short parts-list description: size + main holes."""
    d = " x ".join(nice(v) for v in sorted(dims, reverse=True))
    by_dia = collections.Counter()
    for (ax, dia), gs in F["holes"].items():           # same size holes in any direction count together
        by_dia[dia] += len({g["c"] for g in gs})
    holes = []
    for dia, n in sorted(by_dia.items(), key=lambda kv: -kv[1]):
        th = thread_of(dia)
        holes.append(f"{n}x {th}" if th else (f"{n} HOLES Ø{nice(dia)}" if n > 1 else f"HOLE Ø{nice(dia)}"))
    return d + (", " + ", ".join(holes[:2]) if holes else "")


def draw_part(solid, name, unit, M, folder):
    solid, turned = orient(solid)
    shape = cq.Workplane().add(solid)
    F = extract(shape); C = candidates(F)
    if not C:
        raise ValueError("no measurable features found")
    rows = feature_rows(F, C)
    X = np.array([[float(r.get(f, 0)) for f in M["features"]] for r in rows])
    prob = M["model"].predict_proba(X)[:, 1]
    keep = [p > M["threshold"] for p in prob]
    # Drafting rule: every hole size must carry a diameter callout, whatever the model scored it.
    forced = []
    for i, c in enumerate(C):
        if "hole_dia" in c["srcs"] and not keep[i]:
            keep[i] = True; forced.append(c["key"])
    if "src_angle" not in M["features"]:
        keep = [k and c["kind"] != "ang" and "incl_corner" not in c["srcs"] for c, k in zip(C, keep)]
    sel = drop_redundant([c for c, k in zip(C, keep) if k])
    dims, _, axes = to_specs(sel)
    base_dims = list(dims)
    if "src_angle" not in M["features"]:            # older model: add angled-wall dims by rule
        extra = F.get("incl") or dict(angles=[], corners=[])
        dims += extra["angles"]
        used = {(d["view"], d["kind"], round(d["value"], 2)) for d in dims if d["kind"] in ("h", "v")}
        tiers = {}
        for d in dims:
            if d["kind"] in ("h", "v") and d.get("at") is None:
                tiers[(d["view"], d["side"])] = max(tiers.get((d["view"], d["side"]), 0), d.get("tier", 0))
        for d in sorted(extra["corners"], key=lambda d: d["value"]):
            k = (d["view"], d["kind"], round(d["value"], 2))
            if k in used:
                continue
            used.add(k)
            tiers[(d["view"], d["side"])] = tiers.get((d["view"], d["side"]), 0) + 1
            dims.append(dict(d, tier=tiers[(d["view"], d["side"])]))
    n_rule = len(dims) - len(base_dims)
    part = dict(shape=cq.Compound.makeCompound([solid]), family=name, dims=dims, units=unit,
                centers=hole_centers(F), axes=axes, notes=[])
    os.makedirs(folder, exist_ok=True)
    try:
        make_drawing(part, name, os.path.join(folder, "drawing.dxf"), os.path.join(folder, "drawing.png"))
    except Exception:                                # never lose a drawing to the rule-based extras
        part["dims"] = base_dims; n_rule = 0
        make_drawing(part, name, os.path.join(folder, "drawing.dxf"), os.path.join(folder, "drawing.png"))
    make_drawing(dict(part), name, os.path.join(folder, "drawing_bare.dxf"), None, frame=False)
    cq.exporters.export(shape, os.path.join(folder, "part.step"))
    info = dict(n_candidates=len(C), n_selected=len(sel), n_rule_dims=n_rule,
                selected=[dict(key=c["key"], prob=round(float(p), 3)) for c, p in zip(C, prob) if c in sel],
                forced_hole_dia=forced, turned=turned, complex=len(C) > 120)
    json.dump(info, open(os.path.join(folder, "selected.json"), "w"), indent=1)
    return F, info


def package_pdf(sheets, pdf_path, title, unit):
    import matplotlib; matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_pdf import PdfPages
    from ezdxf import recover
    from ezdxf.addons.drawing import RenderContext, Frontend
    from ezdxf.addons.drawing.matplotlib import MatplotlibBackend
    from ezdxf.addons.drawing.config import Configuration, BackgroundPolicy, ColorPolicy
    W, H = A3[0] / 25.4, A3[1] / 25.4
    cfg = Configuration(background_policy=BackgroundPolicy.WHITE, color_policy=ColorPolicy.BLACK)
    with PdfPages(pdf_path) as pdf:
        for n, (path, label) in enumerate(sheets, 1):
            doc, _ = recover.readfile(path)
            fig = plt.figure(figsize=(W, H)); ax = fig.add_axes([0.03, 0.05, 0.94, 0.9])
            Frontend(RenderContext(doc), MatplotlibBackend(ax), config=cfg).draw_layout(doc.modelspace(), finalize=True)
            fig.set_size_inches(W, H); ax.set_position([0.03, 0.05, 0.94, 0.9]); ax.set_aspect("equal", adjustable="datalim")
            fig.text(0.03, 0.965, label, fontsize=14, weight="bold")
            fig.text(0.97, 0.022, f"{title}  ·  Sheet {n} of {len(sheets)}  ·  Units: {unit.upper()}  ·  Scale: NTS",
                     ha="right", fontsize=9, color="#444")
            pdf.savefig(fig); plt.close(fig)
        pdf.infodict()["Title"] = f"{title} drawings"


def one_sheet(items, part_dirs, asm_bare, out_dxf, out_pdf, title, unit):
    n = len(items)
    rows_tbl = n + 1
    tbl_h = rows_tbl * 6 + 18
    cells = [dict(dxf=asm_bare, box=(10, 10, 210, 287), label=f"ASSEMBLY  {title}")]
    x0, x1, y_top, y_bot = 212, 410, 287, 12 + tbl_h
    cols = 2; nrows = math.ceil(n / cols); h = (y_top - y_bot) / nrows
    for i, it in enumerate(items):
        r, c = divmod(i, cols)
        last_alone = (i == n - 1 and n % 2 == 1)
        cx0 = x0 if (c == 0 or last_alone) else (x0 + x1) / 2 + 1
        cx1 = x1 if (c == 1 or last_alone) else (x0 + x1) / 2 - 1
        cy1 = y_top - r * h; cy0 = cy1 - h + 2
        cells.append(dict(dxf=os.path.join(part_dirs[i], "drawing_bare.dxf"), box=(cx0, cy0, cx1, cy1),
                          label=f"ITEM {it['item']}  {it['name']}  (QTY {len(it['solids'])})"))
    bom = [(it["item"], len(it["solids"]), it["name"], it["desc"]) for it in items]
    compose(cells, out_dxf, parts=bom, table_box=(x0, 10, x1, 10 + tbl_h),
            title_lines=[f"{title}  ASSEMBLY & PART DRAWINGS",
                         f"UNITS: {unit.upper()}   SCALE: NTS   THIRD ANGLE PROJECTION",
                         "DIMENSIONS AUTO-SELECTED: REVIEW BEFORE RELEASE"])
    render(out_dxf, out_pdf)


def convert(path, out_root, M, units=None):
    t0 = time.time()
    stem = safe(os.path.splitext(os.path.basename(path))[0])
    out = os.path.join(out_root, stem); os.makedirs(os.path.join(out, "parts"), exist_ok=True)
    named, unit = read_parts(path, units)
    unit_mm = {"inch": 25.4, "foot": 304.8, "mm": 1.0, "cm": 10.0, "m": 1000.0}[unit]
    groups = group_parts(named, unit_mm)
    report = dict(file=os.path.basename(path), units=unit, solids=len(named), unique_parts=0, parts=[], skipped=[], warnings=[])
    items, part_dirs, sheets = [], [], []
    for g in groups:
        if g["skipped"]:
            report["skipped"].append(dict(name=g["name"], qty=len(g["solids"]), size=[nice(v) for v in g["dims"]], reason=g["skipped"]))
            continue
        item = len(items) + 1
        folder = os.path.join(out, "parts", f"{item:02d}_{safe(g['name'])}")
        try:
            F, info = draw_part(g["solids"][0], g["name"], unit, M, folder)
        except Exception as e:
            report["warnings"].append(f"{g['name']}: could not draw ({type(e).__name__}: {e})")
            continue
        it = dict(item=item, name=g["name"], desc=describe(F, g["dims"]), solids=g["solids"])
        items.append(it); part_dirs.append(folder)
        sheets.append((os.path.join(folder, "drawing.dxf"), f"Item {item} — {g['name']} (qty {len(g['solids'])})"))
        report["parts"].append(dict(item=item, name=g["name"], qty=len(g["solids"]), description=it["desc"],
                                    candidates=info["n_candidates"], selected=info["n_selected"],
                                    review_needed=info["complex"]))
        if info["complex"]:
            report["warnings"].append(f"{g['name']}: complex part ({info['n_candidates']} candidate dimensions) - drawing needs manual cleanup")
    report["unique_parts"] = len(items)
    if not items:
        raise RuntimeError("no drawable parts found")
    is_assembly = len(items) > 1 or len(items[0]["solids"]) > 1
    title = safe(os.path.splitext(os.path.basename(path))[0]).upper()
    if is_assembly:
        make_assembly_drawing(items, os.path.join(out, "assembly.dxf"), None, title=title, units=unit,
                              sheet=f"1 OF {len(items) + 1}")
        sheets.insert(0, (os.path.join(out, "assembly.dxf"), f"Assembly — {title}"))
    package_pdf(sheets, os.path.join(out, f"{stem}_drawings.pdf"), title, unit)
    if is_assembly and 2 <= len(items) <= 8:
        bare = os.path.join(out, "assembly_bare.dxf")
        make_assembly_drawing(items, bare, None, title=title, units=unit, frame=False, iso_pos="above")
        one_sheet(items, part_dirs, bare, os.path.join(out, f"{stem}_one_sheet.dxf"), os.path.join(out, f"{stem}_one_sheet.pdf"), title, unit)
    elif is_assembly and len(items) > 8:
        report["warnings"].append(f"{len(items)} unique parts: too many for one sheet, see the multi-page PDF")
    with open(os.path.join(out, "bom.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(["item", "qty", "name", "description", f"units"])
        for it in items: w.writerow([it["item"], len(it["solids"]), it["name"], it["desc"], unit])
    report["seconds"] = round(time.time() - t0, 1)
    json.dump(report, open(os.path.join(out, "report.json"), "w"), indent=1, ensure_ascii=False)
    return out, report


def main():
    ap = argparse.ArgumentParser(description="STEP file -> engineering drawings (DXF + PDF)")
    ap.add_argument("files", nargs="+", help=".stp / .step files")
    ap.add_argument("-o", "--out", default="drawings_out", help="output folder (default: drawings_out)")
    ap.add_argument("--units", choices=["mm", "inch", "cm", "m"], help="override the units declared in the file")
    ap.add_argument("--model", default=os.path.join(HERE, "models", "selector_combined.joblib"))
    a = ap.parse_args()
    M = joblib.load(a.model)
    force = (a.units, {"inch": 25.4, "mm": 1.0, "cm": 10.0, "m": 1000.0}[a.units]) if a.units else None
    for f in a.files:
        print(f"\n▶ {f}")
        try:
            out, r = convert(f, a.out, M, force)
        except Exception as e:
            print(f"  ✗ failed: {type(e).__name__}: {e}"); continue
        print(f"  units {r['units']} · {r['solids']} solids · {r['unique_parts']} unique parts · {r['seconds']} s")
        for p in r["parts"]:
            print(f"    {p['item']:>2}. {p['name']}  x{p['qty']}  — {p['description']}  ({p['selected']} dims)")
        for s in r["skipped"]:
            print(f"    skipped: {s['name']} x{s['qty']} ({s['reason']})")
        for w in r["warnings"]:
            print(f"    ! {w}")
        print(f"  → {out}")


if __name__ == "__main__":
    main()
