#!/usr/bin/env python3
"""
=============================================================================
  yt_downloader.py - Baixador de Músicas do YouTube em Lote com Capa Embutida
=============================================================================
Este script permite baixar áudios do YouTube (MP3, M4A, FLAC) em lote,
incorporando automaticamente a capa do vídeo (Thumbnail) e as metatags de áudio.
=============================================================================
"""

import sys
import os
import re
import argparse
from pathlib import Path
from typing import List, Optional

import requests
import yt_dlp
from mutagen.id3 import ID3, APIC, TIT2, TPE1, TALB, ID3NoHeaderError
from mutagen.mp3 import MP3
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.prompt import Prompt, Confirm

console = Console()


def clean_url(url: str) -> str:
    """Limpa e valida a URL do YouTube."""
    url = url.strip()
    if not url:
        return ""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    return url


def fix_mp3_cover(mp3_path: Path, thumbnail_url: str):
    """
    Garante a incorporação da capa (Thumbnail) no arquivo MP3 usando Mutagen
    caso o pós-processador do yt-dlp necessite de reforço na tag ID3.
    """
    if not mp3_path.exists() or not thumbnail_url:
        return

    try:
        # Baixa a imagem da capa
        resp = requests.get(thumbnail_url, timeout=10)
        if resp.status_code != 200:
            return
        image_data = resp.content

        # Tenta carregar as tags ID3 existentes ou cria um novo cabeçalho
        try:
            audio = MP3(mp3_path, ID3=ID3)
        except ID3NoHeaderError:
            audio = MP3(mp3_path)
            audio.add_tags()

        # Adiciona ou substitui a arte do álbum (APIC)
        audio.tags.add(
            APIC(
                encoding=3,  # UTF-8
                mime='image/jpeg',  # Tipo MIME da capa
                type=3,      # 3 = Arte da Capa Frontal
                desc='Cover',
                data=image_data
            )
        )
        audio.save()
    except Exception as e:
        console.print(f"[yellow]⚠️ Aviso ao embutir capa via Mutagen: {e}[/yellow]")


