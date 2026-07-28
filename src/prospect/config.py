"""
=============================================================================
  prospect/config.py
  Carrega configurações do .env e define defaults.
=============================================================================
"""
from __future__ import annotations
import os
from pathlib import Path
from dotenv import load_dotenv

# Carrega .env do diretório raiz do projeto
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_ENV_PATH = _PROJECT_ROOT / ".env"
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

# ── Caminhos ───────────────────────────────────────────────────────────────
DATA_DIR: Path = _PROJECT_ROOT / "data"
SESSIONS_DIR: Path = _PROJECT_ROOT / "sessions"
DB_PATH: Path = DATA_DIR / "leads.db"

DATA_DIR.mkdir(exist_ok=True)
SESSIONS_DIR.mkdir(exist_ok=True)
