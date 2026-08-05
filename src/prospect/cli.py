"""
=============================================================================
  prospect/cli.py
  Interface de terminal interativa com Rich.
  
  Menu principal:
    1. Prospectar por Academias
    2. Prospectar por Hashtags
    3. Ver Leads
    4. Ver Leads com WhatsApp
    5. Exportar CSV
    6. Estatísticas
    0. Sair
=============================================================================
"""
from __future__ import annotations
import sys
import signal
from datetime import datetime

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.prompt import Prompt, IntPrompt, Confirm
from rich.text import Text
from rich.live import Live
from rich.layout import Layout
from rich.columns import Columns
from rich import box

from prospect import __version__, supabase_sync
from prospect.config import (
    IG_USERNAME, MIN_FOLLOWERS, MAX_FOLLOWERS,
    SEED_ACCOUNTS, SEARCH_HASHTAGS, DEFAULT_CITY,
)
from prospect.db import (
    init_db, get_all_leads, get_leads_by_status,
    get_stats, export_csv, export_city_csv,
    get_city_summary, get_leads_by_city, list_cities,
    get_contact_funnel, get_pending_contact_leads, set_contact_status,
    backfill_cities, sync_pending_leads, apply_remote_contact_status,
    get_unsynced_leads, get_lead,
)
from prospect.location import normalize_city_input
from prospect.models import (
    Lead, LeadStatus, ContactStatus, ProspectStats, CONTACT_STATUS_LABELS,
)
from prospect.browser import InstagramBrowser
from prospect.pipeline import prospect_from_account, prospect_from_hashtag

console = Console()
_browser: InstagramBrowser | None = None


def _banner() -> Panel:
    """Banner do aplicativo."""
    now = datetime.now().strftime("%d/%m/%Y %H:%M")
    text = Text()
    text.append("🎯  PT Prospect", style="bold cyan")
    text.append(f"   v{__version__}  •  {now}", style="dim")
    return Panel(
        text,
        border_style="cyan",
        box=box.DOUBLE,
        padding=(0, 2),
    )


def _stats_panel() -> Panel:
    """Painel de estatísticas."""
    stats = get_stats()
    grid = Table(show_header=False, box=None, padding=(0, 2))
    grid.add_column(style="bold")
    grid.add_column(style="green")

    grid.add_row("🏢 Academias", str(stats.academies_scraped))
    grid.add_row("👤 Perfis analisados", str(stats.profiles_analyzed))
    grid.add_row("✅ Leads qualificados", str(stats.leads_qualified))
    grid.add_row("📱 WhatsApp encontrado", str(stats.whatsapp_found))
    grid.add_row("📵 Sem WhatsApp", str(stats.whatsapp_not_found))
    grid.add_row("❌ Erros", str(stats.errors))

    return Panel(grid, title="📊 Estatísticas", border_style="green", box=box.ROUNDED)


def _config_panel() -> Panel:
    """Painel de configuração."""
    grid = Table(show_header=False, box=None, padding=(0, 2))
    grid.add_column(style="bold")
    grid.add_column(style="yellow")

    grid.add_row("📷 Conta", f"@{IG_USERNAME}" if IG_USERNAME else "❌ não definida")
    grid.add_row("👥 Seguidores", f"{MIN_FOLLOWERS:,} — {MAX_FOLLOWERS:,}")
    grid.add_row("🏢 Academias", ", ".join(SEED_ACCOUNTS) if SEED_ACCOUNTS else "nenhuma definida")
    grid.add_row("#️⃣  Hashtags", ", ".join(f"#{h}" for h in SEARCH_HASHTAGS[:3]))
    grid.add_row("📍 Cidade padrão", DEFAULT_CITY or "inferir da bio")
    grid.add_row("☁️  Supabase", supabase_sync.status_message())

    pending_sync = len(get_unsynced_leads())
    if pending_sync:
        grid.add_row("⏳ A sincronizar", f"{pending_sync} leads")

    return Panel(grid, title="⚙️  Configuração", border_style="yellow", box=box.ROUNDED)


