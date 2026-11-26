# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_all, collect_dynamic_libs

llama_datas, llama_binaries, llama_hidden = collect_all("llama_cpp")
llama_binaries += collect_dynamic_libs("llama_cpp")

a = Analysis(
    ['recette.py'],
    pathex=[],
    binaries=llama_binaries,
    datas=llama_datas,
    hiddenimports=llama_hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=True,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='recette',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    icon= "icon.ico",
)
