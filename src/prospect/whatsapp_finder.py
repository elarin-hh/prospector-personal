"""
=============================================================================
  prospect/whatsapp_finder.py
  Varredura de links da bio para encontrar WhatsApp.
  
  Suporta:
    - Links diretos (wa.me, api.whatsapp.com)
    - Números na bio
    - Landing pages (varredura HTML)
    - Encurtadores (segue redirects)
    - Linktr.ee e similares
=============================================================================
"""
from __future__ import annotations
import re
import requests
from urllib.parse import urlparse, unquote
from typing import Optional

from bs4 import BeautifulSoup

import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ── Padrões de WhatsApp ────────────────────────────────────────────────────

# Números brasileiros (com ou sem +55, com DDD)
PHONE_PATTERNS = [
    # +55 11 99999-9999 ou variações
    re.compile(r'\+?55\s*\(?(\d{2})\)?\s*(\d{4,5})[\s\-.]?(\d{4})'),
    # (11) 99999-9999
    re.compile(r'\((\d{2})\)\s*(\d{4,5})[\s\-.]?(\d{4})'),
    # 11 99999-9999
    re.compile(r'(?<!\d)(\d{2})\s+(\d{4,5})[\s\-.]?(\d{4})(?!\d)'),
    # 11999999999 (colado, 11 dígitos)
    re.compile(r'(?<!\d)(\d{2})(\d{5})(\d{4})(?!\d)'),
]

# Links de WhatsApp
WHATSAPP_URL_PATTERNS = [
    re.compile(r'(?:https?://)?wa\.me/(\d+)', re.IGNORECASE),
    re.compile(r'(?:https?://)?api\.whatsapp\.com/send\??.*?phone=(\d+)', re.IGNORECASE),
    re.compile(r'(?:https?://)?chat\.whatsapp\.com/[\w]+', re.IGNORECASE),
    re.compile(r'(?:https?://)?wa\.link/[\w]+', re.IGNORECASE),
    re.compile(r'whatsapp\.com/send\??.*?phone=(\d+)', re.IGNORECASE),
    re.compile(r'wa\.me/message/(\d+)', re.IGNORECASE),
]

# User agent para requests
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
}


def _normalize_phone(raw: str) -> str:
    """Normaliza um número de telefone para formato internacional."""
    digits = re.sub(r'\D', '', raw)
    if digits.startswith('55') and len(digits) >= 12:
        return digits
    if len(digits) == 11:  # DDD + 9 dígitos
        return f"55{digits}"
    if len(digits) == 10:  # DDD + 8 dígitos
        return f"55{digits}"
    return digits


def _extract_phones_from_text(text: str) -> list[str]:
    """Extrai números de telefone de um texto."""
    phones = set()
    for pattern in PHONE_PATTERNS:
        for match in pattern.finditer(text):
            groups = match.groups()
            raw = ''.join(groups)
            phone = _normalize_phone(raw)
            if len(phone) >= 12:  # 55 + DDD + número
                phones.add(phone)
    return list(phones)


def _extract_whatsapp_urls(text: str) -> list[str]:
    """Extrai números de WhatsApp de URLs."""
    phones = set()
    for pattern in WHATSAPP_URL_PATTERNS:
        for match in pattern.finditer(text):
            try:
                phone = match.group(1)
                if phone:
                    normalized = _normalize_phone(phone)
                    if len(normalized) >= 12:
                        phones.add(normalized)
            except (IndexError, AttributeError):
                # Link sem número direto (ex: chat.whatsapp.com)
                pass
    return list(phones)


def find_whatsapp_in_bio(bio: str) -> tuple[str, str]:
    """
    Procura WhatsApp na bio do Instagram.
    Retorna (numero, fonte).
    """
    if not bio:
        return "", ""

    # 1. Procura URLs de WhatsApp na bio
    phones = _extract_whatsapp_urls(bio)
    if phones:
        return phones[0], "bio_whatsapp_url"

    # 2. Procura números de telefone na bio
    phones = _extract_phones_from_text(bio)
    if phones:
        return phones[0], "bio_phone"

    return "", ""