def _menu() -> str:
    """Menu principal."""
    console.print()
    table = Table(show_header=False, box=box.SIMPLE_HEAVY, border_style="cyan", padding=(0, 2))
    table.add_column(style="bold cyan", width=4)
    table.add_column(style="white")

    table.add_row("[1]", "🏢 Prospectar por Academias (seguidores)")
    table.add_row("[2]", "#️⃣  Prospectar por Hashtags")
    table.add_row("[3]", "📋 Ver TODOS os Leads")
    table.add_row("[4]", "📱 Ver Leads COM WhatsApp")
    table.add_row("[5]", "⭐️ Ver Leads Qualificados (SEM WhatsApp)")
    table.add_row("[6]", "📤 Exportar CSV")
    table.add_row("[7]", "📊 Estatísticas detalhadas")
    table.add_row("[8]", "🔍 Analisar perfil específico")
    table.add_row("[9]", "🔄 Revarrer WhatsApp nos Leads do Banco")
    table.add_row("[10]", "💬 Disparo Automatizado via WhatsApp Web (Humano)")
    table.add_row("[11]", "🗺️  Leads por Cidade")
    table.add_row("[12]", "✅ Atualizar status de contato de um lead")
    table.add_row("[13]", "☁️  Sincronizar com Supabase")
    table.add_row("[0]", "🚪 Sair")

    console.print(table)
    return Prompt.ask(
        "\n[bold cyan]Escolha uma opção[/]",
        choices=[str(i) for i in range(14)],
        default="1",
    )


_CONTACT_STYLES: dict[ContactStatus, str] = {
    ContactStatus.NOT_CONTACTED: "dim",
    ContactStatus.QUEUED: "cyan",
    ContactStatus.CONTACTED: "bold green",
    ContactStatus.RESPONDED: "bold blue",
    ContactStatus.NO_ANSWER: "yellow",
    ContactStatus.INVALID_NUMBER: "dim red",
    ContactStatus.RESTRICTED: "red",
    ContactStatus.NOT_INTERESTED: "dim",
    ContactStatus.CONVERTED: "bold magenta",
}


def _contact_badge(lead: Lead) -> str:
    """Status de contato formatado para a tabela."""
    label = CONTACT_STATUS_LABELS.get(lead.contact_status, lead.contact_status)
    style = _CONTACT_STYLES.get(lead.contact_status, "white")
    return f"[{style}]{label}[/]"


def _ask_city_hint(prompt: str) -> str:
    """
    Pergunta a cidade da prospecção e normaliza ("floripa" → "Florianópolis").
    A bio do lead sempre tem prioridade sobre essa cidade.
    """
    raw = Prompt.ask(prompt, default=DEFAULT_CITY).strip()
    if not raw:
        return ""

    city, state = normalize_city_input(raw)
    label = f"{city}/{state}" if state else city
    console.print(f"  [dim]📍 Cidade da prospecção: {label}[/]")
    return f"{city} - {state}" if state else city


def _ask_city_choice(prompt: str = "[bold]Cidade[/]") -> str | None:
    """
    Lista as cidades do banco e devolve a escolhida.
    None = usuário cancelou. "" = leads sem cidade identificada.
    """
    cities = list_cities()
    if not cities:
        console.print("[yellow]Nenhuma cidade identificada no banco ainda.[/]")
        console.print("[dim]Use a opção [13] para inferir as cidades das bios já coletadas.[/]")
        return None

    for i, city in enumerate(cities, 1):
        console.print(f"  [cyan]{i:>2}[/] {city}")
    console.print("   [dim]0[/] [dim]leads sem cidade identificada[/]")

    choice = IntPrompt.ask(f"\n{prompt} [dim](número)[/]", default=1)
    if choice == 0:
        return ""
    if 1 <= choice <= len(cities):
        return cities[choice - 1]

    console.print("[yellow]Opção inválida[/]")
    return None


def _ensure_browser() -> InstagramBrowser | None:
    """Garante que o browser está conectado."""
    global _browser
    if _browser is None:
        _browser = InstagramBrowser(on_status=lambda msg: console.print(f"  {msg}"))
        if not _browser.connect():
            console.print("[bold red]❌ Falha ao conectar ao Instagram[/]")
            console.print("[yellow]   Verifique IG_USERNAME e IG_PASSWORD no .env[/]")
            # Cleanup completo para não deixar processo Playwright pendurado
            _browser.close()
            _browser = None
            return None
    return _browser


