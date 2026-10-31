"""Network layer: download candles from Yahoo Finance via yfinance.

Kept separate from levels.py so the maths never needs internet to be tested.
"""
from __future__ import annotations

import sys
import time
from typing import Callable

import pandas as pd
import yfinance as yf

CHUNK_SIZE = 50          # symbols per request: gentler on Yahoo than one huge call
RETRIES = 3              # attempts per chunk before giving up on it
BACKOFF_SECONDS = 2      # wait 2s, then 4s ... between attempts
DOWNLOAD_TIMEOUT = 30    # seconds; a hung request must not eat the whole job


class FetchError(RuntimeError):
    """Raised when NO chunk could be downloaded (no internet, Yahoo down, ...)."""


def _download(chunk: list[str], period: str, interval: str) -> pd.DataFrame:
    data = yf.download(
        tickers=chunk,            # a list is safer than a space-joined string
        period=period,
        interval=interval,
        group_by="ticker",        # top column level = symbol
        threads=True,
        progress=False,           # progress bars make a mess of logs
        auto_adjust=False,        # we want raw High/Low, not dividend-adjusted
        timeout=DOWNLOAD_TIMEOUT,
    )
    # A one-symbol request comes back "flat" (no symbol level). Add the level so
    # extract_symbol() can treat every chunk the same way.
    if data is not None and not data.empty and not isinstance(data.columns, pd.MultiIndex):
        data.columns = pd.MultiIndex.from_product([chunk[:1], data.columns])
    return data


def fetch_ohlc(symbols: list[str], period: str, interval: str, chunk_size: int = CHUNK_SIZE,
               download: Callable = _download, sleep: Callable = time.sleep) -> list[pd.DataFrame]:
    """Download all symbols in chunks, retrying each chunk a few times.

    Returns one DataFrame per chunk that worked. A chunk that keeps failing is
    reported and skipped (its symbols just end up "skipped"), so one bad request
    cannot stop the others. `download` and `sleep` are parameters so tests can
    pass fakes instead of hitting the internet.
    """
    frames: list[pd.DataFrame] = []
    for start in range(0, len(symbols), chunk_size):
        chunk = symbols[start:start + chunk_size]
        problem = "unknown"
        for attempt in range(1, RETRIES + 1):
            try:
                data = download(chunk, period, interval)
                if data is not None and not data.empty:
                    frames.append(data)
                    break
                problem = "Yahoo returned no data"
            except Exception as exc:   # yfinance raises many different error types
                problem = f"{type(exc).__name__}: {exc}"
            if attempt < RETRIES:
                sleep(BACKOFF_SECONDS * attempt)
        else:
            print(f"warning: chunk of {len(chunk)} symbols failed after {RETRIES} tries "
                  f"({problem}); first symbol: {chunk[0]}", file=sys.stderr)
    if not frames:
        raise FetchError("Yahoo returned no data for any chunk (check internet / try again later)")
    return frames


def extract_symbol(raw: pd.DataFrame | list[pd.DataFrame], symbol: str) -> pd.DataFrame | None:
    """Pull one symbol's table out of the downloaded chunk(s).

    Returns None when the symbol is missing or has no rows, so one bad ticker is
    skipped instead of crashing the whole run.
    """
    frames = raw if isinstance(raw, list) else [raw]
    for frame in frames:
        cols = frame.columns
        if not isinstance(cols, pd.MultiIndex):
            continue
        # yfinance versions differ on which level holds the ticker, so check both.
        if symbol in cols.get_level_values(0):
            df = frame[symbol]
        elif symbol in cols.get_level_values(1):
            df = frame.xs(symbol, axis=1, level=1)
        else:
            continue
        df = df.dropna(how="all")
        if not df.empty:
            return df
    return None
