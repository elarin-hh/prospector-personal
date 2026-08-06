"""
=============================================================================
  prospect/supabase_sync.py
  Sincronização dos leads com o Supabase via API REST (PostgREST).

  O SQLite continua sendo a fonte de verdade local — o Supabase é o banco
  compartilhado. Todo erro de rede é engolido e registrado: prospecção
  nunca quebra por causa de sync.

  Fluxos:
    push_leads()          → upsert em lote (on_conflict=username)
    pull_contact_status() → traz de volta o status de contato editado no painel
    city_summary()        → leitura da view leads_por_cidade
=============================================================================
"""
from __future__ import annotations
from typing import Callable, Iterable, Optional

import requests

from prospect.config import (
    SUPABASE_URL, SUPABASE_KEY, SUPABASE_TABLE,
    SUPABASE_SYNC, SUPABASE_TIMEOUT,
)
from prospect.models import Lead, LeadStatus, ContactStatus

BATCH_SIZE = 200
_MIGRATION_HINT = (
    "Tabela não encontrada no Supabase. Aplique "
    "supabase/migrations/0001_create_leads.sql no SQL Editor."
)

# Aviso de tabela ausente é emitido só uma vez por execução
_missing_table_warned = False


class SupabaseError(RuntimeError):
    """Falha ao falar com o Supabase."""


def is_configured() -> bool:
    """True se há URL e chave no .env."""
    return bool(SUPABASE_URL and SUPABASE_KEY)


def is_enabled() -> bool:
    """True se o sync automático durante a prospecção está ligado."""
    return is_configured() and SUPABASE_SYNC


def status_message() -> str:
    """Descrição legível do estado da configuração, pro painel do CLI."""
    if not SUPABASE_URL:
        return "❌ SUPABASE_URL não definida"
    if not SUPABASE_KEY:
        return "❌ SUPABASE_SERVICE_KEY não definida"
    host = SUPABASE_URL.replace("https://", "").replace("http://", "")
    return f"{'🟢' if SUPABASE_SYNC else '⏸️'} {host}"


def _headers(prefer: str = "") -> dict[str, str]:
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
    }
    if prefer:
        headers["Prefer"] = prefer
    return headers


def _endpoint(table: Optional[str] = None) -> str:
    return f"{SUPABASE_URL}/rest/v1/{table or SUPABASE_TABLE}"


def _nullable(value: str) -> Optional[str]:
    """PostgREST rejeita '' em coluna timestamptz — converte pra NULL."""
    return value or None


def lead_to_row(lead: Lead) -> dict:
    """Serializa um Lead no formato da tabela public.leads."""
    contact_status = lead.contact_status
    if isinstance(contact_status, ContactStatus):
        contact_status = contact_status.value

    status = lead.status
    if isinstance(status, LeadStatus):
        status = status.value

    return {
        "username": lead.username,
        "full_name": lead.full_name or "",
        "bio": lead.bio or "",
        "profile_url": lead.profile_url or "",
        "city": lead.city or "",
        "state": lead.state or "",
        "followers": lead.followers or 0,
        "following": lead.following or 0,
        "posts_count": lead.posts_count or 0,
        "is_verified": bool(lead.is_verified),
        "is_business": bool(lead.is_business),
        "last_post_date": lead.last_post_date or "",
        "score": lead.score or 0,
        "bio_link": lead.bio_link or "",
        "whatsapp": lead.whatsapp or "",
        "whatsapp_source": lead.whatsapp_source or "",
        "source_account": lead.source_account or "",
        "status": status,
        "contact_status": contact_status,
        "contact_channel": lead.contact_channel or "",
        "contacted_at": _nullable(lead.contacted_at),
        "contact_attempts": lead.contact_attempts or 0,
        "last_contact_error": lead.last_contact_error or "",
        "notes": lead.notes or "",
    }


def _chunks(items: list, size: int) -> Iterable[list]:
    for i in range(0, len(items), size):
        yield items[i:i + size]


