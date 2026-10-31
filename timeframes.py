"""Timeframe settings and candle resampling.

Same numbers as SMART-LAB-3, so both projects see the market identically.
Every value that differs between 5m and 1d lives HERE, in one table: to tune a
timeframe you edit this table, not the logic.

Yahoo Finance limits (the reason the numbers below look the way they do):
  * 5m, 15m, 30m candles: only the last ~60 days are available
  * 1h candles:           only the last ~730 days
  * 4h candles:           NOT offered at all, so we download 1h and build 4h
                          candles ourselves (see resample_ohlc)
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

# How many of the newest candles feed the high/low calculation (and the ATR).
# One shared number so all timeframes are tuned together.
LOOKBACK = 150


@dataclass(frozen=True)
class Timeframe:
    name: str                  # label used on the command line and in the database
    interval: str              # the interval we ask Yahoo for
    period: str                # how far back to download (must hold >= LOOKBACK candles)
    minutes: int               # length of one candle
    lookback: int = LOOKBACK   # max candles fed to find_level() and ATR
    resample_to: str | None = None   # pandas rule, only when Yahoo lacks the interval


TIMEFRAMES: dict[str, Timeframe] = {
    "5m": Timeframe("5m", interval="5m", period="5d", minutes=5),
    "15m": Timeframe("15m", interval="15m", period="5d", minutes=15),
    "30m": Timeframe("30m", interval="30m", period="1mo", minutes=30),
    "1h": Timeframe("1h", interval="1h", period="3mo", minutes=60),
    "4h": Timeframe("4h", interval="1h", period="6mo", minutes=240, resample_to="4h"),
    "1d": Timeframe("1d", interval="1d", period="1y", minutes=1440),
}


def get_timeframe(name: str) -> Timeframe:
    """Look up a timeframe, with a helpful error instead of a bare KeyError."""
    try:
        return TIMEFRAMES[name]
    except KeyError:
        raise ValueError(f"unknown timeframe {name!r}; choose from {', '.join(TIMEFRAMES)}") from None


def resample_ohlc(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    """Merge small candles into bigger ones (e.g. four 1h candles -> one 4h).

    A bigger candle's High is the max of the small Highs, its Low the min of the
    small Lows, its Close the last Close. Averaging would be wrong: a High is
    "the highest price reached", not an average.

    Caveat: bucket edges follow the timezone of Yahoo's timestamps, so they may
    not line up with your broker's 4h candles.
    """
    rules = {"High": "max", "Low": "min"}
    if "Open" in df.columns:
        rules["Open"] = "first"
    if "Close" in df.columns:
        rules["Close"] = "last"
    out = df.resample(rule).agg(rules)
    # Empty buckets (market closed) become NaN rows; drop them.
    return out.dropna(subset=["High", "Low"])
