"""
=============================================================================
  prospect/browser.py
  Gerenciamento de sessão do Instagram via Playwright.
  Usa navegador real para evitar detecção anti-bot.
=============================================================================
"""
from __future__ import annotations
import json
import time
import random
import subprocess
import sys
from pathlib import Path

from prospect.config import (
    IG_USERNAME, IG_PASSWORD, HEADLESS,
    SESSIONS_DIR, DELAY_MIN, DELAY_MAX,
)
from prospect.runtime import BROWSER_CHANNEL

_STATE_FILE = SESSIONS_DIR / f"{IG_USERNAME}_state.json"


def human_delay(min_s: float = None, max_s: float = None) -> None:
    """Delay humanizado entre ações."""
    mn = min_s if min_s is not None else DELAY_MIN
    mx = max_s if max_s is not None else DELAY_MAX
    time.sleep(random.uniform(mn, mx))


def short_delay() -> None:
    """Delay curto para navegação (1-3s)."""
    time.sleep(random.uniform(1.0, 3.0))


def _ensure_no_event_loop():
    """Garante que não há event loop asyncio rodando."""
    import asyncio
    try:
        loop = asyncio.get_running_loop()
        # Se chegou aqui, tem um loop rodando — precisa parar
        if loop.is_running():
            loop.stop()
    except RuntimeError:
        # Sem loop rodando — ótimo
        pass


