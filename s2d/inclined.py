"""Geometry the axis-aligned feature extractor skips: angled walls and countersinks.

analyze(solid, F) -> dict(
    angles  = [dims]   angle of each distinct inclined face, in the view where it is seen edge-on
    corners = [dims]   positions of the (virtual, sharp) corners where inclined walls end
    csk     = {(axis, dia): (csk_dia, included_angle_deg)}  countersinks on hole groups
)
The returned dims are drawing specs (same format as select.to_specs output).
"""
import math

from OCP.BRepAdaptor import BRepAdaptor_Surface

CIRCLE_VIEW = {"z": "top", "x": "right", "y": "front"}


def p2(view, p):
    return {"front": (p["x"], p["z"]), "top": (p["x"], p["y"]), "right": (p["y"], p["z"])}[view]

AX = ("x", "y", "z")
# 2D coordinates (h, v) of each view, as 3D axis names
VIEW_AXES = {"front": ("x", "z"), "top": ("x", "y"), "right": ("y", "z")}


def _vec(v):
    return {"x": v.x, "y": v.y, "z": v.z}


def _r(x, n=2):
    return round(x + 0.0, n)


def _inclined_lines(solid, size):
    """Planar faces whose normal lies in a principal plane but not on an axis -> 2D lines in one view."""
    out = []
    for f in solid.Faces():
        if f.geomType() != "PLANE":
            continue
        n = _vec(f.normalAt(f.Center()))
        zero = [a for a in AX if abs(n[a]) < 1e-6]
        if len(zero) != 1:                 # axis-aligned (2 zeros) or compound angle (0 zeros)
            continue
        view = CIRCLE_VIEW[zero[0]]        # looking along the zero axis, the face is a line
        h, v = VIEW_AXES[view]
        nh, nv = n[h], n[v]
        d = (-nv, nh)                      # line direction in the view
        pts = [p2(view, _vec(p)) for p in (vx.Center() for vx in f.Vertices())]
        ts = [p[0] * d[0] + p[1] * d[1] for p in pts]
        a, b = pts[ts.index(min(ts))], pts[ts.index(max(ts))]
        L = math.dist(a, b)
        if L < 0.12 * size:                # tiny chamfers etc.
            continue
        off = a[0] * nh + a[1] * nv
        out.append(dict(view=view, n=(nh, nv), d=d, a=a, b=b, L=L, off=off))
    return out


def _axis_lines(solid, view):
    """Axis-aligned planar faces seen edge-on in `view` -> 2D segments."""
    h, v = VIEW_AXES[view]
    out = []
    for f in solid.Faces():
        if f.geomType() != "PLANE":
            continue
        n = _vec(f.normalAt(f.Center()))
        for k, ax in ((0, h), (1, v)):
            if abs(abs(n[ax]) - 1) < 1e-6:
                pts = [p2(view, _vec(vx.Center())) for vx in f.Vertices()]
                c = pts[0][k]
                o = [p[1 - k] for p in pts]
                if k == 0:
                    out.append(dict(n=(1.0, 0.0), d=(0.0, 1.0), a=(c, min(o)), b=(c, max(o)), off=c))
                else:
                    out.append(dict(n=(0.0, 1.0), d=(1.0, 0.0), a=(min(o), c), b=(max(o), c), off=c))
    return out


def _intersect(l1, l2):
    (a1, b1), c1 = l1["n"], l1["off"]
    (a2, b2), c2 = l2["n"], l2["off"]
    det = a1 * b2 - a2 * b1
    if abs(det) < 1e-6:
        return None
    return ((c1 * b2 - c2 * b1) / det, (a1 * c2 - a2 * c1) / det)


def _seg_dist(p, l):
    (ax, ay), (bx, by) = l["a"], l["b"]
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy or 1e-12
    t = max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / L2))
    return math.dist(p, (ax + t * dx, ay + t * dy))


