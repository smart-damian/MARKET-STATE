#!/usr/bin/env python3
"""Scan ONE timeframe for every symbol and save the result to Supabase.

Usage:
    python scan.py 1h --dry-run          # fetch + calculate + print; nothing is written
    python scan.py 5m                    # the real run (needs the two env variables below)

Environment variables (only for real runs; set them in GitHub Secrets, never in code):
    SUPABASE_URL           e.g. https://abcdxyz.supabase.co
    SUPABASE_SERVICE_KEY   the secret key (can write; bypasses Row Level Security)

What one run does:   fetch -> calculate -> save
  * `levels` table:  high, low, ATR, last price for this timeframe (one row per symbol)
  * `symbols` table: ONLY this timeframe's state + its "updated" time
  * the 5m run also refreshes last price and daily volume (nobody else touches those)

Exit codes: 0 ok | 1 no data from Yahoo | 2 bad setup | 3 database write failed
"""
from __future__ import annotations

import argparse
import csv
import math
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import pandas as pd

from fetcher import FetchError, extract_symbol, fetch_ohlc
from levels import analyze
from signals import ATR_MULT, ATR_PERIOD, NEUTRAL, classify, compute_atr
from supabase_client import SupabaseClient, SupabaseError
from timeframes import TIMEFRAMES, Timeframe, get_timeframe, resample_ohlc

DEFAULT_SYMBOLS = Path(__file__).resolve().parent / "symbols.csv"


@dataclass(frozen=True)
class Reading:
    """Everything one run learned about one symbol."""
    ticker: str
    state: str                 # BUY / SELL / NEUTRAL
    high: float
    low: float
    last: float | None
    atr: float
    candle_ts: str | None      # time of the newest candle used (UTC, ISO format)


def finite(x) -> float | None:
    """NaN and infinity are not valid JSON, so they become None (NULL in the database)."""
    if x is None:
        return None
    x = float(x)
    return x if math.isfinite(x) else None


def load_symbols(path: Path) -> list[str]:
    """Read tickers from symbols.csv (columns: ticker, broker_name, asset_class)."""
    with open(path, newline="") as fh:
        reader = csv.DictReader(fh)
        if not reader.fieldnames or "ticker" not in reader.fieldnames:
            raise ValueError(f"{path} needs a header row with a 'ticker' column")
        tickers = [(row["ticker"] or "").strip() for row in reader]
    tickers = [t for t in tickers if t]
    if len(set(tickers)) != len(tickers):
        raise ValueError(f"{path} contains duplicate tickers")
    if not tickers:
        raise ValueError(f"{path} contains no tickers")
    return tickers


def candle_time(df: pd.DataFrame) -> str | None:
    """Timestamp of the newest usable candle, as UTC ISO text."""
    clean = df.dropna(subset=["High", "Low"])
    if clean.empty:
        return None
    ts = pd.Timestamp(clean.index[-1])
    ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
    return ts.isoformat()


def run_scan(tf: Timeframe, symbols: list[str], atr_period: int = ATR_PERIOD, mult: float = ATR_MULT,
             fetch: Callable = fetch_ohlc) -> tuple[list[Reading], list[str]]:
    """Fetch and analyse every symbol for one timeframe. Returns (readings, skipped).

    `fetch` is a parameter so tests can pass a fake downloader (dependency injection).
    """
    raw = fetch(symbols, period=tf.period, interval=tf.interval)
    readings: list[Reading] = []
    skipped: list[str] = []
    for symbol in symbols:
        df = extract_symbol(raw, symbol)
        try:
            if df is None:
                raise ValueError("no data")
            if tf.resample_to:
                df = resample_ohlc(df, tf.resample_to)
            # ATR uses the same newest-`lookback` candles as the levels, so both
            # numbers describe the same stretch of market.
            atr = compute_atr(df.tail(tf.lookback), atr_period)
            result = analyze(symbol, df, tf.lookback)
        except ValueError:
            skipped.append(symbol)   # one bad ticker must not stop the others
            continue
        readings.append(Reading(symbol, classify(result, atr, mult), result.high, result.low,
                                result.last_close, atr, candle_time(df)))
    return readings, skipped


def daily_volumes(symbols: list[str], fetch: Callable = fetch_ohlc) -> dict[str, float | None]:
    """Today's volume per symbol. Yahoo gives 0/NaN for forex and indices: stored as NULL."""
    raw = fetch(symbols, period="5d", interval="1d")
    out: dict[str, float | None] = {}
    for symbol in symbols:
        df = extract_symbol(raw, symbol)
        volume = None
        if df is not None and "Volume" in df.columns and "Close" in df.columns:
            vol = df.dropna(subset=["Close"])["Volume"].dropna()
            if not vol.empty and finite(vol.iloc[-1]) and vol.iloc[-1] > 0:
                volume = finite(vol.iloc[-1])
        out[symbol] = volume
    return out


