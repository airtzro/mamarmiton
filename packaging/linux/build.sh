#!/usr/bin/env bash
# Build the Linux executable: dist/mamarmiton, one self-contained file (x86_64).
#
#   packaging/linux/build.sh            # uses /usr/bin/python3
#   PYTHON=python3.12 packaging/linux/build.sh
#
# Use the distribution's Python (with its python3-tk / tk package), not a uv or
# python.org build: those ship a Tk without Xft, which draws every font as a
# bitmap. The script refuses such a Tk.
#
# The binary runs on distributions whose glibc is at least as new as the one
# it was built on. Then run packaging/linux/install.sh to add it to the menu.
set -euo pipefail

cd "$(dirname "$0")/../.."
PYTHON=${PYTHON:-/usr/bin/python3}
VENV=build/linux-venv

if [ ! -x "$VENV/bin/python" ]; then
  "$PYTHON" -m venv "$VENV"
fi
"$VENV/bin/python" -m pip install --quiet --upgrade pip
"$VENV/bin/python" -m pip install --quiet -e ".[dev]"

"$VENV/bin/python" - <<'PY'
import os
import sys
import tkinter
if not os.environ.get("DISPLAY"):
    print("No display: cannot check that Tk uses Xft (run under xvfb-run to check).")
    sys.exit(0)
root = tkinter.Tk()
root.withdraw()
fonts = root.tk.call("::tk::pkgconfig", "get", "fontsystem")
root.destroy()
if fonts != "xft":
    sys.exit(f"This Tk draws fonts with '{fonts}', not Xft: use the distribution's Python (PYTHON=/usr/bin/python3).")
print(f"Tk {tkinter.TkVersion} with Xft: ok")
PY

"$VENV/bin/pyinstaller" --noconfirm --clean recette.spec
ls -lh dist/mamarmiton
echo "Built dist/mamarmiton. Install it with packaging/linux/install.sh"
