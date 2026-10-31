# cron-job.org settings template

GitHub's own `schedule:` is best-effort and skips runs, so cron-job.org
triggers `scan.yml` through `workflow_dispatch` instead.
Create ONE job per timeframe (6 jobs). Timezone: UTC.

## Shared request settings (same for all 6 jobs)

| Setting | Value |
|---|---|
| URL | https://api.github.com/repos/smart-damian/MARKET-STATE/actions/workflows/scan.yml/dispatches |
| Method | POST |
| Header | Authorization: Bearer <YOUR_TOKEN> |
| Header | Accept: application/vnd.github+json |
| Header | Content-Type: application/json |
| Success | HTTP 204 |
| Notify on failure | ON (email) |

## Per-job settings

| Job title | Body | Schedule (UTC) |
|---|---|---|
| scan-5m | {"ref":"main","inputs":{"timeframe":"5m"}} | every 5 min |
| scan-15m | {"ref":"main","inputs":{"timeframe":"15m"}} | minutes 4,19,34,49 |
| scan-30m | {"ref":"main","inputs":{"timeframe":"30m"}} | minutes 1,31 |
| scan-1h | {"ref":"main","inputs":{"timeframe":"1h"}} | minute 2 |
| scan-4h | {"ref":"main","inputs":{"timeframe":"4h"}} | 00:03, 04:03, 08:03, 12:03, 16:03, 20:03 |
| scan-1d | {"ref":"main","inputs":{"timeframe":"1d"}} | 00:06 |

The minute offsets match SMART-LAB-3, so each run starts a few minutes after
its candle closes.

## GitHub token (fine-grained, ONE token for all 6 jobs)

- Repository access: only MARKET-STATE
- Permission: Actions = Read and write (nothing else)
- Expiry: 90 days. Reminder: replace it by day 80.
- Use a separate token from the one for SMART-LAB-3, so each can be revoked alone.
- NEVER commit the token. It lives only in cron-job.org headers.

## Test from Termux

    export GITHUB_TOKEN='<YOUR_TOKEN>'   # type it, don't save it in a file
    curl -i -X POST \
      -H "Authorization: Bearer $GITHUB_TOKEN" \
      -H "Accept: application/vnd.github+json" \
      -d '{"ref":"main","inputs":{"timeframe":"1d"}}' \
      https://api.github.com/repos/smart-damian/MARKET-STATE/actions/workflows/scan.yml/dispatches

Expect `HTTP/2 204`. Then check the Actions tab for a run with event
`workflow_dispatch`.

## Check that data is flowing (Supabase SQL Editor)

    select max(updated_5m) as m5, max(updated_15m) as m15, max(updated_30m) as m30,
           max(updated_1h) as h1, max(updated_4h) as h4, max(updated_1d) as d1,
           now() as now_utc
    from symbols;

Each value should be recent for its timeframe (the 5m one within a few minutes).

    select count(*) from levels;

Expect 1194 rows: zinc has no intraday data (5 rows missing) and aluminum has
none on 15m (1 row missing). Everything else is 200 x 6 = 1200.

## Troubleshooting

| Status | Meaning |
|---|---|
| 401 | Token expired or mistyped. Create a new one. |
| 403 | Token lacks Actions: Read and write. |
| 404 | Wrong URL, or the token can't see the repo. |
| 422 | Bad body. Check `ref` and the timeframe spelling. |
| 204 but no run | Check that scan.yml is on `main` and workflows are enabled. |
