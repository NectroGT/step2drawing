"""Assembly drawing: assembled views (no hidden lines), overall dimensions, item balloons,
parts list table and title block, as one DXF sheet.

make_assembly_drawing(items, dxf_path, png_path, title, units, sheet)
  items: list of dict(item=1, name="PLATE", desc="180 x 150 x 20", solids=[cq.Solid, ...])
"""
import math
import cadquery as cq
import ezdxf
from ezdxf.enums import TextEntityAlignment
from .drawing import project, edge_geom, gkey, bbox2d

ISO_D = (1 / math.sqrt(3), -1 / math.sqrt(3), 1 / math.sqrt(3))
ISO_X = (1 / math.sqrt(2), 1 / math.sqrt(2), 0.0)
ISO_Y = (-1 / math.sqrt(6), 1 / math.sqrt(6), 2 / math.sqrt(6))
TO2D = {"front": lambda p: (p[0], p[2]), "top": lambda p: (p[0], p[1]), "right": lambda p: (p[1], p[2]),
        "iso": lambda p: (sum(a * b for a, b in zip(p, ISO_X)), sum(a * b for a, b in zip(p, ISO_Y)))}


def visible_anchor(solid):
    """A point on the solid's surface facing the iso viewer (good place for a balloon leader)."""
    best, bp = -1e18, None
    for f in solid.Faces():
        c = f.Center(); p = (c.x, c.y, c.z)
        s = sum(a * b for a, b in zip(p, ISO_D))
        if s > best:
            best, bp = s, p
    return bp


