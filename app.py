"""Web app: upload a STEP file, get dimensioned engineering drawings back.

Runs on Hugging Face Spaces (Gradio SDK) or locally:  python app.py
"""
import glob, html, os, shutil, tempfile, uuid, zipfile

import gradio as gr
import joblib

from convert import convert, HERE
from s2d.compose import render

MODEL = joblib.load(os.path.join(HERE, "models", "selector_combined.joblib"))
MAX_MB = 50
UNIT_CHOICES = ["From file", "mm", "inch", "cm", "m"]
UNIT_MM = {"inch": 25.4, "mm": 1.0, "cm": 10.0, "m": 1000.0}


def _zip_dir(folder, zip_path):
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for root, _, files in os.walk(folder):
            for f in files:
                if f.endswith(".step"):          # don't hand the uploaded geometry back
                    continue
                full = os.path.join(root, f)
                z.write(full, os.path.relpath(full, folder))


def _summary_html(r):
    e = lambda v: html.escape(str(v))
    stats = [("Units", r["units"]), ("Solids", r["solids"]), ("Unique parts", r["unique_parts"]),
             ("Time", f"{r['seconds']} s")]
    tiles = "".join(f'<div class="s2d-stat"><span>{e(k)}</span><b>{e(v)}</b></div>' for k, v in stats)
    rows = "".join(
        f"<tr><td class='num'>{e(p['item'])}</td><td class='name'>{e(p['name'])}</td><td class='num'>×{e(p['qty'])}</td>"
        f"<td>{e(p['description'])}</td><td class='num'>{e(p['selected'])}</td></tr>" for p in r["parts"])
    notes = "".join(f'<div class="s2d-note warn">Skipped {e(s["name"])} ×{e(s["qty"])}: {e(s["reason"])}</div>'
                    for s in r["skipped"])
    notes += "".join(f'<div class="s2d-note warn">{e(w)}</div>' for w in r["warnings"])
    return (f'<div class="s2d-summary"><div class="s2d-stats">{tiles}</div>'
            f'<div class="s2d-table-wrap"><table class="s2d-table"><thead><tr><th>Item</th><th>Part</th><th>Qty</th>'
            f'<th>Description</th><th>Dims</th></tr></thead><tbody>{rows}</tbody></table></div>{notes}'
            '<div class="s2d-note">Dimensions are chosen automatically by a trained model. '
            'Check them before using a drawing for manufacturing.</div></div>')


EMPTY = ('<div class="s2d-empty"><div class="s2d-empty-icon">⌗</div>'
         '<p><b>No drawings yet</b></p><p>Upload a STEP file and press <i>Make drawings</i>. '
         'The parts list and sheet previews appear here.</p></div>')


def run(step_file, units):
    if step_file is None:
        raise gr.Error("Upload a .stp or .step file first.")
    path = step_file if isinstance(step_file, str) else step_file.name
    if not path.lower().endswith((".stp", ".step")):
        raise gr.Error("That isn't a STEP file (.stp / .step).")
    if os.path.getsize(path) > MAX_MB * 1024 * 1024:
        raise gr.Error(f"File is larger than {MAX_MB} MB.")

    work = os.path.join(tempfile.gettempdir(), "s2d_" + uuid.uuid4().hex[:10])
    os.makedirs(work)
    force = (units, UNIT_MM[units]) if units in UNIT_MM else None
    try:
        out, r = convert(path, work, MODEL, force)
    except Exception as e:
        shutil.rmtree(work, ignore_errors=True)
        raise gr.Error(f"Conversion failed: {type(e).__name__}: {e}")

    stem = os.path.basename(out)
    files = [os.path.join(out, f"{stem}_drawings.pdf")]
    one = os.path.join(out, f"{stem}_one_sheet.pdf")
    if os.path.exists(one):
        files.insert(0, one)
    zip_path = os.path.join(work, f"{stem}_all.zip")
    _zip_dir(out, zip_path)
    files.append(zip_path)

    previews = []
    asm_png = os.path.join(out, "assembly.png")
    if os.path.exists(os.path.join(out, "assembly.dxf")):
        try:
            render(os.path.join(out, "assembly.dxf"), png_path=asm_png, sheet=None, dpi=110)
            previews.append((asm_png, "Assembly"))
        except Exception:
            pass
    for p in r["parts"]:
        hits = glob.glob(os.path.join(out, "parts", f"{p['item']:02d}_*", "drawing.png"))
        if hits:
            previews.append((hits[0], f"{p['item']}. {p['name']} ×{p['qty']}"))

    return gr.File(value=files, visible=True), gr.Gallery(value=previews, visible=True), _summary_html(r)