def download_batch(
    urls: List[str],
    output_dir: Path,
    audio_format: str = "mp3",
    bitrate: str = "320",
    embed_thumbnail: bool = True
) -> dict:
    """
    Baixa uma lista de URLs do YouTube em lote com capa e metadados.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    # Estatísticas do download
    stats = {"success": 0, "failed": 0, "total": len(urls)}

    # Configuração de opções do yt-dlp
    postprocessors = [
        {
            'key': 'FFmpegExtractAudio',
            'preferredcodec': audio_format,
            'preferredquality': bitrate,
        },
        {
            'key': 'FFmpegMetadata',
            'add_metadata': True,
        }
    ]

    if embed_thumbnail:
        postprocessors.append({
            'key': 'EmbedThumbnail',
            'already_have_thumbnail': False,
        })

    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': str(output_dir / '%(title)s.%(ext)s'),
        'writethumbnail': embed_thumbnail,
        'postprocessors': postprocessors,
        'quiet': False,
        'no_warnings': True,
        'ignoreerrors': True,
        'prefer_ffmpeg': True,
    }

    console.print(Panel.fit(
        f"[bold green]🚀 Iniciando download de {len(urls)} áudio(s)...[/bold green]\n"
        f"[bold white]Pasta de destino:[/bold white] [cyan]{output_dir.resolve()}[/cyan]\n"
        f"[bold white]Formato:[/bold white] [yellow]{audio_format.upper()} ({bitrate} kbps)[/yellow]",
        title="[bold yellow]🎵 YouTube Batch Music Downloader[/bold yellow]"
    ))

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        for idx, url in enumerate(urls, start=1):
            url = clean_url(url)
            if not url:
                continue

            console.print(f"\n[bold magenta]━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━[/bold magenta]")
            console.print(f"[bold cyan]▶ [{idx}/{len(urls)}] Baixando:[/bold cyan] {url}")

            try:
                # Extrai informações antes para obter metadados e URL da capa
                info = ydl.extract_info(url, download=True)

                if info:
                    stats["success"] += 1
                    title = info.get('title', 'Audio')
                    console.print(f"[bold green]✅ Sucesso:[/bold green] [bold white]{title}[/bold white]")

                    # Reforço de capa para arquivos MP3
                    if embed_thumbnail and audio_format.lower() == "mp3":
                        expected_file = output_dir / f"{title}.mp3"
                        # Sanitiza o nome do arquivo da forma do yt-dlp
                        sanitized_title = re.sub(r'[\\/:*?"<>|]', '_', title)
                        sanitized_file = output_dir / f"{sanitized_title}.mp3"

                        target_file = None
                        if expected_file.exists():
                            target_file = expected_file
                        elif sanitized_file.exists():
                            target_file = sanitized_file
                        else:
                            # Busca por arquivos .mp3 criados recentemente na pasta
                            mp3_files = list(output_dir.glob("*.mp3"))
                            if mp3_files:
                                target_file = max(mp3_files, key=lambda f: f.stat().st_mtime)

                        thumbnail_url = info.get('thumbnail')
                        if target_file and thumbnail_url:
                            fix_mp3_cover(target_file, thumbnail_url)

                else:
                    stats["failed"] += 1
                    console.print(f"[red]❌ Falha ao processar:[/red] {url}")

            except Exception as exc:
                stats["failed"] += 1
                console.print(f"[bold red]❌ Erro no download:[/bold red] {exc}")

    # Exibe resumo final em tabela
    table = Table(title="📊 Resumo da Sessão de Download", border_style="bright_blue")
    table.add_column("Métrica", style="bold white")
    table.add_column("Quantidade", style="bold green", justify="right")

    table.add_row("Total de itens", str(stats["total"]))
    table.add_row("Baixados com sucesso", str(stats["success"]))
    table.add_row("Falhas", f"[red]{stats['failed']}[/red]" if stats["failed"] > 0 else "0")

    console.print("\n")
    console.print(table)
    console.print(f"[bold green]✨ Downloads concluídos! Músicas salvas em:[/bold green] [cyan]{output_dir.resolve()}[/cyan]\n")

    return stats


def interactive_menu():
    """Interface interativa de linha de comando."""
    console.clear()
    console.print(Panel(
        "[bold green]🎵 Baixador de Músicas do YouTube em Lote com Capa[/bold green]\n"
        "[dim]Baixe músicas individuais, playlists ou arquivos de texto com URLs[/dim]",
        expand=False
    ))

    print("\nComo deseja fornecer os links das músicas?")
    print(" [1] Digitar/Colar URLs manuais (separadas por linha ou espaço)")
    print(" [2] Carregar arquivo de texto com URLs (ex: urls.txt)")
    print(" [3] Baixar uma Playlist inteira do YouTube")
    print(" [0] Sair")

    choice = Prompt.ask("\nEscolha uma opção", choices=["0", "1", "2", "3"], default="1")

    if choice == "0":
        console.print("[yellow]Até mais![/yellow]")
        sys.exit(0)

    urls = []

    if choice == "1":
        console.print("\n[bold cyan]Cole as URLs do YouTube abaixo.[/bold cyan] [dim](Digite 'FIM' em uma linha vazia ou aperte Enter duas vezes para iniciar)[/dim]:")
        lines = []
        while True:
            try:
                line = input("> ").strip()
                if line.upper() == "FIM" or (not line and lines):
                    break
                if line:
                    # Permite colar múltiplos links separados por espaço na mesma linha
                    for part in line.split():
                        if part.strip():
                            lines.append(part.strip())
            except (KeyboardInterrupt, EOFError):
                break
        urls = lines

    elif choice == "2":
        filepath = Prompt.ask("Caminho do arquivo com URLs", default="urls.txt")
        file_path = Path(filepath)
        if not file_path.exists():
            console.print(f"[red]❌ Arquivo '{filepath}' não encontrado![/red]")
            return
        with open(file_path, "r", encoding="utf-8") as f:
            urls = [line.strip() for line in f if line.strip() and not line.startswith("#")]

    elif choice == "3":
        playlist_url = Prompt.ask("Cole a URL da Playlist do YouTube")
        urls = [playlist_url]

    if not urls:
        console.print("[red]⚠️ Nenhum link fornecido para download.[/red]")
        return

    # Pasta de destino
    output_dir_str = Prompt.ask("\nPasta onde deseja salvar as músicas", default="downloads_musicas")
    output_dir = Path(output_dir_str)

    # Formato e qualidade
    audio_format = Prompt.ask("Formato de áudio", choices=["mp3", "m4a", "flac", "opus"], default="mp3")
    bitrate = Prompt.ask("Qualidade (kbps)", choices=["320", "256", "192", "128"], default="320")
    embed_cover = Confirm.ask("Embutir a capa do vídeo na música?", default=True)

    # Executa o download em lote
    download_batch(
        urls=urls,
        output_dir=output_dir,
        audio_format=audio_format,
        bitrate=bitrate,
        embed_thumbnail=embed_cover
    )


def main():
    parser = argparse.ArgumentParser(description="Baixador de Músicas do YouTube em Lote com Capa Embutida")
    parser.add_argument("urls", nargs="*", help="URLs dos vídeos/playlists do YouTube")
    parser.add_argument("-f", "--file", help="Arquivo de texto contendo URLs (uma por linha)")
    parser.add_argument("-o", "--output", default="downloads_musicas", help="Pasta de destino (padrão: downloads_musicas)")
    parser.add_argument("--format", default="mp3", choices=["mp3", "m4a", "flac", "opus"], help="Formato de áudio")
    parser.add_argument("--bitrate", default="320", choices=["320", "256", "192", "128"], help="Qualidade em kbps")
    parser.add_argument("--no-cover", action="store_true", help="Não embutir a capa do vídeo")

    args = parser.parse_args()

    urls = []
    if args.urls:
        urls.extend(args.urls)

    if args.file:
        file_path = Path(args.file)
        if file_path.exists():
            with open(file_path, "r", encoding="utf-8") as f:
                urls.extend(line.strip() for line in f if line.strip() and not line.startswith("#"))
        else:
            console.print(f"[red]❌ Arquivo '{args.file}' não foi encontrado.[/red]")
            sys.exit(1)

    if urls:
        download_batch(
            urls=urls,
            output_dir=Path(args.output),
            audio_format=args.format,
            bitrate=args.bitrate,
            embed_thumbnail=not args.no_cover
        )
    else:
        # Se nenhum parâmetro foi passado via CLI, entra no modo interativo
        interactive_menu()


if __name__ == "__main__":
    main()
