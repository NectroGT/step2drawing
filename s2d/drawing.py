"""Turn a part (shape + dimension specs) into a dimensioned third-angle drawing (DXF + PNG)."""
import math, os
_DEBUG = bool(os.environ.get('S2D_DEBUG'))
import cadquery as cq
import ezdxf
from ezdxf.enums import TextEntityAlignment
from OCP.HLRBRep import HLRBRep_Algo, HLRBRep_HLRToShape
from OCP.HLRAlgo import HLRAlgo_Projector
from OCP.gp import gp_Ax2, gp_Pnt, gp_Dir
from OCP.BRepLib import BRepLib

# view direction (towards viewer) and x direction of the 2D view
VIEWS = {
    "front": ((0, -1, 0), (1, 0, 0)),   # 2D = (x, z)
    "top":   ((0, 0, 1), (1, 0, 0)),    # 2D = (x, y)
    "right": ((1, 0, 0), (0, 1, 0)),    # 2D = (y, z)
    "iso":   ((1, -1, 1), (1, 1, 0)),
}


def _edges(comp):
    if comp is None or comp.IsNull():
        return []
    BRepLib.BuildCurves3d_s(comp)
    return cq.Shape.cast(comp).Edges()


def project_fast(shape, view):
    """Mesh-based hidden-line removal: much faster for big assemblies, visually the same at drawing scale."""
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.HLRBRep import HLRBRep_PolyAlgo, HLRBRep_PolyHLRToShape
    d, x = VIEWS[view]
    bb = shape.BoundingBox()
    BRepMesh_IncrementalMesh(shape.wrapped, max(bb.xlen, bb.ylen, bb.zlen) / 800, False, 0.3, True)
    algo = HLRBRep_PolyAlgo(shape.wrapped)
    algo.Projector(HLRAlgo_Projector(gp_Ax2(gp_Pnt(0, 0, 0), gp_Dir(*d), gp_Dir(*x))))
    algo.Update()
    h = HLRBRep_PolyHLRToShape(); h.Update(algo)
    vis = _edges(h.VCompound()) + _edges(h.OutLineVCompound())
    hid = _edges(h.HCompound()) + _edges(h.OutLineHCompound())
    return vis, hid


def project(shape, view, fast=None):
    if fast is None:
        fast = len(shape.Faces()) > 1500
    if fast:
        return project_fast(shape, view)
    d, x = VIEWS[view]
    algo = HLRBRep_Algo()
    algo.Add(shape.wrapped)
    algo.Projector(HLRAlgo_Projector(gp_Ax2(gp_Pnt(0, 0, 0), gp_Dir(*d), gp_Dir(*x))))
    algo.Update(); algo.Hide()
    h = HLRBRep_HLRToShape(algo)
    vis = _edges(h.VCompound()) + _edges(h.OutLineVCompound())
    hid = _edges(h.HCompound()) + _edges(h.OutLineHCompound())
    return vis, hid


def edge_geom(e):
    """Return ('circle', c, r) / ('line', p1, p2) / ('poly', pts) in 2D."""
    gt = e.geomType()
    if gt == "LINE":
        a, b = e.startPoint(), e.endPoint()
        return ("line", (a.x, a.y), (b.x, b.y))
    if gt == "CIRCLE" and e.IsClosed():
        c = e.arcCenter(); return ("circle", (c.x, c.y), e.radius())
    n = 48 if gt in ("CIRCLE", "ELLIPSE") else 64
    pts = e.positions([i / n for i in range(n + 1)])
    return ("poly", [(p.x, p.y) for p in pts])


def gkey(g, nd=3):
    """Direction-independent key so coincident edges are drawn once."""
    if g[0] == "line":
        return ("line",) + tuple(sorted([tuple(round(c, nd) for c in g[1]), tuple(round(c, nd) for c in g[2])]))
    if g[0] == "circle":
        return ("circle", round(g[1][0], nd), round(g[1][1], nd), round(g[2], nd))
    pts = [tuple(round(c, nd) for c in p) for p in g[1]]
    return ("poly",) + tuple(min(pts, pts[::-1]))