THEME = gr.themes.Soft(
    primary_hue=gr.themes.colors.blue,
    neutral_hue=gr.themes.colors.slate,
    radius_size=gr.themes.sizes.radius_md,
    font=[gr.themes.GoogleFont("Inter"), "system-ui", "sans-serif"],
    font_mono=[gr.themes.GoogleFont("JetBrains Mono"), "ui-monospace", "monospace"],
).set(
    body_background_fill="#f4f6f9",
    body_background_fill_dark="#0f1419",
    block_border_width="1px",
    block_shadow="0 1px 2px rgba(15, 23, 42, 0.04)",
    button_primary_background_fill="#1d4ed8",
    button_primary_background_fill_hover="#1e40af",
    button_large_padding="14px 20px",
)

CSS = """
.gradio-container { max-width: 1280px !important; margin: 0 auto !important; }
footer { display: none !important; }

.s2d-hero-wrap .html-container { padding: 0 !important; }
.s2d-hero {
  position: relative; overflow: hidden; border-radius: 14px; padding: 30px 32px 26px; margin-bottom: 6px;
  color: #e6eefc; background-color: #0f2a5c;
  background-image:
    linear-gradient(rgba(255,255,255,.06) 1px, transparent 1px),
    linear-gradient(90deg, rgba(255,255,255,.06) 1px, transparent 1px),
    linear-gradient(rgba(255,255,255,.03) 1px, transparent 1px),
    linear-gradient(90deg, rgba(255,255,255,.03) 1px, transparent 1px);
  background-size: 80px 80px, 80px 80px, 16px 16px, 16px 16px;
}
.s2d-hero h1 { margin: 0 0 6px; font-size: 28px; font-weight: 700; letter-spacing: -0.01em; color: #fff; }
.s2d-hero h1 span { color: #93c5fd; font-weight: 500; }
.s2d-hero code { background: rgba(255,255,255,.12) !important; color: #fff !important; padding: 1px 6px; border-radius: 4px; font-size: 13px; }
.s2d-hero p { margin: 0; max-width: 760px; line-height: 1.55; color: #c7d6f2; font-size: 15px; }
.s2d-chips { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 16px; }
.s2d-chips span {
  font-size: 12.5px; padding: 4px 10px; border-radius: 999px;
  background: rgba(147,197,253,.12); border: 1px solid rgba(147,197,253,.28); color: #dbe7fb;
}

.s2d-panel-title { font-size: 12px; font-weight: 600; letter-spacing: .06em; text-transform: uppercase;
  color: var(--body-text-color-subdued); margin: 2px 0 2px; }
.s2d-tip { font-size: 13px; color: var(--body-text-color-subdued); line-height: 1.5; }
.s2d-tip b { color: var(--body-text-color); font-weight: 600; }

.s2d-stats { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; margin-bottom: 14px; }
.s2d-stat { border: 1px solid var(--border-color-primary); border-radius: 10px; padding: 10px 14px;
  background: var(--background-fill-primary); }
.s2d-stat span { display: block; font-size: 12px; color: var(--body-text-color-subdued); }
.s2d-stat b { display: block; font-size: 20px; font-weight: 650; margin-top: 2px; font-variant-numeric: tabular-nums; }

.s2d-table-wrap { overflow-x: auto; border: 1px solid var(--border-color-primary); border-radius: 10px; }
.s2d-table { border: none !important; width: 100%; border-collapse: collapse; font-size: 14px; margin: 0 !important; }
.s2d-table th { text-align: left; font-size: 12px; font-weight: 600; text-transform: uppercase; letter-spacing: .04em;
  color: var(--body-text-color-subdued); background: var(--background-fill-secondary); padding: 9px 12px;
  border: none !important; border-bottom: 1px solid var(--border-color-primary) !important; }
.s2d-table td { padding: 9px 12px; border: none !important; border-bottom: 1px solid var(--border-color-primary) !important; }
.s2d-table tr:last-child td { border-bottom: none !important; }
.s2d-table td.num { font-variant-numeric: tabular-nums; white-space: nowrap; }
.s2d-table td.name { font-family: var(--font-mono); font-size: 13px; }

.s2d-note { font-size: 13px; color: var(--body-text-color-subdued); margin-top: 10px; line-height: 1.5; }
.s2d-note.warn { color: #92400e; background: #fef3c7; border: 1px solid #fcd34d; border-radius: 8px; padding: 8px 12px; }
.dark .s2d-note.warn { color: #fde68a; background: rgba(245,158,11,.12); border-color: rgba(245,158,11,.35); }

.s2d-empty { text-align: center; padding: 56px 24px; border: 1.5px dashed var(--border-color-primary);
  border-radius: 12px; color: var(--body-text-color-subdued); }
.s2d-empty p { margin: 4px 0; }
.s2d-empty b { color: var(--body-text-color); }
.s2d-empty-icon { font-size: 34px; opacity: .45; margin-bottom: 6px; }

.s2d-footer { text-align: center; font-size: 12.5px; color: var(--body-text-color-subdued); padding: 8px 0 4px; }

@media (max-width: 720px) {
  .s2d-hero { padding: 22px 18px; }
  .s2d-hero h1 { font-size: 22px; }
  .s2d-stats { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
"""

