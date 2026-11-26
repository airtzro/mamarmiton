# -*- mode: python ; coding: utf-8 -*-
# Build the Windows executable:  pyinstaller recette.spec
#
# The AI model (model/*.gguf, ~1 GB) is NOT bundled: put the "model" folder
# next to recette.exe (or in %LOCALAPPDATA%\recette_app\model).

from PyInstaller.utils.hooks import collect_all, collect_dynamic_libs

datas = [("assets/icon.ico", "assets")]
binaries = []
hiddenimports = []

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
    name="recette",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,  # no black console window behind the app
    icon="assets/icon.ico",
)
