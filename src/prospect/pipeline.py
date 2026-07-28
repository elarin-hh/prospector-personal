"""
=============================================================================
  prospect/pipeline.py
  Pipeline completa de prospecção: academia → seguidores → qualificação → WhatsApp.
=============================================================================
"""
from __future__ import annotations
import time
from typing import Callable

from prospect.browser import InstagramBrowser, human_delay
from prospect.scraper import (
    extract_profile_data, get_followers_list,
    search_by_hashtag, _is_personal_trainer,
)
from prospect.whatsapp_finder import find_whatsapp
from prospect.models import Lead, LeadStatus, ProspectStats
from prospect.db import upsert_lead, lead_exists, init_db, get_leads_without_whatsapp
from prospect.config import MIN_FOLLOWERS, MAX_FOLLOWERS


def rescan_whatsapp_existing_leads(
    browser: InstagramBrowser | None = None,
    on_status: Callable[[str], None] = None,
    on_lead: Callable[[Lead], None] = None,
    stats: ProspectStats = None,
) -> None:
    """
    Realiza varredura de WhatsApp em todos os leads existentes no banco de dados
    que ainda não possuem WhatsApp cadastrado.
    """
    emit = on_status or (lambda msg: None)
    notify = on_lead or (lambda lead: None)
    if stats is None:
        stats = ProspectStats()

    leads_to_scan = get_leads_without_whatsapp()
    if not leads_to_scan:
        emit("  ℹ️  Nenhum lead sem WhatsApp pendente no banco de dados.")
        return

    emit(f"\n🔄 Iniciando re-varredura de WhatsApp em {len(leads_to_scan)} leads no banco...")

    for i, lead in enumerate(leads_to_scan, 1):
        emit(f"\n🔍 [{i}/{len(leads_to_scan)}] Reanalisando @{lead.username} ({lead.full_name or 'Personal'})...")

        # Se não tem link ou bio cadastrada e o navegador está disponível, extrai perfil atualizado do Instagram
        if (not lead.bio_link and not lead.bio) and browser:
            emit(f"  🌐 Buscando bio e links de @{lead.username} no Instagram...")
            updated_lead = extract_profile_data(browser, lead.username)
            if updated_lead:
                lead.bio = updated_lead.bio
                lead.bio_link = updated_lead.bio_link
                lead.full_name = updated_lead.full_name or lead.full_name
                lead.followers = updated_lead.followers or lead.followers
                lead.score = updated_lead.score

        if not lead.bio_link and not lead.bio:
            emit(f"  ⏭️  @{lead.username} — sem bio ou link cadastrado.")
            continue

        emit(f"  🔎 Buscando WhatsApp no link/bio...")
        phone, source = find_whatsapp(lead.bio, lead.bio_link, browser=browser)

        if phone:
            lead.whatsapp = phone
            lead.whatsapp_source = source
            lead.status = LeadStatus.WHATSAPP_FOUND
            stats.whatsapp_found += 1
            upsert_lead(lead)
            notify(lead)
            emit(f"  📱 WhatsApp encontrado: +{phone} (via {source})")
        else:
            lead.status = LeadStatus.NO_WHATSAPP
            stats.whatsapp_not_found += 1
            upsert_lead(lead)
            emit(f"  📵 WhatsApp não encontrado")


def prospect_from_account(
    browser: InstagramBrowser,
    account: str,
    max_followers: int = 200,
    on_status: Callable[[str], None] = None,
    on_lead: Callable[[Lead], None] = None,
    stats: ProspectStats = None,
) -> None:
    """
    Pipeline: busca seguidores de uma conta e prospecta personal trainers.
    """
    emit = on_status or (lambda msg: None)
    notify = on_lead or (lambda lead: None)
    if stats is None:
        stats = ProspectStats()

    emit(f"\n🏢 Analisando seguidores de @{account}...")
    stats.academies_scraped += 1

    # Coleta todos os nomes de usuário dos seguidores primeiro (sem mudar de página)
    usernames = list(get_followers_list(browser, account, max_count=max_followers, on_status=on_status))
    emit(f"  📋 {len(usernames)} perfis capturados para análise")

    follower_count = 0
    for username in usernames:
        follower_count += 1

        # Pula se já processado
        if lead_exists(username):
            emit(f"  ⏭️  @{username} já processado")
            continue

        emit(f"  🔍 [{follower_count}] Analisando @{username}...")
        stats.profiles_analyzed += 1

        # Extrai dados do perfil
        lead = extract_profile_data(browser, username)
        if not lead:
            emit(f"  ⚠️  Não consegui extrair dados de @{username}")
            stats.errors += 1
            continue

        lead.source_account = account

        # Verifica se perfil é privado
        if lead.is_private:
            lead.status = LeadStatus.SKIPPED
            lead.notes = "Perfil privado"
            upsert_lead(lead)
            emit(f"  ⏭️  @{username} — perfil privado")
            continue

        # Verifica se é personal trainer
        if not _is_personal_trainer(lead.bio, lead.full_name):
            lead.status = LeadStatus.SKIPPED
            lead.notes = "Não é personal trainer"
            upsert_lead(lead)
            emit(f"  ⏭️  @{username} — não é personal trainer")
            continue

        # Verifica seguidores
        if lead.followers < MIN_FOLLOWERS:
            lead.status = LeadStatus.SKIPPED
            lead.notes = f"Poucos seguidores ({lead.followers})"
            upsert_lead(lead)
            emit(f"  ⏭️  @{username} — {lead.followers} seguidores (mín: {MIN_FOLLOWERS})")
            continue

        if lead.followers > MAX_FOLLOWERS:
            lead.status = LeadStatus.SKIPPED
            lead.notes = f"Muitos seguidores ({lead.followers})"
            upsert_lead(lead)
            emit(f"  ⏭️  @{username} — {lead.followers} seguidores (máx: {MAX_FOLLOWERS})")
            continue

        # Qualificado!
        lead.status = LeadStatus.QUALIFIED
        stats.leads_qualified += 1
        emit(f"  ✅ @{username} qualificado! ({lead.followers} seg, score: {lead.score})")

        # Busca WhatsApp
        if lead.bio_link or lead.bio:
            emit(f"  🔎 Buscando WhatsApp de @{username}...")
            phone, source = find_whatsapp(lead.bio, lead.bio_link, browser=browser)
            if phone:
                lead.whatsapp = phone
                lead.whatsapp_source = source
                lead.status = LeadStatus.WHATSAPP_FOUND
                stats.whatsapp_found += 1
                emit(f"  📱 WhatsApp encontrado: +{phone} (via {source})")
            else:
                lead.status = LeadStatus.NO_WHATSAPP
                stats.whatsapp_not_found += 1
                emit(f"  📵 WhatsApp não encontrado")
        else:
            lead.status = LeadStatus.NO_WHATSAPP
            stats.whatsapp_not_found += 1
            emit(f"  📵 Sem link na bio")

        upsert_lead(lead)
        notify(lead)

        # Delay entre perfis
        human_delay(2, 5)


