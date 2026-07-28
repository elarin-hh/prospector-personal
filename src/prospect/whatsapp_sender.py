"""
=============================================================================
  prospect/whatsapp_sender.py
  Automação segura de disparos via WhatsApp Web usando Playwright.
  
  Recursos:
    - Persistência de perfil/sessão (QR Code escaneado apenas 1 vez)
    - Anti-detecção (Stealth, remoção de navigator.webdriver)
    - Suporte a Spintax: {Olá|Oi|Tudo bem} {nome}
    - Simulação de digitação humana e navegação com pausas aleatórias
    - Tratamento de números inválidos e relatórios de progresso
=============================================================================
"""
from __future__ import annotations
import re
import time
import random
from pathlib import Path
from urllib.parse import quote
from datetime import datetime
from typing import Callable, Optional

from playwright.sync_api import sync_playwright, BrowserContext, Page

from prospect.config import DATA_DIR
from prospect.models import Lead, LeadStatus
from prospect.db import upsert_lead

# Diretório para salvar sessão do WhatsApp Web (cookies, IndexedDB, etc.)
WA_SESSION_DIR = DATA_DIR / "whatsapp_web_profile"


def parse_spintax(text: str) -> str:
    """
    Processa variações em Spintax no formato {opção1|opção2|opção3}.
    Exemplo: "{Olá|Oi|Tudo bem} {nome}" -> "Oi Jefferson"
    """
    pattern = re.compile(r"\{([^{}]+)\}")
    while pattern.search(text):
        text = pattern.sub(lambda m: random.choice(m.group(1).split("|")), text)
    return text


def format_lead_message(template: str, lead: Lead) -> str:
    """Substitui variáveis do lead no template e aplica Spintax."""
    # Extrai primeiro nome
    full_name = (lead.full_name or "").strip()
    first_name = full_name.split()[0] if full_name else lead.username

    text = template.replace("{nome}", first_name)
    text = text.replace("{nome_completo}", full_name or lead.username)
    text = text.replace("{username}", lead.username)
    text = text.replace("{bio}", lead.bio or "")

    return parse_spintax(text)


def should_retry_restricted_lead(lead_notes: str, cooldown_hours: int = 24) -> tuple[bool, str]:
    """
    Verifica se um lead que falhou por restrição temporária já cumpriu a carência de 24h.
    Retorna (pode_retentar, mensagem_explicativa).
    """
    if not lead_notes or ("restrita" not in lead_notes.lower() and "restrito" not in lead_notes.lower()):
        return True, ""

    match = re.search(r"Tentado contato em (\d{4}-\d{2}-\d{2} \d{2}:\d{2})", lead_notes)
    if not match:
        return True, ""

    try:
        attempted_dt = datetime.strptime(match.group(1), "%Y-%m-%d %H:%M")
        elapsed_hours = (datetime.now() - attempted_dt).total_seconds() / 3600
        if elapsed_hours < cooldown_hours:
            remaining_hours = max(1, int(cooldown_hours - elapsed_hours))
            return False, f"tentado há {int(elapsed_hours)}h. Carência de 24h em andamento (restam ~{remaining_hours}h)"
    except Exception:
        pass

    return True, "carência de 24h concluída, re-testando disparo"


