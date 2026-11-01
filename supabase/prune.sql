-- =============================================================================
-- Housekeeping for signal_events (the log of state flips).
-- Run once, after functions.sql: Supabase dashboard > SQL Editor > New query >
-- paste > Run. Safe to re-run.
-- =============================================================================

-- 1) Delete events older than keep_days. The daily (1d) scan calls this once a day.
create or replace function public.prune_signal_events(keep_days integer default 30)
returns integer language plpgsql set search_path = public as $$
declare
  n integer;
begin
  if keep_days < 1 then
    raise exception 'keep_days must be at least 1';
  end if;
  delete from public.signal_events
   where ts < now() - make_interval(days => keep_days);
  get diagnostics n = row_count;
  return n;          -- how many rows were deleted
end $$;

-- Only the secret service_role key may call it (functions are open to everyone
-- by default, so remove that access explicitly).
revoke all on function public.prune_signal_events(integer) from public, anon, authenticated;
grant execute on function public.prune_signal_events(integer) to service_role;

-- 2) Make Realtime announce INSERTS only. Without this, every row the daily
-- cleanup deletes would be sent as a DELETE message to every app listening to
-- all events, wasting the free monthly message quota. signal_events is only
-- ever inserted into, so nothing is lost. Skipped (with a notice) if you have
-- added other tables to the publication, because they might need updates.
do $$
begin
  if (select count(*) from pg_publication_tables where pubname = 'supabase_realtime') = 1
     and exists (select 1 from pg_publication_tables
                 where pubname = 'supabase_realtime' and schemaname = 'public'
                   and tablename = 'signal_events') then
    alter publication supabase_realtime set (publish = 'insert');
  else
    raise notice 'supabase_realtime has other tables: publish setting left alone; tell apps to subscribe to INSERT events only';
  end if;
end $$;

-- Check (expect pubinsert = true, the other three false):
--   select pubinsert, pubupdate, pubdelete, pubtruncate
--   from pg_publication where pubname = 'supabase_realtime';
