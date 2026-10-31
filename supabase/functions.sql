-- =============================================================================
-- Database functions the scanner calls. Run once AFTER schema.sql:
-- Supabase dashboard > SQL Editor > New query > paste > Run. Safe to re-run.
--
-- Why functions: a normal "upsert" of the symbols table would have to resend
-- broker_name and asset_class (they are NOT NULL), which would overwrite any
-- name you edited by hand in Supabase. These functions UPDATE only the columns
-- they own, in ONE request, so each timeframe's run touches only its own state.
-- =============================================================================

-- A timeframe run: set that timeframe's state and "updated" time for many tickers.
create or replace function public.apply_scan(tf text, payload jsonb)
returns integer language plpgsql set search_path = public as $$
declare
  n integer;
begin
  -- tf is pasted into the SQL text below, so only these six values are allowed.
  if tf not in ('5m','15m','30m','1h','4h','1d') then
    raise exception 'unknown timeframe: %', tf;
  end if;
  execute format(
    'update public.symbols s
        set state_%1$s = r.state, updated_%1$s = r.ts
       from jsonb_to_recordset($1) as r(ticker text, state text, ts timestamptz)
      where s.ticker = r.ticker', tf)
  using payload;
  get diagnostics n = row_count;
  return n;          -- how many tickers were updated
end $$;

-- The 5m run only: last price, its time, and today's volume.
create or replace function public.apply_prices(payload jsonb)
returns integer language plpgsql set search_path = public as $$
declare
  n integer;
begin
  update public.symbols s
     set last_price = r.last_price, price_updated = r.ts, volume_1d = r.volume_1d
    from jsonb_to_recordset(payload)
         as r(ticker text, last_price double precision, ts timestamptz, volume_1d double precision)
   where s.ticker = r.ticker;
  get diagnostics n = row_count;
  return n;
end $$;

-- Only the secret service_role key may call them. Functions are open to everyone
-- by default, so remove that access explicitly.
revoke all on function public.apply_scan(text, jsonb) from public, anon, authenticated;
revoke all on function public.apply_prices(jsonb)     from public, anon, authenticated;
grant execute on function public.apply_scan(text, jsonb) to service_role;
grant execute on function public.apply_prices(jsonb)     to service_role;
