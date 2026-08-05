"""
=============================================================================
  prospect/__main__.py
  Ponto de entrada para `python -m prospect` e para o executável congelado.
=============================================================================
"""
from __future__ import annotations
import multiprocessing
import sys


def _selftest() -> int:
    """
    Diagnóstico: confirma que caminhos, banco e navegador funcionam.
    Rode com `PTProspect.exe --selftest` quando algo não abrir.
    """
    from prospect.config import APP_ROOT, DATA_DIR, DB_PATH, IS_FROZEN
    from prospect import runtime

    print("PT Prospect - autoteste\n" + "=" * 40)
    print(f"congelado (.exe) : {IS_FROZEN}")
    print(f"pasta do app     : {APP_ROOT}")
    print(f"pasta de dados   : {DATA_DIR}")
    print(f".env encontrado  : {(APP_ROOT / '.env').exists()}")

    print("\n[1/3] banco de dados...")
    from prospect.db import init_db, get_all_leads
    init_db()
    print(f"      OK - {len(get_all_leads())} leads em {DB_PATH.name}")

    print("\n[2/3] navegador disponivel...")
    if not runtime.bootstrap():
        print("      FALHOU - nao foi possivel obter o Chromium")
        return 1
    print(f"      OK - browsers em {runtime.browsers_dir_in_use() or 'local padrao'}")

    print("\n[3/3] abrindo o navegador...")
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        navegador = p.chromium.launch(channel=runtime.BROWSER_CHANNEL, headless=True)
        pagina = navegador.new_page()
        pagina.goto("about:blank")
        print(f"      OK - Chromium {navegador.version}")
        navegador.close()

    print("\n" + "=" * 40)
    print("Tudo funcionando.")
    return 0


def _run() -> None:
    from prospect.cli import main
    main()


if __name__ == "__main__":
    # Necessário no Windows: sem isso, o .exe pode se re-executar em loop
    # ao criar subprocessos.
    multiprocessing.freeze_support()

    try:
        if "--selftest" in sys.argv:
            sys.exit(_selftest())
        _run()
    except KeyboardInterrupt:
        sys.exit(130)
