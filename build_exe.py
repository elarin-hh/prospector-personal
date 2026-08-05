"""
=============================================================================
  build_exe.py
  Gera o executável Windows do PT Prospect e empacota num .zip.

  Uso:
    python build_exe.py                  # zip leve (~40 MB), baixa o
                                         # Chromium no primeiro uso
    python build_exe.py --with-browsers  # zip completo (~180 MB), roda
                                         # offline, zero configuração
    python build_exe.py --no-zip         # só a pasta dist/, sem compactar

  O resultado sai em dist/PTProspect/ e dist/PTProspect-<variante>.zip
=============================================================================
"""
from __future__ import annotations
import argparse
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
DIST = RAIZ / "dist"
BUILD = RAIZ / "build"
APP = DIST / "PTProspect"
SPEC = RAIZ / "prospect.spec"


def log(msg: str) -> None:
    print(f"  {msg}", flush=True)


def secao(titulo: str) -> None:
    print(f"\n{'=' * 62}\n  {titulo}\n{'=' * 62}", flush=True)


def tamanho(caminho: Path) -> str:
    """Tamanho legível de arquivo ou pasta."""
    if caminho.is_file():
        total = caminho.stat().st_size
    else:
        total = sum(f.stat().st_size for f in caminho.rglob("*") if f.is_file())
    for unidade in ("B", "KB", "MB", "GB"):
        if total < 1024:
            return f"{total:.0f} {unidade}"
        total /= 1024
    return f"{total:.1f} TB"


def limpar() -> None:
    """Remove build/ e a pasta do app, preservando zips de builds anteriores."""
    for pasta in (BUILD, APP):
        if pasta.exists():
            shutil.rmtree(pasta)
            log(f"removido {pasta.relative_to(RAIZ)}/")


def compilar() -> None:
    comando = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean", "--log-level=WARN",
        str(SPEC),
    ]
    resultado = subprocess.run(comando, cwd=RAIZ)
    if resultado.returncode != 0:
        sys.exit(f"❌ PyInstaller falhou (código {resultado.returncode})")

    if not (APP / "PTProspect.exe").exists():
        sys.exit("❌ PTProspect.exe não foi gerado")
    log(f"PTProspect.exe gerado — pasta com {tamanho(APP)}")


def origem_dos_navegadores() -> Path | None:
    """Localiza a pasta de navegadores do Playwright nesta máquina."""
    candidatos = []
    if os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "").strip() not in ("", "0", "1"):
        candidatos.append(Path(os.environ["PLAYWRIGHT_BROWSERS_PATH"]))
    if os.environ.get("LOCALAPPDATA"):
        candidatos.append(Path(os.environ["LOCALAPPDATA"]) / "ms-playwright")
    candidatos.append(Path.home() / "AppData" / "Local" / "ms-playwright")

    for candidato in candidatos:
        if candidato.is_dir():
            return candidato
    return None


def revisao_do_chromium() -> str:
    """
    Descobre qual pasta chromium-<rev> este Playwright realmente usa.

    Perguntar ao Playwright evita copiar revisões antigas que ficaram na
    máquina: só o ms-playwright daqui tinha duas (1208 e 1228, ~800 MB).
    """
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        executavel = Path(p.chromium.executable_path)

    for parte in executavel.parts:
        if parte.startswith("chromium-"):
            return parte
    sys.exit(f"❌ Não identifiquei a revisão do Chromium em {executavel}")


def copiar_navegadores() -> None:
    """
    Copia para a distribuição apenas o necessário:
      - a revisão exata do chromium usada pelo Playwright
      - winldd-* (o Playwright usa para checar DLLs no Windows)

    O chromium_headless_shell (~270 MB) fica de fora porque todos os
    launches passam channel="chromium".
    """
    origem = origem_dos_navegadores()
    if origem is None:
        sys.exit("❌ Navegadores do Playwright não encontrados. Rode: playwright install chromium")

    revisao = revisao_do_chromium()
    log(f"revisão em uso: {revisao}")

    alvos = [origem / revisao]
    alvos += sorted(d for d in origem.iterdir() if d.is_dir() and d.name.startswith("winldd-"))

    destino = APP / "browsers"
    destino.mkdir(parents=True, exist_ok=True)

    for item in alvos:
        if not item.is_dir():
            sys.exit(f"❌ Não encontrei {item}. Rode: playwright install chromium")
        log(f"copiando {item.name} ({tamanho(item)})...")
        shutil.copytree(item, destino / item.name, dirs_exist_ok=True)

    # Marcador que o Playwright usa para gerenciar a pasta de navegadores
    (destino / ".links").mkdir(exist_ok=True)
    log(f"browsers/ com {tamanho(destino)}")


