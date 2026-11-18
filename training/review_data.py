"""Build review-page data: projected views + every candidate dimension with model probability."""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np, joblib, cadquery as cq
from features import extract, candidates, feature_rows
from drawing import project, edge_geom
from apply import to_specs

def poly(g):
    if g[0] == "line": pts = [g[1], g[2]]
    elif g[0] == "circle":
        import math; c, r = g[1], g[2]
        pts = [(c[0] + r * math.cos(t * math.pi / 24), c[1] + r * math.sin(t * math.pi / 24)) for t in range(49)]
    else:
        pts = g[1]
        if len(pts) > 24: pts = pts[::max(1, len(pts) // 24)] + [pts[-1]]
    return [round(v, 2) for p in pts for v in p]

def main(model, manifest_root, out, ids):
    M = joblib.load(model); parts = []
    for item in ids:
        pid, title, step = item[:3]; group = item[3] if len(item) > 3 else ""
        shape = cq.importers.importStep(step)
        solids = [s for v in shape.vals() for s in v.Solids()]
        comp = cq.Compound.makeCompound(solids)
        F = extract(shape); C = candidates(F); rows = feature_rows(F, C)
        X = np.array([[float(r[f]) for f in M["features"]] for r in rows])
        prob = M["model"].predict_proba(X)[:, 1]
        views = {}
        for v in ("front", "top", "right"):
            vis, hid = project(comp, v)
            vg = [poly(edge_geom(e)) for e in vis]; hg = [poly(edge_geom(e)) for e in hid]
            xs = [a for p in vg + hg for a in p[0::2]]; ys = [a for p in vg + hg for a in p[1::2]]
            views[v] = dict(vis=vg, hid=hg, bbox=[min(xs), min(ys), max(xs), max(ys)])
        cands = []
        for c, p in zip(C, prob):
            spec = to_specs([c])[0][0]
            g = dict(view=spec["view"], kind=spec["kind"])
            if spec["kind"] in ("h", "v"):
                g.update(p1=[round(x, 3) for x in spec["p1"]], p2=[round(x, 3) for x in spec["p2"]])
            else:
                g.update(c=[round(x, 3) for x in spec["c"]], r=round(spec["r"], 3))
            cands.append(dict(key=json.dumps(c["key"]), type=c["kind"], value=c["value"], axis=c["axis"],
                              count=c["count"], srcs=sorted(c["srcs"]), prob=round(float(p), 3),
                              model_keep=bool(p > M["threshold"]), text=spec.get("text") or "", g=g))
        cands.sort(key=lambda d: (-d["prob"]))
        parts.append(dict(id=pid, title=title, group=group, size=[round(F["ext"][a], 2) for a in "xyz"], views=views, cands=cands))
        print(pid, len(C), "candidates", sum(len(v["vis"]) + len(v["hid"]) for v in views.values()), "edges")
    json.dump(parts, open(out, "w"), separators=(",", ":"))

if __name__ == "__main__":
    if sys.argv[2].endswith(".json"):            # spec list: [{id, title, step, group}]
        spec = json.load(open(sys.argv[2]))
        main(sys.argv[1], None, sys.argv[3], [(d["id"], d["title"], d["step"], d["group"]) for d in spec]); sys.exit()
    ids = []
    for l in open(sys.argv[2] + "/manifest.jsonl"):
        m = json.loads(l)
        if m["id"] == "superior_style75_die_set": continue
        ids.append((m["id"], m["title"], sys.argv[2] + "/" + m["step"]))
    main(sys.argv[1], sys.argv[2], sys.argv[3], ids)
