"""
=============================================================================
  prospect/db.py
  Persistência de leads em SQLite.
=============================================================================
"""
from __future__ import annotations
import sqlite3
import csv
from pathlib import Path
from typing import Optional

from prospect.config import DB_PATH, DATA_DIR
from prospect.models import Lead, LeadStatus, ProspectStats


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    """Cria tabela de leads se não existir."""
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
            status TEXT DEFAULT 'new',
            score INTEGER DEFAULT 0,
            created_at TEXT DEFAULT '',
            notes TEXT DEFAULT ''
        )
    """)
    conn.commit()
    conn.close()


def upsert_lead(lead: Lead) -> None:
    """Insere ou atualiza um lead."""
    conn = _connect()
    conn.execute("""
        INSERT INTO leads (
            username, full_name, bio, followers, following, posts_count,
            bio_link, whatsapp, whatsapp_source, profile_url,
            is_verified, is_business, last_post_date, source_account,
            status, score, created_at, notes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            status = excluded.status,
            score = CASE WHEN excluded.score > leads.score THEN excluded.score ELSE leads.score END,
            notes = excluded.notes
    """, (
        lead.username, lead.full_name, lead.bio, lead.followers,
        lead.following, lead.posts_count, lead.bio_link, lead.whatsapp,
        lead.whatsapp_source, lead.profile_url, int(lead.is_verified),
        int(lead.is_business), lead.last_post_date, lead.source_account,
        lead.status.value, lead.score, lead.created_at, lead.notes
    ))
    conn.commit()
    conn.close()


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
    rows = conn.execute("SELECT * FROM leads WHERE status = ? ORDER BY score DESC", (status.value,)).fetchall()
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


def export_csv(filepath: Optional[Path] = None) -> Path:
    """Exporta leads com WhatsApp para CSV."""
    if filepath is None:
        from datetime import datetime
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        filepath = DATA_DIR / f"leads_{ts}.csv"

    leads = get_leads_by_status(LeadStatus.WHATSAPP_FOUND)
    all_leads = get_all_leads()

    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "Username", "Nome", "Bio", "Seguidores", "WhatsApp",
            "Fonte WhatsApp", "Link Bio", "URL Perfil", "Score",
            "Fonte", "Status", "Último Post"
        ])
        # Primeiro os que têm WhatsApp, depois os demais
        written = set()
        for lead in leads:
            writer.writerow([
                lead.username, lead.full_name, lead.bio, lead.followers,
                lead.whatsapp, lead.whatsapp_source, lead.bio_link,
                lead.profile_url, lead.score, lead.source_account,
                lead.status.value, lead.last_post_date
            ])
            written.add(lead.username)
        for lead in all_leads:
            if lead.username not in written:
                writer.writerow([
                    lead.username, lead.full_name, lead.bio, lead.followers,
                    lead.whatsapp, lead.whatsapp_source, lead.bio_link,
                    lead.profile_url, lead.score, lead.source_account,
                    lead.status.value, lead.last_post_date
                ])

    return filepath


def _row_to_lead(row: sqlite3.Row) -> Lead:
    """Converte Row em Lead."""
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
        status=LeadStatus(row["status"]),
        score=row["score"],
        created_at=row["created_at"],
        notes=row["notes"],
    )
