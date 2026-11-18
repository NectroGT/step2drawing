"""Build review-page data: projected views + every candidate dimension with model probability.

python training/review_data.py MODEL.joblib SPEC.json OUT.json
SPEC.json: [{id, title, step, group}]
"""
import sys, os, json, math
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np, joblib, cadquery as cq
from s2d.features import extract, candidates, feature_rows
from s2d.drawing import project, edge_geom
from s2d.select import to_specs
from s2d.orient import orient


def poly(g):
    if g[0] == "line":
        pts = [g[1], g[2]]
    elif g[0] == "circle":
        c, r = g[1], g[2]
        pts = [(c[0] + r * math.cos(t * math.pi / 24), c[1] + r * math.sin(t * math.pi / 24)) for t in range(49)]
    else:
        pts = g[1]
        if len(pts) > 24:
            pts = pts[::max(1, len(pts) // 24)] + [pts[-1]]
    return [round(v, 2) for p in pts for v in p]


def r3(p):
    return [round(x, 3) for x in p]


def build(M, pid, title, step, group="", turn=False):
    shape = cq.importers.importStep(step)
    solids = [s for v in shape.vals() for s in v.Solids()]
    if turn:                                  # new parts: same orientation the converter uses
        solids = [orient(s)[0] for s in solids]
        shape = cq.Workplane().add(solids)
    comp = cq.Compound.makeCompound(solids)
    F = extract(shape); C = candidates(F); rows = feature_rows(F, C)
    X = np.array([[float(r.get(f, 0)) for f in M["features"]] for r in rows])
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
            g.update(p1=r3(spec["p1"]), p2=r3(spec["p2"]))
        elif spec["kind"] == "ang":
            g.update(vertex=r3(spec["vertex"]), p_line=r3(spec["p_line"]), p_ref=r3(spec["p_ref"]), r=round(spec["r"], 3))
        else:
            g.update(c=r3(spec["c"]), r=round(spec["r"], 3))
        cands.append(dict(key=json.dumps(c["key"]), type=c["kind"], value=c["value"], axis=c["axis"],
                          count=c["count"], srcs=sorted(c["srcs"]), prob=round(float(p), 3),
                          model_keep=bool(p > M["threshold"]), text=spec.get("text") or "",
                          csk=list(c["csk"]) if c.get("csk") else None, g=g))
    cands.sort(key=lambda d: -d["prob"])
    print(pid, len(C), "candidates", flush=True)
    return dict(id=pid, title=title, group=group, size=[round(F["ext"][a], 2) for a in "xyz"], views=views, cands=cands)


if __name__ == "__main__":
    M = joblib.load(sys.argv[1])
    spec = json.load(open(sys.argv[2]))
    parts = [build(M, d["id"], d["title"], d["step"], d.get("group", ""), d.get("turn", False)) for d in spec]
    json.dump(parts, open(sys.argv[3], "w"), separators=(",", ":"))
