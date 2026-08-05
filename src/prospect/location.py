"""
=============================================================================
  prospect/location.py
  Inferência de cidade/estado a partir da bio do Instagram.

  Ordem de resolução (da mais confiável para a menos):
    1. Segmento marcado com 📍 / "atendo em" / "localização"
    2. Padrão "Cidade - UF", "Cidade/UF", "Cidade, UF"
    3. Nome de cidade conhecida em qualquer lugar do texto
    4. Apelido de cidade (floripa, bh, poa, cwb...)
=============================================================================
"""
from __future__ import annotations
import re
import unicodedata


UFS = (
    "AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS",
    "MG", "PA", "PB", "PR", "PE", "PI", "RJ", "RN", "RS", "RO", "RR", "SC",
    "SP", "SE", "TO",
)

# ── Cidades conhecidas: capitais + maiores municípios do país ───────────────
KNOWN_CITIES: dict[str, str] = {
    # Capitais
    "Rio Branco": "AC", "Maceió": "AL", "Macapá": "AP", "Manaus": "AM",
    "Salvador": "BA", "Fortaleza": "CE", "Brasília": "DF", "Vitória": "ES",
    "Goiânia": "GO", "São Luís": "MA", "Cuiabá": "MT", "Campo Grande": "MS",
    "Belo Horizonte": "MG", "Belém": "PA", "João Pessoa": "PB",
    "Curitiba": "PR", "Recife": "PE", "Teresina": "PI",
    "Rio de Janeiro": "RJ", "Natal": "RN", "Porto Alegre": "RS",
    "Porto Velho": "RO", "Boa Vista": "RR", "Florianópolis": "SC",
    "São Paulo": "SP", "Aracaju": "SE", "Palmas": "TO",

    # São Paulo
    "Guarulhos": "SP", "Campinas": "SP", "São Bernardo do Campo": "SP",
    "Santo André": "SP", "Osasco": "SP", "São José dos Campos": "SP",
    "Ribeirão Preto": "SP", "Sorocaba": "SP", "Santos": "SP",
    "Mauá": "SP", "São José do Rio Preto": "SP", "Mogi das Cruzes": "SP",
    "Diadema": "SP", "Jundiaí": "SP", "Piracicaba": "SP", "Carapicuíba": "SP",
    "Bauru": "SP", "Itaquaquecetuba": "SP", "São Vicente": "SP",
    "Franca": "SP", "Praia Grande": "SP", "Guarujá": "SP", "Taubaté": "SP",
    "Limeira": "SP", "Suzano": "SP", "Sumaré": "SP", "Barueri": "SP",
    "Embu das Artes": "SP", "São Carlos": "SP", "Marília": "SP",
    "Indaiatuba": "SP", "Cotia": "SP", "Americana": "SP", "Jacareí": "SP",
    "Araraquara": "SP", "Itapevi": "SP", "Presidente Prudente": "SP",
    "Hortolândia": "SP", "Rio Claro": "SP", "São Caetano do Sul": "SP",
    "Araçatuba": "SP", "Ferraz de Vasconcelos": "SP", "Santa Bárbara d'Oeste": "SP",
    "Itu": "SP", "Bragança Paulista": "SP", "Pindamonhangaba": "SP",
    "Botucatu": "SP", "São Sebastião": "SP", "Atibaia": "SP",
    "Mogi Guaçu": "SP", "Jaú": "SP", "Ourinhos": "SP", "Valinhos": "SP",
    "Sertãozinho": "SP", "Birigui": "SP", "Votorantim": "SP",
    "Caraguatatuba": "SP", "Ubatuba": "SP", "Ilhabela": "SP",

    # Rio de Janeiro
    "São Gonçalo": "RJ", "Duque de Caxias": "RJ", "Nova Iguaçu": "RJ",
    "Niterói": "RJ", "Campos dos Goytacazes": "RJ", "Belford Roxo": "RJ",
    "São João de Meriti": "RJ", "Petrópolis": "RJ", "Volta Redonda": "RJ",
    "Magé": "RJ", "Macaé": "RJ", "Itaboraí": "RJ", "Cabo Frio": "RJ",
    "Nova Friburgo": "RJ", "Barra Mansa": "RJ", "Angra dos Reis": "RJ",
    "Teresópolis": "RJ", "Mesquita": "RJ", "Nilópolis": "RJ",
    "Resende": "RJ", "Búzios": "RJ", "Armação dos Búzios": "RJ",

    # Minas Gerais
    "Uberlândia": "MG", "Contagem": "MG", "Juiz de Fora": "MG",
    "Betim": "MG", "Montes Claros": "MG", "Ribeirão das Neves": "MG",
    "Uberaba": "MG", "Uberaba ": "MG", "Governador Valadares": "MG",
    "Ipatinga": "MG", "Sete Lagoas": "MG", "Divinópolis": "MG",
    "Santa Luzia": "MG", "Ibirité": "MG", "Poços de Caldas": "MG",
    "Patos de Minas": "MG", "Pouso Alegre": "MG", "Teófilo Otoni": "MG",
    "Barbacena": "MG", "Sabará": "MG", "Varginha": "MG", "Itabira": "MG",
    "Araguari": "MG", "Passos": "MG", "Ubá": "MG", "Nova Lima": "MG",
    "Lavras": "MG", "Itajubá": "MG", "Alfenas": "MG", "Uberaba/MG": "MG",

    # Paraná
    "Londrina": "PR", "Maringá": "PR", "Ponta Grossa": "PR",
    "Cascavel": "PR", "São José dos Pinhais": "PR", "Foz do Iguaçu": "PR",
    "Colombo": "PR", "Guarapuava": "PR", "Guarapuava ": "PR",
    "Paranaguá": "PR", "Araucária": "PR", "Toledo": "PR",
    "Apucarana": "PR", "Pinhais": "PR", "Campo Largo": "PR",
    "Arapongas": "PR", "Almirante Tamandaré": "PR", "Umuarama": "PR",
    "Piraquara": "PR", "Cambé": "PR", "Fazenda Rio Grande": "PR",
    "Sarandi": "PR", "Guaratuba": "PR", "Matinhos": "PR",
    "Campo Mourão": "PR", "Francisco Beltrão": "PR", "Pato Branco": "PR",

    # Rio Grande do Sul
    "Caxias do Sul": "RS", "Canoas": "RS", "Pelotas": "RS",
    "Santa Maria": "RS", "Gravataí": "RS", "Viamão": "RS",
    "Novo Hamburgo": "RS", "São Leopoldo": "RS", "Rio Grande": "RS",
    "Alvorada": "RS", "Passo Fundo": "RS", "Sapucaia do Sul": "RS",
    "Uruguaiana": "RS", "Santa Cruz do Sul": "RS", "Cachoeirinha": "RS",
    "Bagé": "RS", "Bento Gonçalves": "RS", "Erechim": "RS",
    "Guaíba": "RS", "Santana do Livramento": "RS", "Ijuí": "RS",
    "Lajeado": "RS", "Torres": "RS", "Capão da Canoa": "RS",
    "Gramado": "RS", "Canela": "RS",

    # Santa Catarina
    "Joinville": "SC", "Blumenau": "SC", "São José": "SC",
    "Criciúma": "SC", "Chapecó": "SC", "Itajaí": "SC",
    "Jaraguá do Sul": "SC", "Lages": "SC", "Palhoça": "SC",
    "Balneário Camboriú": "SC", "Brusque": "SC", "Tubarão": "SC",
    "Camboriú": "SC", "São Bento do Sul": "SC", "Caçador": "SC",
    "Concórdia": "SC", "Navegantes": "SC", "Rio do Sul": "SC",
    "Araranguá": "SC", "Gaspar": "SC", "Biguaçu": "SC", "Bombinhas": "SC",
    "Garopaba": "SC", "Imbituba": "SC", "Joaçaba": "SC",

    # Bahia
    "Feira de Santana": "BA", "Vitória da Conquista": "BA",
    "Camaçari": "BA", "Camaçari ": "BA", "Juazeiro": "BA",
    "Itabuna": "BA", "Lauro de Freitas": "BA", "Ilhéus": "BA",
    "Jequié": "BA", "Teixeira de Freitas": "BA", "Alagoinhas": "BA",
    "Barreiras": "BA", "Porto Seguro": "BA", "Simões Filho": "BA",
    "Paulo Afonso": "BA", "Eunápolis": "BA", "Santo Antônio de Jesus": "BA",

    # Pernambuco
    "Jaboatão dos Guararapes": "PE", "Olinda": "PE", "Caruaru": "PE",
    "Petrolina": "PE", "Paulista": "PE", "Cabo de Santo Agostinho": "PE",
    "Camaragibe": "PE", "Garanhuns": "PE", "Vitória de Santo Antão": "PE",
    "Igarassu": "PE", "São Lourenço da Mata": "PE",

    # Ceará
    "Caucaia": "CE", "Juazeiro do Norte": "CE", "Maracanaú": "CE",
    "Sobral": "CE", "Crato": "CE", "Itapipoca": "CE", "Maranguape": "CE",
    "Iguatu": "CE", "Quixadá": "CE", "Aquiraz": "CE",

    # Goiás / DF entorno
    "Aparecida de Goiânia": "GO", "Anápolis": "GO", "Rio Verde": "GO",
    "Luziânia": "GO", "Águas Lindas de Goiás": "GO", "Valparaíso de Goiás": "GO",
    "Trindade": "GO", "Formosa": "GO", "Novo Gama": "GO", "Itumbiara": "GO",
    "Senador Canedo": "GO", "Catalão": "GO", "Jataí": "GO", "Caldas Novas": "GO",

    # Espírito Santo
    "Serra": "ES", "Vila Velha": "ES", "Cariacica": "ES",
    "Cachoeiro de Itapemirim": "ES", "Cachoeiro do Itapemirim": "ES",
    "Linhares": "ES", "São Mateus": "ES", "Colatina": "ES",
    "Guarapari": "ES", "Aracruz": "ES",

    # Pará / Amazonas / Norte
    "Ananindeua": "PA", "Santarém": "PA", "Marabá": "PA",
    "Castanhal": "PA", "Parauapebas": "PA", "Abaetetuba": "PA",
    "Itaituba": "PA", "Bragança": "PA", "Altamira": "PA",
    "Parintins": "AM", "Itacoatiara": "AM", "Manacapuru": "AM",
    "Ji-Paraná": "RO", "Ariquemes": "RO", "Vilhena": "RO",
    "Araguaína": "TO", "Gurupi": "TO",

    # Nordeste restante
    "Mossoró": "RN", "Parnamirim": "RN", "Campina Grande": "PB",
    "Santa Rita": "PB", "Patos": "PB", "Bayeux": "PB",
    "Arapiraca": "AL", "Imperatriz": "MA", "São José de Ribamar": "MA",
    "Timon": "MA", "Caxias": "MA", "Codó": "MA", "Parnaíba": "PI",
    "Nossa Senhora do Socorro": "SE", "Lagarto": "SE", "Itabaiana": "SE",

    # Centro-Oeste restante
    "Várzea Grande": "MT", "Rondonópolis": "MT", "Sinop": "MT",
    "Tangará da Serra": "MT", "Cáceres": "MT", "Sorriso": "MT",
    "Dourados": "MS", "Três Lagoas": "MS", "Corumbá": "MS",
    "Ponta Porã": "MS", "Naviraí": "MS",
}

