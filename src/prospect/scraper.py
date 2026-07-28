"""
=============================================================================
  prospect/scraper.py
  Scraping de perfis e seguidores do Instagram via Playwright.
  
  Extrai:
    - Dados do perfil (bio, seguidores, posts)
    - Seguidores de academias
    - Primeiro post recente (para verificar atividade)
=============================================================================
"""
from __future__ import annotations
import re
import json
import time
import random
from datetime import datetime, timedelta
from typing import Generator

from playwright.sync_api import Page

from urllib.parse import unquote
from bs4 import BeautifulSoup

from prospect.browser import InstagramBrowser, human_delay, short_delay
from prospect.models import Lead, LeadStatus
from prospect.config import MIN_FOLLOWERS, MAX_FOLLOWERS


# ── Keywords para identificar personal trainers ────────────────────────────

PT_KEYWORDS = [
    "personal trainer", "personal treinador", "treinador pessoal",
    "personal training", "treinamento personalizado",
    "preparador físico", "preparação física",
    "educador físico", "educação física",
    "coach fitness", "fitness coach",
    "cref", "conselho regional de educação física",
    "personal ", "treinador ", "coach ",
    "🏋️", "💪", "🏃", "🏋", "🏆",
]

NEGATIVE_KEYWORDS = [
    "academia", "gym ", "studio", "estúdio", "box crossfit",
    "unidade ", "rede de ", "franquia",
    "loja", "store", "shop", "marca",
    "suplemento", "supplement",
]


def _is_personal_trainer(bio: str, full_name: str) -> bool:
    """Verifica se o perfil parece ser de um personal trainer."""
    text = f"{bio} {full_name}".lower()

    # Verifica palavras negativas (academias, lojas, etc)
    for neg in NEGATIVE_KEYWORDS:
        if neg in text:
            return False

    # Verifica palavras positivas
    for kw in PT_KEYWORDS:
        if kw in text:
            return True

    return False


def _score_profile(lead: Lead) -> int:
    """Calcula score de 0-100 para o perfil."""
    score = 0
    text = f"{lead.bio} {lead.full_name}".lower()

    # Bio keywords (+30 max)
    for kw in PT_KEYWORDS:
        if kw in text:
            score += 10
            if score >= 30:
                break

    # CREF na bio (+20)
    if re.search(r'cref[\s\-:]?\s*\d', text, re.IGNORECASE):
        score += 20

    # Seguidores (2k-50k = ideal)
    if 2000 <= lead.followers <= 10000:
        score += 15
    elif 10000 < lead.followers <= 30000:
        score += 10
    elif 30000 < lead.followers <= 50000:
        score += 5

    # Link na bio (+10)
    if lead.bio_link:
        score += 10

    # Conta ativa — post recente (+15)
    if lead.is_active:
        score += 15

    # Conta business (+5)
    if lead.is_business:
        score += 5

    return min(score, 100)


