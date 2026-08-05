"""
=============================================================================
  prospect/db.py
  Persistência de leads em SQLite + espelhamento no Supabase.

  O SQLite é a fonte de verdade local (funciona offline). Cada gravação
  tenta subir o lead pro Supabase; se falhar, a linha fica marcada como
  não-sincronizada e pode ser reenviada depois (sync_pending_leads).
=============================================================================
"""
from __future__ import annotations
import sqlite3
import csv
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from prospect import supabase_sync
from prospect.config import DB_PATH, DATA_DIR
from prospect.location import extract_location, resolve_location
from prospect.models import (
    Lead, LeadStatus, ContactStatus, ProspectStats, CONTACT_STATUS_LABELS,
)


# Colunas adicionadas depois da primeira versão do schema — aplicadas via
# ALTER TABLE em bancos que já existem, para não perder os leads coletados.
_ADDED_COLUMNS: dict[str, str] = {
    "city": "TEXT DEFAULT ''",
    "state": "TEXT DEFAULT ''",
    "contact_status": "TEXT DEFAULT 'not_contacted'",
    "contact_channel": "TEXT DEFAULT ''",
    "contacted_at": "TEXT DEFAULT ''",
    "contact_attempts": "INTEGER DEFAULT 0",
    "last_contact_error": "TEXT DEFAULT ''",
    "synced_at": "TEXT DEFAULT ''",
}


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    """Cria a tabela de leads se não existir e aplica migrações pendentes."""
    conn = _connect()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS leads (
            username TEXT PRIMARY KEY,
            full_name TEXT DEFAULT '',
            bio TEXT DEFAULT '',
            followers INTEGER DEFAULT 0,
            following INTEGER DEFAULT 0,
            posts_count INTEGER DEFAULT 0,
            bio_link TEXT DEFAULT '',
            whatsapp TEXT DEFAULT '',
            whatsapp_source TEXT DEFAULT '',
            profile_url TEXT DEFAULT '',
            is_verified INTEGER DEFAULT 0,
            is_business INTEGER DEFAULT 0,
            last_post_date TEXT DEFAULT '',
            source_account TEXT DEFAULT '',
            city TEXT DEFAULT '',
            state TEXT DEFAULT '',
            status TEXT DEFAULT 'new',
            contact_status TEXT DEFAULT 'not_contacted',
            contact_channel TEXT DEFAULT '',
            contacted_at TEXT DEFAULT '',
            contact_attempts INTEGER DEFAULT 0,
            last_contact_error TEXT DEFAULT '',
            score INTEGER DEFAULT 0,
            created_at TEXT DEFAULT '',
            notes TEXT DEFAULT '',
            synced_at TEXT DEFAULT ''
        )
    """)

    # Migração de bancos criados antes das colunas de cidade/contato
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(leads)")}
    for column, ddl in _ADDED_COLUMNS.items():
        if column not in existing:
            conn.execute(f"ALTER TABLE leads ADD COLUMN {column} {ddl}")

    conn.execute("CREATE INDEX IF NOT EXISTS leads_city_idx ON leads (city)")
    conn.execute("CREATE INDEX IF NOT EXISTS leads_contact_status_idx ON leads (contact_status)")
    conn.commit()
    conn.close()


def _write_lead(conn: sqlite3.Connection, lead: Lead) -> None:
    """Grava o lead no SQLite preservando campos de contato já registrados."""
    conn.execute("""
        INSERT INTO leads (
            username, full_name, bio, followers, following, posts_count,
            bio_link, whatsapp, whatsapp_source, profile_url,
            is_verified, is_business, last_post_date, source_account,
            city, state, status, contact_status, contact_channel,
            contacted_at, contact_attempts, last_contact_error,
            score, created_at, notes, synced_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '')
        ON CONFLICT(username) DO UPDATE SET
            full_name = excluded.full_name,
            bio = excluded.bio,
            followers = excluded.followers,
            following = excluded.following,
            posts_count = excluded.posts_count,
            bio_link = CASE WHEN excluded.bio_link != '' THEN excluded.bio_link ELSE leads.bio_link END,
            whatsapp = CASE WHEN excluded.whatsapp != '' THEN excluded.whatsapp ELSE leads.whatsapp END,
            whatsapp_source = CASE WHEN excluded.whatsapp_source != '' THEN excluded.whatsapp_source ELSE leads.whatsapp_source END,
            profile_url = excluded.profile_url,
            is_verified = excluded.is_verified,
            is_business = excluded.is_business,
            last_post_date = excluded.last_post_date,
            source_account = CASE WHEN excluded.source_account != '' THEN excluded.source_account ELSE leads.source_account END,
            city = CASE WHEN excluded.city != '' THEN excluded.city ELSE leads.city END,
            state = CASE WHEN excluded.state != '' THEN excluded.state ELSE leads.state END,
            status = excluded.status,
            -- Nunca regride o histórico de contato numa re-análise do perfil
            contact_status = CASE WHEN excluded.contact_status != 'not_contacted'
                                  THEN excluded.contact_status ELSE leads.contact_status END,
            contact_channel = CASE WHEN excluded.contact_channel != '' THEN excluded.contact_channel ELSE leads.contact_channel END,
            contacted_at = CASE WHEN excluded.contacted_at != '' THEN excluded.contacted_at ELSE leads.contacted_at END,
            contact_attempts = MAX(excluded.contact_attempts, leads.contact_attempts),
            last_contact_error = excluded.last_contact_error,
            score = CASE WHEN excluded.score > leads.score THEN excluded.score ELSE leads.score END,
            notes = excluded.notes,
            synced_at = ''
    """, (
        lead.username, lead.full_name, lead.bio, lead.followers,
        lead.following, lead.posts_count, lead.bio_link, lead.whatsapp,
        lead.whatsapp_source, lead.profile_url, int(lead.is_verified),
        int(lead.is_business), lead.last_post_date, lead.source_account,
        lead.city, lead.state, _value(lead.status),
        _value(lead.contact_status), lead.contact_channel,
        lead.contacted_at, lead.contact_attempts, lead.last_contact_error,
        lead.score, lead.created_at, lead.notes,
    ))


def upsert_lead(lead: Lead, sync: Optional[bool] = None) -> None:
    """
    Insere ou atualiza um lead e (se configurado) espelha no Supabase.

    Falha de rede não interrompe a prospecção: o lead fica pendente de sync.
    """
    if not lead.city:
        _infer_location(lead)

    conn = _connect()
    _write_lead(conn, lead)
    conn.commit()
    conn.close()

    should_sync = supabase_sync.is_enabled() if sync is None else sync
    if should_sync and supabase_sync.push_lead(lead):
        lead.synced_at = datetime.now().isoformat()
        mark_synced([lead.username], lead.synced_at)


def _infer_location(lead: Lead) -> None:
    """Preenche city/state a partir da bio, com fallback pra DEFAULT_CITY."""
    from prospect.config import DEFAULT_CITY
    lead.city, lead.state = resolve_location(
        lead.bio, lead.full_name, hint=DEFAULT_CITY
    )


def _value(status) -> str:
    """Extrai o valor string de um Enum ou passa a string adiante."""
    return status.value if hasattr(status, "value") else str(status)


def lead_exists(username: str) -> bool:
    """Verifica se um lead já existe no banco."""
    conn = _connect()
    row = conn.execute("SELECT 1 FROM leads WHERE username = ?", (username,)).fetchone()
    conn.close()
    return row is not None


def get_lead(username: str) -> Optional[Lead]:
    """Busca um lead pelo username."""
    conn = _connect()
    row = conn.execute("SELECT * FROM leads WHERE username = ?", (username,)).fetchone()
    conn.close()
    if not row:
        return None
    return _row_to_lead(row)


def get_leads_by_status(status: LeadStatus) -> list[Lead]:
    """Busca leads por status."""
    conn = _connect()
    rows = conn.execute(
        "SELECT * FROM leads WHERE status = ? ORDER BY score DESC", (_value(status),)
    ).fetchall()
    conn.close()
    return [_row_to_lead(r) for r in rows]


def get_leads_without_whatsapp() -> list[Lead]:
    """Busca todos os leads que não possuem WhatsApp cadastrado."""
    conn = _connect()
    rows = conn.execute("""
        SELECT * FROM leads
        WHERE (whatsapp = '' OR whatsapp IS NULL)
          AND status IN ('no_whatsapp', 'qualified', 'new')
        ORDER BY score DESC
    """).fetchall()
    conn.close()
    return [_row_to_lead(r) for r in rows]


def get_all_leads() -> list[Lead]:
    """Busca todos os leads ordenados por score."""
    conn = _connect()
    rows = conn.execute("SELECT * FROM leads ORDER BY score DESC").fetchall()
    conn.close()
    return [_row_to_lead(r) for r in rows]


# ── Consultas por cidade ───────────────────────────────────────────────────

def get_leads_by_city(city: str, only_pending: bool = False) -> list[Lead]:
    """
    Leads de uma cidade. `city` vazia devolve os sem cidade identificada.
    `only_pending` filtra quem tem WhatsApp e ainda não foi contatado.
    """
    query = "SELECT * FROM leads WHERE city = ?"
    params: list = [city or ""]

    if only_pending:
        query += " AND whatsapp != '' AND contact_status = 'not_contacted'"

    query += " ORDER BY score DESC"

    conn = _connect()
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [_row_to_lead(r) for r in rows]


def get_city_summary() -> list[dict]:
    """
    Resumo agregado por cidade — espelha a view leads_por_cidade do Supabase.
    """
    conn = _connect()
    rows = conn.execute("""
        SELECT
            city,
            state,
            COUNT(*) AS total,
            SUM(CASE WHEN whatsapp != '' THEN 1 ELSE 0 END) AS com_whatsapp,
            SUM(CASE WHEN contact_status != 'not_contacted' THEN 1 ELSE 0 END) AS contatados,
            SUM(CASE WHEN contact_status = 'not_contacted' AND whatsapp != '' THEN 1 ELSE 0 END) AS pendentes,
            SUM(CASE WHEN contact_status = 'responded' THEN 1 ELSE 0 END) AS responderam,
            SUM(CASE WHEN contact_status = 'converted' THEN 1 ELSE 0 END) AS convertidos,
            CAST(AVG(score) AS INTEGER) AS score_medio
        FROM leads
        GROUP BY city, state
        ORDER BY total DESC
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def list_cities() -> list[str]:
    """Cidades distintas presentes no banco (sem as vazias)."""
    conn = _connect()
    rows = conn.execute(
        "SELECT DISTINCT city FROM leads WHERE city != '' ORDER BY city"
    ).fetchall()
    conn.close()
    return [r["city"] for r in rows]