class InstagramBrowser:
    """Gerencia sessão do Instagram com Playwright."""

    def __init__(self, on_status=None):
        self._pw = None
        self._browser = None
        self._context = None
        self._page = None
        self._on_status = on_status or (lambda msg: None)
        self._connected = False

    def _emit(self, msg: str) -> None:
        self._on_status(msg)

    @property
    def page(self):
        if self._page is None:
            raise RuntimeError("Browser not initialized. Call connect() first.")
        return self._page

    def connect(self) -> bool:
        """Inicializa o navegador e faz login no Instagram."""
        # Limpa qualquer instância anterior
        self._cleanup_resources()

        self._emit("🌐 Iniciando navegador...")
        try:
            from playwright.sync_api import sync_playwright
            self._pw = sync_playwright().start()
        except Exception as e:
            self._emit(f"❌ Erro ao iniciar Playwright: {e}")
            # Tenta limpar event loop e tentar novamente
            _ensure_no_event_loop()
            try:
                from playwright.sync_api import sync_playwright
                self._pw = sync_playwright().start()
            except Exception as e2:
                self._emit(f"❌ Falha definitiva ao iniciar Playwright: {e2}")
                return False

        try:
            self._browser = self._pw.chromium.launch(
                headless=HEADLESS,
                # Chromium completo nos dois modos: dispensa o
                # chromium_headless_shell na distribuição (-270 MB)
                channel=BROWSER_CHANNEL,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                ],
            )
        except Exception as e:
            self._emit(f"❌ Erro ao abrir navegador: {e}")
            self._emit("   Tente: playwright install chromium")
            self._cleanup_resources()
            return False

        # Tenta restaurar sessão salva
        if _STATE_FILE.exists():
            self._emit("🔄 Restaurando sessão salva...")
            try:
                self._context = self._browser.new_context(
                    storage_state=str(_STATE_FILE),
                    viewport={"width": 1366, "height": 768},
                    user_agent=(
                        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
                    ),
                )
                self._page = self._context.new_page()
                self._page.goto("https://www.instagram.com/", wait_until="networkidle", timeout=30000)
                time.sleep(3)

                # Verifica se está logado
                if self._is_logged_in():
                    self._emit(f"✅ Sessão restaurada como @{IG_USERNAME}")
                    self._dismiss_dialogs()
                    self._connected = True
                    return True
                else:
                    self._emit("⚠️  Sessão expirada, fazendo login novamente...")
                    try:
                        self._page.close()
                        self._context.close()
                    except Exception:
                        pass
                    self._page = None
                    self._context = None
            except Exception as e:
                self._emit(f"⚠️  Erro ao restaurar sessão: {e}")
                try:
                    if self._page:
                        self._page.close()
                    if self._context:
                        self._context.close()
                except Exception:
                    pass
                self._page = None
                self._context = None

        # Login novo
        success = self._do_login()
        self._connected = success
        return success

    def _do_login(self) -> bool:
        """Realiza login com usuário e senha."""
        if not IG_USERNAME or not IG_PASSWORD:
            self._emit("❌ Preencha IG_USERNAME e IG_PASSWORD no .env")
            return False

        self._emit(f"🔐 Fazendo login como @{IG_USERNAME}...")
        self._context = self._browser.new_context(
            viewport={"width": 1366, "height": 768},
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
        )
        self._page = self._context.new_page()

        try:
            # Navega para a página de login
            self._page.goto(
                "https://www.instagram.com/accounts/login/",
                wait_until="networkidle",
                timeout=45000,
            )
            time.sleep(3)

            # Aceita cookies se houver botão
            self._try_accept_cookies()

            # Espera o formulário de login carregar (múltiplos seletores)
            login_selectors = [
                'input[name="email"]',
                'input[name="username"]',
                'input[aria-label="Phone number, username, or email"]',
                'input[aria-label="Número de telefone, nome de usuário ou e-mail"]',
                'input[type="text"]',
            ]

            username_input = None
            for selector in login_selectors:
                try:
                    el = self._page.locator(selector).first
                    el.wait_for(state="visible", timeout=8000)
                    username_input = el
                    self._emit(f"  ✔ Campo de login encontrado")
                    break
                except Exception:
                    continue

            if username_input is None:
                # Talvez já esteja logado
                if self._is_logged_in():
                    self._emit(f"✅ Já logado como @{IG_USERNAME}")
                    self._save_session()
                    return True
                self._emit("❌ Não encontrei o formulário de login")
                self._emit(f"   URL atual: {self._page.url}")
                return False

            # Preenche login com digitação humana
            username_input.click()
            time.sleep(random.uniform(0.3, 0.8))
            username_input.fill("")  # Limpa campo
            time.sleep(0.2)
            for char in IG_USERNAME:
                username_input.type(char, delay=random.randint(50, 150))
            
            time.sleep(random.uniform(0.5, 1.0))

            # Preenche senha
            password_selectors = [
                'input[name="pass"]',
                'input[name="password"]',
                'input[type="password"]',
                'input[aria-label="Password"]',
                'input[aria-label="Senha"]',
            ]

            password_input = None
            for selector in password_selectors:
                try:
                    el = self._page.locator(selector).first
                    if el.is_visible():
                        password_input = el
                        break
                except Exception:
                    continue

            if password_input is None:
                self._emit("❌ Não encontrei o campo de senha")
                return False

            password_input.click()
            time.sleep(random.uniform(0.3, 0.8))
            password_input.fill("")
            time.sleep(0.2)
            for char in IG_PASSWORD:
                password_input.type(char, delay=random.randint(30, 120))

            time.sleep(random.uniform(0.8, 1.5))

            # Clica no botão de login
            submit_selectors = [
                'input[type="submit"]',
                'button[type="submit"]',
                'button:has-text("Log in")',
                'button:has-text("Entrar")',
                'div[role="button"]:has-text("Log in")',
                'div[role="button"]:has-text("Entrar")',
            ]

            submitted = False
            for selector in submit_selectors:
                try:
                    el = self._page.locator(selector).first
                    if el.is_visible():
                        el.click()
                        submitted = True
                        break
                except Exception:
                    continue

            if not submitted:
                # Fallback: pressiona Enter no campo de senha
                self._emit("  ⚠️  Botão de submit não encontrado, tentando Enter...")
                try:
                    password_input.press("Enter")
                except Exception:
                    pass

            # Aguarda resposta do login — espera a URL mudar da página de login
            self._emit("  ⏳ Aguardando resposta do Instagram...")

            # Espera URL mudar (prova que o submit foi aceito)
            for _ in range(20):  # até 20 segundos
                time.sleep(1)
                try:
                    current_url = self._page.url
                    if "/accounts/login/" not in current_url:
                        break
                except Exception:
                    continue

            # Aguarda estabilizar
            try:
                self._page.wait_for_load_state("domcontentloaded", timeout=15000)
            except Exception:
                pass
            time.sleep(3)

            # Lê estado atual com proteção contra navegação
            current_url = self._page.url
            page_text = ""
            try:
                page_text = self._page.content().lower()
            except Exception:
                time.sleep(3)
                try:
                    page_text = self._page.content().lower()
                except Exception:
                    pass

            # Verifica se precisa de verificação
            if "challenge" in current_url or "suspicious" in page_text or "verificar" in page_text or "confirm" in page_text:
                self._emit("⚠️  Instagram pediu verificação de segurança.")
                self._emit("   Resolva manualmente no navegador e pressione ENTER aqui.")
                if not HEADLESS:
                    input("   >>> Pressione ENTER após resolver a verificação... ")
                    time.sleep(3)
                    # Re-read after user resolved
                    current_url = self._page.url
                else:
                    self._emit("❌ Verificação necessária — rode com HEADLESS=false no .env")
                    return False

            # Verifica se login foi bem-sucedido
            if "/accounts/login/" not in current_url:
                self._emit(f"✅ Login bem-sucedido como @{IG_USERNAME}")
                self._save_session()
                self._dismiss_dialogs()
                return True

            # Verifica se tem erro na página
            if "incorrect" in page_text or "incorreta" in page_text or "wrong" in page_text:
                self._emit("❌ Senha incorreta. Verifique o .env")
                return False

            # Tenta verificar se está logado
            if self._is_logged_in():
                self._emit(f"✅ Login bem-sucedido como @{IG_USERNAME}")
                self._save_session()
                self._dismiss_dialogs()
                return True

            self._emit("❌ Login falhou — verifique usuário e senha no .env")
            self._emit(f"   URL: {current_url}")
            return False

        except Exception as e:
            self._emit(f"❌ Erro no login: {e}")
            return False

    def _try_accept_cookies(self) -> None:
        """Tenta aceitar diálogos de cookies."""
        cookie_selectors = [
            "button:has-text('Allow essential and optional cookies')",
            "button:has-text('Allow all cookies')",
            "button:has-text('Permitir todos os cookies')",
            "button:has-text('Permitir cookies essenciais e opcionais')",
            "button:has-text('Accept')",
            "button:has-text('Aceitar')",
            "button:has-text('Allow')",
            "button:has-text('Permitir')",
        ]
        for selector in cookie_selectors:
            try:
                btn = self._page.locator(selector)
                if btn.count() > 0 and btn.first.is_visible():
                    btn.first.click()
                    time.sleep(2)
                    return
            except Exception:
                continue

    def _is_logged_in(self) -> bool:
        """Verifica se está logado no Instagram."""
        try:
            url = self._page.url
            # Se estiver na página de login, não está logado
            if "/accounts/login/" in url:
                return False
            # Se estiver em /accounts/onetap/ ou na home, está logado
            if "/accounts/onetap/" in url:
                return True
            # Verifica se tem elementos da home (com timeout curto)
            try:
                self._page.wait_for_selector(
                    'svg[aria-label="Home"], svg[aria-label="Início"], '
                    'a[href="/direct/inbox/"], svg[aria-label="Search"], '
                    'svg[aria-label="Pesquisar"], svg[aria-label="New post"], '
                    'svg[aria-label="Nova publicação"]',
                    timeout=5000
                )
                return True
            except Exception:
                pass
            # Fallback: se não tem campo de login, provavelmente está logado
            login_inputs = self._page.locator('input[name="username"]').count()
            return login_inputs == 0
        except Exception:
            return False

    def _dismiss_dialogs(self) -> None:
        """Fecha diálogos modais (notificações, salvar info, etc)."""
        dismiss_selectors = [
            "button:has-text('Agora não')",
            "button:has-text('Not Now')",
            "button:has-text('Not now')",
            "button:has-text('Ahora no')",
            'button:has-text("Não ativar")',
            'button:has-text("Turn Off")',
        ]
        for _ in range(3):
            dismissed = False
            for selector in dismiss_selectors:
                try:
                    btn = self._page.locator(selector)
                    if btn.count() > 0 and btn.first.is_visible():
                        btn.first.click()
                        time.sleep(1.5)
                        dismissed = True
                        break
                except Exception:
                    continue
            if not dismissed:
                break

    def _save_session(self) -> None:
        """Salva estado da sessão para reutilização."""
        try:
            if self._context:
                state = self._context.storage_state()
                with open(_STATE_FILE, "w") as f:
                    json.dump(state, f)
                self._emit(f"💾 Sessão salva em {_STATE_FILE}")
        except Exception as e:
            self._emit(f"⚠️  Não foi possível salvar sessão: {e}")

    def navigate_to_profile(self, username: str) -> bool:
        """Navega até o perfil de um usuário."""
        try:
            url = f"https://www.instagram.com/{username}/"
            self.page.goto(url, wait_until="domcontentloaded", timeout=20000)
            time.sleep(random.uniform(2, 4))
            # Verifica se o perfil existe
            content = self.page.content()
            if "Página não encontrada" in content or "Page Not Found" in content:
                return False
            return True
        except Exception:
            return False

    def scroll_page(self, times: int = 3) -> None:
        """Scroll suave na página."""
        for _ in range(times):
            self.page.evaluate("window.scrollBy(0, window.innerHeight * 0.8)")
            time.sleep(random.uniform(1.0, 2.5))

    def _cleanup_resources(self) -> None:
        """Limpa todos os recursos do Playwright sem emitir mensagens."""
        try:
            if self._page:
                self._page.close()
        except Exception:
            pass
        try:
            if self._context:
                self._context.close()
        except Exception:
            pass
        try:
            if self._browser:
                self._browser.close()
        except Exception:
            pass
        try:
            if self._pw:
                self._pw.stop()
        except Exception:
            pass
        self._page = None
        self._context = None
        self._browser = None
        self._pw = None
        self._connected = False

    def close(self) -> None:
        """Fecha navegador e libera recursos."""
        if self._connected and self._context:
            self._save_session()
        self._cleanup_resources()
        self._emit("🔒 Navegador encerrado")
