#!/bin/bash
# macOS: double-click, then drag a .stp file into the Terminal window and press Enter.
cd "$(dirname "$0")"
read -r -p "Drag a STEP file here and press Enter: " F
F="${F%\'}"; F="${F#\'}"; F="${F% }"
python3 convert.py "$F" -o "$(dirname "$F")/drawings"
read -r -p "Done. Press Enter to close."