# ---- row builders: plain dicts, exactly what the database expects -------------

def levels_rows(tf_name: str, readings: list[Reading], now_iso: str) -> list[dict]:
    return [{"ticker": r.ticker, "timeframe": tf_name, "high": finite(r.high), "low": finite(r.low),
             "last_price": finite(r.last), "atr": finite(r.atr), "candle_ts": r.candle_ts,
             "updated_at": now_iso} for r in readings]


def state_rows(readings: list[Reading], now_iso: str) -> list[dict]:
    return [{"ticker": r.ticker, "state": r.state, "ts": now_iso} for r in readings]


def price_rows(readings: list[Reading], volumes: dict[str, float | None], now_iso: str) -> list[dict]:
    return [{"ticker": r.ticker, "last_price": finite(r.last), "ts": now_iso,
             "volume_1d": volumes.get(r.ticker)} for r in readings]


def print_table(tf: Timeframe, readings: list[Reading], skipped: list[str], now: datetime) -> None:
    print(f"STATES {tf.name}  {now:%Y-%m-%d %H:%M} UTC")
    print(f"{'TICKER':<16}{'STATE':<9}{'HIGH':>12}{'LOW':>12}{'LAST':>12}{'ATR':>11}")
    for r in readings:
        last = "-" if r.last is None else f"{r.last:.6g}"
        print(f"{r.ticker:<16}{r.state:<9}{r.high:>12.6g}{r.low:>12.6g}{last:>12}{r.atr:>11.4g}")
    if skipped:
        print(f"skipped ({len(skipped)}): {', '.join(skipped)}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Scan one timeframe and save the states to Supabase.")
    parser.add_argument("timeframe", choices=list(TIMEFRAMES))
    parser.add_argument("--symbols-file", default=str(DEFAULT_SYMBOLS))
    parser.add_argument("--atr-period", type=int, default=ATR_PERIOD)
    parser.add_argument("--atr-mult", type=float, default=ATR_MULT,
                        help=f"near a level = within this many ATRs (default {ATR_MULT:g})")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the full table; read and write nothing in Supabase")
    args = parser.parse_args()
    if args.atr_period < 1 or args.atr_mult <= 0:
        parser.error("--atr-period must be >= 1 and --atr-mult must be > 0")

    tf = get_timeframe(args.timeframe)
    try:
        symbols = load_symbols(Path(args.symbols_file))
    except (OSError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    client = None
    if not args.dry_run:
        url, key = os.environ.get("SUPABASE_URL", ""), os.environ.get("SUPABASE_SERVICE_KEY", "")
        if not url or not key:
            print("Error: set SUPABASE_URL and SUPABASE_SERVICE_KEY, or use --dry-run", file=sys.stderr)
            return 2
        try:
            client = SupabaseClient(url, key)
            off = client.inactive_tickers()
        except (ValueError, SupabaseError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 3
        if off:
            print(f"{len(off)} symbol(s) switched off in the database: not scanned")
            symbols = [s for s in symbols if s not in off]

    started = time.time()
    print(f"[{tf.name}] fetching {len(symbols)} symbols ({tf.period} of {tf.interval} candles)...")
    try:
        readings, skipped = run_scan(tf, symbols, args.atr_period, args.atr_mult)
    except FetchError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    if not readings:
        print("Error: no symbol produced usable data.", file=sys.stderr)
        return 1

    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    counts = {s: sum(r.state == s for r in readings) for s in ("BUY", "SELL", NEUTRAL)}
    print(f"[{tf.name}] {len(readings)} scanned in {time.time() - started:.1f}s: "
          f"BUY {counts['BUY']}, SELL {counts['SELL']}, NEUTRAL {counts[NEUTRAL]}, skipped {len(skipped)}")
    if args.dry_run:
        print_table(tf, readings, skipped, now)
        if tf.name == "5m":
            vols = daily_volumes([r.ticker for r in readings])
            print(f"daily volume found for {sum(v is not None for v in vols.values())} of {len(vols)} symbols")
        print("(dry run: nothing written)")
        return 0
    if skipped:
        print(f"skipped (state and levels left unchanged): {', '.join(skipped)}")

    # ---- save. levels first, then states: a state never points at missing levels.
    try:
        client.upsert_levels(levels_rows(tf.name, readings, now_iso))
        matched = client.rpc("apply_scan", {"tf": tf.name, "payload": state_rows(readings, now_iso)})
        if matched != len(readings):
            print(f"Warning: {len(readings)} states sent but {matched} rows matched in `symbols` "
                  f"(did you run supabase/seed_symbols.sql?)", file=sys.stderr)
        if tf.name == "5m":   # only the 5m run owns last price + daily volume
            vols = daily_volumes([r.ticker for r in readings])
            client.rpc("apply_prices", {"payload": price_rows(readings, vols, now_iso)})
    except (FetchError, SupabaseError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 3
    print(f"[{tf.name}] saved {len(readings)} rows to Supabase.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