def extract_profile_data(browser: InstagramBrowser, username: str) -> Lead | None:
    """
    Extrai dados do perfil de um usuário.
    Retorna Lead ou None se não conseguir.
    """
    page = browser.page

    if not browser.navigate_to_profile(username):
        return None

    try:
        # Aguarda o carregamento do perfil (conteúdo principal)
        try:
            page.wait_for_selector("main", timeout=10000)
        except Exception:
            pass
        time.sleep(1.5)

        lead = Lead(username=username, profile_url=f"https://www.instagram.com/{username}/")

        # ── Extrai dados da página ──
        content = page.content()

        # Verifica se perfil é privado via elementos visíveis do DOM
        try:
            priv_el = page.locator("h2:has-text('privada'), h2:has-text('private'), h3:has-text('privada'), h3:has-text('private'), span:has-text('Esta conta é privada')")
            if priv_el.count() > 0 and priv_el.first.is_visible():
                lead.is_private = True
        except Exception:
            pass

        # Nome completo
        try:
            name_el = page.locator("header span[class*='x1lliihq']").first
            if name_el.count() > 0:
                lead.full_name = name_el.inner_text().strip()
        except Exception:
            pass

        if not lead.full_name:
            try:
                # Fallback: meta tag
                meta = page.locator('meta[property="og:title"]')
                if meta.count() > 0:
                    title = meta.get_attribute("content") or ""
                    # "Nome (@username) • Instagram photos and videos"
                    match = re.match(r'(.+?)\s*\(@', title)
                    if match:
                        lead.full_name = match.group(1).strip()
            except Exception:
                pass

        # Bio
        try:
            # A bio fica em uma div dentro do header
            bio_el = page.locator("header section div[class*='x7a106z'] span, header section div > span[dir='auto']")
            if bio_el.count() > 0:
                lead.bio = bio_el.first.inner_text().strip()
        except Exception:
            pass

        if not lead.bio:
            try:
                meta_desc = page.locator('meta[property="og:description"]')
                if meta_desc.count() > 0:
                    desc = meta_desc.get_attribute("content") or ""
                    # Remove prefixo de contagem
                    match = re.search(r'["\-]\s*(.+)', desc)
                    if match:
                        lead.bio = match.group(1).strip(' "')
            except Exception:
                pass

        # Seguidores, seguindo, posts
        try:
            stats = page.locator("header section ul li, header section a[href*='/followers'] span, header section span[title]")
            numbers = []
            for i in range(min(stats.count(), 10)):
                try:
                    el = stats.nth(i)
                    text = el.inner_text().strip()
                    # Converte "1.234" ou "10,5 mil" ou "1.2M"
                    num = _parse_count(text)
                    if num is not None:
                        numbers.append(num)
                except Exception:
                    continue
        except Exception:
            numbers = []

        # Tenta extrair contagens de meta description ou do conteúdo
        if len(numbers) < 2:
            numbers = _extract_counts_from_page(page, content)

        if len(numbers) >= 3:
            lead.posts_count = numbers[0]
            lead.followers = numbers[1]
            lead.following = numbers[2]
        elif len(numbers) >= 2:
            lead.followers = numbers[0]
            lead.following = numbers[1]

        # Link(s) da bio (utilizando BeautifulSoup no HTML atualizado da página)
        bio_links = []
        try:
            content = page.content()
            soup = BeautifulSoup(content, "html.parser")
            for a in soup.find_all("a", href=True):
                href = a.get("href", "")
                if not href or href == "#" or "/stories/" in href or "/p/" in href or "/reel/" in href:
                    continue
                # Ignora links do rodapé da Meta / Instagram
                if any(meta_domain in href.lower() for meta_domain in ("about.meta.com", "developers.facebook.com", "meta.ai", "threads.com", "threads.net", "help/instagram")):
                    continue

                # Desembrulha redirect do Instagram (l.instagram.com/?u=... ou /r?u=...)
                match = re.search(r'[?&]u=([^&]+)', href)
                if match:
                    unwrapped = unquote(match.group(1))
                    if unwrapped and "instagram.com" not in unwrapped and unwrapped not in bio_links:
                        bio_links.append(unwrapped)
                elif href.startswith("http") and "instagram.com" not in href and href not in bio_links:
                    bio_links.append(href)
        except Exception:
            pass

        # Se houver botão de múltiplos links ("... e mais X links")
        try:
            more_btn = page.locator("main button:has-text('link'), main span:has-text('outros links'), main a:has-text('mais'), header button:has-text('link')")
            if more_btn.count() > 0 and more_btn.first.is_visible():
                more_btn.first.click()
                time.sleep(1.5)
                dialog_anchors = page.locator("div[role='dialog'] a[href]")
                for i in range(dialog_anchors.count()):
                    try:
                        href = dialog_anchors.nth(i).get_attribute("href") or ""
                        match = re.search(r'[?&]u=([^&]+)', href)
                        if match:
                            unwrapped = unquote(match.group(1))
                            if unwrapped and "instagram.com" not in unwrapped and unwrapped not in bio_links:
                                bio_links.append(unwrapped)
                        elif href.startswith("http") and "instagram.com" not in href and href not in bio_links:
                            bio_links.append(href)
                    except Exception:
                        continue
                page.keyboard.press("Escape")
                time.sleep(0.5)
        except Exception:
            pass

        lead.bio_link = ", ".join(bio_links) if bio_links else ""

        # Último post
        try:
            # Verifica data do primeiro post visível
            first_post = page.locator("article a[href*='/p/'], main div[class*='_aagw'] a").first
            if first_post.count() > 0:
                first_post.click()
                time.sleep(2)
                # Tenta pegar a data do post
                time_el = page.locator("time[datetime]")
                if time_el.count() > 0:
                    dt_str = time_el.first.get_attribute("datetime") or ""
                    if dt_str:
                        lead.last_post_date = dt_str[:19]
                # Fecha modal do post
                try:
                    close_btn = page.locator("div[role='dialog'] button[aria-label='Close'], div[role='dialog'] svg[aria-label='Close'], button svg[aria-label='Fechar']")
                    if close_btn.count() > 0:
                        close_btn.first.click()
                        time.sleep(0.5)
                    else:
                        page.keyboard.press("Escape")
                        time.sleep(0.5)
                except Exception:
                    page.keyboard.press("Escape")
                    time.sleep(0.5)
        except Exception:
            pass

        # Se não conseguiu data do post, tenta pela meta
        if not lead.last_post_date:
            try:
                # Último post a menos de 30 dias: conta como ativa
                posts_visible = page.locator("article a[href*='/p/'], main div[class*='_aagw'] img").count()
                if posts_visible > 0:
                    # Assume ativo se tem posts visíveis (heurística)
                    lead.last_post_date = (datetime.now() - timedelta(days=15)).isoformat()
            except Exception:
                pass

        # Calcula score
        lead.score = _score_profile(lead)

        return lead

    except Exception as e:
        print(f"⚠️ Erro em extract_profile_data: {e}")
        import traceback
        traceback.print_exc()
        return None