def backfill_cities(on_status: Optional[Callable[[str], None]] = None) -> int:
    """
    Infere cidade/estado dos leads que ainda não têm, a partir da bio.
    Retorna quantos leads foram atualizados.
    """
    emit = on_status or (lambda msg: None)

    conn = _connect()
    rows = conn.execute(
        "SELECT username, bio, full_name FROM leads WHERE city = '' OR city IS NULL"
    ).fetchall()

    updated = 0
    for row in rows:
        city, state = extract_location(row["bio"], row["full_name"])
        if not city:
            continue
        conn.execute(
            "UPDATE leads SET city = ?, state = ?, synced_at = '' WHERE username = ?",
            (city, state, row["username"]),
        )
        updated += 1

    conn.commit()
    conn.close()

    emit(f"  🗺️  {updated} de {len(rows)} leads sem cidade tiveram a cidade inferida da bio")
    return updated


# ── Status de contato ──────────────────────────────────────────────────────

def set_contact_status(
    username: str,
    contact_status: ContactStatus,
    channel: str = "",
    error: str = "",
    increment_attempts: bool = False,
    sync: Optional[bool] = None,
) -> Optional[Lead]:
    """
    Atualiza o status de contato de um lead e sincroniza.
    Retorna o lead atualizado (ou None se não existe).
    """
    status_value = _value(contact_status)
    contacted_at = (
        datetime.now().isoformat()
        if contact_status != ContactStatus.NOT_CONTACTED
        else ""
    )

    conn = _connect()
    row = conn.execute("SELECT 1 FROM leads WHERE username = ?", (username,)).fetchone()
    if not row:
        conn.close()
        return None

    conn.execute("""
        UPDATE leads SET
            contact_status = ?,
            contact_channel = CASE WHEN ? != '' THEN ? ELSE contact_channel END,
            contacted_at = CASE WHEN ? != '' THEN ? ELSE contacted_at END,
            contact_attempts = contact_attempts + ?,
            last_contact_error = ?,
            synced_at = ''
        WHERE username = ?
    """, (
        status_value,
        channel, channel,
        contacted_at, contacted_at,
        1 if increment_attempts else 0,
        error,
        username,
    ))
    conn.commit()
    conn.close()

    lead = get_lead(username)
    should_sync = supabase_sync.is_enabled() if sync is None else sync
    if lead and should_sync and supabase_sync.push_lead(lead):
        # Reflete o sync no objeto devolvido, não só no banco
        lead.synced_at = datetime.now().isoformat()
        mark_synced([username], lead.synced_at)

    return lead