def bbox2d(geoms):
    xs, ys = [], []
    for g in geoms:
        if g[0] == "line":
            xs += [g[1][0], g[2][0]]; ys += [g[1][1], g[2][1]]
        elif g[0] == "circle":
            xs += [g[1][0] - g[2], g[1][0] + g[2]]; ys += [g[1][1] - g[2], g[1][1] + g[2]]
        else:
            xs += [p[0] for p in g[1]]; ys += [p[1] for p in g[1]]
    return min(xs), min(ys), max(xs), max(ys)


def _scaled(part, k):
    """Copy of the part with geometry and dimension anchors scaled by k (drawing scale k:1)."""
    S = lambda p: (p[0] * k, p[1] * k)
    dims = []
    for d in part["dims"]:
        d = dict(d)
        for f in ("p1", "p2", "c", "vertex", "p_line", "p_ref"):
            if f in d:
                d[f] = S(d[f])
        for f in ("r", "at"):
            if d.get(f) is not None:
                d[f] = d[f] * k
        dims.append(d)
    centers = [dict(c, c=S(c["c"]), r=c["r"] * k) for c in part.get("centers", [])]
    axes = []
    for a in part.get("axes", []):
        a = dict(a)
        for f in ("p1", "p2", "center"):
            if f in a:
                a[f] = S(a[f])
        if "circle" in a:
            a["circle"] *= k
        axes.append(a)
    shape = part["shape"].val() if hasattr(part["shape"], "val") else part["shape"]
    return dict(part, shape=shape.scale(k), dims=dims, centers=centers, axes=axes)


def drawing_scale(size, units):
    mm = size * {"inch": 25.4, "cm": 10.0, "m": 1000.0, "foot": 304.8}.get(units, 1.0)
    return 5 if mm < 20 else 2 if mm < 50 else 1