def escrever_extras(com_navegadores: bool) -> None:
    """Coloca .env.example e um LEIA-ME ao lado do .exe."""
    exemplo = RAIZ / ".env.example"
    if exemplo.exists():
        shutil.copy2(exemplo, APP / ".env.example")

    primeiro_uso = (
        "O Chromium já vem incluído — funciona sem internet na primeira vez."
        if com_navegadores else
        "Na PRIMEIRA execução o programa baixa o Chromium (~416 MB).\n"
        "   Deixe a janela aberta até terminar. Depois disso funciona offline."
    )

    (APP / "LEIA-ME.txt").write_text(
        f"""PT Prospect
===========

Prospeccao de personal trainers no Instagram.
Nao precisa de Python instalado.


COMO USAR
---------

1. Renomeie o arquivo ".env.example" para ".env"

2. Abra o ".env" no Bloco de Notas e preencha:

     IG_USERNAME=seu_usuario_do_instagram
     IG_PASSWORD=sua_senha

   Para gravar os leads no Supabase, preencha tambem:

     SUPABASE_URL=https://xxxxxxxx.supabase.co
     SUPABASE_SERVICE_KEY=<a service_role key do projeto>

   Sem essas duas, o programa funciona normalmente e guarda tudo
   localmente em data/leads.db.

3. De um duplo clique em PTProspect.exe


PRIMEIRA EXECUCAO
-----------------

{primeiro_uso}


ONDE FICAM SEUS DADOS
---------------------

Tudo ao lado do executavel, nesta mesma pasta:

  .env         suas credenciais
  data/        banco de leads (leads.db) e os CSV exportados
  sessions/    sessao do Instagram, pra nao logar toda vez
  browsers/    o navegador usado pela automacao

Para fazer backup, copie a pasta data/.
Para mover o programa, mova a pasta inteira.


AVISO
-----

A automacao acessa Instagram e WhatsApp Web com a SUA conta. Use com
moderacao: volume alto de acessos ou disparos pode levar a bloqueio
temporario ou permanente das contas. Os intervalos padrao entre acoes
existem justamente para reduzir esse risco - nao os diminua sem
necessidade.
""",
        encoding="utf-8",
    )
    log("LEIA-ME.txt e .env.example incluidos")


def compactar(com_navegadores: bool) -> Path:
    variante = "completo" if com_navegadores else "leve"
    destino = DIST / f"PTProspect-{variante}.zip"

    arquivos = [f for f in APP.rglob("*") if f.is_file()]
    log(f"compactando {len(arquivos)} arquivos...")

    with zipfile.ZipFile(destino, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for i, arquivo in enumerate(arquivos, 1):
            zf.write(arquivo, Path("PTProspect") / arquivo.relative_to(APP))
            if i % 500 == 0:
                log(f"  {i}/{len(arquivos)}...")

    return destino


def main() -> None:
    parser = argparse.ArgumentParser(description="Gera o .exe do PT Prospect")
    parser.add_argument(
        "--with-browsers", action="store_true",
        help="embute o Chromium (~180 MB no zip, roda offline de primeira)",
    )
    parser.add_argument("--no-zip", action="store_true", help="não compactar")
    args = parser.parse_args()

    if sys.platform != "win32":
        print("⚠️  Este build gera um .exe Windows e deve rodar no Windows.")

    secao("1/5  Limpando builds anteriores")
    limpar()

    secao("2/5  Compilando com PyInstaller")
    compilar()

    secao("3/5  Navegador")
    if args.with_browsers:
        copiar_navegadores()
    else:
        log("build leve — o Chromium será baixado no primeiro uso")

    secao("4/5  Arquivos de apoio")
    escrever_extras(args.with_browsers)

    if args.no_zip:
        secao("5/5  Pronto")
        log(f"pasta: {APP}  ({tamanho(APP)})")
        return

    secao("5/5  Compactando")
    zip_final = compactar(args.with_browsers)

    print(f"\n{'=' * 62}")
    print("  ✅ Build concluído")
    print(f"{'=' * 62}")
    print(f"  pasta : {APP}  ({tamanho(APP)})")
    print(f"  zip   : {zip_final}  ({tamanho(zip_final)})")
    print("\n  Distribua o .zip. O usuário extrai e roda PTProspect.exe.")


if __name__ == "__main__":
    main()