def make_assembly_drawing(items, dxf_path, png_path=None, title="ASSEMBLY", units="mm", sheet="1 of 1", frame=True, iso_pos="right"):
    solids = [s for it in items for s in it["solids"]]
    comp = cq.Compound.makeCompound(solids)
    bb = comp.BoundingBox()
    size = max(bb.xlen, bb.ylen, bb.zlen)
    th = max(2.5, round(size / 45, 2)); gap = 8 * th; sp = 2.6 * th

    views = {}
    for v in ("front", "top", "right", "iso"):
        vis, _ = project(comp, v)                       # assembly views: visible lines only
        g, seen = [], set()
        for e in vis:
            x = edge_geom(e); k = gkey(x)
            if k not in seen:
                seen.add(k); g.append(x)
        views[v] = g
    b = {v: bbox2d(views[v]) for v in views}
    off = {"front": (0.0, 0.0)}
    off["top"] = (0.0, b["front"][3] - b["top"][1] + gap)
    off["right"] = (b["front"][2] - b["right"][0] + gap, 0.0)
    if iso_pos == "above":            # stack the 3D view above the top view (for tall areas)
        top_y1 = b["top"][3] + off["top"][1]
        off["iso"] = ((b["front"][0] + b["right"][2] + off["right"][0]) / 2 - (b["iso"][0] + b["iso"][2]) / 2,
                      top_y1 - b["iso"][1] + 1.6 * gap)
    else:
        off["iso"] = (b["right"][2] + off["right"][0] - b["iso"][0] + 2.2 * gap, b["front"][3] - b["iso"][1] + gap)

    doc = ezdxf.new("R2010", setup=True)
    doc.header["$INSUNITS"] = 1 if units == "inch" else 4
    for name, color, lt, lw in [("VISIBLE", 7, "CONTINUOUS", 50), ("DIM", 5, "CONTINUOUS", 18),
                                ("BALLOON", 1, "CONTINUOUS", 25), ("TEXT", 7, "CONTINUOUS", 25),
                                ("BORDER", 7, "CONTINUOUS", 50)]:
        doc.layers.add(name, color=color, linetype=lt, lineweight=lw)
    msp = doc.modelspace()
    T = lambda v, p: (p[0] + off[v][0], p[1] + off[v][1])
    for v, gs in views.items():
        for g in gs:
            a = {"layer": "VISIBLE"}
            if g[0] == "line":
                msp.add_line(T(v, g[1]), T(v, g[2]), dxfattribs=a)
            elif g[0] == "circle":
                msp.add_circle(T(v, g[1]), g[2], dxfattribs=a)
            else:
                msp.add_lwpolyline([T(v, p) for p in g[1]], dxfattribs=a)

    # overall dimensions
    ds = dict(dimtxt=th, dimasz=th * 0.9, dimexo=th * 0.4, dimexe=th * 0.5, dimgap=th * 0.35, dimdec=2,
              dimzin=8, dimdsep=ord("."), dimlfac=1.0, dimclrd=5, dimclre=5)
    fx0, fy0, fx1, fy1 = b["front"]; tx0, ty0, tx1, ty1 = b["top"]
    msp.add_linear_dim(base=(0, fy0 - 2 * sp), p1=T("front", (fx0, fy0)), p2=T("front", (fx1, fy0)), angle=0,
                       override=ds, dxfattribs={"layer": "DIM"}).render()
    msp.add_linear_dim(base=(fx0 - 2 * sp, 0), p1=T("front", (fx0, fy0)), p2=T("front", (fx0, fy1)), angle=90,
                       override=ds, dxfattribs={"layer": "DIM"}).render()
    msp.add_linear_dim(base=(tx0 - 2 * sp + off["top"][0], 0), p1=T("top", (tx0, ty0)), p2=T("top", (tx0, ty1)),
                       angle=90, override=ds, dxfattribs={"layer": "DIM"}).render()

    # balloons on the iso view
    ix0, iy0, ix1, iy1 = b["iso"]
    cx, cy = (ix0 + ix1) / 2, (iy0 + iy1) / 2
    rx, ry = (ix1 - ix0) / 2 + 3.5 * th, (iy1 - iy0) / 2 + 3.5 * th
    R = 1.4 * th
    anchors = []
    for it in items:
        s = max(it["solids"], key=lambda x: sum(a * b for a, b in zip(visible_anchor(x), ISO_D)))
        p = TO2D["iso"](visible_anchor(s))
        anchors.append((math.atan2(p[1] - cy, p[0] - cx), it, p))
    anchors.sort(key=lambda a: a[0])
    used = []
    sep = min(0.45, 2 * math.pi / max(1, len(anchors)) * 0.85)   # spacing that always fits around the view
    for ang, it, p in anchors:
        for _ in range(200):
            if not any(abs(math.remainder(ang - u, 2 * math.pi)) < sep for u in used):
                break
            ang += sep / 4
        used.append(ang)
        bx, by = cx + rx * math.cos(ang), cy + ry * math.sin(ang)
        B, P = T("iso", (bx, by)), T("iso", p)
        dx, dy = P[0] - B[0], P[1] - B[1]; L = math.hypot(dx, dy) or 1
        msp.add_circle(B, R, dxfattribs={"layer": "BALLOON"})
        msp.add_line((B[0] + dx / L * R, B[1] + dy / L * R), P, dxfattribs={"layer": "BALLOON"})
        msp.add_circle(P, th * 0.25, dxfattribs={"layer": "BALLOON"})
        h = msp.add_hatch(dxfattribs={"layer": "BALLOON"}); h.paths.add_edge_path().add_arc(P, th * 0.25, 0, 360)
        msp.add_text(str(it["item"]), height=th * 1.2, dxfattribs={"layer": "TEXT"}).set_placement(
            B, align=TextEntityAlignment.MIDDLE_CENTER)

    if not frame:
        doc.saveas(dxf_path)
        return
    # border, parts list, title block
    ext = ezdxf.bbox.extents(msp)
    m = 4 * th
    rowh = 2.0 * th
    cols = [("ITEM", 6), ("QTY", 6), ("PART NAME", 16), ("DESCRIPTION / SIZE", 26)]
    tw = sum(w for _, w in cols) * th
    nrows = len(items) + 1
    X1 = ext.extmax.x + m
    X0 = min(ext.extmin.x - m, X1 - tw - 6 * th)
    Y0 = ext.extmin.y - m - (nrows * rowh + 8 * th)
    Y1 = ext.extmax.y + m
    msp.add_lwpolyline([(X0, Y0), (X1, Y0), (X1, Y1), (X0, Y1)], close=True, dxfattribs={"layer": "BORDER"})
    # title block
    tbh = 6 * th; tx = X1 - tw
    msp.add_lwpolyline([(tx, Y0), (X1, Y0), (X1, Y0 + tbh), (tx, Y0 + tbh)], close=True, dxfattribs={"layer": "BORDER"})
    for i, s in enumerate([f"{title}   ASSEMBLY DRAWING", f"UNITS: {units.upper()}   SCALE: NTS   THIRD ANGLE PROJECTION",
                           f"SHEET {sheet}   DIMENSIONS: OVERALL ONLY"]):
        msp.add_text(s, height=th * 0.9, dxfattribs={"layer": "TEXT"}).set_placement(
            (tx + th, Y0 + tbh - (i + 1) * 1.8 * th), align=TextEntityAlignment.BOTTOM_LEFT)
    # parts list, stacked above the title block (header at the bottom, as is customary)
    y = Y0 + tbh
    rows = [("ITEM", "QTY", "PART NAME", "DESCRIPTION / SIZE")] + \
           [(str(it["item"]), str(len(it["solids"])), it["name"], it["desc"]) for it in items]
    for r, row in enumerate(rows):
        x = tx
        for (hdr, w), val in zip(cols, row):
            msp.add_lwpolyline([(x, y), (x + w * th, y), (x + w * th, y + rowh), (x, y + rowh)], close=True,
                               dxfattribs={"layer": "BORDER"})
            msp.add_text(val, height=th * 0.8, dxfattribs={"layer": "TEXT"}).set_placement(
                (x + 0.5 * th, y + rowh / 2), align=TextEntityAlignment.MIDDLE_LEFT)
            x += w * th
        y += rowh
    doc.saveas(dxf_path)

    if png_path:
        import matplotlib; matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from ezdxf.addons.drawing import RenderContext, Frontend
        from ezdxf.addons.drawing.matplotlib import MatplotlibBackend
        from ezdxf.addons.drawing.config import Configuration, BackgroundPolicy, ColorPolicy
        cfg = Configuration(background_policy=BackgroundPolicy.WHITE, color_policy=ColorPolicy.BLACK)
        W, H = X1 - X0, Y1 - Y0
        fig = plt.figure(figsize=(16, 16 * H / W)); ax = fig.add_axes([0, 0, 1, 1])
        Frontend(RenderContext(doc), MatplotlibBackend(ax), config=cfg).draw_layout(msp, finalize=True)
        fig.savefig(png_path, dpi=110, facecolor="white"); plt.close(fig)
