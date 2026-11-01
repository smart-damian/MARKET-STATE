# MARKET-STATE

Scans **200 symbols** (40 crypto, 41 forex, 7 metals, 2 energy, 8 indices, 102 stocks) on
**six timeframes** (5m, 15m, 30m, 1h, 4h, 1d) and keeps a live **BUY / SELL / NEUTRAL**
state for each one in a Supabase database. Other apps read that table instead of scanning
the market themselves. No Telegram, no alerts: just data.

It is the sibling of [SMART-LAB-3](https://github.com/smart-damian/SMART-LAB-3) and uses
the same maths (150-candle lookback, ATR 100, "within 2 ATR").

## The state rule

For one symbol on one timeframe:

| State | Meaning |
|---|---|
| **SELL** | price is within 2 ATR of the high (`high - last <= 2 ATR`) |
| **BUY** | price is within 2 ATR of the low (`last - low <= 2 ATR`) |
| **NEUTRAL** | neither, or both at once (range narrower than 4 ATR, which is contradictory) |

*ATR (Average True Range)* is the normal size of one candle's move on that symbol and
timeframe, so "2 ATR" means the same thing for gold on 1d and EURUSD on 5m.

`agree_signal` / `agree_count` (computed by the database): BUY or SELL wins only if
**strictly more** timeframes show it than the other side; a tie is NEUTRAL with count 0.

## Database (Supabase)

| Table | What it holds | Who writes |
|---|---|---|
| `symbols` | one row per ticker: broker name, class, last price, daily volume, six `state_*` + `updated_*` columns, `agree_signal`, `agree_count` | scanner, through two functions |
| `levels` | high, low, ATR, last price per ticker and timeframe (200 x 6 rows) | scanner |
| `signal_events` | one row each time a state or the agreement flips | the database itself (trigger) |

* **Each timeframe's run updates only its own state columns.** Only the **5m run** updates
  last price and daily volume. Volume is NULL where Yahoo has none (forex, indices).
* A symbol with no usable data on a timeframe is **skipped**: its state and `updated_*`
  stay as they were. Check `updated_*` to see how fresh a state is.
* **Realtime is ON only for `signal_events`.** `symbols` changes every few minutes, so
  streaming it would use up the free monthly message quota. Apps read `symbols` over REST
  (poll every 30-60 s, ask only for the columns you need) and subscribe to `signal_events`
  to be pushed the moment something flips.
* **Old events are deleted after 30 days** by the daily run, so the log stays small. Apps
  should subscribe to **INSERT** events only (`prune.sql` also makes Realtime announce
  inserts only, so cleanup deletes never cost messages). Subscribe with a filter (a
  timeframe or ticker) to receive fewer messages.
* Apps use the public read-only key. Only the secret key can write.

## Requirements

* Python 3.10+ (3.12 on GitHub), packages in `requirements.txt`
* A Supabase project (free tier is enough)
* Termux (Ubuntu) on Android, or any Linux

## One-time setup

1. **Supabase SQL Editor**, run these four files in order (each is safe to re-run):
   1. `supabase/schema.sql`: tables, triggers, security
   2. `supabase/seed_symbols.sql`: the 200 symbols
   3. `supabase/functions.sql`: the two functions the scanner calls
   4. `supabase/prune.sql`: old-event cleanup + Realtime announces inserts only
2. **GitHub repo > Settings > Secrets and variables > Actions**, add two secrets:
   * `SUPABASE_URL` (e.g. `https://abcdxyz.supabase.co`)
   * `SUPABASE_SERVICE_KEY` (the secret key; **never** put it in code, chat or a file)
3. **cron-job.org**: one job per timeframe that calls the GitHub `workflow_dispatch` API
   for `scan.yml` (GitHub's own `schedule:` is not used because it skips runs).
   Settings template: `docs/cron-job-org-settings.md`.

## Run it in Termux (Ubuntu)

```bash
apt update && apt install -y python3 python3-venv python3-pip git
git clone https://github.com/smart-damian/MARKET-STATE /work/MARKET-STATE
cd /work/MARKET-STATE
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python -m pytest                 # all tests (no internet needed)
python scan.py 1d --dry-run      # fetch + calculate + print, writes nothing

# real run: type the keys in this session only, never save them in a file
export SUPABASE_URL='https://YOUR-PROJECT.supabase.co'
export SUPABASE_SERVICE_KEY='...'
python scan.py 5m
```

Exit codes: `0` ok, `1` no data from Yahoo, `2` bad setup, `3` database write failed.

## Example output

```
[1d] fetching 200 symbols (1y of 1d candles)...
[1d] 200 scanned in 12.3s: BUY 87, SELL 41, NEUTRAL 72, skipped 0
STATES 1d  2026-10-31 15:20 UTC
TICKER          STATE            HIGH         LOW        LAST        ATR
BTC-USD         BUY           87363.8     76189.9     77445.1       2041
ETH-USD         BUY            2805.5      2173.3     2247.99      82.41
HYPE32196-USD   NEUTRAL       97.9777     74.5919       87.85      4.209
```

## How it works

1. cron-job.org triggers `scan.yml` with a `timeframe` (one run per timeframe).
2. `scan.py` reads `symbols.csv`, drops tickers switched off in the database (`active = false`).
3. `fetcher.py` downloads the candles from Yahoo in chunks of 50, with retries and timeouts.
   (Yahoo has no 4h candles, so 4h is built from 1h.)
4. `levels.py` finds the most-defended high and low; `signals.py` computes ATR and the state.
5. The results are saved: `levels` rows (upsert), then this timeframe's states through
   `apply_scan()`; the 5m run also calls `apply_prices()`.
6. The database recomputes `agree_signal` / `agree_count` and logs any flip in `signal_events`.
7. The 1d run also deletes `signal_events` older than 30 days (`prune_signal_events()`).

| File | Job |
|---|---|
| `scan.py` | command line + the run (fetch, calculate, save) |
| `fetcher.py` | Yahoo downloads (chunks, retries) |
| `levels.py`, `signals.py`, `timeframes.py` | the maths and the settings table |
| `supabase_client.py` | small HTTP client with timeouts and retries |
| `symbols.csv` | the 200 symbols (ticker, broker name, class) |
| `supabase/*.sql` | schema, seed, functions, pruning |
| `tests/` | pytest suite |

## Changing things

* **Tune a timeframe or the lookback:** edit the table in `timeframes.py`.
* **Change "near a level":** `ATR_MULT` / `ATR_PERIOD` in `signals.py`.
* **Add or remove a symbol:** edit `symbols.csv` and `supabase/seed_symbols.sql`, re-run the seed.
* **Pause a symbol:** set `active = false` in the `symbols` table.
* **Rename a broker symbol:** edit `broker_name` in Supabase (`levels` follows automatically).
* **Keep events longer or shorter:** `--keep-days` / `KEEP_DAYS` in `scan.py` (default 30).

## Security

Public repo, private data. The Supabase secret key lives only in GitHub Secrets, and no
code prints it. If a key ever leaks, revoke it in the Supabase dashboard straight away.