def _countersinks(solid, F):
    out = {}
    for f in solid.Faces():
        if f.geomType() != "CONE":
            continue
        co = BRepAdaptor_Surface(f.wrapped).Cone()
        d = co.Axis().Direction()
        dv = {"x": d.X(), "y": d.Y(), "z": d.Z()}
        ax = [a for a in AX if abs(abs(dv[a]) - 1) < 1e-6]
        if not ax:
            continue
        ax = ax[0]
        apex = {"x": co.Apex().X(), "y": co.Apex().Y(), "z": co.Apex().Z()}
        o = [a for a in AX if a != ax]
        bb = f.BoundingBox()
        lo, hi = {"x": (bb.xmin, bb.xmax), "y": (bb.ymin, bb.ymax), "z": (bb.zmin, bb.zmax)}[ax]
        tan = math.tan(co.SemiAngle())
        rads = sorted(abs(z - apex[ax]) * tan for z in (lo, hi))
        for (hax, dia), groups in F["holes"].items():
            if hax != ax:
                continue
            for g in groups:
                if (abs(g["c"][0] - apex[o[0]]) < 1e-2 and abs(g["c"][1] - apex[o[1]]) < 1e-2
                        and abs(rads[0] - dia / 2) < 0.05 and rads[1] > dia / 2 + 0.1
                        # a bigger coaxial bore of the cone's wide diameter = stepped hole, not a countersink
                        and not any(c["axis"] == ax and abs(c["c"][0] - g["c"][0]) < 1e-2 and abs(c["c"][1] - g["c"][1]) < 1e-2
                                    and abs(c["dia"] - 2 * rads[1]) < 0.05 for c in F["cyl"])):
                    out[(ax, dia)] = (_r(2 * rads[1]), _r(math.degrees(2 * co.SemiAngle()), 1))
    return out