def _action_prospect_accounts() -> None:
    """Prospectar via seguidores de academias."""
    browser = _ensure_browser()
    if not browser:
        return

    # Pede contas de academias
    default = ",".join(SEED_ACCOUNTS) if SEED_ACCOUNTS else ""
    raw = Prompt.ask(
        "[bold]Contas de academias (separadas por vírgula)[/]",
        default=default,
    )
    accounts = [a.strip().lstrip("@") for a in raw.split(",") if a.strip()]

    if not accounts:
        console.print("[yellow]Nenhuma conta informada[/]")
        return

    max_per = IntPrompt.ask("[bold]Máximo de seguidores por academia[/]", default=100)
    city_hint = _ask_city_hint("[bold]Cidade dessas academias[/] [dim](opcional — usada quando a bio não diz)[/]")

    stats = ProspectStats()
    leads_found: list[Lead] = []

    def on_status(msg: str):
        console.print(msg)

    def on_lead(lead: Lead):
        leads_found.append(lead)

    try:
        for account in accounts:
            prospect_from_account(
                browser, account,
                max_followers=max_per,
                on_status=on_status,
                on_lead=on_lead,
                stats=stats,
                city_hint=city_hint,
            )
    except KeyboardInterrupt:
        console.print("\n[yellow]⚠️  Interrompido pelo usuário[/]")

    # Resumo
    console.print()
    _print_summary(stats, leads_found)


def _action_prospect_hashtags() -> None:
    """Prospectar via hashtags."""
    browser = _ensure_browser()
    if not browser:
        return

    default = ",".join(SEARCH_HASHTAGS) if SEARCH_HASHTAGS else "personaltrainer"
    raw = Prompt.ask(
        "[bold]Hashtags (separadas por vírgula)[/]",
        default=default,
    )
    hashtags = [h.strip().lstrip("#") for h in raw.split(",") if h.strip()]

    if not hashtags:
        console.print("[yellow]Nenhuma hashtag informada[/]")
        return

    max_per = IntPrompt.ask("[bold]Máximo de perfis por hashtag[/]", default=20)
    city_hint = _ask_city_hint("[bold]Cidade dessas hashtags[/] [dim](opcional — usada quando a bio não diz)[/]")

    stats = ProspectStats()
    leads_found: list[Lead] = []

    def on_status(msg: str):
        console.print(msg)

    def on_lead(lead: Lead):
        leads_found.append(lead)

    try:
        for hashtag in hashtags:
            prospect_from_hashtag(
                browser, hashtag,
                max_profiles=max_per,
                on_status=on_status,
                on_lead=on_lead,
                stats=stats,
                city_hint=city_hint,
            )
    except KeyboardInterrupt:
        console.print("\n[yellow]⚠️  Interrompido pelo usuário[/]")

    console.print()
    _print_summary(stats, leads_found)


def _action_list_leads(status_filter: LeadStatus = None) -> None:
    """Lista leads no terminal."""
    if status_filter:
        leads = get_leads_by_status(status_filter)
        title = f"Leads — {status_filter.value}"
    else:
        leads = get_all_leads()
        title = "Todos os Leads"

    if not leads:
        console.print("[yellow]Nenhum lead encontrado[/]")
        return

    table = Table(
        title=f"📋 {title} ({len(leads)})",
        box=box.ROUNDED,
        border_style="cyan",
        show_lines=True,
    )
    table.add_column("#", style="dim", width=4)
    table.add_column("Username", style="bold cyan", max_width=20)
    table.add_column("Nome", max_width=18)
    table.add_column("Cidade", style="magenta", max_width=18)
    table.add_column("Seg.", style="green", justify="right", width=8)
    table.add_column("Score", style="yellow", justify="right", width=6)
    table.add_column("WhatsApp", style="bold green", max_width=18)
    table.add_column("Status", max_width=14)
    table.add_column("Contato", max_width=16)
    table.add_column("Origem", style="dim", max_width=13)

    for i, lead in enumerate(leads[:50], 1):
        status_style = {
            LeadStatus.WHATSAPP_FOUND: "bold green",
            LeadStatus.QUALIFIED: "bold yellow",
            LeadStatus.NO_WHATSAPP: "dim red",
            LeadStatus.SKIPPED: "dim",
            LeadStatus.ERROR: "red",
        }.get(lead.status, "white")

        whatsapp_display = f"+{lead.whatsapp}" if lead.whatsapp else "—"

        table.add_row(
            str(i),
            f"@{lead.username}",
            lead.full_name[:18],
            lead.location_label,
            f"{lead.followers:,}",
            str(lead.score),
            whatsapp_display,
            f"[{status_style}]{lead.status.value}[/]",
            _contact_badge(lead),
            lead.source_account[:13] if lead.source_account else "—",
        )

    console.print(table)

    if len(leads) > 50:
        console.print(f"[dim]... e mais {len(leads) - 50} leads (exporte CSV para ver todos)[/]")


