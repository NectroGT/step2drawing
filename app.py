"""Web app: upload a STEP file, get dimensioned engineering drawings back.

Runs on Hugging Face Spaces (Gradio SDK) or locally:  python app.py
"""
import glob, os, shutil, tempfile, uuid, zipfile

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

    lines = [f"**Units:** {r['units']}  ·  **Solids:** {r['solids']}  ·  **Unique parts:** {r['unique_parts']}  ·  {r['seconds']} s", "",
             "| Item | Part | Qty | Description | Dims |", "|---|---|---|---|---|"]
    for p in r["parts"]:
        lines.append(f"| {p['item']} | {p['name']} | {p['qty']} | {p['description']} | {p['selected']} |")
    for s in r["skipped"]:
        lines.append(f"\nSkipped: {s['name']} ×{s['qty']} ({s['reason']})")
    for w in r["warnings"]:
        lines.append(f"\n⚠️ {w}")
    lines.append("\n_Dimensions are chosen automatically by a trained model. Check them before using a drawing for manufacturing._")
    return files, previews, "\n".join(lines)


with gr.Blocks(title="STEP → Engineering Drawing") as demo:
    gr.Markdown("# STEP → Engineering Drawing\n"
                "Upload a `.stp` / `.step` part or assembly. You get orthographic views with hidden lines, "
                "auto-selected dimensions, a title block, an assembly sheet with balloons and a BOM, as PDF and DXF.")
    with gr.Row():
        with gr.Column(scale=1):
            inp = gr.File(label="STEP file", file_types=[".stp", ".step"])
            units = gr.Dropdown(UNIT_CHOICES, value="From file", label="Units",
                                info="Use an override only if the file declares the wrong units.")
            btn = gr.Button("Make drawings", variant="primary")
            out_files = gr.File(label="Downloads (PDF, and a ZIP with DXF + BOM)", file_count="multiple")
        with gr.Column(scale=2):
            summary = gr.Markdown()
            gallery = gr.Gallery(label="Preview", columns=2, height="auto")
    btn.click(run, [inp, units], [out_files, gallery, summary], concurrency_limit=2)
    gr.Markdown("Uploaded files are processed temporarily and not kept. Large assemblies can take a few minutes.")

if __name__ == "__main__":
    demo.queue(max_size=20).launch()
