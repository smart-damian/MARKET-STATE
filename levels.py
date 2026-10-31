"""Core logic: find the "most-defended" high and low for a price series.

This file has NO network and NO database code on purpose. Pure functions
(data in -> answer out) are easy to test and easy to reuse. That is the main
design improvement over the original script, where fetching, maths and
printing were all mixed together.

The idea (from the original script)
-----------------------------------
Walk backwards from today. For every window size 1, 2, 3 ... N days, record
the highest High inside that window. The value that shows up for the most
window sizes is a high that nothing has beaten for a long time, which is
what traders loosely call a resistance level. Do the same with Lows and
the lowest Low for a support level.
"""
from __future__ import annotations

from dataclasses import dataclass
from statistics import mode

import pandas as pd


@dataclass(frozen=True)
class SymbolLevels:
    """Result for one symbol. frozen=True makes it read-only, so it can't be
    changed by accident after it has been computed."""

    symbol: str
    high: float
    low: float
    last_close: float | None
    bars: int  # how many daily candles were really used


def find_level(series: pd.Series, kind: str, lookback: int = 120) -> float:
    """Return the most frequent running max ("high") or running min ("low").

    `series` is oldest -> newest, exactly as yfinance returns it.
    """
    if kind not in ("high", "low"):
        raise ValueError(f"kind must be 'high' or 'low', got {kind!r}")

    # Drop gaps first: holidays and missing candles are NaN, and NaN
    # would silently poison comparisons.
    # Then reverse so index 0 is the newest candle, like the original code.
    newest_first = series.dropna().iloc[::-1].iloc[:lookback]
    if newest_first.empty:
        raise ValueError("no usable price data")

    # cummax() at position n equals max(newest_first[0:n+1]). That is exactly
    # what the original while-loop computed, but in one fast vectorised call
    # instead of 120 manual slices.
    running = newest_first.cummax() if kind == "high" else newest_first.cummin()

    # On a tie, statistics.mode returns the first value it saw. The list
    # starts at the newest candle, so ties go to the more recent level.
    return float(mode(running.tolist()))


def analyze(symbol: str, df: pd.DataFrame, lookback: int = 120) -> SymbolLevels:
    """Compute both levels for one symbol's OHLC table (raises ValueError if unusable)."""
    missing = {"High", "Low"} - set(df.columns)
    if missing:
        raise ValueError(f"missing columns: {sorted(missing)}")

    high = find_level(df["High"], "high", lookback)
    low = find_level(df["Low"], "low", lookback)

    closes = df["Close"].dropna() if "Close" in df.columns else pd.Series(dtype=float)
    last_close = float(closes.iloc[-1]) if not closes.empty else None

    return SymbolLevels(
        symbol=symbol,
        high=high,
        low=low,
        last_close=last_close,
        bars=int(df["High"].dropna().shape[0]),
    )