def _action_export() -> None:
    """Exporta leads para CSV."""
    filepath = export_csv()
    console.print(f"\n[bold green]✅ Exportado para:[/] {filepath}")

    leads_wpp = get_leads_by_status(LeadStatus.WHATSAPP_FOUND)
    all_leads = get_all_leads()
    console.print(f"   📱 {len(leads_wpp)} leads com WhatsApp")
    console.print(f"   📋 {len(all_leads)} leads total")


def _action_stats() -> None:
    """Mostra estatísticas detalhadas."""
    stats = get_stats()

    table = Table(
        title="📊 Estatísticas Detalhadas",
        box=box.DOUBLE,
        border_style="green",
    )
    table.add_column("Métrica", style="bold")
    table.add_column("Valor", style="green", justify="right")
    table.add_column("Detalhes", style="dim")

    table.add_row("Academias analisadas", str(stats.academies_scraped), "contas-semente")
    table.add_row("Perfis analisados", str(stats.profiles_analyzed), "total de verificações")
    table.add_row("Leads qualificados", str(stats.leads_qualified), f"≥{MIN_FOLLOWERS} seg + PT na bio")

    if stats.leads_qualified > 0:
        conv_rate = (stats.whatsapp_found / stats.leads_qualified) * 100
        table.add_row("WhatsApp encontrado", str(stats.whatsapp_found), f"{conv_rate:.1f}% de conversão")
    else:
        table.add_row("WhatsApp encontrado", str(stats.whatsapp_found), "—")

    table.add_row("Sem WhatsApp", str(stats.whatsapp_not_found), "sem link ou não encontrado")
    table.add_row("Erros", str(stats.errors), "perfis inacessíveis")

    console.print(table)

    # Funil de contato
    funnel = get_contact_funnel()
    if funnel:
        contact_table = Table(
            title="📞 Funil de Contato", box=box.ROUNDED, border_style="cyan"
        )
        contact_table.add_column("Status de contato", style="bold")
        contact_table.add_column("Leads", justify="right", style="cyan")

        for status in ContactStatus:
            total = funnel.get(status.value, 0)
            if total:
                contact_table.add_row(CONTACT_STATUS_LABELS[status], str(total))

        console.print()
        console.print(contact_table)

    # Cidades com mais leads
    summary = get_city_summary()
    top_cities = [r for r in summary if r["city"]][:5]
    if top_cities:
        console.print("\n[bold magenta]🗺️  Top 5 cidades:[/]")
        for row in top_cities:
            console.print(
                f"  • {row['city']}/{row['state'] or '—'} — {row['total']} leads, "
                f"{row['com_whatsapp'] or 0} com WhatsApp, "
                f"{row['pendentes'] or 0} pendentes de contato"
            )

    # Top leads
    wpp_leads = get_leads_by_status(LeadStatus.WHATSAPP_FOUND)
    if wpp_leads:
        console.print(f"\n[bold green]🏆 Top 5 Leads com WhatsApp:[/]")
        for i, lead in enumerate(wpp_leads[:5], 1):
            console.print(
                f"  {i}. @{lead.username} — {lead.followers:,} seg — "
                f"+{lead.whatsapp} — score {lead.score}"
            )


