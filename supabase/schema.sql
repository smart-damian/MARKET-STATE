-- =============================================================================
-- MARKET-STATE database schema (Supabase / Postgres)
-- Run once: Supabase dashboard > SQL Editor > New query > paste > Run.
-- Safe to re-run: each statement checks before it changes anything.
-- Every table carries ticker + broker_name.
-- =============================================================================

-- 1) symbols: one row per ticker. This is the table your apps read. -----------
create table if not exists public.symbols (
  ticker        text primary key,              -- Yahoo symbol, e.g. GC=F
  broker_name   text not null,                 -- broker name, e.g. XAUUSD (editable)
  asset_class   text not null
                check (asset_class in ('crypto','forex','metal','energy','index','stock')),
  active        boolean not null default true, -- false = stop scanning, keep the row

  -- Written by the 5m run only. volume_1d stays NULL where Yahoo has no volume.
  last_price    double precision,
  price_updated timestamptz,
  volume_1d     double precision,

  -- One state per timeframe. Each timeframe's run writes ONLY its own pair.
  -- High/low/ATR for every timeframe (including daily) live in `levels`.
  state_5m  text not null default 'NEUTRAL' check (state_5m  in ('BUY','SELL','NEUTRAL')),
  state_15m text not null default 'NEUTRAL' check (state_15m in ('BUY','SELL','NEUTRAL')),
  state_30m text not null default 'NEUTRAL' check (state_30m in ('BUY','SELL','NEUTRAL')),
  state_1h  text not null default 'NEUTRAL' check (state_1h  in ('BUY','SELL','NEUTRAL')),
  state_4h  text not null default 'NEUTRAL' check (state_4h  in ('BUY','SELL','NEUTRAL')),
  state_1d  text not null default 'NEUTRAL' check (state_1d  in ('BUY','SELL','NEUTRAL')),
  -- When each state was last refreshed, so apps can tell a stale state.
  updated_5m  timestamptz,
  updated_15m timestamptz,
  updated_30m timestamptz,
  updated_1h  timestamptz,
  updated_4h  timestamptz,
  updated_1d  timestamptz,

  -- Computed by the database (trigger below); runs never write these.
  buy_count    smallint not null default 0,
  sell_count   smallint not null default 0,
  agree_signal text not null default 'NEUTRAL'
               check (agree_signal in ('BUY','SELL','NEUTRAL')),
  agree_count  smallint not null default 0,

  -- Lets `levels` point at the (ticker, broker_name) PAIR, so the two tables can
  -- never disagree about a broker name (see the foreign key in `levels`).
  constraint symbols_ticker_broker_key unique (ticker, broker_name)
);

-- 2) levels: high, low and ATR per ticker and timeframe (200 x 6 rows) --------
create table if not exists public.levels (
  ticker      text not null,
  broker_name text not null,                   -- filled automatically if omitted
  timeframe   text not null check (timeframe in ('5m','15m','30m','1h','4h','1d')),
  high        double precision not null,
  low         double precision not null,
  last_price  double precision,
  atr         double precision not null,
  candle_ts   timestamptz,                     -- time of the newest candle used
  updated_at  timestamptz not null default now(),
  primary key (ticker, timeframe),
  -- Renaming a broker_name in `symbols` automatically renames it here too.
  foreign key (ticker, broker_name)
    references public.symbols (ticker, broker_name)
    on update cascade on delete cascade
);

-- Trigger: if a run sends a level row without broker_name, look it up.
create or replace function public.levels_fill_broker_name()
returns trigger language plpgsql set search_path = public as $$
begin
  if new.broker_name is null then
    select s.broker_name into new.broker_name
    from public.symbols s where s.ticker = new.ticker;
  end if;
  return new;
end $$;

drop trigger if exists levels_broker_name on public.levels;
create trigger levels_broker_name
  before insert on public.levels
  for each row execute function public.levels_fill_broker_name();

-- 3) signal_events: one row per state flip. The ONLY table with Realtime on. --
create table if not exists public.signal_events (
  id              bigint generated always as identity primary key,
  ts              timestamptz not null default now(),
  ticker          text not null,
  broker_name     text not null,               -- the name at the time of the event
  timeframe       text not null
                  check (timeframe in ('5m','15m','30m','1h','4h','1d','agree')),
  old_state       text,
  new_state       text not null,
  old_agree_count smallint,
  agree_signal    text,
  agree_count     smallint
);
create index if not exists signal_events_ts_idx on public.signal_events (ts desc);
create index if not exists signal_events_ticker_idx on public.signal_events (ticker, ts desc);
-- Later: delete rows older than ~30 days so this table stays small.

