"""Geometry features and candidate dimensions from any (axis-aligned) STEP file.

extract(shape) -> dict with bbox, cylinder groups, hole groups, plane levels
candidates(feat) -> list of deduplicated candidate dimensions. Each has
    key     : ("lin", axis, value) | ("dia", value) | ("rad", value)   (what appears on a drawing)
    srcs    : set of source tags explaining where it came from
    anchor  : geometry needed to draw it (world coordinates)
    + numeric attributes used as model features
"""
import math, collections
import cadquery as cq
from OCP.BRepTools import BRepTools

AX = "xyz"
# ISO metric threads as they appear in STEP files: tap drill or minor (core) diameter
THREADS = [("M2", 1.6, 1.567), ("M2.5", 2.05, 2.013), ("M3", 2.5, 2.459), ("M4", 3.3, 3.242),
           ("M5", 4.2, 4.134), ("M6", 5.0, 4.917), ("M8", 6.8, 6.647), ("M10", 8.5, 8.376), ("M12", 10.2, 10.106)]


def thread_of(dia, tol=0.03):
    for name, drill, minor in THREADS:
        if abs(dia - drill) <= tol or abs(dia - minor) <= tol:
            return name
    return None
TOL = 1e-3


def rv(x, n=3):
    return round(float(x) + 0.0, n)


def axis_of(d):
    """Return 'x'/'y'/'z' if direction is axis-aligned, else None."""
    a = [abs(d[0]), abs(d[1]), abs(d[2])]
    i = max(range(3), key=lambda k: a[k])
    return AX[i] if a[i] > 1 - 1e-6 else None