def get_followers_list(browser: InstagramBrowser, username: str, max_count: int = 200, on_status=None) -> Generator[str, None, None]:
    """
    Obtém lista de seguidores de um perfil.
    Utiliza o campo de busca interno do modal de seguidores para filtrar por termos-chave ("personal", "treinador", "coach", "cref").
    Yield nomes de usuário um a um.
    """
    emit = on_status or (lambda msg: None)
    page = browser.page

    if not browser.navigate_to_profile(username):
        emit(f"  ⚠️  Perfil @{username} não encontrado")
        return

    try:
        # Clica no link de seguidores com seletores atualizados do Instagram
        followers_selectors = [
            f"a[href='/{username}/followers/']",
            f"a[href='/{username}/followers']",
            "a:has-text('seguidores')",
            "a:has-text('followers')",
            "header section ul li:nth-child(2) a",
            "header section a[href*='followers']",
        ]

        followers_link = None
        for sel in followers_selectors:
            try:
                el = page.locator(sel)
                if el.count() > 0 and el.first.is_visible():
                    followers_link = el.first
                    break
            except Exception:
                continue

        if followers_link is None:
            emit(f"  ⚠️  Não encontrei link de seguidores de @{username}")
            return

        followers_link.click()
        time.sleep(3)

        # Aguarda o modal de seguidores
        dialog = page.locator("div[role='dialog']")
        if dialog.count() == 0:
            time.sleep(2)

        seen = set()

        # Verifica se há campo de busca no modal
        search_keywords = ["personal", "treinador", "coach", "cref"]
        search_input = page.locator("div[role='dialog'] input").first

        if search_input.count() > 0 and search_input.is_visible():
            emit(f"  🔎 Usando busca interna nos seguidores por palavras-chave: {', '.join(search_keywords)}...")
            for kw in search_keywords:
                if len(seen) >= max_count:
                    break

                try:
                    # Limpa e digita a palavra-chave
                    search_input.click()
                    search_input.fill("")
                    time.sleep(0.3)
                    search_input.fill(kw)
                    time.sleep(2.5)

                    stale_count = 0
                    max_stale = 3

                    while len(seen) < max_count and stale_count < max_stale:
                        user_links = page.locator("div[role='dialog'] a[href^='/']")
                        before = len(seen)

                        for i in range(user_links.count()):
                            try:
                                el = user_links.nth(i)
                                href = el.get_attribute("href")
                                if href:
                                    uname = href.strip("/").split("/")[0]
                                    if (
                                        uname
                                        and uname not in seen
                                        and uname not in ("explore", "reels", "stories", "direct", "p", "tv")
                                        and uname != username
                                    ):
                                        seen.add(uname)
                                        yield uname
                            except Exception:
                                continue

                        if len(seen) == before:
                            stale_count += 1
                        else:
                            stale_count = 0

                        if len(seen) >= max_count:
                            break

                        # Scroll no modal para carregar mais resultados da busca
                        try:
                            page.evaluate("""() => {
                                const dialog = document.querySelector("div[role='dialog']");
                                if (!dialog) return;
                                const divs = Array.from(dialog.querySelectorAll('div'));
                                const scrollable = divs.find(d => d.scrollHeight > d.clientHeight && d.clientHeight > 100);
                                if (scrollable) {
                                    scrollable.scrollTop += 800;
                                }
                            }""")
                        except Exception:
                            pass

                        time.sleep(random.uniform(1.5, 2.5))
                except Exception:
                    continue
        else:
            # Fallback: rolagem normal sem busca
            stale_count = 0
            max_stale = 5

            while len(seen) < max_count and stale_count < max_stale:
                user_links = page.locator("div[role='dialog'] a[href^='/']")
                before = len(seen)

                for i in range(user_links.count()):
                    try:
                        el = user_links.nth(i)
                        href = el.get_attribute("href")
                        if href:
                            uname = href.strip("/").split("/")[0]
                            if (
                                uname
                                and uname not in seen
                                and uname not in ("explore", "reels", "stories", "direct", "p", "tv")
                                and uname != username
                            ):
                                seen.add(uname)
                                yield uname
                    except Exception:
                        continue

                if len(seen) == before:
                    stale_count += 1
                else:
                    stale_count = 0

                if len(seen) >= max_count:
                    break

                try:
                    page.evaluate("""() => {
                        const dialog = document.querySelector("div[role='dialog']");
                        if (!dialog) return;
                        const divs = Array.from(dialog.querySelectorAll('div'));
                        const scrollable = divs.find(d => d.scrollHeight > d.clientHeight && d.clientHeight > 100);
                        if (scrollable) {
                            scrollable.scrollTop += 800;
                        }
                    }""")
                except Exception:
                    pass

                time.sleep(random.uniform(1.5, 3.0))

        # Fecha o modal
        try:
            close = page.locator("div[role='dialog'] button svg[aria-label='Close'], button svg[aria-label='Fechar']")
            if close.count() > 0:
                close.first.click()
            else:
                page.keyboard.press("Escape")
        except Exception:
            page.keyboard.press("Escape")

        time.sleep(1)

    except Exception as e:
        emit(f"  ❌ Erro ao listar seguidores de @{username}: {e}")


