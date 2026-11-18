"""Compose several frameless DXF drawings onto one sheet (paper units: mm).

compose(cells, out_dxf, sheet=(420, 297), parts=[...], title_lines=[...])
  cells: list of dict(dxf=path, box=(x0, y0, x1, y1) on paper in mm, label="ITEM 1  PLATE  QTY 1")
"""
import ezdxf
from ezdxf import bbox
from ezdxf.addons import Importer
from ezdxf.math import Matrix44
from ezdxf.enums import TextEntityAlignment


def _place(src_path, tdoc, box, label, pad=4.0, label_h=3.0):
    sdoc = ezdxf.readfile(src_path)
    smsp = sdoc.modelspace()
    for d in list(smsp.query("DIMENSION")):      # explode so nothing depends on dimension styles/blocks
        d.explode()
    ext = ezdxf.bbox.extents(smsp)
    x0, y0, x1, y1 = box
    w, h = (x1 - x0) - 2 * pad, (y1 - y0) - 2 * pad - label_h * 1.8
    k = min(w / (ext.extmax.x - ext.extmin.x), h / (ext.extmax.y - ext.extmin.y))
    cx_src, cy_src = (ext.extmin.x + ext.extmax.x) / 2, (ext.extmin.y + ext.extmax.y) / 2
    cx, cy = (x0 + x1) / 2, y0 + pad + h / 2
    m = Matrix44.chain(Matrix44.translate(-cx_src, -cy_src, 0), Matrix44.scale(k, k, k), Matrix44.translate(cx, cy, 0))
    tmsp = tdoc.modelspace()
    before = set(e.dxf.handle for e in tmsp)
    imp = Importer(sdoc, tdoc)
    imp.import_entities(smsp)
    imp.import_tables()
    imp.finalize()
    for e in tmsp:
        if e.dxf.handle not in before:
            try:
                e.transform(m)
            except Exception:
                e.destroy() if e.is_alive else None
    tmsp.add_text(label, height=label_h, dxfattribs={"layer": "TEXT"}).set_placement(
        (x0 + 2, y1 - 2), align=TextEntityAlignment.TOP_LEFT)
    return k


def compose(cells, out_dxf, sheet=(420, 297), parts=(), title_lines=(), table_box=None, units_note=""):
    doc = ezdxf.new("R2010", setup=True)
    doc.header["$INSUNITS"] = 4
    doc.header["$LTSCALE"] = 0.35
    for name, color, lw in [("FRAME", 8, 18), ("BORDER", 7, 50), ("TEXT", 7, 25)]:
        if name not in doc.layers:
            doc.layers.add(name, color=color, lineweight=lw)
    msp = doc.modelspace()
    scales = [_place(c["dxf"], doc, c["box"], c["label"]) for c in cells]
    # parts list + title block
    x0, y0, x1, y1 = table_box
    cols = [("ITEM", 0.10), ("QTY", 0.10), ("PART NAME", 0.28), ("DESCRIPTION / SIZE", 0.52)]
    rows = [tuple(c for c, _ in cols)] + [tuple(r) for r in parts]
    tb_h = 5.5 * len(title_lines) + 2
    rh = min(6.0, ((y1 - y0) - tb_h) / len(rows))
    msp.add_lwpolyline([(x0, y0), (x1, y0), (x1, y0 + tb_h), (x0, y0 + tb_h)], close=True, dxfattribs={"layer": "BORDER"})
    for i, s in enumerate(title_lines):
        msp.add_text(s, height=3.0 if i == 0 else 2.4, dxfattribs={"layer": "TEXT"}).set_placement(
            (x0 + 2, y0 + tb_h - 1.5 - i * 5.5), align=TextEntityAlignment.TOP_LEFT)
    y = y0 + tb_h
    for row in rows:                    # header at the bottom, items stacked above (standard BOM order)
        x = x0
        for (hdr, frac), val in zip(cols, row):
            cw = (x1 - x0) * frac
            msp.add_lwpolyline([(x, y), (x + cw, y), (x + cw, y + rh), (x, y + rh)], close=True, dxfattribs={"layer": "BORDER"})
            mt = msp.add_mtext(str(val), dxfattribs={"layer": "TEXT", "char_height": min(2.5, rh * 0.45)})
            mt.set_location((x + 1.5, y + rh / 2), attachment_point=4)   # 4 = middle left
            x += cw
        y += rh
    doc.saveas(out_dxf)
    return scales


def render(dxf_path, pdf_path=None, png_path=None, sheet=(420, 297), dpi=150):
    import matplotlib; matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from ezdxf.addons.drawing import RenderContext, Frontend
    from ezdxf.addons.drawing.matplotlib import MatplotlibBackend
    from ezdxf.addons.drawing.config import Configuration, BackgroundPolicy, ColorPolicy
    doc = ezdxf.readfile(dxf_path)
    cfg = Configuration(background_policy=BackgroundPolicy.WHITE, color_policy=ColorPolicy.BLACK, lineweight_scaling=0.6)
    if sheet is None:                     # fit the drawing's own extents (model-space drawings)
        ext = bbox.extents(doc.modelspace())
        x0, y0, x1, y1 = ext.extmin.x, ext.extmin.y, ext.extmax.x, ext.extmax.y
        pad = 0.01 * max(x1 - x0, y1 - y0)
        x0, y0, x1, y1 = x0 - pad, y0 - pad, x1 + pad, y1 + pad
        W = 16.0; H = W * (y1 - y0) / (x1 - x0)
    else:
        x0, y0, x1, y1 = 0, 0, sheet[0], sheet[1]
        W, H = sheet[0] / 25.4, sheet[1] / 25.4
    fig = plt.figure(figsize=(W, H)); ax = fig.add_axes([0, 0, 1, 1])
    Frontend(RenderContext(doc), MatplotlibBackend(ax), config=cfg).draw_layout(doc.modelspace(), finalize=True)
    fig.set_size_inches(W, H); ax.set_position([0, 0, 1, 1])
    ax.set_xlim(x0, x1); ax.set_ylim(y0, y1); ax.set_aspect("equal")
    if pdf_path: fig.savefig(pdf_path)
    if png_path: fig.savefig(png_path, dpi=dpi, facecolor="white")
    plt.close(fig)