def surf_point_normal(f, u, v):
    """Point and outward normal (respecting face orientation) at (u, v)."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.gp import gp_Pnt, gp_Vec
    from OCP.TopAbs import TopAbs_REVERSED
    sa = BRepAdaptor_Surface(f.wrapped)
    p, du, dv = gp_Pnt(), gp_Vec(), gp_Vec()
    sa.D1(u, v, p, du, dv)
    n = du.Crossed(dv)
    if f.wrapped.Orientation() == TopAbs_REVERSED:
        n.Reverse()
    return {"x": p.X(), "y": p.Y(), "z": p.Z()}, {"x": n.X(), "y": n.Y(), "z": n.Z()}


def extract(shape):
    if hasattr(shape, "vals"):
        solids = [s for v in shape.vals() for s in v.Solids()]
    else:
        solids = list(shape.Solids())
    comp = cq.Compound.makeCompound(solids)
    bb = comp.BoundingBox()
    bmin = {"x": bb.xmin, "y": bb.ymin, "z": bb.zmin}
    bmax = {"x": bb.xmax, "y": bb.ymax, "z": bb.zmax}

    cyl = {}            # (axis, c1, c2, dia) -> info
    levels = {a: set() for a in AX}
    for f in comp.Faces():
        gt = f.geomType()
        if gt == "PLANE":
            n = f.normalAt(f.Center()); a = axis_of((n.x, n.y, n.z))
            if a:
                levels[a].add(rv(getattr(f.Center(), a)))
        elif gt == "CYLINDER":
            from OCP.BRepAdaptor import BRepAdaptor_Surface
            c = BRepAdaptor_Surface(f.wrapped).Cylinder()   # handles trimmed surfaces too
            d = c.Axis().Direction(); a = axis_of((d.X(), d.Y(), d.Z()))
            if not a:
                continue
            loc = c.Axis().Location(); L = {"x": loc.X(), "y": loc.Y(), "z": loc.Z()}
            o = [k for k in AX if k != a]
            key = (a, rv(L[o[0]]), rv(L[o[1]]), rv(2 * c.Radius()))
            umin, umax, vmin, vmax = BRepTools.UVBounds_s(f.wrapped)
            P, nn = surf_point_normal(f, (umin + umax) / 2, (vmin + vmax) / 2)
            rad = [P[o[0]] - L[o[0]], P[o[1]] - L[o[1]]]
            internal = (rad[0] * nn[o[0]] + rad[1] * nn[o[1]]) < 0
            fb = f.BoundingBox(); lo = {"x": fb.xmin, "y": fb.ymin, "z": fb.zmin}[a]; hi = {"x": fb.xmax, "y": fb.ymax, "z": fb.zmax}[a]
            g = cyl.setdefault(key, dict(axis=a, other=o, c=(key[1], key[2]), dia=key[3], span=0.0,
                                         internal=internal, lo=lo, hi=hi, nfaces=0))
            g["span"] += (umax - umin); g["lo"] = min(g["lo"], lo); g["hi"] = max(g["hi"], hi); g["nfaces"] += 1
    for g in cyl.values():
        g["full"] = g["span"] > 2 * math.pi - 1e-3
        g["through"] = abs(g["lo"] - bmin[g["axis"]]) < 1e-2 and abs(g["hi"] - bmax[g["axis"]]) < 1e-2
        g["length"] = g["hi"] - g["lo"]
    # hole groups: full internal cylinders with same axis direction and diameter
    hg = collections.defaultdict(list)
    for g in cyl.values():
        if g["full"] and g["internal"]:
            hg[(g["axis"], g["dia"])].append(g)
    return dict(solids=len(solids), bmin=bmin, bmax=bmax,
                ext={a: bmax[a] - bmin[a] for a in AX}, cyl=list(cyl.values()),
                holes={k: v for k, v in hg.items()}, levels={a: sorted(v) for a, v in levels.items()})


def candidates(F):
    C = {}
    ext, bmin = F["ext"], F["bmin"]
    big = max(ext.values())

    def add(key, src, anchor, **attr):
        if key[-1] < 0.05:      # ignore degenerate / tiny values
            return
        c = C.get(key)
        if c is None:
            c = C[key] = dict(key=key, kind=key[0], axis=key[1] if key[0] == "lin" else None, value=key[-1],
                              srcs=set(), anchor=anchor, count=1, through=False, internal=False,
                              thread=thread_of(key[-1]) if key[0] == "dia" else None)
        c["srcs"].add(src)
        for k, v in attr.items():
            if k == "count":
                c["count"] = max(c["count"], v)
            elif k in ("through", "internal"):
                c[k] = c[k] or v
            else:
                c[k] = v

    # overall extents
    for a in AX:
        p1 = dict(F["bmin"]); p2 = dict(F["bmin"]); p2[a] = F["bmax"][a]
        add(("lin", a, rv(ext[a])), "overall", dict(type="lin", axis=a, p1=p1, p2=p2))
    # plane levels: consecutive gaps and positions from min
    for a in AX:
        lv = sorted(set([rv(F["bmin"][a])] + F["levels"][a] + [rv(F["bmax"][a])]))
        for i in range(len(lv) - 1):
            p1 = dict(F["bmin"]); p2 = dict(F["bmin"]); p1[a] = lv[i]; p2[a] = lv[i + 1]
            add(("lin", a, rv(lv[i + 1] - lv[i])), "level_gap", dict(type="lin", axis=a, p1=p1, p2=p2))
        for v in lv[1:-1]:
            p1 = dict(F["bmin"]); p2 = dict(F["bmin"]); p2[a] = v
            add(("lin", a, rv(v - lv[0])), "level_pos", dict(type="lin", axis=a, p1=p1, p2=p2))
    # cylinders
    for g in F["cyl"]:
        a, o = g["axis"], g["other"]
        ctr = {a: (g["lo"] + g["hi"]) / 2, o[0]: g["c"][0], o[1]: g["c"][1]}
        anc = dict(type="dia", axis=a, center=ctr, r=g["dia"] / 2, lo=g["lo"], hi=g["hi"])
        if g["full"]:
            src = "hole_dia" if g["internal"] else "ext_dia"
            add(("dia", g["dia"]), src, anc, internal=g["internal"], through=g["through"], cyl_len=g["length"])
        else:
            add(("rad", rv(g["dia"] / 2)), "radius_concave" if g["internal"] else "radius_convex",
                dict(type="rad", axis=a, center=ctr, r=g["dia"] / 2))
    # hole groups: count, positions, pitches, spans, bolt circles
    for (a, d), gs in F["holes"].items():
        centers = sorted({g["c"] for g in gs})
        o = [k for k in AX if k != a]
        n = len(centers)
        g0 = gs[0]
        anc_hole = dict(type="dia", axis=a, center={a: (g0["lo"] + g0["hi"]) / 2, o[0]: centers[0][0], o[1]: centers[0][1]},
                        r=d / 2, lo=g0["lo"], hi=g0["hi"])
        def thru_at(cc):  # union of all coaxial internal cylinders at this centre spans the part
            segs = sorted((g["lo"], g["hi"]) for g in F["cyl"] if g["axis"] == a and g["internal"] and g["c"] == cc)
            cur = F["bmin"][a]
            for lo, hi in segs:
                if lo > cur + 1e-2: return False
                cur = max(cur, hi)
            return cur >= F["bmax"][a] - 1e-2
        add(("dia", d), "hole_dia", anc_hole, count=n, internal=True, through=all(thru_at(c) for c in centers))
        hz = (g0["lo"] + g0["hi"]) / 2
        for j, ax in enumerate(o):
            coords = sorted({c[j] for c in centers})
            other = o[1 - j]
            oc = min(c[1 - j] for c in centers)
            def pt(v):
                p = {a: hz, ax: v, other: oc}; return p
            for v in coords[:4]:
                p1 = pt(bmin[ax]); p2 = pt(v)
                add(("lin", ax, rv(v - bmin[ax])), "hole_pos", dict(type="lin", axis=ax, p1=p1, p2=p2, hole_axis=a),
                    count=n)
            for i in range(len(coords) - 1):
                add(("lin", ax, rv(coords[i + 1] - coords[i])), "hole_pitch",
                    dict(type="lin", axis=ax, p1=pt(coords[i]), p2=pt(coords[i + 1]), hole_axis=a), count=n)
            if len(coords) > 2:
                add(("lin", ax, rv(coords[-1] - coords[0])), "hole_span",
                    dict(type="lin", axis=ax, p1=pt(coords[0]), p2=pt(coords[-1]), hole_axis=a), count=n)
        if n >= 3:
            cx = sum(c[0] for c in centers) / n; cy = sum(c[1] for c in centers) / n
            rs = [math.hypot(c[0] - cx, c[1] - cy) for c in centers]
            ang = sorted(math.atan2(c[1] - cy, c[0] - cx) for c in centers)
            gaps = [(ang[(i + 1) % n] - ang[i]) % (2 * math.pi) for i in range(n)]
            regular = max(abs(gp - 2 * math.pi / n) for gp in gaps) < 1e-3
            if max(rs) - min(rs) < 1e-3 and rs[0] > 1e-3 and regular:
                ctr = {a: hz, o[0]: cx, o[1]: cy}
                add(("dia", rv(2 * rs[0])), "bolt_circle", dict(type="dia", axis=a, center=ctr, r=rs[0], bc=True), count=n)
    # numeric attributes
    out = []
    for c in C.values():
        c["rel_big"] = c["value"] / big if big else 0
        c["rel_axis"] = c["value"] / ext[c["axis"]] if c["axis"] and ext[c["axis"]] else 0
        out.append(c)
    return out


SOURCES = ["overall", "level_gap", "level_pos", "hole_dia", "ext_dia", "radius_concave", "radius_convex",
           "hole_pos", "hole_pitch", "hole_span", "bolt_circle"]


def part_context(F, cands):
    ext = sorted(F["ext"].values())
    nh = sum(len({g["c"] for g in gs}) for gs in F["holes"].values())
    return dict(p_n_solids=F["solids"], p_n_holes=nh, p_n_hole_groups=len(F["holes"]),
                p_n_ext_cyl=sum(1 for g in F["cyl"] if g["full"] and not g["internal"]),
                p_n_rad=sum(1 for g in F["cyl"] if not g["full"]),
                p_aspect1=ext[0] / ext[2] if ext[2] else 0, p_aspect2=ext[1] / ext[2] if ext[2] else 0,
                p_n_levels=sum(len(v) for v in F["levels"].values()), p_n_cands=len(cands))


def feature_rows(F, cands):
    ctx = part_context(F, cands)
    rows = []
    by_kind = collections.defaultdict(list)
    for c in cands:
        by_kind[(c["kind"], c["axis"])].append(c["value"])
    for c in cands:
        vals = sorted(by_kind[(c["kind"], c["axis"])])
        r = dict(kind_lin=c["kind"] == "lin", kind_dia=c["kind"] == "dia", kind_rad=c["kind"] == "rad",
                 ax_x=c["axis"] == "x", ax_y=c["axis"] == "y", ax_z=c["axis"] == "z",
                 value=c["value"], rel_big=c["rel_big"], rel_axis=c["rel_axis"],
                 rank_in_kind=vals.index(c["value"]) / max(1, len(vals) - 1), n_in_kind=len(vals),
                 count=c["count"], through=c["through"], internal=c["internal"], n_srcs=len(c["srcs"]),
                 is_thread=bool(c.get("thread")) and c["internal"],
                 is_round=abs(c["value"] - round(c["value"])) < 1e-6,
                 is_half=abs(c["value"] * 2 - round(c["value"] * 2)) < 1e-6)
        for s in SOURCES:
            r["src_" + s] = s in c["srcs"]
        r.update(ctx)
        rows.append(r)
    return rows


def label_keys(labels):
    """Map generator labels.json dimensions to candidate keys."""
    VA = {("front", "h"): "x", ("front", "v"): "z", ("top", "h"): "x", ("top", "v"): "y",
          ("right", "h"): "y", ("right", "v"): "z"}
    keys = set()
    for d in labels["dimensions"]:
        if d["kind"] == "dia" or (d["kind"] in ("h", "v") and (d.get("text") or "").startswith("Ø")):
            keys.add(("dia", rv(d["value"])))
        elif d["kind"] == "rad":
            keys.add(("rad", rv(d["value"])))
        else:
            keys.add(("lin", VA[(d["view"], d["kind"])], rv(d["value"])))
    return keys