def search_by_hashtag(browser: InstagramBrowser, hashtag: str, max_profiles: int = 30, on_status=None) -> Generator[str, None, None]:
    """
    Busca perfis que usaram uma hashtag.
    Yield nomes de usuário.
    """
    emit = on_status or (lambda msg: None)
    page = browser.page

    tag = hashtag.lstrip("#")
    url = f"https://www.instagram.com/explore/tags/{tag}/"

    try:
        page.goto(url, wait_until="domcontentloaded", timeout=20000)
        short_delay()

        content = page.content()
        if "Página não encontrada" in content or "Page Not Found" in content:
            emit(f"  ⚠️  Hashtag #{tag} não encontrada")
            return

        if "login" in page.url.lower():
            emit(f"  ⚠️  Instagram pediu login — sessão expirada")
            return

        seen = set()

        # Scroll para carregar mais posts
        for scroll_i in range(3):
            browser.scroll_page(times=2)
            time.sleep(random.uniform(1.5, 3.0))

        # Pega links dos posts
        post_links = page.locator("article a[href*='/p/'], main a[href*='/p/']")
        post_count = min(post_links.count(), max_profiles * 2)

        for i in range(post_count):
            if len(seen) >= max_profiles:
                break

            try:
                href = post_links.nth(i).get_attribute("href")
                if not href:
                    continue

                # Abre o post
                post_links.nth(i).click()
                time.sleep(2)

                # Extrai username do post
                user_link = page.locator("div[role='dialog'] a[href^='/']:not([href*='/p/']):not([href*='/explore/'])")
                if user_link.count() > 0:
                    user_href = user_link.first.get_attribute("href") or ""
                    uname = user_href.strip("/").split("/")[-1] if user_href else ""
                    if uname and uname not in seen:
                        seen.add(uname)
                        yield uname

                # Fecha modal
                try:
                    page.keyboard.press("Escape")
                except Exception:
                    pass
                time.sleep(random.uniform(0.8, 1.5))

            except Exception:
                try:
                    page.keyboard.press("Escape")
                except Exception:
                    pass
                continue

    except Exception as e:
        emit(f"  ❌ Erro ao buscar #{tag}: {e}")