# ── Apelidos e abreviações comuns em bios ──────────────────────────────────
CITY_ALIASES: dict[str, tuple[str, str]] = {
    "floripa": ("Florianópolis", "SC"),
    "floripa/sc": ("Florianópolis", "SC"),
    "fpolis": ("Florianópolis", "SC"),
    "sampa": ("São Paulo", "SP"),
    "sao paulo capital": ("São Paulo", "SP"),
    "sp capital": ("São Paulo", "SP"),
    "bh": ("Belo Horizonte", "MG"),
    "beaga": ("Belo Horizonte", "MG"),
    "bhz": ("Belo Horizonte", "MG"),
    "poa": ("Porto Alegre", "RS"),
    "cwb": ("Curitiba", "PR"),
    "ctba": ("Curitiba", "PR"),
    "rio": ("Rio de Janeiro", "RJ"),
    "bsb": ("Brasília", "DF"),
    "brasilia df": ("Brasília", "DF"),
    "gyn": ("Goiânia", "GO"),
    "ssa": ("Salvador", "BA"),
    "fortal": ("Fortaleza", "CE"),
    "jampa": ("João Pessoa", "PB"),
    "vix": ("Vitória", "ES"),
    "sjc": ("São José dos Campos", "SP"),
    "abc paulista": ("Santo André", "SP"),
    "bc": ("Balneário Camboriú", "SC"),
    "balneario": ("Balneário Camboriú", "SC"),
    "cascavel pr": ("Cascavel", "PR"),
    "recife pe": ("Recife", "PE"),
    "sao jose do rio preto": ("São José do Rio Preto", "SP"),
    "ribeirao": ("Ribeirão Preto", "SP"),
    "campo grande ms": ("Campo Grande", "MS"),
}