def get_leads_by_contact_status(contact_status: ContactStatus) -> list[Lead]:
    """Leads em um determinado status de contato."""
    conn = _connect()
    rows = conn.execute(
        "SELECT * FROM leads WHERE contact_status = ? ORDER BY score DESC",
        (_value(contact_status),),
    ).fetchall()
    conn.close()
    return [_row_to_lead(r) for r in rows]


def get_pending_contact_leads(city: str = "") -> list[Lead]:
    """
    Fila de disparo: tem WhatsApp e nunca foi contatado.
    `city` vazia = todas as cidades.
    """
    query = """
        SELECT * FROM leads
        WHERE whatsapp != ''
          AND contact_status = 'not_contacted'
    """
    params: list = []
    if city:
        query += " AND city = ?"
        params.append(city)
    query += " ORDER BY score DESC"

    conn = _connect()
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [_row_to_lead(r) for r in rows]


# ── Sincronização com o Supabase ───────────────────────────────────────────

def get_unsynced_leads() -> list[Lead]:
    """Leads que ainda não subiram pro Supabase."""
    conn = _connect()
    rows = conn.execute(
        "SELECT * FROM leads WHERE synced_at = '' OR synced_at IS NULL ORDER BY score DESC"
    ).fetchall()
    conn.close()
    return [_row_to_lead(r) for r in rows]