def find_whatsapp_in_link(url: str, browser: Optional[InstagramBrowser] = None) -> tuple[str, str]:
    """
    Acessa uma URL e procura WhatsApp no conteúdo.
    Segue redirects, analisa HTML de landing pages e usa Playwright caso necessário.
    Retorna (numero, fonte).
    """
    if not url:
        return "", ""

    # Desembrulha URL do l.instagram.com caso recebida ainda com o wrapper
    match = re.search(r'[?&]u=([^&]+)', url)
    if match:
        url = unquote(match.group(1))

    # Garante que tem schema
    if not url.startswith("http"):
        url = f"https://{url}"

    try:
        # 1. Verifica se a URL em si é um link de WhatsApp direto
        phones = _extract_whatsapp_urls(url)
        if phones:
            return phones[0], "link_direto"

        # 2. Segue redirects e analisa o HTML via HTTP rápido
        try:
            response = requests.get(
                url,
                headers=_HEADERS,
                timeout=12,
                allow_redirects=True,
                verify=False,
            )
            final_url = response.url
            phones = _extract_whatsapp_urls(final_url)
            if phones:
                return phones[0], "redirect_whatsapp"

            phone, source = _scan_html_for_whatsapp(response.text, final_url)
            if phone:
                return phone, source
        except Exception:
            pass

        # 3. Fallback: Se não encontrou via HTTP e temos o navegador Playwright, renderiza a página JS
        if browser and browser.page:
            try:
                context = browser.page.context
                page = context.new_page()
                page.goto(url, timeout=12000, wait_until="domcontentloaded")
                import time
                time.sleep(2.5)
                rendered_html = page.content()
                final_url = page.url
                page.close()

                phones = _extract_whatsapp_urls(final_url)
                if phones:
                    return phones[0], "browser_redirect_whatsapp"

                phone, source = _scan_html_for_whatsapp(rendered_html, final_url)
                if phone:
                    return phone, f"browser_{source}"
            except Exception:
                pass

        return "", ""

    except Exception:
        return "", ""


