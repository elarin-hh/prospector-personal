# PT Prospect 🎯

> **Prospecção automatizada de personal trainers no Instagram** — encontra WhatsApp via bio e landing pages.

Ferramenta de terminal que:
1. 🏢 **Busca academias** no Instagram
2. 👥 **Analisa seguidores** identificando personal trainers
3. 🔍 **Filtra perfis qualificados** (2k+ seguidores, posts recentes)
4. 📱 **Encontra WhatsApp** na bio, landing pages, encurtadores e linktr.ee
5. 📤 **Exporta resultados** em CSV

---

## 🚀 Instalação

```bash
cd /home/desenv06/Documentos/prospect
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
playwright install chromium
```

---

## ⚙️ Configuração

```bash
# Preencha suas credenciais no .env
nano .env
```

| Variável | Padrão | Descrição |
|---|---|---|
| `IG_USERNAME` | — | Seu usuário do Instagram |
| `IG_PASSWORD` | — | Sua senha do Instagram |
| `HEADLESS` | `false` | `true` = sem janela visível |
| `SEED_ACCOUNTS` | — | Contas de academias (vírgula) |
| `SEARCH_HASHTAGS` | `personaltrainer,...` | Hashtags para buscar |
| `MIN_FOLLOWERS` | `2000` | Mínimo de seguidores |
| `MAX_FOLLOWERS` | `50000` | Máximo de seguidores |
| `DELAY_MIN` | `3` | Delay mínimo entre ações (s) |
| `DELAY_MAX` | `8` | Delay máximo entre ações (s) |

---

## 🖥️ Uso

```bash
# Ativa o ambiente virtual
source .venv/bin/activate

# Inicia o terminal interativo
prospect
```

### Menu Principal
- **[1] Prospectar por Academias** — busca seguidores de contas de academias
- **[2] Prospectar por Hashtags** — busca posts com hashtags de fitness
- **[3] Ver Leads** — lista todos os leads no terminal
- **[4] Ver Leads com WhatsApp** — mostra apenas leads com WhatsApp encontrado
- **[5] Exportar CSV** — salva resultados em `data/leads_*.csv`
- **[6] Estatísticas** — dashboard com métricas detalhadas
- **[7] Analisar perfil** — analisa um perfil específico

---

## 🔍 Como funciona a detecção de WhatsApp

1. **Bio** — procura `wa.me/`, números de telefone
2. **Link da bio** — segue redirects, analisa HTML
3. **Landing pages** — varredura de links, atributos, scripts
4. **Linktr.ee** — segue cada link procurando WhatsApp
5. **Encurtadores** — resolve bit.ly, etc e verifica destino

---

## 📊 Critérios de Qualificação

| Critério | Valor |
|---|---|
| Seguidores | ≥ 2.000 |
| Bio keywords | "personal trainer", "CREF", "educador físico", etc |
| Atividade | Posts nos últimos 30 dias |
| Scoring | 0–100 baseado em bio, seguidores, link, atividade |
