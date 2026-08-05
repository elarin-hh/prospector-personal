"""
=============================================================================
  prospect/models.py
  Modelos de dados com dataclasses.
=============================================================================
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class LeadStatus(str, Enum):
    NEW = "new"
    QUALIFIED = "qualified"
    WHATSAPP_FOUND = "whatsapp_found"
    NO_WHATSAPP = "no_whatsapp"
    SKIPPED = "skipped"
    ERROR = "error"


class ContactStatus(str, Enum):
    """Já contatamos esse lead? Espelha o CHECK constraint do Supabase."""
    NOT_CONTACTED = "not_contacted"
    QUEUED = "queued"
    CONTACTED = "contacted"
    RESPONDED = "responded"
    NO_ANSWER = "no_answer"
    INVALID_NUMBER = "invalid_number"
    RESTRICTED = "restricted"
    NOT_INTERESTED = "not_interested"
    CONVERTED = "converted"


CONTACT_STATUS_LABELS: dict[ContactStatus, str] = {
    ContactStatus.NOT_CONTACTED: "não contatado",
    ContactStatus.QUEUED: "na fila",
    ContactStatus.CONTACTED: "contatado",
    ContactStatus.RESPONDED: "respondeu",
    ContactStatus.NO_ANSWER: "sem resposta",
    ContactStatus.INVALID_NUMBER: "número inválido",
    ContactStatus.RESTRICTED: "bloqueado pelo WhatsApp",
    ContactStatus.NOT_INTERESTED: "sem interesse",
    ContactStatus.CONVERTED: "convertido",
}


@dataclass
class Lead:
    """Representa um lead de personal trainer."""
    username: str
    full_name: str = ""
    bio: str = ""
    followers: int = 0
    following: int = 0
    posts_count: int = 0
    bio_link: str = ""
    whatsapp: str = ""
    whatsapp_source: str = ""  # onde encontrou (bio, link, landing_page)
    profile_url: str = ""
    is_verified: bool = False
    is_business: bool = False
    is_private: bool = False
    last_post_date: str = ""
    source_account: str = ""  # academia de onde veio
    city: str = ""            # cidade inferida da bio ou informada na prospecção
    state: str = ""           # UF correspondente
    status: LeadStatus = LeadStatus.NEW
    contact_status: ContactStatus = ContactStatus.NOT_CONTACTED
    contact_channel: str = ""       # whatsapp, instagram_dm, ligacao...
    contacted_at: str = ""          # ISO 8601 do último contato
    contact_attempts: int = 0
    last_contact_error: str = ""
    score: int = 0
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    notes: str = ""
    synced_at: str = ""             # último push bem-sucedido pro Supabase

    @property
    def was_contacted(self) -> bool:
        """True se já houve qualquer tentativa de contato com o lead."""
        return self.contact_status != ContactStatus.NOT_CONTACTED

    @property
    def location_label(self) -> str:
        """Cidade formatada para exibição ('Curitiba/PR')."""
        if self.city and self.state:
            return f"{self.city}/{self.state}"
        return self.city or "—"

    @property
    def is_active(self) -> bool:
        """Verifica se tem post nos últimos 30 dias."""
        if not self.last_post_date:
            return False
        try:
            last = datetime.fromisoformat(self.last_post_date)
            return (datetime.now() - last).days <= 30
        except (ValueError, TypeError):
            return False


@dataclass
class ProspectStats:
    """Estatísticas da prospecção."""
    academies_scraped: int = 0
    profiles_analyzed: int = 0
    leads_qualified: int = 0
    whatsapp_found: int = 0
    whatsapp_not_found: int = 0
    errors: int = 0
    start_time: str = field(default_factory=lambda: datetime.now().isoformat())