def _action_analyze_profile() -> None:
    """Analisa um perfil específico."""
    browser = _ensure_browser()
    if not browser:
        return

    username = Prompt.ask("[bold]Username do perfil[/]").strip().lstrip("@")
    if not username:
        return

    console.print(f"\n🔍 Analisando @{username}...")

    from prospect.scraper import extract_profile_data, _is_personal_trainer
    from prospect.whatsapp_finder import find_whatsapp

    lead = extract_profile_data(browser, username)
    if not lead:
        console.print("[red]❌ Não consegui extrair dados do perfil[/]")
        return

    # Mostra detalhes do perfil
    table = Table(title=f"👤 @{username}", box=box.ROUNDED, border_style="cyan")
    table.add_column("Campo", style="bold")
    table.add_column("Valor")

    table.add_row("Nome", lead.full_name or "—")
    table.add_row("Bio", lead.bio[:100] or "—")
    table.add_row("Seguidores", f"{lead.followers:,}")
    table.add_row("Seguindo", f"{lead.following:,}")
    table.add_row("Posts", f"{lead.posts_count:,}")
    table.add_row("Link Bio", lead.bio_link or "—")
    table.add_row("Último Post", lead.last_post_date or "—")
    table.add_row("É PT?", "✅ Sim" if _is_personal_trainer(lead.bio, lead.full_name) else "❌ Não")
    table.add_row("Score", str(lead.score))

    console.print(table)

    # Busca WhatsApp se tiver link
    if lead.bio_link or lead.bio:
        console.print("\n🔎 Buscando WhatsApp...")
        phone, source = find_whatsapp(lead.bio, lead.bio_link)
        if phone:
            console.print(f"[bold green]📱 WhatsApp: +{phone}[/] (via {source})")
            lead.whatsapp = phone
            lead.whatsapp_source = source
            lead.status = LeadStatus.WHATSAPP_FOUND
        else:
            console.print("[yellow]📵 WhatsApp não encontrado[/]")
            lead.status = LeadStatus.NO_WHATSAPP
    else:
        console.print("[yellow]📵 Sem link na bio[/]")

    if Confirm.ask("Salvar este lead?", default=True):
        from prospect.db import upsert_lead
        upsert_lead(lead)
        console.print("[green]✅ Lead salvo![/]")


def _action_rescan_whatsapp() -> None:
    """Realiza varredura de WhatsApp em leads já cadastrados no banco de dados."""
    from prospect.db import get_leads_without_whatsapp
    from prospect.pipeline import rescan_whatsapp_existing_leads

    pending_leads = get_leads_without_whatsapp()
    if not pending_leads:
        console.print("\n[yellow]ℹ️  Nenhum lead pendente sem WhatsApp encontrado no banco de dados.[/]")
        return

    console.print(f"\n[bold cyan]🔄 {len(pending_leads)} leads pendentes sem WhatsApp no banco de dados.[/]")
    
    use_browser = Confirm.ask(
        "[bold]Deseja usar o navegador Playwright para renderizar páginas dinâmicas (Canva/JS)?[/]",
        default=True,
    )

    browser = _ensure_browser() if use_browser else None

    stats = ProspectStats()
    leads_found: list[Lead] = []

    def on_status(msg: str):
        console.print(msg)

    def on_lead(lead: Lead):
        leads_found.append(lead)

    try:
        rescan_whatsapp_existing_leads(
            browser=browser,
            on_status=on_status,
            on_lead=on_lead,
            stats=stats,
        )
    except KeyboardInterrupt:
        console.print("\n[yellow]⚠️  Interrompido pelo usuário[/]")

    console.print()
    _print_summary(stats, leads_found)