def prospect_from_hashtag(
    browser: InstagramBrowser,
    hashtag: str,
    max_profiles: int = 30,
    on_status: Callable[[str], None] = None,
    on_lead: Callable[[Lead], None] = None,
    stats: ProspectStats = None,
) -> None:
    """
    Pipeline: busca posts de uma hashtag e prospecta personal trainers.
    """
    emit = on_status or (lambda msg: None)
    notify = on_lead or (lambda lead: None)
    if stats is None:
        stats = ProspectStats()

    tag = hashtag.lstrip("#")
    emit(f"\n#️⃣  Buscando perfis da hashtag #{tag}...")

    usernames = list(search_by_hashtag(browser, tag, max_profiles=max_profiles, on_status=on_status))
    emit(f"  📋 {len(usernames)} perfis capturados da hashtag #{tag}")

    count = 0
    for username in usernames:
        count += 1

        if lead_exists(username):
            emit(f"  ⏭️  @{username} já processado")
            continue

        emit(f"  🔍 [{count}] Analisando @{username}...")
        stats.profiles_analyzed += 1

        lead = extract_profile_data(browser, username)
        if not lead:
            emit(f"  ⚠️  Não consegui extrair dados de @{username}")
            stats.errors += 1
            continue

        lead.source_account = f"#{tag}"

        # Verifica se perfil é privado
        if lead.is_private:
            lead.status = LeadStatus.SKIPPED
            lead.notes = "Perfil privado"
            upsert_lead(lead)
            emit(f"  ⏭️  @{username} — perfil privado")
            continue

        # Verifica se é personal trainer
        if not _is_personal_trainer(lead.bio, lead.full_name):
            lead.status = LeadStatus.SKIPPED
            lead.notes = "Não é personal trainer"
            upsert_lead(lead)
            emit(f"  ⏭️  @{username} — não é personal trainer")
            continue

        # Verifica seguidores
        if lead.followers < MIN_FOLLOWERS:
            lead.status = LeadStatus.SKIPPED
            lead.notes = f"Poucos seguidores ({lead.followers})"
            upsert_lead(lead)
            emit(f"  ⏭️  @{username} — {lead.followers} seguidores (mín: {MIN_FOLLOWERS})")
            continue

        if lead.followers > MAX_FOLLOWERS:
            lead.status = LeadStatus.SKIPPED
            lead.notes = f"Muitos seguidores ({lead.followers})"
            upsert_lead(lead)
            emit(f"  ⏭️  @{username} — {lead.followers} seguidores (máx: {MAX_FOLLOWERS})")
            continue

        # Qualificado!
        lead.status = LeadStatus.QUALIFIED
        stats.leads_qualified += 1
        emit(f"  ✅ @{username} qualificado! ({lead.followers} seg, score: {lead.score})")

        # Busca WhatsApp
        if lead.bio_link or lead.bio:
            emit(f"  🔎 Buscando WhatsApp de @{username}...")
            phone, source = find_whatsapp(lead.bio, lead.bio_link, browser=browser)
            if phone:
                lead.whatsapp = phone
                lead.whatsapp_source = source
                lead.status = LeadStatus.WHATSAPP_FOUND
                stats.whatsapp_found += 1
                emit(f"  📱 WhatsApp encontrado: +{phone} (via {source})")
            else:
                lead.status = LeadStatus.NO_WHATSAPP
                stats.whatsapp_not_found += 1
                emit(f"  📵 WhatsApp não encontrado")
        else:
            lead.status = LeadStatus.NO_WHATSAPP
            stats.whatsapp_not_found += 1
            emit(f"  📵 Sem link na bio")

        upsert_lead(lead)
        notify(lead)

        human_delay(2, 5)