def make_drawing(part, part_id, dxf_path, png_path=None, frame=True, _shift=None, _depth=0):
    part_in = part
    shift = _shift or {}
    shape = part["shape"].val() if hasattr(part["shape"], "val") else part["shape"]
    size = max(shape.BoundingBox().xlen, shape.BoundingBox().ylen, shape.BoundingBox().zlen)
    SCL = part.get("scale") or drawing_scale(size, part.get("units", "mm"))
    if SCL != 1:
        part = _scaled(part, SCL); shape = part["shape"]; size *= SCL
    th = max(2.5, round(size / 28, 1))         # text height
    gap = 9 * th                               # spacing between views (room for dims)
    sp = 2.6 * th                              # spacing between dimension tiers
    # all text reads horizontally, so a stack of vertical dimensions needs room for the widest value
    def _w(d):
        t = (d.get("text") or "<>").replace("<>", f'{d["value"]:g}')
        return len(t) * 0.68 * th
    vdims = [d for d in part["dims"] if d["kind"] == "v"]
    spv = max(sp, max((_w(d) for d in vdims), default=0) + 1.2 * th)
    vt = max((d.get("tier", 1) for d in vdims if d.get("at") is None), default=0)
    gapx = gap + vt * (spv - sp)

    proj = {}
    for v in VIEWS:
        vis, hid = project(shape, v)
        vg, seen = [], set()
        for g in map(edge_geom, vis):
            gk = gkey(g)
            if gk not in seen:
                seen.add(gk); vg.append(g)
        hg = []
        for g in map(edge_geom, hid):   # drop hidden edges duplicated or covered by visible ones
            gk = gkey(g)
            if gk not in seen:
                seen.add(gk); hg.append(g)
        proj[v] = (vg, hg)
    bb = {v: bbox2d(proj[v][0] + proj[v][1]) for v in VIEWS}

    # third-angle layout: top above front, right to the right of front, iso top-right
    off = {"front": (0.0, 0.0)}
    off["top"] = (0.0, bb["front"][3] - bb["top"][1] + gap)
    off["right"] = (bb["front"][2] - bb["right"][0] + gapx, 0.0)
    off["iso"] = (bb["front"][2] - bb["iso"][0] + gapx, bb["front"][3] - bb["iso"][1] + gap)
    for v_, (dx_, dy_) in shift.items():          # extra room found necessary on an earlier pass
        off[v_] = (off[v_][0] + dx_, off[v_][1] + dy_)
    # align top view x-extents with front, right view z with front (same projection => automatic)

    doc = ezdxf.new("R2010", setup=True)
    doc.header["$LTSCALE"] = max(0.4, size / 90)
    doc.header["$INSUNITS"] = 4  # mm
    for name, color, lt, lw in [("VISIBLE", 7, "CONTINUOUS", 50), ("HIDDEN", 8, "DASHED2", 25),
                                ("CENTER", 1, "CENTER2", 18), ("DIM", 5, "CONTINUOUS", 18),
                                ("TEXT", 7, "CONTINUOUS", 25), ("BORDER", 7, "CONTINUOUS", 50)]:
        doc.layers.add(name, color=color, linetype=lt, lineweight=lw)
    msp = doc.modelspace()
    ds = dict(dimtxt=th, dimasz=th * 0.9, dimexo=th * 0.4, dimexe=th * 0.5, dimgap=th * 0.35,
              dimdec=2, dimzin=8, dimdsep=ord('.'), dimlfac=1.0 / SCL, dimtih=1, dimtoh=1, dimclrd=5, dimclre=5, dimclrt=7)

    def _drop(dr):
        name = dr.dimension.dxf.geometry
        msp.delete_entity(dr.dimension)
        if name in doc.blocks:
            doc.blocks.delete_block(name, safe=False)

    def T(view, p):
        o = off[view]; return (p[0] + o[0], p[1] + o[1])

    for v in VIEWS:
        for layer, geoms in (("VISIBLE", proj[v][0]), ("HIDDEN", proj[v][1])):
            for g in geoms:
                a = {"layer": layer}
                if g[0] == "line":
                    msp.add_line(T(v, g[1]), T(v, g[2]), dxfattribs=a)
                elif g[0] == "circle":
                    msp.add_circle(T(v, g[1]), g[2], dxfattribs=a)
                else:
                    msp.add_lwpolyline([T(v, p) for p in g[1]], dxfattribs=a)

    # centre marks and centre lines
    for c in part.get("centers", []):
        (x, y), r = T(c["view"], c["c"]), c["r"]; e = r + th * 0.8
        msp.add_line((x - e, y), (x + e, y), dxfattribs={"layer": "CENTER"})
        msp.add_line((x, y - e), (x, y + e), dxfattribs={"layer": "CENTER"})
    for a in part.get("axes", []):
        if "circle" in a:
            msp.add_circle(T(a["view"], a.get("center", (0, 0))), a["circle"], dxfattribs={"layer": "CENTER"})
        else:
            msp.add_line(T(a["view"], a["p1"]), T(a["view"], a["p2"]), dxfattribs={"layer": "CENTER"})

    # dimensions: each one is tried in a few positions; the first whose text collides with nothing wins
    def build(dm):
        v = dm["view"]; x0, y0, x1, y1 = bb[v]
        if dm["kind"] in ("h", "v"):
            p1, p2 = T(v, dm["p1"]), T(v, dm["p2"])
            if dm["kind"] == "h":
                if dm.get("at") is not None:
                    yb = dm["at"] + off[v][1]
                else:
                    yb = (y0 - dm["tier"] * sp if dm["side"] == "below" else y1 + dm["tier"] * sp) + off[v][1]
                base, angle = ((p1[0] + p2[0]) / 2, yb), 0
                meas = abs(p2[0] - p1[0])
            else:
                if dm.get("at") is not None:
                    xb = dm["at"] + off[v][0]
                else:
                    xb = (x0 - dm["tier"] * spv if dm["side"] == "left" else x1 + dm["tier"] * spv) + off[v][0]
                base, angle = (xb, (p1[1] + p2[1]) / 2), 90
                meas = abs(p2[1] - p1[1])
            assert abs(meas / SCL - dm["value"]) < 1e-3, (part_id, dm["name"], meas / SCL, dm["value"])
            kw = {}
            vt_ = {"dimtad": 0} if dm["kind"] == "v" else {}      # horizontal text centred in a vertical dim line
            if dm.get("shift"):
                kw["location"] = (base[0] + dm["shift"], base[1]) if angle == 0 else (base[0], base[1] + dm["shift"])
            d = msp.add_linear_dim(base=base, p1=p1, p2=p2, angle=angle, text=dm.get("text") or "<>",
                                   override=dict(ds, dimtmove=0, **vt_) if kw else dict(ds, **vt_), dxfattribs={"layer": "DIM"}, **kw)
        elif dm["kind"] == "dia":
            assert abs(2 * dm["r"] / SCL - dm["value"]) < 1e-3
            txt = (dm.get("text") or "<>").replace("Ø<>", "<>")
            if len(txt) > 8:                 # long callout: leader out of the view, horizontal text
                a_ = dm["angle"] if math.cos(math.radians(dm["angle"])) < 0 else 180 - dm["angle"]   # leader to the left
                if dm.get("down"):
                    a_ = -a_ if math.sin(math.radians(a_)) > 0 else a_
                elif dm.get("up"):
                    a_ = -a_ if math.sin(math.radians(a_)) < 0 else a_
                cx, cy = T(v, dm["c"]); ux, uy = math.cos(math.radians(a_)), math.sin(math.radians(a_))
                bx0, by0, bx1, by1 = x0 + off[v][0], y0 + off[v][1], x1 + off[v][0], y1 + off[v][1]
                tx = ((bx1 if ux > 0 else bx0) - cx) / ux if abs(ux) > 1e-6 else 1e9
                ty = ((by1 if uy > 0 else by0) - cy) / uy if abs(uy) > 1e-6 else 1e9
                t = min(tx, ty) + 2.5 * th
                loc = (cx + t * ux, cy + t * uy)
                k = dm.get("stack", 0)
                if k:                                # stack further callouts above/below the first one
                    loc = (loc[0], loc[1] + (k if uy >= 0 else -k) * 2.6 * th)
                d = msp.add_diameter_dim(center=(cx, cy), radius=dm["r"], location=loc, text=txt,
                                         override=dict(ds, dimtoh=1, dimtix=0), dxfattribs={"layer": "DIM"})
            else:                            # short callout: leader out of the circle, horizontal text
                cx, cy = T(v, dm["c"]); ux, uy = math.cos(math.radians(dm["angle"])), math.sin(math.radians(dm["angle"]))
                t = dm["r"] + (2.0 + dm.get("push", 0)) * th
                d = msp.add_diameter_dim(center=(cx, cy), radius=dm["r"], location=(cx + t * ux, cy + t * uy),
                                         text=txt, override=dict(ds, dimtoh=1, dimtix=0), dxfattribs={"layer": "DIM"})
        elif dm["kind"] == "ang":
            c = T(v, dm["vertex"]); pa = T(v, dm["p_line"]); pb = T(v, dm["p_ref"])
            if (pa[0] - c[0]) * (pb[1] - c[1]) - (pa[1] - c[1]) * (pb[0] - c[0]) < 0:
                pa, pb = pb, pa                      # ezdxf measures counter-clockwise p1 -> p2
            ua = math.atan2(pa[1] - c[1], pa[0] - c[0]); ub = math.atan2(pb[1] - c[1], pb[0] - c[0])
            if ub < ua:
                ub += 2 * math.pi
            mid = (ua + ub) / 2
            base = (c[0] + dm["r"] * math.cos(mid), c[1] + dm["r"] * math.sin(mid))
            val = f'{dm["value"]:.1f}'.rstrip("0").rstrip(".") + "°"
            txt = (dm.get("text") or "<>").replace("<>", val)
            d = msp.add_angular_dim_3p(base=base, center=c, p1=pa, p2=pb, text=txt,
                                       override=ds, dxfattribs={"layer": "DIM"})
        elif dm.get("outside"):              # convex arc: text outside the part, leader onto the arc
            cx, cy = T(v, dm["c"]); ux, uy = math.cos(math.radians(dm["angle"])), math.sin(math.radians(dm["angle"]))
            bx0, by0, bx1, by1 = x0 + off[v][0], y0 + off[v][1], x1 + off[v][0], y1 + off[v][1]
            tx = ((bx1 if ux > 0 else bx0) - cx) / ux if abs(ux) > 1e-6 else 1e9
            ty = ((by1 if uy > 0 else by0) - cy) / uy if abs(uy) > 1e-6 else 1e9
            t = max(min(tx, ty), dm["r"]) + (1.5 + dm.get("push", 0)) * th
            d = msp.add_radius_dim(center=(cx, cy), radius=dm["r"], location=(cx + t * ux, cy + t * uy),
                                   text=(dm.get("text") or "<>").replace("R<>", "<>"),
                                   override=dict(ds, dimtoh=1, dimtix=0), dxfattribs={"layer": "DIM"})
        elif dm.get("inside") and dm["r"] > 2.2 * th:   # concave arc with room: text in the hollow, at the centre
            cx, cy = T(v, dm["c"]); ux, uy = math.cos(math.radians(dm["angle"])), math.sin(math.radians(dm["angle"]))
            f_ = 0.15 + dm.get("push", 0) * 0.2
            d = msp.add_radius_dim(center=(cx, cy), radius=dm["r"], location=(cx - f_ * dm["r"] * ux, cy - f_ * dm["r"] * uy),
                                   text=(dm.get("text") or "<>").replace("R<>", "<>"),
                                   override=dict(ds, dimtih=1, dimtix=1), dxfattribs={"layer": "DIM"})
        else:                                  # leader from outside the arc, horizontal text
            cx, cy = T(v, dm["c"]); ux, uy = math.cos(math.radians(dm["angle"])), math.sin(math.radians(dm["angle"]))
            t = dm["r"] + (2.0 + dm.get("push", 0)) * th
            d = msp.add_radius_dim(center=(cx, cy), radius=dm["r"], location=(cx + t * ux, cy + t * uy),
                                   text=(dm.get("text") or "<>").replace("R<>", "<>"),
                                   override=dict(ds, dimtoh=1, dimtix=0), dxfattribs={"layer": "DIM"})
        return d


    def tw(dm):                                       # rough text width
        return max(2, len(str(dm.get("text") or "")) + len(f'{dm["value"]:g}')) * 0.62 * th

    def variants(dm):
        k = dm["kind"]
        if k in ("h", "v"):
            w = tw(dm)
            out = [dm] + [dict(dm, shift=f * w) for f in (0.9, -0.9, 1.8, -1.8)]
            if dm.get("at") is None:
                out += [dict(dm, tier=dm["tier"] + 1), dict(dm, tier=dm["tier"] + 1, shift=0.9 * w)]
            return out
        if k == "ang":
            return [dict(dm, r=dm["r"] * f) for f in (1.0, 0.7, 1.35, 1.7, 0.5)]
        if k == "dia" and len((dm.get("text") or "<>").replace("Ø<>", "<>")) > 8:
            return [dict(dm, stack=j, **o) for o in ({}, {"down": 1}, {"up": 1}) for j in (0, 1, -1, 2, -2)]
        if dm.get("outside") or (dm.get("inside") and dm["r"] > 2.2 * th):
            return [dict(dm, angle=dm["angle"] + da, push=p) for p in (0, 1.5) for da in (0, 15, -15, 30, -30)]
        return [dict(dm, angle=dm["angle"] + da, push=p) for p in (0, 2.0) for da in (0, 30, -30, 60, -60, 90, -90, 135, -135, 180)]

    def parts_of(d):
        blk = doc.blocks.get(d.dimension.dxf.geometry)
        boxes, segs = [], []
        for e in blk:
            t = e.dxftype()
            if t in ("MTEXT", "TEXT"):
                ext = ezdxf.bbox.extents([e])
                if ext.has_data:
                    g = 0.2 * th
                    x0_, y0_, x1_, y1_ = ext.extmin.x, ext.extmin.y, ext.extmax.x, ext.extmax.y
                    try:
                        n = len(e.plain_text() if t == "MTEXT" else e.dxf.text)
                    except Exception:
                        n = 0
                    rot = abs((e.dxf.get("rotation", 0) or 0) % 180 - 90) < 45       # vertical text
                    want = n * 0.68 * th                     # rendered width runs wider than ezdxf's estimate
                    if rot and (y1_ - y0_) < want:
                        cy_ = (y0_ + y1_) / 2; y0_, y1_ = cy_ - want / 2, cy_ + want / 2
                    elif not rot and (x1_ - x0_) < want:
                        cx_ = (x0_ + x1_) / 2; x0_, x1_ = cx_ - want / 2, cx_ + want / 2
                    boxes.append((x0_ - g, y0_ - g, x1_ + g, y1_ + g))
            elif t == "LINE":
                segs.append(((e.dxf.start.x, e.dxf.start.y), (e.dxf.end.x, e.dxf.end.y)))
        return boxes, segs

    def seg_hits_box(a, b, bx):
        x0_, y0_, x1_, y1_ = bx
        if max(a[0], b[0]) < x0_ or min(a[0], b[0]) > x1_ or max(a[1], b[1]) < y0_ or min(a[1], b[1]) > y1_:
            return False
        for t in (0.0, 0.2, 0.4, 0.5, 0.6, 0.8, 1.0):      # sampled test is plenty at drawing scale
            px, py = a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])
            if x0_ <= px <= x1_ and y0_ <= py <= y1_:
                return True
        return False

    def ovl(p, q):
        return max(0.0, min(p[2], q[2]) - max(p[0], q[0])) * max(0.0, min(p[3], q[3]) - max(p[1], q[1]))

    geo = []                                            # visible part edges as segments (sheet coordinates)
    for gv in VIEWS:
        for g in proj[gv][0]:
            pts = [g[1], g[2]] if g[0] == "line" else (
                [(g[1][0] + g[2] * math.cos(t_ * math.pi / 8), g[1][1] + g[2] * math.sin(t_ * math.pi / 8)) for t_ in range(17)]
                if g[0] == "circle" else g[1])
            pts = [T(gv, p_) for p_ in pts]
            geo += list(zip(pts[:-1], pts[1:]))

    placed, txt_boxes, dim_segs, by_view = [], [], [], {}
    # fixed-position dimensions first (their extension lines cannot move), movable callouts last
    order = {"h": 0, "v": 0, "ang": 1, "rad": 2, "dia": 2}
    view_boxes = [(bb[gv][0] + off[gv][0], bb[gv][1] + off[gv][1], bb[gv][2] + off[gv][0], bb[gv][3] + off[gv][1])
                  for gv in VIEWS]
    for dm in sorted(part["dims"], key=lambda d: order.get(d["kind"], 3)):
        best = None
        free_text = dm.get("outside") or (dm["kind"] == "dia" and len((dm.get("text") or "<>").replace("Ø<>", "<>")) > 8)
        for cand in variants(dm):
            d = build(cand); d.render()
            for e in doc.blocks.get(d.dimension.dxf.geometry):     # every value reads horizontally
                if e.dxftype() in ("MTEXT", "TEXT") and abs(e.dxf.get("rotation", 0) or 0) > 1e-6:
                    e.dxf.rotation = 0
            boxes, segs = parts_of(d)
            score = 0.0
            for bx in boxes:
                score += sum(100.0 * (1 + ovl(bx, q)) for q in txt_boxes if ovl(bx, q) > 0)
                score += sum(3.0 for a, b in dim_segs if seg_hits_box(a, b, bx))
                score += sum(1.0 for a, b in geo if seg_hits_box(a, b, bx))
                if free_text:                            # text that is meant to sit outside the views
                    score += sum(20.0 for q in view_boxes if ovl(bx, q) > 0)
            for a, b in segs:                            # this dimension's lines through earlier text
                score += sum(3.0 for q in txt_boxes if seg_hits_box(a, b, q))
            if _DEBUG:
                print("  ", cand["name"], {k: cand.get(k) for k in ("stack", "down", "up", "shift", "tier", "angle", "push") if cand.get(k) is not None}, round(score, 1))
            if best is None or score < best[0]:
                if best is not None:
                    _drop(best[1])
                best = (score, d, boxes, segs, cand)
            else:
                _drop(d)
            if score == 0:
                break
        _, d, boxes, segs, cand = best
        txt_boxes += boxes; dim_segs += segs
        placed.append(cand)
        by_view.setdefault(cand["view"], []).append(d.dimension)

    # keep every view's dimensions clear of the other views: grow the gaps and lay the sheet out again
    g_ = 1.5 * th
    grp = {}
    for gv in VIEWS:
        X = [bb[gv][0] + off[gv][0], bb[gv][1] + off[gv][1], bb[gv][2] + off[gv][0], bb[gv][3] + off[gv][1]]
        if by_view.get(gv):
            e_ = ezdxf.bbox.extents(by_view[gv])
            if e_.has_data:
                X = [min(X[0], e_.extmin.x), min(X[1], e_.extmin.y), max(X[2], e_.extmax.x), max(X[3], e_.extmax.y)]
        grp[gv] = X
    need = {}
    def hit(a, b):
        return grp[a][0] < grp[b][2] + g_ and grp[b][0] < grp[a][2] + g_ and grp[a][1] < grp[b][3] + g_ and grp[b][1] < grp[a][3] + g_
    def add_shift(v_, dx_, dy_):
        ox, oy = need.get(v_, (0.0, 0.0)); need[v_] = (max(ox, dx_), max(oy, dy_))
    if hit("front", "top"):
        add_shift("top", 0.0, grp["front"][3] + g_ - grp["top"][1])
    if hit("front", "right"):
        add_shift("right", grp["front"][2] + g_ - grp["right"][0], 0.0)
    if hit("top", "right"):
        add_shift("right", grp["top"][2] + g_ - grp["right"][0], 0.0)
    for a in ("front", "top", "right"):
        if hit(a, "iso"):
            dx_ = grp[a][2] + g_ - grp["iso"][0]; dy_ = grp[a][3] + g_ - grp["iso"][1]
            add_shift("iso", dx_, 0.0) if dx_ <= dy_ else add_shift("iso", 0.0, dy_)
    if need and _depth < 4:
        tot = dict(shift)
        for v_, (dx_, dy_) in need.items():
            ox, oy = tot.get(v_, (0.0, 0.0)); tot[v_] = (ox + max(dx_, 0.0), oy + max(dy_, 0.0))
        for v_ in ("top", "right"):              # the 3D view sits beyond both: keep it beyond them
            if v_ in need:
                ox, oy = tot.get("iso", (0.0, 0.0))
                tot["iso"] = (ox + max(need[v_][0], 0.0), oy + max(need[v_][1], 0.0))
        return make_drawing(part_in, part_id, dxf_path, png_path, frame, _shift=tot, _depth=_depth + 1)

    # notes
    for n in part.get("notes", []):
        v = n["view"]; x0, y0, x1, y1 = bb[v]
        y = (y1 + 2.4 * sp) if n["at"] == "above" else (y0 - 3.2 * sp)
        msp.add_text(n["text"], height=th, dxfattribs={"layer": "TEXT"}).set_placement(
            T(v, (x0, y)), align=TextEntityAlignment.BOTTOM_LEFT)

    if not frame:                      # bare views + dimensions, for placing on a combined sheet
        doc.saveas(dxf_path)
        return placed
    # border + title block
    ext = ezdxf.bbox.extents(msp)
    m = 4 * th
    X0, Y0, X1, Y1 = ext.extmin.x - m, ext.extmin.y - m - 7 * th, ext.extmax.x + m, ext.extmax.y + m
    tbw = 42 * th
    X0 = min(X0, X1 - tbw - 2 * th)          # keep the title block inside the border
    msp.add_lwpolyline([(X0, Y0), (X1, Y0), (X1, Y1), (X0, Y1)], close=True, dxfattribs={"layer": "BORDER"})
    tx = X1 - tbw
    msp.add_lwpolyline([(tx, Y0), (X1, Y0), (X1, Y0 + 6 * th), (tx, Y0 + 6 * th)], close=True,
                       dxfattribs={"layer": "BORDER"})
    lines = [f"PART: {part_id}   FAMILY: {part['family'].upper()}",
             f"UNITS: {part.get('units', 'mm').upper()}   SCALE: {SCL}:1   THIRD ANGLE PROJECTION",
             "UNLESS NOTED: TOL ±0.1   MATERIAL: AL 6061-T6"]
    for i, s in enumerate(lines):
        msp.add_text(s, height=th * 0.9, dxfattribs={"layer": "TEXT"}).set_placement(
            (tx + th, Y0 + 6 * th - (i + 1) * 1.8 * th), align=TextEntityAlignment.BOTTOM_LEFT)
    doc.saveas(dxf_path)

    if png_path:
        import matplotlib; matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from ezdxf.addons.drawing import RenderContext, Frontend
        from ezdxf.addons.drawing.matplotlib import MatplotlibBackend
        from ezdxf.addons.drawing.config import Configuration, BackgroundPolicy, ColorPolicy
        cfg = Configuration(background_policy=BackgroundPolicy.WHITE, color_policy=ColorPolicy.BLACK,
                            lineweight_scaling=1.0)
        W, H = X1 - X0, Y1 - Y0
        fig = plt.figure(figsize=(14, 14 * H / W) if W > H else (14 * W / H, 14))
        ax = fig.add_axes([0, 0, 1, 1])
        Frontend(RenderContext(doc), MatplotlibBackend(ax), config=cfg).draw_layout(msp, finalize=True)
        fig.savefig(png_path, dpi=110, facecolor="white"); plt.close(fig)
    return placed