def mark_synced(usernames: list[str], timestamp: str = "") -> None:
    """Marca leads como sincronizados com o Supabase."""
    if not usernames:
        return
    stamp = timestamp or datetime.now().isoformat()
    conn = _connect()
    conn.executemany(
        "UPDATE leads SET synced_at = ? WHERE username = ?",
        [(stamp, u) for u in usernames],
    )
    conn.commit()
    conn.close()


def sync_pending_leads(
    on_status: Optional[Callable[[str], None]] = None,
    force_all: bool = False,
) -> tuple[int, int, str]:
    """
    Envia pro Supabase os leads pendentes (ou todos, se force_all).
    Retorna (sincronizados, pendentes_restantes, erro).
    """
    emit = on_status or (lambda msg: None)
    leads = get_all_leads() if force_all else get_unsynced_leads()

    if not leads:
        emit("  ☁️  Nada pendente — Supabase já está em dia.")
        return 0, 0, ""

    emit(f"  ☁️  Enviando {len(leads)} leads pro Supabase...")
    synced, error = supabase_sync.push_leads(leads, on_status=emit)
    mark_synced(synced)

    remaining = len(leads) - len(synced)
    return len(synced), remaining, error


def apply_remote_contact_status(
    on_status: Optional[Callable[[str], None]] = None,
) -> tuple[int, str]:
    """
    Traz do Supabase o status de contato e aplica no SQLite.
    Use quando o status foi editado direto no painel do Supabase.
    Retorna (leads_atualizados, erro).
    """
    emit = on_status or (lambda msg: None)
    records, error = supabase_sync.pull_contact_status(on_status=emit)
    if error:
        return 0, error

    conn = _connect()
    updated = 0
    for record in records:
        username = record.get("username")
        if not username:
            continue
        row = conn.execute(
            "SELECT contact_status, city FROM leads WHERE username = ?", (username,)
        ).fetchone()
        if not row:
            continue

        remote_status = record.get("contact_status") or "not_contacted"
        remote_city = record.get("city") or ""
        if remote_status == row["contact_status"] and remote_city == row["city"]:
            continue

        conn.execute("""
            UPDATE leads SET
                contact_status = ?,
                contact_channel = ?,
                contacted_at = ?,
                contact_attempts = ?,
                last_contact_error = ?,
                city = CASE WHEN ? != '' THEN ? ELSE city END,
                state = CASE WHEN ? != '' THEN ? ELSE state END
            WHERE username = ?
        """, (
            remote_status,
            record.get("contact_channel") or "",
            record.get("contacted_at") or "",
            record.get("contact_attempts") or 0,
            record.get("last_contact_error") or "",
            remote_city, remote_city,
            record.get("state") or "", record.get("state") or "",
            username,
        ))
        updated += 1

    conn.commit()
    conn.close()

    emit(f"  ☁️  {updated} leads atualizados a partir do Supabase")
    return updated, ""


def get_stats() -> ProspectStats:
    """Retorna estatísticas gerais."""
    conn = _connect()
    stats = ProspectStats()

    row = conn.execute("SELECT COUNT(*) as c FROM leads").fetchone()
    stats.profiles_analyzed = row["c"] if row else 0

    row = conn.execute("SELECT COUNT(*) as c FROM leads WHERE status IN ('qualified', 'whatsapp_found', 'no_whatsapp')").fetchone()
    stats.leads_qualified = row["c"] if row else 0

    row = conn.execute("SELECT COUNT(*) as c FROM leads WHERE status = 'whatsapp_found'").fetchone()
    stats.whatsapp_found = row["c"] if row else 0

    row = conn.execute("SELECT COUNT(*) as c FROM leads WHERE status = 'no_whatsapp'").fetchone()
    stats.whatsapp_not_found = row["c"] if row else 0

    row = conn.execute("SELECT COUNT(*) as c FROM leads WHERE status = 'error'").fetchone()
    stats.errors = row["c"] if row else 0

    row = conn.execute("SELECT COUNT(DISTINCT source_account) as c FROM leads WHERE source_account != ''").fetchone()
    stats.academies_scraped = row["c"] if row else 0

    conn.close()
    return stats


