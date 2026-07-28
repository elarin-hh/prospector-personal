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
    status: LeadStatus = LeadStatus.NEW
    score: int = 0
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    notes: str = ""

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