def _action_leads_by_city() -> None:
    """Dashboard de leads agrupados por cidade."""
    summary = get_city_summary()
    if not summary:
        console.print("[yellow]Nenhum lead no banco ainda.[/]")
        return

    table = Table(
        title="🗺️  Leads por Cidade",
        box=box.ROUNDED,
        border_style="magenta",
        show_lines=False,
    )
    table.add_column("Cidade", style="bold magenta", max_width=24)
    table.add_column("UF", style="dim", width=4)
    table.add_column("Total", justify="right", style="bold")
    table.add_column("Com WA", justify="right", style="green")
    table.add_column("Contatados", justify="right", style="cyan")
    table.add_column("Pendentes", justify="right", style="bold yellow")
    table.add_column("Responderam", justify="right", style="blue")
    table.add_column("Score méd.", justify="right", style="dim")

    for row in summary:
        table.add_row(
            row["city"] or "[dim]— não identificada —[/]",
            row["state"] or "—",
            str(row["total"]),
            str(row["com_whatsapp"] or 0),
            str(row["contatados"] or 0),
            str(row["pendentes"] or 0),
            str(row["responderam"] or 0),
            str(row["score_medio"] or 0),
        )

    console.print(table)

    unknown = next((r["total"] for r in summary if not r["city"]), 0)
    if unknown:
        console.print(
            f"[dim]{unknown} leads sem cidade identificada — "
            f"a opção [13] tenta inferir da bio.[/]"
        )

    if not Confirm.ask("\n[bold]Abrir os leads de uma cidade?[/]", default=False):
        return

    console.print()
    city = _ask_city_choice("[bold]Qual cidade[/]")
    if city is None:
        return

    only_pending = Confirm.ask(
        "[bold]Só os pendentes de contato (com WhatsApp)?[/]", default=False
    )
    leads = get_leads_by_city(city, only_pending=only_pending)

    if not leads:
        console.print("[yellow]Nenhum lead nesse filtro.[/]")
        return

    _print_lead_table(leads, f"{city or 'Sem cidade'} ({len(leads)})")

    if Confirm.ask("\n[bold]Exportar essa cidade para CSV?[/]", default=False):
        filepath, total = export_city_csv(city)
        console.print(f"[bold green]✅ {total} leads exportados para:[/] {filepath}")


def _action_update_contact_status() -> None:
    """Atualiza manualmente o status de contato de um lead."""
    username = Prompt.ask("[bold]Username do lead[/]").strip().lstrip("@")
    if not username:
        return

    lead = get_lead(username)
    if not lead:
        console.print(f"[red]❌ @{username} não está no banco.[/]")
        return

    console.print(
        f"\n[bold cyan]@{lead.username}[/] — {lead.full_name or 'Personal'} — "
        f"📍 {lead.location_label}"
    )
    console.print(f"  Status atual: {_contact_badge(lead)}")
    if lead.contacted_at:
        console.print(f"  [dim]Último contato: {lead.contacted_at[:16]} "
                      f"({lead.contact_attempts} tentativa(s))[/]")

    options = list(ContactStatus)
    console.print("\n[bold]Novo status de contato:[/]")
    for i, status in enumerate(options, 1):
        console.print(f"  [cyan]{i:>2}[/] {CONTACT_STATUS_LABELS[status]}")

    choice = IntPrompt.ask("\n[bold]Escolha[/]", default=1)
    if not 1 <= choice <= len(options):
        console.print("[yellow]Opção inválida[/]")
        return

    new_status = options[choice - 1]
    channel = ""
    if new_status != ContactStatus.NOT_CONTACTED:
        channel = Prompt.ask(
            "[bold]Canal[/]", default=lead.contact_channel or "whatsapp"
        ).strip()

    updated = set_contact_status(
        username, new_status, channel=channel,
        increment_attempts=(new_status == ContactStatus.CONTACTED),
    )
    if not updated:
        console.print("[red]❌ Falha ao atualizar.[/]")
        return

    console.print(f"\n[bold green]✅ Status atualizado:[/] {_contact_badge(updated)}")
    if supabase_sync.is_configured():
        if updated.synced_at:
            console.print("[dim]   ☁️  Espelhado no Supabase.[/]")
        else:
            console.print("[dim]   ⏳ Pendente de sync — use a opção [13].[/]")


