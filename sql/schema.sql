-- Supabase schema for the options edge scanner.
-- Run this in the Supabase SQL editor once.
--
-- Free-tier sizing: iv_history at 30 tickers x 2 runs/day is ~22k rows/year,
-- a few MB. Nowhere near the 500MB cap.

-- ---------------------------------------------------------------------------
-- IV history: one row per ticker per run. This is what makes IV rank possible.
-- ---------------------------------------------------------------------------
create table if not exists iv_history (
    id           bigserial primary key,
    ticker       text        not null,
    observed_at  timestamptz not null default now(),
    atm_iv       double precision,
    hv20         double precision,
    spot         double precision,
    iv_hv_ratio  double precision,
    term_points  double precision,
    skew_points  double precision
);

create index if not exists iv_history_ticker_time
    on iv_history (ticker, observed_at desc);

-- ---------------------------------------------------------------------------
-- Alerts sent: dedupe source of truth, and the record for judging hit rate.
-- ---------------------------------------------------------------------------
create table if not exists alerts_sent (
    id          bigserial primary key,
    occ_symbol  text        not null,
    ticker      text        not null,
    direction   text        not null,
    strike      double precision,
    expiration  date,
    sent_at     timestamptz not null default now(),
    iv          double precision,
    delta       double precision,
    breakeven   double precision,
    rationale   text
);

create index if not exists alerts_sent_dedupe
    on alerts_sent (occ_symbol, direction, sent_at desc);

create index if not exists alerts_sent_ticker_time
    on alerts_sent (ticker, sent_at desc);

-- ---------------------------------------------------------------------------
-- Convenience view: IV rank per ticker computed in SQL rather than Python.
-- ---------------------------------------------------------------------------
create or replace view iv_rank_current as
with bounds as (
    select
        ticker,
        min(atm_iv) as iv_low,
        max(atm_iv) as iv_high,
        count(*)    as observations
    from iv_history
    where observed_at > now() - interval '365 days'
      and atm_iv is not null
    group by ticker
),
latest as (
    select distinct on (ticker)
        ticker, atm_iv, observed_at
    from iv_history
    where atm_iv is not null
    order by ticker, observed_at desc
)
select
    l.ticker,
    l.atm_iv         as current_iv,
    b.iv_low,
    b.iv_high,
    b.observations,
    case
        when b.iv_high > b.iv_low
        then round(((l.atm_iv - b.iv_low) / (b.iv_high - b.iv_low) * 100)::numeric, 1)
        else null
    end as iv_rank,
    l.observed_at
from latest l
join bounds b using (ticker);

-- ---------------------------------------------------------------------------
-- RLS: these tables are written with the service key from a trusted cloud
-- environment only. Enable RLS with no public policies so an anon key that
-- leaks cannot read the history.
-- ---------------------------------------------------------------------------
alter table iv_history  enable row level security;
alter table alerts_sent enable row level security;

-- A view has no RLS of its own, and by default executes with its creator's
-- privileges. Without this, iv_rank_current would read iv_history rows that
-- RLS is supposed to hide. security_invoker makes it run as the caller.
alter view iv_rank_current set (security_invoker = true);

-- Projects created with "Enable automatic RLS" get a SECURITY DEFINER event
-- trigger function that is granted to PUBLIC, anon, and authenticated. Event
-- triggers fire through the DDL machinery rather than EXECUTE grants, so
-- revoking these keeps the behaviour and drops it off the REST surface.
-- Safe to skip if the function does not exist on your project.
do $$
begin
    if exists (
        select 1 from pg_proc p
        join pg_namespace n on n.oid = p.pronamespace
        where n.nspname = 'public' and p.proname = 'rls_auto_enable'
    ) then
        revoke execute on function public.rls_auto_enable() from public;
        revoke execute on function public.rls_auto_enable() from anon;
        revoke execute on function public.rls_auto_enable() from authenticated;
    end if;
end $$;