class WhatsAppWebSender:
    """Gerencia conexões e disparos via WhatsApp Web."""

    def __init__(self, on_status: Optional[Callable[[str], None]] = None):
        self.emit = on_status or (lambda msg: print(msg))
        self.playwright = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None

    def start(self, headless: bool = False) -> bool:
        """Inicia o navegador com perfil persistente para WhatsApp Web."""
        try:
            WA_SESSION_DIR.mkdir(parents=True, exist_ok=True)
            self.emit("🌐 Iniciando navegador para WhatsApp Web...")

            self.playwright = sync_playwright().start()

            args = [
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-infobars",
                "--window-size=1280,800",
            ]

            self.context = self.playwright.chromium.launch_persistent_context(
                user_data_dir=str(WA_SESSION_DIR),
                headless=headless,
                args=args,
                viewport={"width": 1280, "height": 800},
                user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            )

            # Injeta script stealth para remover flag de automação
            self.context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            """)

            self.page = self.context.pages[0] if self.context.pages else self.context.new_page()

            # Acessa WhatsApp Web
            self.page.goto("https://web.whatsapp.com/", wait_until="domcontentloaded")
            self.emit("⏳ Aguardando carregamento do WhatsApp Web...")

            # Verifica se precisa escanear QR Code ou se já está logado
            return self._wait_for_login()

        except Exception as e:
            self.emit(f"❌ Erro ao iniciar WhatsApp Web: {e}")
            self.close()
            return False

    def _wait_for_login(self, timeout_seconds: int = 120) -> bool:
        """Aguardar autenticação (escaneamento de QR Code ou restauração de sessão)."""
        start_time = time.time()
        qr_emitted = False

        while time.time() - start_time < timeout_seconds:
            try:
                # Verifica se a lista de conversas / busca está visível (logado)
                if self.page.locator("div[contenteditable='true'][data-tab='3'], #side, header span[data-icon='chat']").count() > 0:
                    self.emit("✅ WhatsApp Web conectado e pronto!")
                    return True

                # Verifica se QR Code está visível
                if self.page.locator("canvas[aria-label*='Scan'], canvas").count() > 0:
                    if not qr_emitted:
                        self.emit("📱 QR Code detectado! Por favor, escaneie o QR Code no seu celular para conectar.")
                        qr_emitted = True

                time.sleep(2)
            except Exception:
                time.sleep(2)

        self.emit("❌ Tempo limite esgotado para login no WhatsApp Web.")
        return False

    def send_message(self, phone: str, message: str) -> tuple[bool, str]:
        """
        Envia uma mensagem para um número formatado.
        Retorna (sucesso, mensagem_ou_erro).
        """
        if not self.page:
            return False, "Navegador não inicializado"

        # Garante apenas dígitos no número (ex: 554199538159)
        clean_phone = re.sub(r"\D", "", phone)
        if not clean_phone.startswith("55") and len(clean_phone) in (10, 11):
            clean_phone = "55" + clean_phone

        encoded_msg = quote(message)
        url = f"https://web.whatsapp.com/send?phone={clean_phone}&text={encoded_msg}"

        try:
            self.emit(f"  🔗 Abrindo conversa com +{clean_phone}...")
            self.page.goto(url, wait_until="domcontentloaded", timeout=30000)

            # Aguarda carregamento do chat ou alerta de número inválido
            start_wait = time.time()
            chat_found = False

            while time.time() - start_wait < 18:
                # 1. Se o campo de texto do chat carregou e está visível, o chat está PRONTO!
                input_box = self.page.locator("footer div[contenteditable='true'], div[contenteditable='true'][data-tab='10']")
                if input_box.count() > 0 and input_box.first.is_visible():
                    chat_found = True
                    break

                # 2. Checa pop-up de número inválido (ex: "The phone number isn't on WhatsApp" / "inválido")
                invalid_popups = self.page.locator(
                    "*:has-text('inválido'), *:has-text('invalid'), *:has-text('não está no WhatsApp'), "
                    "*:has-text('isn\\'t on WhatsApp'), *:has-text('isn’t on WhatsApp'), *:has-text('is not on WhatsApp'), "
                    "*:has-text('isn\\'t on'), *:has-text('isn’t on')"
                )
                if invalid_popups.count() > 0 and invalid_popups.first.is_visible():
                    try:
                        ok_btn = self.page.locator("button:has-text('OK'), button:has-text('Ok'), button")
                        if ok_btn.count() > 0:
                            ok_btn.first.click()
                    except Exception:
                        pass
                    return False, "Número não possui WhatsApp registrado"

                time.sleep(1)

            if not chat_found:
                # Checa por frases de número inválido se o chat não carregou
                invalid_keywords = [
                    "isn't on", "isn’t on", "is not on", "invalid", "inválido", "não está no whatsapp"
                ]
                for ikw in invalid_keywords:
                    try:
                        elem = self.page.locator(f"*:has-text('{ikw}')")
                        if elem.count() > 0 and elem.first.is_visible():
                            return False, "Número não possui WhatsApp registrado"
                    except Exception:
                        pass

                # Checa se a tela/modal contém o aviso de restrição
                restricted_keywords = [
                    "linked devices", "restricted", "restrita", "restrito",
                    "start new chats", "iniciar novas conversas", "show details", "detalhes"
                ]
                for kw in restricted_keywords:
                    try:
                        elem = self.page.locator(f"*:has-text('{kw}')")
                        if elem.count() > 0 and elem.first.is_visible():
                            return False, "CONTA_RESTRITA: Sua conta em dispositivos conectados está restrita pelo WhatsApp."
                    except Exception:
                        pass

                return False, "Falha ao carregar caixa de diálogo do chat"

            input_box = self.page.locator("footer div[contenteditable='true'], div[contenteditable='true'][data-tab='10']").first
            input_box.click()
            time.sleep(random.uniform(0.5, 1.2))

            # Simula digitação humana rápida de confirmação/ajuste
            send_btn = self.page.locator("button[aria-label='Enviar'], button[aria-label='Send'], span[data-icon='send']").first

            # Se por algum motivo o texto não preencheu via URL, digita manualmente
            text_in_box = input_box.inner_text().strip()
            if not text_in_box:
                self.emit("  ⌨️ Digitando mensagem...")
                for char in message:
                    input_box.type(char, delay=random.randint(30, 90))
                time.sleep(0.5)

            # Clica no botão de envio
            if send_btn.count() > 0 and send_btn.is_visible():
                send_btn.click()
            else:
                self.page.keyboard.press("Enter")

            time.sleep(3.5)

            # Checa se o WhatsApp exibiu o ícone vermelho / botão "Try again" (falha no envio da mensagem)
            retry_btn = self.page.locator(
                "button:has-text('Try again'), "
                "button:has-text('Tentar novamente'), "
                "span:has-text('Try again'), "
                "span:has-text('Tentar novamente'), "
                "span[data-icon='msg-check-failed']"
            )
            if retry_btn.count() > 0 and retry_btn.first.is_visible():
                self.emit("  ⚠️ Alerta 'Try again' detectado na mensagem. Tentando re-enviar...")
                try:
                    retry_btn.first.click()
                    time.sleep(3.0)
                except Exception:
                    pass

                # Re-verifica se o erro persiste
                if retry_btn.count() > 0 and retry_btn.first.is_visible():
                    return False, "Falha no envio da mensagem (WhatsApp exibiu 'Try again')"

            self.emit(f"  ✅ Mensagem enviada para +{clean_phone}!")
            return True, "Enviado com sucesso"

        except Exception as e:
            return False, f"Erro no envio: {e}"

    def close(self) -> None:
        """Encerra sessão e fecha o navegador."""
        try:
            if self.context:
                self.context.close()
            if self.playwright:
                self.playwright.stop()
        except Exception:
            pass
        finally:
            self.context = None
            self.page = None
            self.playwright = None
            self.emit("🔒 WhatsApp Web encerrado.")


def run_outreach_campaign(
    leads: list[Lead],
    template: str,
    min_delay_sec: int = 60,
    max_delay_sec: int = 180,
    max_contacts: int = 20,
    on_status: Optional[Callable[[str], None]] = None,
) -> dict:
    """
    Executa uma campanha de mensagens humanizadas no WhatsApp Web.
    """
    emit = on_status or (lambda msg: print(msg))
    sender = WhatsAppWebSender(on_status=emit)

    if not sender.start(headless=False):
        return {"sent": 0, "failed": 0, "invalid": 0, "restricted": 0}

    results = {"sent": 0, "failed": 0, "invalid": 0, "restricted": 0}
    leads_to_process = leads[:max_contacts]

    emit(f"\n🚀 Iniciando campanha para {len(leads_to_process)} leads (intervalo: {min_delay_sec}-{max_delay_sec}s)...")

    try:
        for i, lead in enumerate(leads_to_process, 1):
            if not lead.whatsapp:
                continue

            if "enviado" in (lead.notes or "").lower():
                emit(f"  ⏭️  @{lead.username} (+{lead.whatsapp}) já contatado anteriormente. Ignorando.")
                continue

            if "restrita" in (lead.notes or "").lower() or "restrito" in (lead.notes or "").lower():
                can_retry, reason = should_retry_restricted_lead(lead.notes, cooldown_hours=24)
                if not can_retry:
                    emit(f"  ⏭️  @{lead.username} (+{lead.whatsapp}) — {reason}. Ignorando por enquanto.")
                    continue
                else:
                    emit(f"  🔄 @{lead.username} (+{lead.whatsapp}) — {reason}. Re-tentando disparo...")

            emit(f"\n💬 [{i}/{len(leads_to_process)}] Preparando envio para @{lead.username} (+{lead.whatsapp})...")
            msg_text = format_lead_message(template, lead)
            emit(f"  📝 Mensagem gerada:\n   \"{msg_text[:80]}...\"")

            success, status_msg = sender.send_message(lead.whatsapp, msg_text)

            if success:
                results["sent"] += 1
                lead.notes = f"WhatsApp enviado em {time.strftime('%Y-%m-%d %H:%M')}"
                upsert_lead(lead)
            elif "CONTA_RESTRITA" in status_msg or "restrita" in status_msg.lower():
                results["restricted"] += 1
                lead.notes = f"Tentado contato em {time.strftime('%Y-%m-%d %H:%M')}, mas conta WhatsApp estava restrita para novas conversas."
                upsert_lead(lead)
                emit(f"  ⚠️  @{lead.username} — {status_msg}")
            elif "inválido" in status_msg.lower() or "não possui" in status_msg.lower() or "invalid" in status_msg.lower():
                results["invalid"] += 1
                lead.status = LeadStatus.NO_WHATSAPP
                lead.whatsapp_source = "invalid_number"
                lead.notes = f"Número +{lead.whatsapp} verificado no WhatsApp Web como não registrado (inválido)."
                upsert_lead(lead)
                emit(f"  📵 @{lead.username} (+{lead.whatsapp}) reclassificado para 'Sem WhatsApp' (inválido).")
            else:
                results["failed"] += 1
                emit(f"  ❌ Falha no envio: {status_msg}")

            # Pausa aleatória entre disparos se não for o último
            if i < len(leads_to_process):
                wait_seconds = random.randint(min_delay_sec, max_delay_sec)
                emit(f"  ⏳ Pausa de segurança anti-banimento: aguardando {wait_seconds}s...")
                time.sleep(wait_seconds)

    except KeyboardInterrupt:
        emit("\n⚠️ Campanha interrompida pelo usuário.")
    finally:
        sender.close()

    emit(f"\n📊 Resumo da Campanha: {results['sent']} enviados, {results['restricted']} restritos pelo WhatsApp, {results['failed']} falhas, {results['invalid']} inválidos.")
    return results
