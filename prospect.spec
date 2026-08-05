# -*- mode: python ; coding: utf-8 -*-
"""
=============================================================================
  prospect.spec
  Empacota o PT Prospect num executável Windows que roda sem Python.

  Não rode diretamente — use:  python build_exe.py
=============================================================================
"""
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

# ── Driver do Playwright ───────────────────────────────────────────────────
# O Playwright fala com o navegador por um driver Node (node.exe + package/).
# Sem isso o .exe abre mas nenhum navegador sobe.
playwright_datas = collect_data_files("playwright", include_py_files=True)

hidden = [
    "playwright",
    "playwright.sync_api",
    "playwright._impl._driver",
    *collect_submodules("playwright"),
]

a = Analysis(
    ["src/prospect/__main__.py"],
    pathex=["src"],
    binaries=[],
    datas=playwright_datas,
    hiddenimports=hidden,
    hookspath=[],
    runtime_hooks=[],
    # Bibliotecas científicas/GUI que nada aqui usa — só inflam o pacote
    excludes=[
        "tkinter", "unittest", "pydoc", "doctest",
        "numpy", "pandas", "matplotlib", "scipy",
        "PIL", "PyQt5", "PySide2", "IPython", "pytest",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="PTProspect",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    # Aplicação de terminal: precisa de console para o menu Rich
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

# onedir (não onefile): o onefile extrai ~100 MB a cada execução e o
# Playwright fica bem mais lento. A pasta inteira vai zipada.
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="PTProspect",
)
