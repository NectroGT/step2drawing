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
try:
    from .inclined import analyze as _inclined
except ImportError:                       # flat import (training scripts)
    from inclined import analyze as _inclined

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
    floors = []
    for f in comp.Faces():
        gt = f.geomType()
        if gt == "PLANE":
            n = f.normalAt(f.Center()); a = axis_of((n.x, n.y, n.z))
            if a:
                levels[a].add(rv(getattr(f.Center(), a)))
                lv = getattr(f.Center(), a)
                if bmin[a] + 1e-3 < lv < bmax[a] - 1e-3:           # recessed or raised flat: pocket floor, step
                    fb = f.BoundingBox()
                    floors.append(dict(axis=a, level=lv, area=f.Area(),
                                       lo={"x": fb.xmin, "y": fb.ymin, "z": fb.zmin},
                                       hi={"x": fb.xmax, "y": fb.ymax, "z": fb.zmax}))
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
            pos = c.Position(); X_ = pos.XDirection(); Y_ = pos.YDirection()
            g.setdefault("arcs", []).append(dict(umin=umin, umax=umax, X=(X_.X(), X_.Y(), X_.Z()),
                                                 Y=(Y_.X(), Y_.Y(), Y_.Z()), mid=(lo + hi) / 2, area=f.Area()))
    for g in cyl.values():
        g["full"] = g["span"] > 2 * math.pi - 1e-3
        # partial arcs: tangent extremes (quadrant points) that lie on the arc, and the arc's mid direction
        g["quads"] = []
        if not g["full"]:
            a, o = g["axis"], g["other"]; r = g["dia"] / 2
            for arc in g["arcs"]:
                for t in o:
                    for sgn in (1, -1):
                        e = [0.0, 0.0, 0.0]; e[AX.index(t)] = sgn
                        u = math.atan2(sum(e[i] * arc["Y"][i] for i in range(3)), sum(e[i] * arc["X"][i] for i in range(3)))
                        while u < arc["umin"] - 1e-6:
                            u += 2 * math.pi
                        if u <= arc["umax"] + 1e-6:
                            P = {a: arc["mid"], o[0]: g["c"][0], o[1]: g["c"][1]}
                            P[t] += sgn * r
                            g["quads"].append((t, rv(P[t]), P))
            big_arc = max(g["arcs"], key=lambda q: q["area"])
            um = (big_arc["umin"] + big_arc["umax"]) / 2
            g["mid_dir"] = tuple(math.cos(um) * big_arc["X"][i] + math.sin(um) * big_arc["Y"][i] for i in range(3))
        g["through"] = abs(g["lo"] - bmin[g["axis"]]) < 1e-2 and abs(g["hi"] - bmax[g["axis"]]) < 1e-2
        g["length"] = g["hi"] - g["lo"]
    # hole groups: full internal cylinders with same axis direction and diameter
    hg = collections.defaultdict(list)
    for g in cyl.values():
        if g["full"] and g["internal"]:
            hg[(g["axis"], g["dia"])].append(g)
    F = dict(solids=len(solids), bmin=bmin, bmax=bmax, floors=floors,
             ext={a: bmax[a] - bmin[a] for a in AX}, cyl=list(cyl.values()),
             holes={k: v for k, v in hg.items()}, levels={a: sorted(v) for a, v in levels.items()})
    try:                                   # angled walls + countersinks
        F["incl"] = _inclined(comp, F)
    except Exception:
        F["incl"] = dict(angles=[], corners=[], csk={})
    return F


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
            anc_r = dict(type="rad", axis=a, center=ctr, r=g["dia"] / 2)
            if g.get("mid_dir"):
                view = {"z": ("x", "y"), "x": ("y", "z"), "y": ("x", "z")}[a]
                md = dict(zip(AX, g["mid_dir"]))
                anc_r["ang"] = math.degrees(math.atan2(md[view[1]], md[view[0]]))
            add(("rad", rv(g["dia"] / 2)), "radius_concave" if g["internal"] else "radius_convex", anc_r)
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
    # arc edges: tangent lines of partial arcs, combined with flat-face levels (e.g. hook gap to an arc top)
    for t in AX:
        planes = {rv(v) for v in [F["bmin"][t], F["bmax"][t]] + F["levels"][t]}
        tang = {}
        for g in F["cyl"]:
            for (tt, v, P) in g.get("quads", []):
                if tt == t and not any(abs(v - p) < 2e-3 for p in planes) and v not in tang:
                    tang[v] = (P, g["axis"])
        if not tang:
            continue
        pts = sorted([(v, None, None) for v in planes] + [(v, P, ga) for v, (P, ga) in tang.items()])
        def anchor_pt(v, P):
            if P is not None:
                return dict(P)
            q = dict(F["bmin"]); q[t] = v; return q
        for i in range(len(pts) - 1):
            (v1, P1, a1), (v2, P2, a2) = pts[i], pts[i + 1]
            if P1 is None and P2 is None:
                continue
            ha = a1 or a2
            add(("lin", t, rv(v2 - v1)), "arc_gap", dict(type="lin", axis=t, p1=anchor_pt(v1, P1), p2=anchor_pt(v2, P2), hole_axis=ha))
        lo = rv(F["bmin"][t]); hi = rv(F["bmax"][t])
        for v, (P, ga) in tang.items():
            for ref in (lo, hi):
                add(("lin", t, rv(abs(v - ref))), "arc_pos", dict(type="lin", axis=t, p1=anchor_pt(ref, None), p2=dict(P), hole_axis=ga))
    # pockets and steps: size of each recessed/raised flat floor, and its position from the part edges
    big = max(F["ext"].values())
    seen_fl = set()
    for fl in F.get("floors", []):
        if fl["area"] < 0.01 * big * big:
            continue
        a = fl["axis"]; o = [t for t in AX if t != a]
        sig = (a, rv(fl["level"]), tuple(rv(fl["hi"][t] - fl["lo"][t]) for t in o))
        dup = sig in seen_fl; seen_fl.add(sig)
        for t in o:
            ext_t = fl["hi"][t] - fl["lo"][t]
            if not (0.05 < ext_t < F["ext"][t] - 0.05):
                continue
            u = [q for q in o if q != t][0]
            P1 = {a: fl["level"], u: fl["lo"][u], t: fl["lo"][t]}; P2 = dict(P1); P2[t] = fl["hi"][t]
            add(("lin", t, rv(ext_t)), "pocket_size", dict(type="lin", axis=t, p1=P1, p2=P2, hole_axis=a),
                count=2 if dup else 1)
            for edge, ref in ((fl["lo"][t], F["bmin"][t]), (fl["hi"][t], F["bmax"][t])):
                if abs(edge - ref) > 0.05:
                    Q1 = dict(P1); Q1[t] = ref; Q2 = dict(P1); Q2[t] = edge
                    add(("lin", t, rv(abs(edge - ref))), "pocket_pos", dict(type="lin", axis=t, p1=Q1, p2=Q2, hole_axis=a))
    # spacing between centres of equal, aligned partial arcs (e.g. the flat between two fillets)
    parts_ = [g for g in F["cyl"] if not g["full"]]
    for i, g1 in enumerate(parts_):
        for g2 in parts_[i + 1:]:
            if g1["axis"] != g2["axis"] or abs(g1["dia"] - g2["dia"]) > 1e-3:
                continue
            a, o = g1["axis"], g1["other"]
            for k in (0, 1):
                if abs(g1["c"][1 - k] - g2["c"][1 - k]) < 1e-3 and abs(g1["c"][k] - g2["c"][k]) > 0.05:
                    zc = (g1["lo"] + g1["hi"]) / 2
                    P1 = {a: zc, o[0]: g1["c"][0], o[1]: g1["c"][1]}; P2 = {a: zc, o[0]: g2["c"][0], o[1]: g2["c"][1]}
                    add(("lin", o[k], rv(abs(g1["c"][k] - g2["c"][k]))), "arc_pitch",
                        dict(type="lin", axis=o[k], p1=P1, p2=P2, hole_axis=a, pitch_r=g1["dia"] / 2,
                             convex=not g1["internal"], dir=(P1[o[1 - k]] - g1["c"][1 - k])))
    # angled walls: angles (single or included V angle) and corner positions in the top view
    inc = F.get("incl") or dict(angles=[], corners=[], csk={})
    for dm in inc["angles"]:
        add(("ang", rv(dm["value"], 1)), "angle_incl" if dm.get("included") else "angle",
            dict(type="ang", view=dm["view"], vertex=dm["vertex"], p_line=dm["p_line"], p_ref=dm["p_ref"], r=dm["r"]))
    VAX = {"top": ("x", "y", "z"), "front": ("x", "z", "y"), "right": ("y", "z", "x")}
    for dm in inc["corners"]:
        h, v, n = VAX[dm["view"]]
        P1 = {h: dm["p1"][0], v: dm["p1"][1], n: F["bmax"][n]}; P2 = {h: dm["p2"][0], v: dm["p2"][1], n: F["bmax"][n]}
        add(("lin", dm["ax"], rv(dm["value"])), "incl_corner", dict(type="lin", axis=dm["ax"], p1=P1, p2=P2, hole_axis=n))
    # countersinks on hole diameters (thread-entry chamfers excluded)
    for (ax, dia), v in inc["csk"].items():
        c = C.get(("dia", dia))
        if c and "hole_dia" in c["srcs"]:
            nom = float(c["thread"][1:]) if c.get("thread") else 0
            if not (nom and v[0] <= 1.15 * nom):
                c["csk"] = v
    # numeric attributes
    out = []
    for c in C.values():
        c["rel_big"] = c["value"] / big if big else 0
        c["rel_axis"] = c["value"] / ext[c["axis"]] if c["axis"] and ext[c["axis"]] else 0
        out.append(c)
    return out


SOURCES = ["overall", "level_gap", "level_pos", "hole_dia", "ext_dia", "radius_concave", "radius_convex",
           "hole_pos", "hole_pitch", "hole_span", "bolt_circle", "angle", "angle_incl", "incl_corner", "arc_gap", "arc_pos", "arc_pitch", "pocket_size", "pocket_pos"]


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
        r = dict(kind_lin=c["kind"] == "lin", kind_dia=c["kind"] == "dia", kind_rad=c["kind"] == "rad", kind_ang=c["kind"] == "ang", has_csk=bool(c.get("csk")),
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
