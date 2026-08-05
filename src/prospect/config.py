"""
=============================================================================
  prospect/config.py
  Carrega configurações do .env e define defaults.
=============================================================================
"""
from __future__ import annotations
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# True quando rodando a partir do .exe gerado pelo PyInstaller
IS_FROZEN: bool = getattr(sys, "frozen", False)


def _app_root() -> Path:
    """
    Raiz onde ficam .env, data/ e sessions/.

    No .exe, `__file__` aponta para dentro da pasta interna do PyInstaller,
    então usamos a pasta do executável — o app fica portátil e os dados
    sobrevivem entre execuções.
    """
    if IS_FROZEN:
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent.parent


APP_ROOT: Path = _app_root()

# Carrega .env de onde o app está instalado
_ENV_PATH = APP_ROOT / ".env"
load_dotenv(_ENV_PATH)


def _get(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def _getint(key: str, default: int) -> int:
    try:
        return int(os.environ.get(key, default))
    except (TypeError, ValueError):
        return default


def _getbool(key: str, default: bool) -> bool:
    val = os.environ.get(key, "").strip().lower()
    if val in ("1", "true", "yes", "on"):
        return True
    if val in ("0", "false", "no", "off"):
        return False
    return default


def _getlist(key: str, default: str = "") -> list[str]:
    raw = os.environ.get(key, default)
    return [s.strip() for s in raw.split(",") if s.strip()]


# ── Credenciais Instagram ──────────────────────────────────────────────────
IG_USERNAME: str = _get("IG_USERNAME")
IG_PASSWORD: str = _get("IG_PASSWORD")

# ── Navegador ──────────────────────────────────────────────────────────────
HEADLESS: bool = _getbool("HEADLESS", False)

# ── Prospecção ─────────────────────────────────────────────────────────────
SEED_ACCOUNTS: list[str] = _getlist("SEED_ACCOUNTS")
SEARCH_HASHTAGS: list[str] = _getlist("SEARCH_HASHTAGS", "personaltrainer,personaltrainerbrasil,treinopersonal")

# ── Filtros ────────────────────────────────────────────────────────────────
MIN_FOLLOWERS: int = _getint("MIN_FOLLOWERS", 2000)
MAX_FOLLOWERS: int = _getint("MAX_FOLLOWERS", 50000)

# ── Rate Limiting ──────────────────────────────────────────────────────────
DELAY_MIN: int = _getint("DELAY_MIN", 3)
DELAY_MAX: int = _getint("DELAY_MAX", 8)

# ── Supabase ───────────────────────────────────────────────────────────────
# URL do projeto (ex: https://xxxxxxxx.supabase.co)
SUPABASE_URL: str = _get("SUPABASE_URL").rstrip("/")
# Use a SERVICE ROLE KEY: a tabela tem RLS habilitado sem policy pública.
SUPABASE_KEY: str = _get("SUPABASE_SERVICE_KEY") or _get("SUPABASE_KEY")
SUPABASE_TABLE: str = _get("SUPABASE_TABLE", "leads")
# Envia cada lead pro Supabase durante a prospecção
SUPABASE_SYNC: bool = _getbool("SUPABASE_SYNC", True)
SUPABASE_TIMEOUT: int = _getint("SUPABASE_TIMEOUT", 15)

# ── Localização ────────────────────────────────────────────────────────────
# Cidade padrão dos leads quando não é possível inferir da bio (opcional)
DEFAULT_CITY: str = _get("DEFAULT_CITY")

# ── Caminhos ───────────────────────────────────────────────────────────────
DATA_DIR: Path = APP_ROOT / "data"
SESSIONS_DIR: Path = APP_ROOT / "sessions"
DB_PATH: Path = DATA_DIR / "leads.db"

# Navegadores embutidos na distribuição (opcional — ver prospect/runtime.py)
BROWSERS_DIR: Path = APP_ROOT / "browsers"

DATA_DIR.mkdir(parents=True, exist_ok=True)
SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
