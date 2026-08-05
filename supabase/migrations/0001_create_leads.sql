-- =============================================================================
--  0001_create_leads.sql
--  Banco de leads do PT Prospect no Supabase.
--
--  Como aplicar:
--    Supabase Dashboard → SQL Editor → New query → cole este arquivo → Run
--
--  É idempotente: pode rodar novamente sem quebrar nada.
-- =============================================================================

-- ── Tabela principal ───────────────────────────────────────────────────────
-- gen_random_uuid() é nativo no Postgres 13+ (não precisa de pgcrypto).
create table if not exists public.leads (
    id               uuid primary key default gen_random_uuid(),

    -- Identidade (chave de deduplicação vinda do Instagram)
    username         text not null unique,
    full_name        text        not null default '',
    bio              text        not null default '',
    profile_url      text        not null default '',

    -- Localização — usada para separar os leads por cidade
    city             text        not null default '',
    state            text        not null default '',

    -- Métricas do perfil
    followers        integer     not null default 0,
    following        integer     not null default 0,
    posts_count      integer     not null default 0,
    is_verified      boolean     not null default false,
    is_business      boolean     not null default false,
    last_post_date   text        not null default '',
    score            integer     not null default 0,

    -- Contato
    bio_link         text        not null default '',
    whatsapp         text        not null default '',
    whatsapp_source  text        not null default '',

    -- Origem da prospecção (academia semente ou #hashtag)
    source_account   text        not null default '',

    -- Status do funil de prospecção
    status           text        not null default 'new',

    -- Status de contato: contatamos esse lead ou não?
    contact_status     text      not null default 'not_contacted',
    contact_channel    text      not null default '',
    contacted_at       timestamptz,
    contact_attempts   integer   not null default 0,
    last_contact_error text      not null default '',

    notes            text        not null default '',
    created_at       timestamptz not null default now(),
    updated_at       timestamptz not null default now()
);


-- ── Domínios de status (idempotente via drop/add) ──────────────────────────
alter table public.leads drop constraint if exists leads_status_check;
alter table public.leads add constraint leads_status_check check (
    status in ('new', 'qualified', 'whatsapp_found', 'no_whatsapp', 'skipped', 'error')
);

alter table public.leads drop constraint if exists leads_contact_status_check;
alter table public.leads add constraint leads_contact_status_check check (
    contact_status in (
        'not_contacted',   -- ainda não falamos com o lead
        'queued',          -- na fila de disparo
        'contacted',       -- mensagem entregue
        'responded',       -- o lead respondeu
        'no_answer',       -- contatado, sem resposta
        'invalid_number',  -- número não existe no WhatsApp
        'restricted',      -- WhatsApp bloqueou o disparo (tentar depois)
        'not_interested',  -- respondeu que não tem interesse
        'converted'        -- virou cliente
    )
);


-- ── Índices para as consultas do dia a dia ─────────────────────────────────
create index if not exists leads_city_idx           on public.leads (city);
create index if not exists leads_state_idx          on public.leads (state);
create index if not exists leads_contact_status_idx on public.leads (contact_status);
create index if not exists leads_status_idx         on public.leads (status);
create index if not exists leads_score_idx          on public.leads (score desc);

-- Fila de disparo: por cidade, quem tem WhatsApp e ainda não foi contatado
create index if not exists leads_city_pending_idx
    on public.leads (city, score desc)
    where contact_status = 'not_contacted' and whatsapp <> '';


-- ── updated_at automático ──────────────────────────────────────────────────
create or replace function public.leads_touch_updated_at()
returns trigger
language plpgsql
as $$
begin
    new.updated_at = now();
    return new;
end;
$$;

drop trigger if exists leads_touch_updated_at on public.leads;
create trigger leads_touch_updated_at
    before update on public.leads
    for each row
    execute function public.leads_touch_updated_at();


-- ── View: resumo por cidade ────────────────────────────────────────────────
create or replace view public.leads_por_cidade as
select
    nullif(city, '')                                       as city,
    nullif(state, '')                                      as state,
    count(*)                                               as total,
    count(*) filter (where whatsapp <> '')                 as com_whatsapp,
    count(*) filter (where contact_status <> 'not_contacted') as contatados,
    count(*) filter (where contact_status = 'not_contacted'
                       and whatsapp <> '')                 as pendentes,
    count(*) filter (where contact_status = 'responded')    as responderam,
    count(*) filter (where contact_status = 'converted')    as convertidos,
    round(avg(score))                                      as score_medio
from public.leads
group by 1, 2
order by total desc;


-- ── RLS ────────────────────────────────────────────────────────────────────
-- Habilitado sem policy pública: nenhuma chave anon/authenticated lê ou grava.
-- A CLI usa a SERVICE ROLE KEY, que ignora RLS por definição.
alter table public.leads enable row level security;