def get_contact_funnel() -> dict[str, int]:
    """Contagem de leads por status de contato."""
    conn = _connect()
    rows = conn.execute(
        "SELECT contact_status, COUNT(*) AS total FROM leads GROUP BY contact_status"
    ).fetchall()
    conn.close()
    return {r["contact_status"]: r["total"] for r in rows}


def export_csv(filepath: Optional[Path] = None) -> Path:
    """Exporta leads com WhatsApp para CSV."""
    if filepath is None:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        filepath = DATA_DIR / f"leads_{ts}.csv"

    leads = get_leads_by_status(LeadStatus.WHATSAPP_FOUND)
    all_leads = get_all_leads()

    def _row(lead: Lead) -> list:
        return [
            lead.username, lead.full_name, lead.bio, lead.followers,
            lead.city, lead.state,
            lead.whatsapp, lead.whatsapp_source, lead.bio_link,
            lead.profile_url, lead.score, lead.source_account,
            lead.status.value,
            CONTACT_STATUS_LABELS.get(lead.contact_status, lead.contact_status),
            lead.contacted_at, lead.contact_attempts, lead.last_post_date,
        ]

    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "Username", "Nome", "Bio", "Seguidores", "Cidade", "UF",
            "WhatsApp", "Fonte WhatsApp", "Link Bio", "URL Perfil", "Score",
            "Fonte", "Status", "Status Contato", "Contatado em", "Tentativas",
            "Último Post",
        ])
        # Primeiro os que têm WhatsApp, depois os demais
        written = set()
        for lead in leads:
            writer.writerow(_row(lead))
            written.add(lead.username)
        for lead in all_leads:
            if lead.username not in written:
                writer.writerow(_row(lead))

    return filepath


def export_city_csv(city: str, filepath: Optional[Path] = None) -> tuple[Path, int]:
    """Exporta os leads de uma cidade específica. Retorna (arquivo, total)."""
    leads = get_leads_by_city(city)

    if filepath is None:
        slug = "".join(c if c.isalnum() else "_" for c in (city or "sem_cidade")).lower()
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        filepath = DATA_DIR / f"leads_{slug}_{ts}.csv"

    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "Username", "Nome", "Cidade", "UF", "Seguidores", "WhatsApp",
            "Score", "Status", "Status Contato", "Contatado em", "URL Perfil",
        ])
        for lead in leads:
            writer.writerow([
                lead.username, lead.full_name, lead.city, lead.state,
                lead.followers, lead.whatsapp, lead.score, lead.status.value,
                CONTACT_STATUS_LABELS.get(lead.contact_status, lead.contact_status),
                lead.contacted_at, lead.profile_url,
            ])

    return filepath, len(leads)


def _row_to_lead(row: sqlite3.Row) -> Lead:
    """Converte Row em Lead."""
    keys = row.keys()

    def _get(key: str, default=""):
        return row[key] if key in keys and row[key] is not None else default

    try:
        contact_status = ContactStatus(_get("contact_status", "not_contacted"))
    except ValueError:
        contact_status = ContactStatus.NOT_CONTACTED

    return Lead(
        username=row["username"],
        full_name=row["full_name"],
        bio=row["bio"],
        followers=row["followers"],
        following=row["following"],
        posts_count=row["posts_count"],
        bio_link=row["bio_link"],
        whatsapp=row["whatsapp"],
        whatsapp_source=row["whatsapp_source"],
        profile_url=row["profile_url"],
        is_verified=bool(row["is_verified"]),
        is_business=bool(row["is_business"]),
        last_post_date=row["last_post_date"],
        source_account=row["source_account"],
        city=_get("city"),
        state=_get("state"),
        status=LeadStatus(row["status"]),
        contact_status=contact_status,
        contact_channel=_get("contact_channel"),
        contacted_at=_get("contacted_at"),
        contact_attempts=_get("contact_attempts", 0),
        last_contact_error=_get("last_contact_error"),
        score=row["score"],
        created_at=row["created_at"],
        notes=row["notes"],
        synced_at=_get("synced_at"),
    )