-- 4) Trigger: compute the agreement columns on every insert/update. -----------
-- Rule: BUY or SELL wins only if STRICTLY more timeframes show it; a tie is
-- NEUTRAL with count 0. This also removes any race between the six runs.
create or replace function public.symbols_compute_agreement()
returns trigger language plpgsql set search_path = public as $$
declare
  states text[] := array[new.state_5m, new.state_15m, new.state_30m,
                         new.state_1h, new.state_4h, new.state_1d];
begin
  new.buy_count  := (select count(*) from unnest(states) s where s = 'BUY');
  new.sell_count := (select count(*) from unnest(states) s where s = 'SELL');
  if new.buy_count > new.sell_count then
    new.agree_signal := 'BUY';  new.agree_count := new.buy_count;
  elsif new.sell_count > new.buy_count then
    new.agree_signal := 'SELL'; new.agree_count := new.sell_count;
  else
    new.agree_signal := 'NEUTRAL'; new.agree_count := 0;
  end if;
  return new;
end $$;

drop trigger if exists symbols_agreement on public.symbols;
create trigger symbols_agreement
  before insert or update on public.symbols
  for each row execute function public.symbols_compute_agreement();

-- 5) Trigger: log a signal_events row whenever a state or the agreement changes.
create or replace function public.symbols_log_events()
returns trigger language plpgsql set search_path = public as $$
declare
  tf    text;
  old_s text;
  new_s text;
begin
  foreach tf in array array['5m','15m','30m','1h','4h','1d'] loop
    old_s := to_jsonb(old) ->> ('state_' || tf);
    new_s := to_jsonb(new) ->> ('state_' || tf);
    if new_s is distinct from old_s then
      insert into public.signal_events
        (ticker, broker_name, timeframe, old_state, new_state, agree_signal, agree_count)
      values (new.ticker, new.broker_name, tf, old_s, new_s,
              new.agree_signal, new.agree_count);
    end if;
  end loop;

  if new.agree_signal is distinct from old.agree_signal
     or new.agree_count is distinct from old.agree_count then
    insert into public.signal_events
      (ticker, broker_name, timeframe, old_state, new_state,
       old_agree_count, agree_signal, agree_count)
    values (new.ticker, new.broker_name, 'agree', old.agree_signal, new.agree_signal,
            old.agree_count, new.agree_signal, new.agree_count);
  end if;
  return null;
end $$;

drop trigger if exists symbols_events on public.symbols;
create trigger symbols_events
  after update on public.symbols
  for each row execute function public.symbols_log_events();

-- 6) Realtime: ONLY signal_events. symbols and levels change every few minutes,
-- so streaming them would burn the free monthly message quota.
do $$
begin
  if exists (select 1 from pg_publication where pubname = 'supabase_realtime') then
    if not exists (select 1 from pg_publication_tables
                   where pubname = 'supabase_realtime' and schemaname = 'public'
                     and tablename = 'signal_events') then
      alter publication supabase_realtime add table public.signal_events;
    end if;
    if exists (select 1 from pg_publication_tables
               where pubname = 'supabase_realtime' and schemaname = 'public'
                 and tablename = 'symbols') then
      alter publication supabase_realtime drop table public.symbols;
    end if;
    if exists (select 1 from pg_publication_tables
               where pubname = 'supabase_realtime' and schemaname = 'public'
                 and tablename = 'levels') then
      alter publication supabase_realtime drop table public.levels;
    end if;
  end if;
end $$;

-- 7) Security: apps can only READ. Only the secret service_role key can write,
-- because it bypasses Row Level Security and no write policy exists for others.
alter table public.symbols       enable row level security;
alter table public.levels        enable row level security;
alter table public.signal_events enable row level security;

drop policy if exists "read symbols" on public.symbols;
create policy "read symbols" on public.symbols
  for select to anon, authenticated using (true);
drop policy if exists "read levels" on public.levels;
create policy "read levels" on public.levels
  for select to anon, authenticated using (true);
drop policy if exists "read signal_events" on public.signal_events;
create policy "read signal_events" on public.signal_events
  for select to anon, authenticated using (true);

revoke all on public.symbols, public.levels, public.signal_events from anon, authenticated;
grant select on public.symbols, public.levels, public.signal_events to anon, authenticated;