def _action_supabase_sync() -> None:
    """Sincroniza leads e cidades com o Supabase."""
    console.print(f"\n[bold cyan]☁️  Supabase[/] — {supabase_sync.status_message()}")

    if not supabase_sync.is_configured():
        console.print(
            "\n[yellow]Configure no .env:[/]\n"
            "  SUPABASE_URL=https://xxxxxxxx.supabase.co\n"
            "  SUPABASE_SERVICE_KEY=<service_role key>\n\n"
            "[dim]E aplique supabase/migrations/0001_create_leads.sql "
            "no SQL Editor do Supabase.[/]"
        )
        return

    ok, message = supabase_sync.check_connection()
    console.print(f"  {'✅' if ok else '❌'} {message}")
    if not ok:
        return

    # Preenche cidades faltantes antes de subir
    pending_cities = [l for l in get_all_leads() if not l.city]
    if pending_cities and Confirm.ask(
        f"\n[bold]{len(pending_cities)} leads sem cidade. Inferir da bio agora?[/]",
        default=True,
    ):
        backfill_cities(on_status=lambda msg: console.print(msg))

    unsynced = get_unsynced_leads()
    force_all = False
    if not unsynced:
        console.print("\n[green]Todos os leads já estão sincronizados.[/]")
        force_all = Confirm.ask("[bold]Reenviar tudo mesmo assim?[/]", default=False)
        if not force_all:
            return
    else:
        console.print(f"\n[bold]{len(unsynced)} leads pendentes de sincronização.[/]")

    synced, remaining, error = sync_pending_leads(
        on_status=lambda msg: console.print(msg), force_all=force_all
    )
    console.print(f"\n[bold green]✅ {synced} leads enviados ao Supabase[/]")
    if remaining:
        console.print(f"[yellow]⚠️  {remaining} ainda pendentes[/]")
    if error:
        console.print(f"[red]   {error}[/]")

    if Confirm.ask(
        "\n[bold]Trazer do Supabase os status de contato editados no painel?[/]",
        default=False,
    ):
        updated, pull_error = apply_remote_contact_status(
            on_status=lambda msg: console.print(msg)
        )
        if pull_error:
            console.print(f"[red]❌ {pull_error}[/]")
        else:
            console.print(f"[bold green]✅ {updated} leads atualizados localmente[/]")


def _print_lead_table(leads: list[Lead], title: str) -> None:
    """Tabela compacta de leads, usada pelas telas de cidade."""
    table = Table(title=f"📋 {title}", box=box.ROUNDED, border_style="cyan")
    table.add_column("#", style="dim", width=4)
    table.add_column("Username", style="bold cyan", max_width=22)
    table.add_column("Nome", max_width=20)
    table.add_column("Cidade", style="magenta", max_width=18)
    table.add_column("Seg.", justify="right", style="green", width=8)
    table.add_column("Score", justify="right", style="yellow", width=6)
    table.add_column("WhatsApp", style="bold green", max_width=18)
    table.add_column("Contato", max_width=16)

    for i, lead in enumerate(leads[:80], 1):
        table.add_row(
            str(i),
            f"@{lead.username}",
            lead.full_name[:20],
            lead.location_label,
            f"{lead.followers:,}",
            str(lead.score),
            f"+{lead.whatsapp}" if lead.whatsapp else "—",
            _contact_badge(lead),
        )

    console.print(table)
    if len(leads) > 80:
        console.print(f"[dim]... e mais {len(leads) - 80} leads[/]")


def _print_summary(stats: ProspectStats, leads: list[Lead]) -> None:
    """Imprime resumo da prospecção."""
    console.print(Panel(
        f"[bold]Resumo da Prospecção[/]\n\n"
        f"  👤 Perfis analisados: {stats.profiles_analyzed}\n"
        f"  ✅ Qualificados: {stats.leads_qualified}\n"
        f"  📱 WhatsApp encontrado: {stats.whatsapp_found}\n"
        f"  📵 Sem WhatsApp: {stats.whatsapp_not_found}\n"
        f"  ❌ Erros: {stats.errors}",
        border_style="green",
        box=box.ROUNDED,
    ))

    wpp_leads = [l for l in leads if l.whatsapp]
    no_wpp_qualified = [l for l in leads if not l.whatsapp and l.status in (LeadStatus.QUALIFIED, LeadStatus.NO_WHATSAPP)]

    if wpp_leads:
        console.print("\n[bold green]📱 PERFIS COM WHATSAPP:[/]")
        for lead in wpp_leads:
            console.print(
                f"  • [bold green]@{lead.username}[/] — {lead.full_name or 'Personal'} — "
                f"[bold]+{lead.whatsapp}[/] ({lead.whatsapp_source})"
            )

    if no_wpp_qualified:
        console.print("\n[bold yellow]⭐️ PERFIS QUALIFICADOS (SEM WHATSAPP):[/]")
        for lead in no_wpp_qualified:
            console.print(
                f"  • [bold yellow]@{lead.username}[/] — {lead.full_name or 'Personal'} — "
                f"{lead.followers:,} seg (Score: {lead.score})"
            )


