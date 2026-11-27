# -*- mode: python ; coding: utf-8 -*-
# Build the executable:  pyinstaller recette.spec
#   Windows -> dist/recette.exe
#   Linux   -> dist/mamarmiton (one file; see packaging/linux/build.sh)
#
# The AI model (model/*.gguf, ~1 GB) is NOT bundled: put the "model" folder
# next to the executable, or in the data folder (%LOCALAPPDATA%\recette_app\model
# on Windows, ~/.local/share/recette_app/model on Linux).

import sys

from PyInstaller.utils.hooks import collect_all, collect_dynamic_libs

WINDOWS = sys.platform.startswith("win")

datas = [("assets/icon.ico", "assets"), ("assets/fonts", "assets/fonts")]
binaries = []
# Pillow finds Tk through this module at run time; PyInstaller cannot see that import
hiddenimports = ["PIL._tkinter_finder", "PIL.ImageTk"]

# customtkinter ships theme JSON files that must be bundled
ctk_datas, ctk_binaries, ctk_hidden = collect_all("customtkinter")
datas += ctk_datas
binaries += ctk_binaries
hiddenimports += ctk_hidden

# llama-cpp-python is optional: bundle it only if installed
try:
    import llama_cpp  # noqa: F401

    llama_datas, llama_binaries, llama_hidden = collect_all("llama_cpp")
    datas += llama_datas
    binaries += llama_binaries + collect_dynamic_libs("llama_cpp")
    hiddenimports += llama_hidden
except ImportError:
    pass

a = Analysis(
    ["recette.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest"],
    noarchive=True,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="recette" if WINDOWS else "mamarmiton",
    debug=False,
    bootloader_ignore_signals=False,
    strip=not WINDOWS,  # smaller Linux binary
    upx=WINDOWS,  # UPX can break shared libraries on Linux
    console=False,  # no black console window behind the app (Windows)
    icon="assets/icon.ico" if WINDOWS else None,
)