def _parse_count(text: str) -> int | None:
    """Converte texto de contagem ('1.234', '10,5 mil', '1.2M') em número."""
    if not text:
        return None
    text = text.strip().lower().replace(",", ".").replace(" ", "")

    # 10.5mil ou 10.5k
    match = re.match(r'([\d.]+)\s*(mil|k)', text)
    if match:
        return int(float(match.group(1)) * 1000)

    # 1.2m ou 1.2mi
    match = re.match(r'([\d.]+)\s*(m|mi)', text)
    if match:
        return int(float(match.group(1)) * 1_000_000)

    # Número normal: 1.234 -> 1234
    text_clean = text.replace(".", "")
    try:
        return int(text_clean)
    except ValueError:
        return None


def _extract_counts_from_page(page: Page, content: str) -> list[int]:
    """Extrai contagens (posts, followers, following) do conteúdo da página."""
    numbers = []

    # Tenta extrair do meta og:description
    try:
        meta = page.locator('meta[property="og:description"]')
        if meta.count() > 0:
            desc = meta.get_attribute("content") or ""
            # "1,234 Followers, 567 Following, 89 Posts - ..."
            counts = re.findall(r'([\d,.]+[kKmM]?)\s*(?:Followers|Following|Posts|Seguidores|Seguindo|Publicações)', desc)
            for c in counts:
                n = _parse_count(c)
                if n is not None:
                    numbers.append(n)
    except Exception:
        pass

    if len(numbers) >= 2:
        return numbers

    # Tenta pelo HTML — procura spans com números dentro de li do header
    try:
        header_spans = page.locator("header section ul li span span, header section ul li button span, header section ul li a span span")
        for i in range(min(header_spans.count(), 10)):
            text = header_spans.nth(i).inner_text().strip()
            n = _parse_count(text)
            if n is not None:
                numbers.append(n)
                if len(numbers) >= 3:
                    break
    except Exception:
        pass

    # Remove duplicatas mantendo ordem
    seen = set()
    unique = []
    for n in numbers:
        if n not in seen:
            seen.add(n)
            unique.append(n)

    return unique