def _cleanup(signum=None, frame=None) -> None:
    """Limpeza ao sair."""
    global _browser
    if _browser:
        try:
            _browser.close()
        except Exception:
            pass
        _browser = None
    if signum is not None:
        console.print("\n[bold cyan]👋 Encerramento solicitado via Ctrl+C. Até mais![/]")
        sys.exit(0)


def _action_send_whatsapp_campaign() -> None:
    """Disparo de mensagens de campanha via WhatsApp Web com simulação humana."""
    from prospect.whatsapp_sender import run_outreach_campaign

    # Fila = tem WhatsApp e nunca foi contatado
    city = ""
    if list_cities() and Confirm.ask(
        "\n[bold]Filtrar a campanha por cidade?[/]", default=False
    ):
        console.print()
        chosen = _ask_city_choice("[bold]Cidade da campanha[/]")
        if chosen is None:
            return
        city = chosen

    leads = get_pending_contact_leads(city=city)
    if not leads:
        scope = f" em {city}" if city else ""
        console.print(
            f"\n[yellow]⚠️ Nenhum lead com WhatsApp pendente de contato{scope}.[/]"
        )
        console.print("[dim]   Leads já contatados não entram em novo disparo.[/]")
        return

    scope = f" em [magenta]{city}[/]" if city else ""
    console.print(
        f"\n[bold cyan]💬 {len(leads)} leads com WhatsApp aguardando primeiro contato{scope}.[/]"
    )

    default_template = "{Oii}, tudo bem"

    template = Prompt.ask("\n[bold]Digite a mensagem (suporta {nome}, {username}, Spintax {A|B})[/]", default=default_template)
    max_contacts = IntPrompt.ask("[bold]Quantidade máxima de mensagens nesta sessão[/]", default=10)
    min_delay = IntPrompt.ask("[bold]Intervalo mínimo de pausa anti-banimento (segundos)[/]", default=60)
    max_delay = IntPrompt.ask("[bold]Intervalo máximo de pausa anti-banimento (segundos)[/]", default=150)

    if not Confirm.ask("\n[bold yellow]Iniciar disparos humanizados no WhatsApp Web?[/]", default=True):
        return

    run_outreach_campaign(
        leads=leads,
        template=template,
        min_delay_sec=min_delay,
        max_delay_sec=max_delay,
        max_contacts=max_contacts,
        on_status=lambda msg: console.print(msg),
    )


def main() -> None:
    """Ponto de entrada principal."""
    signal.signal(signal.SIGINT, _cleanup)
    signal.signal(signal.SIGTERM, _cleanup)

    init_db()

    try:
        while True:
            console.clear()
            console.print(_banner())
            console.print(Columns([_stats_panel(), _config_panel()], padding=2))

            choice = _menu()

            if choice == "0":
                console.print("[bold cyan]👋 Até mais![/]")
                break
            elif choice == "1":
                _action_prospect_accounts()
            elif choice == "2":
                _action_prospect_hashtags()
            elif choice == "3":
                _action_list_leads()
            elif choice == "4":
                _action_list_leads(LeadStatus.WHATSAPP_FOUND)
            elif choice == "5":
                _action_list_leads(LeadStatus.NO_WHATSAPP)
            elif choice == "6":
                _action_export()
            elif choice == "7":
                _action_stats()
            elif choice == "8":
                _action_analyze_profile()
            elif choice == "9":
                _action_rescan_whatsapp()
            elif choice == "10":
                _action_send_whatsapp_campaign()
            elif choice == "11":
                _action_leads_by_city()
            elif choice == "12":
                _action_update_contact_status()
            elif choice == "13":
                _action_supabase_sync()

            if choice != "0":
                console.print()
                Prompt.ask("[dim]Pressione ENTER para continuar[/]", default="")

    except KeyboardInterrupt:
        console.print("\n[bold cyan]👋 Até mais![/]")
    finally:
        _cleanup()


if __name__ == "__main__":
    main()
