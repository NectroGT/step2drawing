"""Turn a part (shape + dimension specs) into a dimensioned third-angle drawing (DXF + PNG)."""
import math
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


def make_drawing(part, part_id, dxf_path, png_path=None, frame=True):
    shape = part["shape"].val() if hasattr(part["shape"], "val") else part["shape"]
    size = max(shape.BoundingBox().xlen, shape.BoundingBox().ylen, shape.BoundingBox().zlen)
    th = max(2.5, round(size / 28, 1))         # text height
    gap = 9 * th                               # spacing between views (room for dims)
    sp = 2.6 * th                              # spacing between dimension tiers

    proj = {}
    for v in VIEWS:
        vis, hid = project(shape, v)
        vg, seen = [], set()
        for g in map(edge_geom, vis):
            k = gkey(g)
            if k not in seen:
                seen.add(k); vg.append(g)
        hg = []
        for g in map(edge_geom, hid):   # drop hidden edges duplicated or covered by visible ones
            k = gkey(g)
            if k not in seen:
                seen.add(k); hg.append(g)
        proj[v] = (vg, hg)
    bb = {v: bbox2d(proj[v][0] + proj[v][1]) for v in VIEWS}

    # third-angle layout: top above front, right to the right of front, iso top-right
    off = {"front": (0.0, 0.0)}
    off["top"] = (0.0, bb["front"][3] - bb["top"][1] + gap)
    off["right"] = (bb["front"][2] - bb["right"][0] + gap, 0.0)
    off["iso"] = (bb["front"][2] - bb["iso"][0] + gap, bb["front"][3] - bb["iso"][1] + gap)
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
              dimdec=2, dimzin=8, dimdsep=ord('.'), dimlfac=1.0, dimtih=0, dimtoh=0, dimclrd=5, dimclre=5, dimclrt=7)

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

    # dimensions
    placed = []
    for dm in part["dims"]:
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
                    xb = (x0 - dm["tier"] * sp if dm["side"] == "left" else x1 + dm["tier"] * sp) + off[v][0]
                base, angle = (xb, (p1[1] + p2[1]) / 2), 90
                meas = abs(p2[1] - p1[1])
            assert abs(meas - dm["value"]) < 1e-3, (part_id, dm["name"], meas, dm["value"])
            d = msp.add_linear_dim(base=base, p1=p1, p2=p2, angle=angle, text=dm.get("text") or "<>",
                                   override=ds, dxfattribs={"layer": "DIM"})
        elif dm["kind"] == "dia":
            assert abs(2 * dm["r"] - dm["value"]) < 1e-3
            d = msp.add_diameter_dim(center=T(v, dm["c"]), radius=dm["r"], angle=dm["angle"],
                                     text=(dm.get("text") or "<>").replace("Ø<>", "<>"), override=ds, dxfattribs={"layer": "DIM"})
        else:
            d = msp.add_radius_dim(center=T(v, dm["c"]), radius=dm["r"], angle=dm["angle"],
                                   text=(dm.get("text") or "<>").replace("R<>", "<>"), override=ds, dxfattribs={"layer": "DIM"})
        d.render()
        placed.append(dm)

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
             f"UNITS: {part.get('units', 'mm').upper()}   SCALE: 1:1   THIRD ANGLE PROJECTION",
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