# Marcadores fortes: o texto seguinte é quase certamente uma localização,
# então aceitamos até nomes de cidade que não estão na nossa lista.
_STRONG_MARKERS = (
    "📍",
    "atendo em", "atendimento em", "atendimentos em", "atendo na",
    "localizacao", "localizado em", "localizada em",
    "cidade:", "local:", "base em", "moro em", "aqui em",
    "presencial em", "treinos em", "consultoria em",
)

# Marcadores fracos: sugerem contexto geográfico, mas aparecem em bio
# decorativa ("🇧🇷 coach"). Só aceitamos cidade conhecida vinda deles.
_WEAK_MARKERS = ("🌎", "🌍", "🏠", "🇧🇷")

# Tokens que podem preceder uma cidade solta no texto. Sem um desses antes,
# um nome de cidade é provavelmente sobrenome ("Rita Mesquita").
_LOCATION_CUES = frozenset({
    "~",  # delimitador (|, -, /, vírgula, nova linha)
    "em", "no", "na", "nos", "nas", "regiao", "grande",
    "atendo", "atendimento", "atendimentos", "atendendo",
    "cidade", "local", "localizacao", "moro", "aqui", "sou",
    "presencial", "presenciais", "treinos", "treino", "consultoria",
    "base", "personal", "trainer", "treinador", "treinadora",
    "coach", "cref", "fisico", "fisica", "fitness", "studio", "academia",
})