def push_leads(
    leads: list[Lead],
    on_status: Optional[Callable[[str], None]] = None,
) -> tuple[list[str], str]:
    """
    Faz upsert dos leads no Supabase, em lotes, usando username como chave.

    Retorna (usernames_sincronizados, mensagem_de_erro).
    Erro vazio = tudo certo.
    """
    global _missing_table_warned
    emit = on_status or (lambda msg: None)

    if not is_configured():
        return [], "Supabase não configurado (veja SUPABASE_URL / SUPABASE_SERVICE_KEY no .env)"
    if not leads:
        return [], ""

    synced: list[str] = []
    last_error = ""

    for batch in _chunks(leads, BATCH_SIZE):
        rows = [lead_to_row(lead) for lead in batch]
        try:
            response = requests.post(
                _endpoint(),
                params={"on_conflict": "username"},
                json=rows,
                # merge-duplicates = upsert; return=minimal economiza banda
                headers=_headers("resolution=merge-duplicates,return=minimal"),
                timeout=SUPABASE_TIMEOUT,
            )
        except requests.RequestException as exc:
            last_error = f"falha de rede: {exc}"
            emit(f"  ☁️  Supabase indisponível — {last_error}")
            break

        if 200 <= response.status_code < 300:
            synced.extend(lead.username for lead in batch)
            continue

        if response.status_code == 404:
            if not _missing_table_warned:
                emit(f"  ☁️  {_MIGRATION_HINT}")
                _missing_table_warned = True
            last_error = _MIGRATION_HINT
            break

        last_error = f"HTTP {response.status_code}: {response.text[:200]}"
        emit(f"  ☁️  Supabase recusou o lote — {last_error}")
        break

    return synced, last_error


def push_lead(lead: Lead, on_status: Optional[Callable[[str], None]] = None) -> bool:
    """Upsert de um único lead. True se sincronizou."""
    synced, _ = push_leads([lead], on_status=on_status)
    return bool(synced)


def pull_contact_status(
    on_status: Optional[Callable[[str], None]] = None,
) -> tuple[list[dict], str]:
    """
    Busca o status de contato de todos os leads no Supabase.
    Útil quando o status foi editado direto no painel do Supabase.

    Retorna (registros, erro). Cada registro: username, contact_status,
    contact_channel, contacted_at, contact_attempts, notes.
    """
    emit = on_status or (lambda msg: None)

    if not is_configured():
        return [], "Supabase não configurado"

    columns = (
        "username,contact_status,contact_channel,contacted_at,"
        "contact_attempts,last_contact_error,notes,city,state"
    )
    records: list[dict] = []
    offset = 0
    page_size = 1000

    while True:
        try:
            response = requests.get(
                _endpoint(),
                params={"select": columns, "limit": page_size, "offset": offset},
                headers=_headers(),
                timeout=SUPABASE_TIMEOUT,
            )
        except requests.RequestException as exc:
            return records, f"falha de rede: {exc}"

        if response.status_code == 404:
            return records, _MIGRATION_HINT
        if not (200 <= response.status_code < 300):
            return records, f"HTTP {response.status_code}: {response.text[:200]}"

        page = response.json()
        records.extend(page)
        if len(page) < page_size:
            break
        offset += page_size

    emit(f"  ☁️  {len(records)} registros lidos do Supabase")
    return records, ""


def city_summary() -> tuple[list[dict], str]:
    """Lê a view leads_por_cidade. Retorna (linhas, erro)."""
    if not is_configured():
        return [], "Supabase não configurado"

    try:
        response = requests.get(
            _endpoint("leads_por_cidade"),
            params={"order": "total.desc"},
            headers=_headers(),
            timeout=SUPABASE_TIMEOUT,
        )
    except requests.RequestException as exc:
        return [], f"falha de rede: {exc}"

    if response.status_code == 404:
        return [], _MIGRATION_HINT
    if not (200 <= response.status_code < 300):
        return [], f"HTTP {response.status_code}: {response.text[:200]}"

    return response.json(), ""


def check_connection() -> tuple[bool, str]:
    """
    Testa credenciais e existência da tabela.
    Retorna (ok, mensagem).
    """
    if not is_configured():
        return False, "Defina SUPABASE_URL e SUPABASE_SERVICE_KEY no .env"

    try:
        response = requests.get(
            _endpoint(),
            params={"select": "username", "limit": 1},
            headers=_headers("count=exact"),
            timeout=SUPABASE_TIMEOUT,
        )
    except requests.RequestException as exc:
        return False, f"Não consegui alcançar o Supabase: {exc}"

    if response.status_code == 404:
        return False, _MIGRATION_HINT
    if response.status_code in (401, 403):
        return False, (
            "Chave rejeitada. A tabela tem RLS habilitado — use a "
            "SERVICE ROLE KEY (Settings → API → service_role)."
        )
    if not (200 <= response.status_code < 300):
        return False, f"HTTP {response.status_code}: {response.text[:200]}"

    total = ""
    content_range = response.headers.get("content-range", "")
    if "/" in content_range:
        total = content_range.split("/")[-1]

    return True, f"Conectado — tabela '{SUPABASE_TABLE}' com {total or '?'} leads"
