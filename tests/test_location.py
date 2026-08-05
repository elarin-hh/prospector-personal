"""
=============================================================================
  tests/test_location.py
  Inferência de cidade/estado a partir do texto do perfil.

  Rode com:  python tests/test_location.py
=============================================================================
"""
from __future__ import annotations
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from prospect.location import extract_location, normalize_city_input

# (texto do perfil, esperado) — "" significa cidade não identificada
CASOS_EXTRACAO: list[tuple[str, str]] = [
    # ── Marcador explícito de localização ──────────────────────────────────
    ("Personal Trainer CREF 123456-G/PR 📍 Curitiba - PR", "Curitiba/PR"),
    ("Treinador | 📍Floripa | consultoria online", "Florianópolis/SC"),
    ("Educador fisico. Atendo em Balneario Camboriu/SC", "Balneário Camboriú/SC"),
    ("📍 Nova Odessa | personal", "Nova Odessa"),
    ("Personal trainer 📍 Mesquita - RJ", "Mesquita/RJ"),

    # ── Padrão "Cidade - UF" / "Cidade UF" ────────────────────────────────
    ("Treinos personalizados 🏋️ Rio de Janeiro/RJ", "Rio de Janeiro/RJ"),
    ("Personal | Sao Jose dos Campos SP", "São José dos Campos/SP"),
    ("Studio em Santos - SP", "Santos/SP"),
    ("Personal Serra ES", "Serra/ES"),
    ("CREF 0000 Londrina PR treinos", "Londrina/PR"),

    # A cidade real deve ser extraída de dentro da frase capturada
    ("Eduardo Miara — Personal Curitiba-PR", "Curitiba/PR"),
    ("Amanda Lima | Nail Designer | Centro Curitiba-PR", "Curitiba/PR"),

    # ── Cidade conhecida com contexto de localização ──────────────────────
    ("Personal trainer em Sao Paulo, atendimento domiciliar", "São Paulo/SP"),
    ("Personal trainer 📍 Uberlandia - MG | link abaixo", "Uberlândia/MG"),

    # ── Apelidos ──────────────────────────────────────────────────────────
    ("coach fitness | bh | cref 9999", "Belo Horizonte/MG"),

    # ── Sem evidência: melhor vazio do que errado ─────────────────────────
    ("Personal online mundo todo 💪", ""),
    ("Fernanda Souza Personal", ""),
    ("FerrotrekkingBrasil🇧🇷🏴 RogerTrilhas 🏴", ""),
    ("Mesaque Figueiredo 24y 🇧🇷 atlhete & posing coach", ""),

    # Sobrenome que também é nome de cidade NÃO deve virar cidade
    ("ritac.mesquita", ""),
    ("rosicleia_passos", ""),
    ("lorenatrindade_oficial", ""),
    ("Julia Mesquita", ""),
    ("Rita Cardoso Santos", ""),
]

# Cidade digitada pelo usuário → par canônico
CASOS_INPUT: list[tuple[str, str]] = [
    ("curitiba", "Curitiba/PR"),
    ("floripa", "Florianópolis/SC"),
    ("sao paulo - sp", "São Paulo/SP"),
    ("SAO PAULO/SP", "São Paulo/SP"),
    ("Belo Horizonte, mg", "Belo Horizonte/MG"),
    ("bh", "Belo Horizonte/MG"),
    ("Vitoria", "Vitória/ES"),
    ("Nova Odessa", "Nova Odessa"),
    ("", ""),
]


def _label(city: str, state: str) -> str:
    if state:
        return f"{city}/{state}"
    return city


def main() -> int:
    falhas: list[str] = []

    print("=== extract_location (bio / nome do perfil) ===")
    for texto, esperado in CASOS_EXTRACAO:
        obtido = _label(*extract_location(texto, ""))
        ok = obtido == esperado
        if not ok:
            falhas.append(texto)
        marca = "OK   " if ok else "FALHA"
        mostrar = obtido or "(vazio)"
        alvo = esperado or "(vazio)"
        print(f"{marca} {mostrar:24} esperado={alvo:24} <- {texto[:46]}")

    print("\n=== normalize_city_input (cidade digitada) ===")
    for raw, esperado in CASOS_INPUT:
        obtido = _label(*normalize_city_input(raw))
        ok = obtido == esperado
        if not ok:
            falhas.append(raw)
        marca = "OK   " if ok else "FALHA"
        print(f"{marca} {raw!r:22} -> {obtido or '(vazio)':24} esperado={esperado or '(vazio)'}")

    total = len(CASOS_EXTRACAO) + len(CASOS_INPUT)
    print("\n" + "=" * 60)
    if falhas:
        print(f"❌ {len(falhas)} de {total} falharam")
        return 1
    print(f"✅ {total} casos passaram")
    return 0


if __name__ == "__main__":
    sys.exit(main())