# Palavras que denunciam que o trecho não é um nome de cidade
_NOT_CITY_TOKENS = frozenset({
    "personal", "trainer", "treinador", "treinadora", "coach", "cref",
    "atleta", "athlete", "atlhete", "posing", "bodybuilder",
    "nutri", "nutricionista", "fisioterapeuta", "fisio", "biomedico",
    "online", "consultoria", "assessoria", "aluno", "alunos", "aluna",
    "fitness", "academia", "studio", "estudio", "box", "crossfit",
    "brasil", "brazil", "mundo", "todo", "dm", "link", "bio", "whatsapp",
    "wpp", "zap", "contato", "agende", "agendamento", "vagas", "vaga",
    "emagrecimento", "hipertrofia", "musculacao", "yoga", "pilates",
    "designer", "nail", "anos", "y", "beauty", "makeup", "hair",
})

_UF_RE = "|".join(UFS)
# "Cidade - SP", "Cidade/SP", "Cidade, SP"
_CITY_UF_RE = re.compile(
    r"([A-ZÀ-Ú][A-Za-zÀ-ÿ'´`.\- ]{2,34}?)\s*[-/,·|]\s*(" + _UF_RE + r")\b"
)
# "Cidade UF" separados só por espaço — usado apenas para confirmar cidade
# que já está na nossa lista (senão casaria "Rita Souza SP")
_CITY_UF_LOOSE_RE = re.compile(
    r"([A-ZÀ-Ú][A-Za-zÀ-ÿ'´`.\- ]{2,34}?)\s+(" + _UF_RE + r")\b"
)
# Idem, mas para texto digitado pelo usuário: case-insensitive e ancorado
_CITY_UF_INPUT_RE = re.compile(
    r"^\s*(.{2,40}?)\s*[-/,]\s*(" + _UF_RE + r")\s*$", re.IGNORECASE
)
# Normaliza acentos/case do texto para comparação
_PUNCT_RE = re.compile(r"[^a-z0-9 ]+")
_PUNCT_KEEP_DELIM_RE = re.compile(r"[^a-z0-9~ ]+")
_DELIM_RE = re.compile(r"[|•·/,;\-–—\n\r]+")
_SPACE_RE = re.compile(r"\s+")