def _scan_html_for_whatsapp(html: str, source_url: str = "") -> tuple[str, str]:
    """
    Faz varredura profunda do HTML procurando WhatsApp.
    Retorna (numero, fonte).
    """
    if not html:
        return "", ""

    # 0. Busca direta no texto/HTML bruto (pega URLs em JSON/JS/Next.js/React state de wa.me, api.whatsapp.com, etc)
    phones = _extract_whatsapp_urls(html)
    if phones:
        return phones[0], "raw_html_whatsapp_url"

    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception:
        return "", ""

    # 1. Procura em links (href)
    for link in soup.find_all("a", href=True):
        href = link.get("href", "")
        phones = _extract_whatsapp_urls(href)
        if phones:
            return phones[0], "landing_page_link"

        # Links tel: podem ser WhatsApp
        if href.startswith("tel:"):
            phone_raw = href.replace("tel:", "").strip()
            normalized = _normalize_phone(phone_raw)
            if len(normalized) >= 12:
                return normalized, "landing_page_tel"

    # 2. Procura em atributos de dados (data-phone, data-whatsapp, etc)
    for tag in soup.find_all(attrs=True):
        for attr_name, attr_value in tag.attrs.items():
            if isinstance(attr_value, str):
                attr_lower = attr_name.lower()
                if any(kw in attr_lower for kw in ("phone", "whatsapp", "telefone", "celular", "zap")):
                    phones = _extract_phones_from_text(attr_value)
                    if phones:
                        return phones[0], "landing_page_data_attr"
                # Procura URLs de WhatsApp em qualquer atributo
                if "wa.me" in attr_value or "whatsapp.com" in attr_value:
                    phones = _extract_whatsapp_urls(attr_value)
                    if phones:
                        return phones[0], "landing_page_attr_url"

    # 3. Procura em scripts (onclick, data-url, etc)
    for script in soup.find_all("script"):
        script_text = script.string or ""
        phones = _extract_whatsapp_urls(script_text)
        if phones:
            return phones[0], "landing_page_script"
        # Procura padrões de configuração com phone
        phone_patterns = re.findall(r'["\'](?:phone|whatsapp|celular|telefone|zap)["\']?\s*[:=]\s*["\']([^"\']+)["\']', script_text, re.IGNORECASE)
        for raw_phone in phone_patterns:
            phones = _extract_phones_from_text(raw_phone)
            if phones:
                return phones[0], "landing_page_script_config"

    # 4. Procura em meta tags
    for meta in soup.find_all("meta"):
        content = meta.get("content", "")
        name = meta.get("name", "").lower() + meta.get("property", "").lower()
        if content:
            phones = _extract_whatsapp_urls(content)
            if phones:
                return phones[0], "landing_page_meta"

    # 5. Procura no texto bruto da página
    body = soup.find("body")
    if body:
        text = body.get_text(separator=" ", strip=True)
        wpp_mentions = re.findall(
            r'(?:whatsapp|wpp|zap|whats)\s*[:\-]?\s*\(?\+?55?\s*\(?(\d{2})\)?\s*(\d{4,5})[\s\-.]?(\d{4})',
            text, re.IGNORECASE
        )
        for groups in wpp_mentions:
            raw = ''.join(groups)
            phone = _normalize_phone(raw)
            if len(phone) >= 12:
                return phone, "landing_page_text"

        # Tenta qualquer telefone solto no texto do body
        phones = _extract_phones_from_text(text)
        if phones:
            return phones[0], "landing_page_body_phone"

    # 6. Procura links internos de linktree e agregadores
    domain = urlparse(source_url).netloc.lower() if source_url else ""
    if any(d in domain for d in ("linktr.ee", "linktree", "beacons.ai", "bio.link", "tap.bio", "lnk.bio", "hoo.be", "heylink.me", "vlink.me", "solo.to", "msha.ke", "instabio.cc", "campsite.bio")):
        for link in soup.find_all("a", href=True):
            href = link.get("href", "")
            if href and href.startswith("http") and "wa.me" not in href and "whatsapp" not in href:
                try:
                    sub_response = requests.get(
                        href, headers=_HEADERS, timeout=8,
                        allow_redirects=True, verify=False
                    )
                    sub_final_url = sub_response.url
                    phones = _extract_whatsapp_urls(sub_final_url)
                    if phones:
                        return phones[0], "linktree_redirect"
                    result = _scan_nested_html(sub_response.text)
                    if result[0]:
                        return result
                except Exception:
                    continue

    return "", ""


def _scan_nested_html(html: str) -> tuple[str, str]:
    """Varredura simplificada de HTML aninhado (2º nível)."""
    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception:
        return "", ""

    # Procura em links
    for link in soup.find_all("a", href=True):
        href = link.get("href", "")
        phones = _extract_whatsapp_urls(href)
        if phones:
            return phones[0], "nested_link"

    # Procura no HTML bruto
    phones = _extract_whatsapp_urls(html)
    if phones:
        return phones[0], "nested_html"

    return "", ""


def find_whatsapp(bio: str, link: str | list[str], browser: Optional[InstagramBrowser] = None) -> tuple[str, str]:
    """
    Busca completa de WhatsApp: primeiro na bio, depois no(s) link(s).
    Suporta string de link individual ou lista de links.
    Retorna (numero, fonte).
    """
    # 1. Tenta na bio
    phone, source = find_whatsapp_in_bio(bio)
    if phone:
        return phone, source

    # 2. Tenta nos links
    if isinstance(link, str):
        urls = [u.strip() for u in link.split(",") if u.strip()]
    elif isinstance(link, list):
        urls = link
    else:
        urls = []

    for u in urls:
        if not u:
            continue
        phone, source = find_whatsapp_in_link(u, browser=browser)
        if phone:
            return phone, source

    return "", ""