HERO = """
<div class="s2d-hero">
  <h1>STEP <span>→</span> Engineering Drawing</h1>
  <p>Upload a <code>.stp</code> / <code>.step</code> part or assembly and get a dimensioned drawing set back,
  as PDF and editable DXF.</p>
  <div class="s2d-chips">
    <span>Orthographic views + hidden lines</span><span>Auto-selected dimensions</span>
    <span>Title block</span><span>Assembly sheet with balloons</span><span>Bill of materials</span>
  </div>
</div>
"""

with gr.Blocks(title="STEP → Engineering Drawing") as demo:
    gr.HTML(HERO, padding=False, elem_classes="s2d-hero-wrap")
    with gr.Row(equal_height=False):
        with gr.Column(scale=4, min_width=300):
            with gr.Group():
                inp = gr.File(label="STEP file", file_types=[".stp", ".step"], height=170)
                units = gr.Dropdown(UNIT_CHOICES, value="From file", label="Units",
                                    info="Override only if the file declares the wrong units.")
            btn = gr.Button("Make drawings", variant="primary", size="lg")
            gr.HTML(f'<div class="s2d-tip">Up to <b>{MAX_MB} MB</b>. Large assemblies can take a few minutes. '
                    'Uploaded files are processed temporarily and not kept.</div>')
            out_files = gr.File(label="Downloads: PDF, plus a ZIP with DXF and BOM", file_count="multiple",
                                interactive=False, visible=False)
        with gr.Column(scale=8, min_width=360):
            gr.HTML('<div class="s2d-panel-title">Results</div>')
            summary = gr.HTML(EMPTY)
            gallery = gr.Gallery(label="Sheet previews", columns=2, height="auto", object_fit="contain",
                                 visible=False, preview=False)
    gr.HTML(padding=False, value='<div class="s2d-footer">Dimensions are picked by a gradient-boosted model trained on reviewed '
            'real parts and synthetic parts. Always check a drawing before manufacturing.</div>')
    btn.click(run, [inp, units], [out_files, gallery, summary], concurrency_limit=2)

if __name__ == "__main__":
    demo.queue(max_size=20).launch(theme=THEME, css=CSS)
