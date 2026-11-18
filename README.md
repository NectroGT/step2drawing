---
title: STEP to Engineering Drawing
emoji: 📐
colorFrom: blue
colorTo: gray
sdk: gradio
sdk_version: 6.32.0
python_version: "3.12"
app_file: app.py
pinned: false
short_description: Upload a STEP file, get dimensioned drawings (PDF/DXF)
---

# step2drawing: STEP file to engineering drawings

Give it a `.stp` / `.step` file and you get a dimensioned drawing set back (PDF + DXF). Dimensions are picked by a trained model (gradient-boosted classifier over candidate dimensions), trained on 3,400 variants of 34 hand-reviewed real parts (weighted ×3) plus 2,999 synthetic parts. It also handles angled walls, countersinks and arc-edge dimensions, and turns extruded parts so their profile is the front view.


## Use it in the browser

Open the web app, upload a `.stp` / `.step` file, and download the PDF drawings plus a ZIP with the DXF files and BOM. Nothing to install. To run the same app on your own machine: `pip install -r requirements.txt gradio` then `python app.py`, and open http://127.0.0.1:7860.

## 1. Install (once)

1. **Install Python 3.10–3.13.** On Windows, get it from python.org and tick **"Add python.exe to PATH"** during setup.
2. **Unzip this folder** somewhere permanent, for example `C:\tools\step2drawing`.
3. **Open a terminal in that folder** (Windows: Shift + right-click → "Open in Terminal") and run:
   ```
   pip install -r requirements.txt
   ```
   This takes a few minutes the first time (CadQuery / OpenCascade is about 200 MB).

> If `pip install cadquery` fails on your machine, use conda instead:
> `conda create -n s2d -c conda-forge python=3.12 cadquery` then `conda activate s2d` and `pip install -r requirements.txt`.

## 2. Use it

**Windows (easiest):** drag one or more `.stp` files onto **`convert.bat`**. The drawings appear in a `drawings` folder next to your STEP file.

**macOS:** double-click **`convert.command`**, drag the STEP file into the window, and press Enter.

**Command line (any OS):**
```
python convert.py mypart.stp
python convert.py a.stp b.step -o C:\out
python convert.py old_export.stp --units mm      # override wrong units in the file
```

## 3. What you get

For each STEP file, in `<output>/<file name>/`:

| file | what it is |
|---|---|
| `<name>_drawings.pdf` | A3 drawing set: an assembly sheet (for assemblies) plus one sheet per unique part |
| `<name>_one_sheet.pdf` / `.dxf` | Everything on one A3 sheet (assemblies with 2–8 unique parts) |
| `assembly.dxf` | Assembly drawing (assembled views, numbered balloons, parts list), editable in CAD |
| `parts/NN_<name>/drawing.dxf` | Each part's drawing, editable in AutoCAD, DraftSight, NX, SolidWorks and so on |
| `bom.csv` | Parts list: item, quantity, name, size, holes and threads |
| `report.json` | Units, parts found, glue layers skipped, warnings |

**What it does automatically:**
- **Units:** reads the units the file declares (mm or inch) and labels the drawings with them.
- **Part names:** taken from the STEP file (for example PLATE, BOLT). Single-body files that contain several parts are numbered PART 01, PART 02…
- **Assemblies:** split into unique parts, with quantities counted. Layers thinner than 0.2 mm (glue, foil) are skipped.
- **Threads:** tapped holes are recognised and called out as M-sizes (M3–M12).

## 4. Limits: always review before release

- **Dimensions are chosen by the model** (about 80% right on parts it never saw). Check every sheet, then delete or add dimensions in the DXF.
- **Complex parts** (more than 120 candidate dimensions, flagged in the output) need manual cleanup.
- **Tolerances, material and GD&T** in the title block are placeholders.
- **Parts must be axis-aligned.** Features that aren't along X, Y or Z are ignored.
- **Scale:** the PDFs are fitted to A3, so the scale is "NTS". Read values from the dimensions; don't measure the paper.

Speed: a small assembly takes about 10 s; a 130-solid machine base about 3 minutes.

## 5. Improving the model

The `training/` folder has the scripts used to build the current model:
1. Review parts on the Dimension Review page.
2. Save the labels.
3. Run `augment_real.py` → `relabel_real.py` → `eval_real.py` → `train_combined.py`.
4. Replace `models/selector_combined.joblib` with the new model.

You can also point the tool at a different model with `--model`.
