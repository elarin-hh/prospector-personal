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
| `SUPABASE_URL` | — | URL do projeto Supabase |
| `SUPABASE_SERVICE_KEY` | — | **service_role** key (a tabela tem RLS) |
| `SUPABASE_TABLE` | `leads` | Nome da tabela |
| `SUPABASE_SYNC` | `true` | Envia cada lead durante a prospecção |
| `DEFAULT_CITY` | — | Cidade usada quando a bio não revela a localização |

---

## ☁️ Banco de leads no Supabase

Os leads vivem em duas camadas: **SQLite local** (fonte de verdade, funciona
offline) e **Supabase** (banco compartilhado). Cada gravação tenta subir o lead;
se a rede falhar, ele fica marcado como pendente e você sincroniza depois.

### 1. Criar a tabela

Supabase Dashboard → **SQL Editor** → New query → cole
[`supabase/migrations/0001_create_leads.sql`](supabase/migrations/0001_create_leads.sql)
→ **Run**. É idempotente, pode rodar de novo sem quebrar nada.

Isso cria:

| Objeto | O que é |
|---|---|
| `public.leads` | Tabela de leads, com `city`/`state` e `contact_status` |
| `leads_por_cidade` | View com o resumo agregado por cidade |
| Índices | Por cidade, status de contato, score e fila de disparo |
| Trigger | `updated_at` automático |
| RLS | Habilitado **sem policy pública** |

### 2. Configurar as credenciais

Project Settings → API. Use a **service_role key** — a tabela tem RLS ativo sem
policy pública, então a chave `anon` não lê nem grava.

```bash
SUPABASE_URL=https://xxxxxxxxxxxx.supabase.co
SUPABASE_SERVICE_KEY=<cole aqui a sua service_role key>
```

### 3. Sincronizar

O sync roda sozinho durante a prospecção (`SUPABASE_SYNC=true`). A opção **[13]**
do menu faz o resto: testa a conexão, infere cidades faltantes, envia os
pendentes e, se você quiser, traz de volta os status de contato editados
direto no painel do Supabase.

---

## 📍 Separação por cidade

A cidade é deduzida na hora do scraping, em ordem de confiança:

1. Trecho marcado na bio — `📍 Curitiba`, `Atendo em Balneário Camboriú`
2. Padrão `Cidade - UF` / `Cidade/UF` em qualquer parte do texto
3. Cidade conhecida (capitais + ~250 municípios) **com contexto de localização
   antes** — `personal trainer em Curitiba` conta, `Rita Mesquita` não
4. Apelidos — `floripa`, `bh`, `poa`, `cwb`, `sampa`, `jampa`...
5. `city_hint` informado na prospecção, ou `DEFAULT_CITY` do `.env`

Sem evidência suficiente a cidade fica **vazia** — melhor "não identificada" do
que gravar sobrenome como cidade. Use a opção **[13]** para inferir a cidade
dos leads já coletados.

---

## 📞 Status de contato

Cada lead carrega `contact_status`, atualizado automaticamente pela campanha do
WhatsApp e editável pela opção **[12]**:

| Status | Quando |
|---|---|
| `not_contacted` | Ainda não falamos com o lead (padrão) |
| `queued` | Na fila de disparo |
| `contacted` | Mensagem entregue |
| `responded` | O lead respondeu |
| `no_answer` | Contatado, sem resposta |
| `invalid_number` | Número não existe no WhatsApp |
| `restricted` | WhatsApp bloqueou o disparo (retenta após 24h) |
| `not_interested` | Respondeu que não tem interesse |
| `converted` | Virou cliente |

A campanha **[10]** só dispara para quem tem WhatsApp e está `not_contacted`,
com filtro opcional por cidade — não repete contato. Uma re-análise do perfil
nunca apaga o histórico de contato.

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
- **[3] Ver TODOS os Leads** — lista todos os leads no terminal
- **[4] Ver Leads COM WhatsApp** — mostra apenas leads com WhatsApp encontrado
- **[5] Ver Leads Qualificados (SEM WhatsApp)**
- **[6] Exportar CSV** — salva resultados em `data/leads_*.csv`
- **[7] Estatísticas** — métricas, funil de contato e top cidades
- **[8] Analisar perfil** — analisa um perfil específico
- **[9] Revarrer WhatsApp** — reprocessa leads do banco sem WhatsApp
- **[10] Disparo via WhatsApp Web** — campanha humanizada, opcionalmente por cidade
- **[11] Leads por Cidade** — resumo por cidade + drill-down e export
- **[12] Atualizar status de contato** — marca contatado, respondeu, convertido...
- **[13] Sincronizar com Supabase** — infere cidades, envia pendentes, traz status

---

## 🧪 Testes

```bash
python tests/test_location.py   # inferência de cidade/UF
python tests/test_flow.py       # cidade + status de contato + payload do Supabase
```

Sem dependências extras — saem com código 1 se algo falhar. O `test_flow.py`
roda num SQLite temporário e **zera as credenciais do `.env`**, estourando em
qualquer chamada de rede, para nunca gravar lead de teste na tabela real.

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