def _strip_accents(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _norm(text: str) -> str:
    """Normaliza para comparação: sem acento, minúsculo, sem pontuação."""
    text = _strip_accents(text or "").lower()
    text = _PUNCT_RE.sub(" ", text)
    return _SPACE_RE.sub(" ", text).strip()


def _norm_search(text: str) -> str:
    """
    Como _norm, mas preserva os delimitadores como '~' para que a busca
    saiba distinguir "Rita Mesquita" de "Personal | Mesquita".
    """
    text = _strip_accents(text or "").lower()
    text = _DELIM_RE.sub(" ~ ", text)
    text = _PUNCT_KEEP_DELIM_RE.sub(" ", text)
    return _SPACE_RE.sub(" ", text).strip()


def _preceded_by_cue(text: str, start: int) -> bool:
    """True se o token imediatamente antes de `start` indica localização."""
    before = text[:start].strip()
    if not before:
        return True  # cidade no início do texto
    return before.split()[-1] in _LOCATION_CUES


# Índice de busca: nome normalizado → (nome canônico, UF)
_CITY_INDEX: dict[str, tuple[str, str]] = {}
for _city, _uf in KNOWN_CITIES.items():
    _key = _norm(_city)
    if _key and _key not in _CITY_INDEX:
        _CITY_INDEX[_key] = (_city.strip(), _uf)

# Nomes que também são sobrenomes brasileiros comuns — só valem com um
# marcador explícito de localização, nunca soltos no meio do texto.
_AMBIGUOUS = {
    "serra", "patos", "rio", "palmas", "caxias", "santos", "franca", "itu",
    "mesquita", "passos", "trindade", "braganca", "bragança", "campos",
    "cardoso", "barbacena", "nova lima", "salvador", "vitoria", "colombo",
    "toledo", "torres", "gaspar", "brusque", "lagarto", "timon", "catalao",
}


def _match_known_city(
    text_norm: str,
    require_cue: bool = False,
    allow_ambiguous: bool = False,
) -> tuple[str, str] | None:
    """
    Procura uma cidade conhecida no texto normalizado por _norm_search.

    `require_cue` exige um token de localização antes do nome — evita casar
    sobrenome ("Rita Mesquita") como cidade.
    """
    best: tuple[str, str] | None = None
    best_len = 0

    for key, (city, uf) in _CITY_INDEX.items():
        if len(key) < 4:
            continue
        if key in _AMBIGUOUS and not allow_ambiguous:
            continue

        match = re.search(rf"(?<![a-z0-9]){re.escape(key)}(?![a-z0-9])", text_norm)
        if not match:
            continue
        if require_cue and not _preceded_by_cue(text_norm, match.start()):
            continue
        # Prefere o match mais longo ("São José dos Campos" > "São José")
        if len(key) > best_len:
            best, best_len = (city, uf), len(key)

    return best


def _match_alias(text_norm: str, require_cue: bool = False) -> tuple[str, str] | None:
    best: tuple[str, str] | None = None
    best_len = 0

    for alias, value in CITY_ALIASES.items():
        key = _norm(alias)
        if not key:
            continue
        match = re.search(rf"(?<![a-z0-9]){re.escape(key)}(?![a-z0-9])", text_norm)
        if not match:
            continue
        if require_cue and not _preceded_by_cue(text_norm, match.start()):
            continue
        if len(key) > best_len:
            best, best_len = value, len(key)

    return best


def _refine_city(phrase: str, uf: str = "") -> tuple[str, str]:
    """
    Extrai a cidade de uma frase capturada pelo padrão "... - UF".
    "Personal Curitiba" → Curitiba/PR; "Centro Curitiba" → Curitiba/PR.
    """
    inner = _match_known_city(_norm_search(phrase), allow_ambiguous=True)
    if inner:
        return inner[0], (uf or inner[1])
    return _canonicalize(phrase, uf)


def _looks_like_city(segment_norm: str) -> bool:
    """Heurística para aceitar um nome de cidade fora da nossa lista."""
    tokens = segment_norm.split()
    if not (1 <= len(tokens) <= 3):
        return False
    if any(t in _NOT_CITY_TOKENS for t in tokens):
        return False
    if any(char.isdigit() for char in segment_norm):
        return False
    return 3 <= len(segment_norm) <= 28


def _canonicalize(raw_city: str, uf: str = "") -> tuple[str, str]:
    """Converte um nome bruto de cidade no nome canônico + UF."""
    key = _norm(raw_city)
    if key in _CITY_INDEX:
        city, known_uf = _CITY_INDEX[key]
        return city, (uf or known_uf)
    # Não é cidade conhecida: preserva o nome informado em Title Case
    pretty = " ".join(w if w.lower() in ("de", "do", "da", "dos", "das", "e")
                      else w.capitalize()
                      for w in raw_city.strip().split())
    return pretty, uf


def _location_segments(text: str) -> list[tuple[str, bool]]:
    """
    Extrai trechos que provavelmente contêm a localização.
    Retorna (trecho, marcador_forte).
    """
    segments: list[tuple[str, bool]] = []
    lowered = _strip_accents(text).lower()

    for markers, strong in ((_STRONG_MARKERS, True), (_WEAK_MARKERS, False)):
        for marker in markers:
            norm_marker = _strip_accents(marker).lower()
            start = 0
            while True:
                idx = lowered.find(norm_marker, start)
                if idx == -1:
                    break
                begin = idx + len(norm_marker)
                # Vai até quebra de linha ou próximo separador forte
                chunk = text[begin:begin + 60]
                chunk = re.split(r"[\n\r|•·;]|https?://", chunk)[0]
                if chunk.strip():
                    segments.append((chunk.strip(" -–—:,."), strong))
                start = begin

    # Marcadores fortes primeiro
    segments.sort(key=lambda item: not item[1])
    return segments


def extract_location(bio: str, full_name: str = "") -> tuple[str, str]:
    """
    Deduz (cidade, estado) a partir da bio e do nome de exibição do perfil.
    Retorna ("", "") quando não há evidência suficiente — é melhor deixar
    a cidade vazia do que gravar um sobrenome como cidade.
    """
    text = f"{bio or ''}\n{full_name or ''}".strip()
    if not text:
        return "", ""

    # 1. Trechos marcados com 📍 / "atendo em" — maior confiança
    for segment, strong in _location_segments(text):
        seg_norm = _norm_search(segment)

        match = _CITY_UF_RE.search(segment)
        if match:
            return _refine_city(match.group(1), match.group(2).upper())

        # Dentro de um marcador, sobrenome-cidade é evidência aceitável
        found = _match_known_city(seg_norm, allow_ambiguous=True)
        if found:
            return found

        found = _match_alias(seg_norm)
        if found:
            return found

        # Cidade fora da lista só é aceita com marcador forte (📍, "atendo em")
        if strong and _looks_like_city(seg_norm):
            return _canonicalize(segment)

    # 2. Padrão "Cidade - UF" em qualquer parte do texto
    match = _CITY_UF_RE.search(text)
    if match:
        return _refine_city(match.group(1), match.group(2).upper())

    # 2b. "Cidade UF" só com espaço ("Personal Serra ES"): aceita apenas se a
    #     frase contiver uma cidade conhecida — o UF confirma a evidência.
    for match in _CITY_UF_LOOSE_RE.finditer(text):
        found = _match_known_city(_norm_search(match.group(1)), allow_ambiguous=True)
        if found:
            return found[0], match.group(2).upper()

    # 3. Cidade conhecida solta no texto — exige contexto de localização antes
    text_norm = _norm_search(text)
    found = _match_known_city(text_norm, require_cue=True)
    if found:
        return found

    # 4. Apelidos, também com contexto
    found = _match_alias(text_norm, require_cue=True)
    if found:
        return found

    return "", ""


def resolve_location(bio: str, full_name: str = "", hint: str = "") -> tuple[str, str]:
    """
    Resolve (cidade, estado) priorizando a evidência da bio e caindo
    para `hint` (cidade informada na prospecção ou DEFAULT_CITY).
    """
    city, state = extract_location(bio, full_name)
    if city:
        return city, state
    if hint:
        return normalize_city_input(hint)
    return "", ""


def normalize_city_input(raw: str) -> tuple[str, str]:
    """
    Normaliza uma cidade digitada pelo usuário (ex: "curitiba", "floripa",
    "sao paulo - sp") no par canônico (cidade, UF).
    """
    raw = (raw or "").strip()
    if not raw:
        return "", ""

    # Aceita "curitiba - pr", "SAO PAULO/SP", "Belo Horizonte, mg"
    match = _CITY_UF_INPUT_RE.match(raw)
    if match:
        return _canonicalize(match.group(1), match.group(2).upper())

    raw_norm = _norm(raw)
    if raw_norm in _CITY_INDEX:
        return _CITY_INDEX[raw_norm]

    found = _match_alias(raw_norm)
    if found:
        return found

    return _canonicalize(raw)