def analyze(solid, F):
    size = max(F["ext"].values())
    bmin = F["bmin"]
    lines = _inclined_lines(solid, size)

    # ---- angles ---------------------------------------------------------------------------
    # mirrored pairs (symmetric about the view's vertical centre line) -> one included angle at
    # their (virtual) apex; other inclined faces -> angle from the nearer axis at an endpoint.
    angles, seen = [], set()
    th = max(2.5, round(size / 28, 1))
    used = set()
    for i, ln in enumerate(lines):
        if i in used:
            continue
        h, v = VIEW_AXES[ln["view"]]
        cx = (F["bmin"][h] + F["bmax"][h]) / 2
        mirror = None
        for j, m in enumerate(lines):
            if j <= i or j in used or m["view"] != ln["view"]:
                continue
            if abs(m["n"][0] + ln["n"][0]) < 1e-4 and abs(m["n"][1] - ln["n"][1]) < 1e-4:
                ra = (2 * cx - ln["a"][0], ln["a"][1])            # reflected endpoint lies on m?
                if abs(ra[0] * m["n"][0] + ra[1] * m["n"][1] - m["off"]) < 1e-2:
                    mirror = j; break
        dx, dy = ln["d"]
        a_h = math.degrees(math.atan2(abs(dy), abs(dx)))          # from horizontal, 0..90
        if mirror is not None:
            m = lines[mirror]
            X = _intersect(ln, m)
            used.update((i, mirror))
            hb = (F["bmin"][h] - 1e-6, F["bmax"][h] + 1e-6); vb = (F["bmin"][v] - 1e-6, F["bmax"][v] + 1e-6)
            if X is not None and not (hb[0] <= X[0] <= hb[1] and vb[0] <= X[1] <= vb[1]):
                # the two faces meet outside the part (bevelled corners): dimension the real corner,
                # measured inside the material, once for the pair
                end = min((ln["a"], ln["b"]), key=lambda p: math.dist(p, X))
                far1 = max((ln["a"], ln["b"]), key=lambda p: math.dist(p, X))
                best = None
                for o in _axis_lines(solid, ln["view"]):
                    P = _intersect(ln, o)
                    if P is None:
                        continue
                    dd = math.dist(P, end)
                    if dd <= max(3.0, 0.08 * size) and _seg_dist(P, o) <= max(3.0, 0.08 * size) and (best is None or dd < best[0]):
                        best = (dd, P, o)
                on_box = lambda q: any(abs(q[0] - F[b][h]) < 1e-3 or abs(q[1] - F[b][v]) < 1e-3 for b in ("bmin", "bmax"))
                if best and not (on_box(best[1]) and on_box(far1)):
                    best = None                       # only bevels that cut an outer corner of the silhouette
                if best:
                    _, P, o = best
                    na = math.atan2(ln["n"][1], ln["n"][0])
                    # the adjacent face's outward normal: away from the part's middle
                    mid = ((F["bmin"][h] + F["bmax"][h]) / 2, (F["bmin"][v] + F["bmax"][v]) / 2)
                    m = o["n"] if (P[0] - mid[0]) * o["n"][0] + (P[1] - mid[1]) * o["n"][1] > 0 else (-o["n"][0], -o["n"][1])
                    if ln["n"][0] * (P[0] - mid[0]) + ln["n"][1] * (P[1] - mid[1]) < 0:
                        na += math.pi
                    dn = abs((math.degrees(na - math.atan2(m[1], m[0])) + 180) % 360 - 180)
                    val = _r(180 - dn, 1)
                    key = (ln["view"], val)
                    if key not in seen:
                        seen.add(key)
                        oa, ob = o["a"], o["b"]
                        refp = max((oa, ob), key=lambda q: math.dist(q, P))     # along the adjacent face, into the part
                        r = min(0.35 * math.dist(far1, P), 0.35 * math.dist(refp, P), 5 * th)
                        angles.append(dict(view=ln["view"], kind="ang", vertex=P, p_line=far1, p_ref=refp, r=r,
                                           value=val, included=True, text="2X <>", name=f'["ang", "{ln["view"]}", {val}]'))
                    continue
            if X is not None:
                val = _r(2 * (90 - a_h), 1) if a_h > 45 else _r(2 * a_h, 1)
                key = (ln["view"], val)
                if key in seen:
                    continue
                seen.add(key)
                far1 = max((ln["a"], ln["b"]), key=lambda p: math.dist(p, X))
                far2 = max((m["a"], m["b"]), key=lambda p: math.dist(p, X))
                r = min(0.5 * min(math.dist(far1, X), math.dist(far2, X)), 5 * th)
                angles.append(dict(view=ln["view"], kind="ang", vertex=X, p_line=far1, p_ref=far2, r=r,
                                   value=val, included=True, name=f'["ang", "{ln["view"]}", {val}]'))
                continue
        used.add(i)
        ref_h = a_h <= 45
        val = _r(a_h if ref_h else 90 - a_h, 1)
        key = (ln["view"], val)
        if key in seen:
            continue
        seen.add(key)
        a, b = ln["a"], ln["b"]
        vtx, far = (a, b) if (a[1] < b[1] if ref_h else a[0] < b[0]) else (b, a)
        if ref_h:
            ref = (vtx[0] + (1 if far[0] >= vtx[0] else -1) * ln["L"], vtx[1])
        else:
            ref = (vtx[0], vtx[1] + (1 if far[1] >= vtx[1] else -1) * ln["L"])
        angles.append(dict(view=ln["view"], kind="ang", vertex=vtx, p_line=far, p_ref=ref,
                           r=min(0.55 * ln["L"], 6 * th), value=val, name=f'["ang", "{ln["view"]}", {val}]'))

    # ---- virtual corners of inclined walls, in every view where such walls are seen edge-on ----
    corners = []
    tol = max(3.0, 0.08 * size)
    by_view = {}
    for ln in lines:
        by_view.setdefault(ln["view"], []).append(ln)
    for view, ls in by_view.items():
        others = ls + _axis_lines(solid, view)
        h, v = VIEW_AXES[view]
        cx = (F["bmin"][h] + F["bmax"][h]) / 2
        pts = []
        for ln in ls:
            for end in (ln["a"], ln["b"]):
                best = None
                for o in others:
                    if o is ln:
                        continue
                    X = _intersect(ln, o)
                    if X is None:
                        continue
                    dd = math.dist(X, end)
                    if dd <= tol and _seg_dist(X, o) <= tol and (best is None or dd < best[0]):
                        best = (dd, X)
                if best:
                    pts.append(best[1])
        uniq = []
        for p in pts:
            if any(math.dist(p, q) < 1e-2 for q in uniq):
                continue
            if p[0] > cx + 1e-3 and any(math.dist((2 * cx - p[0], p[1]), q) < 1e-2 for q in pts):
                continue
            uniq.append(p)
        done, vc = set(), []
        for p in uniq:
            for k, ax in ((0, h), (1, v)):
                val = _r(p[k] - bmin[ax])
                if val < 0.05 or (ax, val) in done:
                    continue
                done.add((ax, val))
                o_ = (bmin[h], p[1]) if k == 0 else (p[0], bmin[v])
                vc.append(dict(view=view, kind="h" if k == 0 else "v", ax=ax, p1=o_, p2=p, value=abs(p[k] - bmin[ax]),
                               name=f'["corner", "{ax}", {val}]', side="above" if k == 0 else "left"))
        if len(vc) <= 6:          # more = polygonal shape (hex etc.): positions would flood the view
            corners += vc
    per_view = {}
    angles = [a for a in angles if per_view.setdefault(a["view"], []).append(a) or len(per_view[a["view"]]) <= 3]
    return dict(angles=angles, corners=corners, csk=_countersinks(solid, F))
