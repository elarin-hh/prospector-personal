"""
=============================================================================
  prospect/runtime.py
  Bootstrap do Playwright quando o app roda como .exe.

  O PyInstaller empacota o código e o driver do Playwright, mas NÃO os
  navegadores (o Chromium tem ~416 MB). Este módulo resolve os dois cenários:

    1. Distribuição COM navegador: existe uma pasta browsers/ ao lado do
       .exe → aponta o Playwright para ela. Funciona 100% offline.
    2. Distribuição SEM navegador: baixa o Chromium no primeiro uso, para
       dentro de browsers/, e nas próximas execuções cai no caso 1.

  Precisa rodar ANTES de qualquer sync_playwright().start().
=============================================================================
"""
from __future__ import annotations
import os
import subprocess
import sys
from pathlib import Path
from typing import Callable, Optional

from prospect.config import BROWSERS_DIR, IS_FROZEN

# Canal usado em todos os launches. Evita o chromium_headless_shell (+270 MB)
# quando headless=True: usamos o Chromium completo nos dois modos.
BROWSER_CHANNEL = "chromium"

_BROWSER_PREFIX = "chromium-"


def configure_console() -> None:
    """
    Garante que o console do Windows aceite os emojis da interface.

    Sem isso o .exe morre no banner: o console abre em cp1252, o Rich cai
    no renderizador legado e o primeiro emoji levanta UnicodeEncodeError.
    Chame antes de criar o Console() do Rich.
    """
    if sys.platform == "win32":
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            kernel32.SetConsoleOutputCP(65001)  # UTF-8
            kernel32.SetConsoleCP(65001)
        except Exception:
            pass

    # errors="replace": um caractere exótico degrada para "?" em vez de
    # derrubar o programa inteiro.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


def _has_chromium(path: Path) -> bool:
    """True se a pasta contém um Chromium instalado pelo Playwright."""
    if not path.is_dir():
        return False
    return any(
        child.is_dir() and child.name.startswith(_BROWSER_PREFIX)
        for child in path.iterdir()
    )


def browsers_dir_in_use() -> Optional[Path]:
    """Pasta de navegadores ativa, se estiver definida via env var."""
    raw = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "").strip()
    if raw and raw not in ("0", "1"):
        return Path(raw)
    return None


def setup_browser_path() -> Optional[Path]:
    """
    Aponta o Playwright para a pasta browsers/ ao lado do executável.

    Só interfere quando congelado: em desenvolvimento o Playwright usa o
    local padrão (%LOCALAPPDATA%\\ms-playwright), que já funciona.
    Retorna a pasta configurada, ou None se usamos o padrão.
    """
    if not IS_FROZEN:
        return None

    # Respeita configuração explícita do usuário
    if os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
        return browsers_dir_in_use()

    BROWSERS_DIR.mkdir(parents=True, exist_ok=True)
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(BROWSERS_DIR)
    return BROWSERS_DIR


def chromium_installed() -> bool:
    """Verifica se há Chromium disponível onde o Playwright vai procurar."""
    configured = browsers_dir_in_use()
    if configured is not None:
        return _has_chromium(configured)

    # Local padrão do Playwright
    local = os.environ.get("LOCALAPPDATA")
    if local and _has_chromium(Path(local) / "ms-playwright"):
        return True
    return _has_chromium(Path.home() / "AppData" / "Local" / "ms-playwright")


def install_chromium(on_status: Optional[Callable[[str], None]] = None) -> bool:
    """
    Baixa o Chromium via Playwright. São ~416 MB, então avisamos o usuário.
    Retorna True se terminou com sucesso.
    """
    emit = on_status or (lambda msg: print(msg))

    destino = browsers_dir_in_use()
    emit("📥 Chromium não encontrado — baixando (~416 MB, só na primeira vez)...")
    if destino:
        emit(f"   Destino: {destino}")
    emit("   Isso pode levar alguns minutos. Não feche a janela.")

    if IS_FROZEN:
        # No .exe não há python.exe: usamos o driver do Playwright embutido
        from playwright._impl._driver import compute_driver_executable
        node, cli = compute_driver_executable()
        comando = [str(node), str(cli), "install", "chromium"]
    else:
        comando = [sys.executable, "-m", "playwright", "install", "chromium"]

    try:
        resultado = subprocess.run(comando, capture_output=True, text=True, timeout=1800)
    except subprocess.TimeoutExpired:
        emit("❌ Download excedeu 30 minutos. Verifique sua conexão e tente de novo.")
        return False
    except Exception as exc:
        emit(f"❌ Não consegui executar o download: {exc}")
        return False

    if resultado.returncode != 0:
        detalhe = (resultado.stderr or resultado.stdout or "").strip()
        emit(f"❌ Falha no download do Chromium: {detalhe[:400]}")
        return False

    emit("✅ Chromium instalado!")
    return True


def bootstrap(on_status: Optional[Callable[[str], None]] = None) -> bool:
    """
    Prepara o ambiente do Playwright. Chame uma vez, no início do programa.
    Retorna False se não há navegador e não foi possível instalar.
    """
    setup_browser_path()

    if chromium_installed():
        return True

    return install_chromium(on_status=on_status)
