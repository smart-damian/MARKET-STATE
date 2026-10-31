"""ATR and the BUY / SELL / NEUTRAL rule.

The maths is copied unchanged from SMART-LAB-3 (alerts.py): same ATR period,
same Wilder smoothing, same "within 2 ATR" test. Only the output differs: instead
of Telegram alerts, each timeframe gets one STATE per symbol.

Pure functions: numbers in, answer out. No internet, no database, so they are
easy to test.

ATR (Average True Range) in plain words: how far a candle typically moves on this
symbol and timeframe. It makes distances comparable: 0.0050 is huge for EURUSD on
5m candles but tiny for gold on daily candles.

State rule (same as the old alerts):
    SELL  price is within 2 ATR of the high   (high - last <= 2 ATR)
    BUY   price is within 2 ATR of the low    (last - low  <= 2 ATR)
    NEUTRAL  neither, OR both at once. "Both" happens when the range is narrower
             than 4 ATR; the old code fired two alerts, which is contradictory,
             so here it is NEUTRAL.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from levels import SymbolLevels

ATR_PERIOD = 100   # smoothing length (same as SMART-LAB-3)
ATR_MULT = 2.0     # near a level = within this many ATRs

SELL = "SELL"      # price is near the HIGH
BUY = "BUY"        # price is near the LOW
NEUTRAL = "NEUTRAL"


@dataclass(frozen=True)
class Alert:
    symbol: str
    signal: str          # SELL or BUY
    level: float         # the high (for SELL) or the low (for BUY)
    last: float
    distance: float      # high - last (SELL) or last - low (BUY)
    atr: float
    atr_multiple: float  # distance / atr, e.g. 1.4 means "1.4 ATRs away"


def true_range(df: pd.DataFrame) -> pd.Series:
    """True Range for every candle. The first candle has no previous close, so
    max() simply ignores that missing piece and uses High - Low."""
    prev_close = df["Close"].shift(1)
    parts = pd.concat(
        [df["High"] - df["Low"], (df["High"] - prev_close).abs(), (df["Low"] - prev_close).abs()],
        axis=1,
    )
    return parts.max(axis=1)


def compute_atr(df: pd.DataFrame, period: int = ATR_PERIOD) -> float:
    """Latest ATR value using Wilder's smoothing. Raises ValueError if it can't be trusted.

    1. Start with the plain average of the first `period` True Ranges.
    2. For every later candle:  atr = (previous_atr * (period - 1) + TR) / period
    """
    missing = {"High", "Low", "Close"} - set(df.columns)
    if missing:
        raise ValueError(f"missing columns: {sorted(missing)}")
    clean = df[["High", "Low", "Close"]].dropna()
    if len(clean) < period + 1:
        # Too few candles and the average is mostly noise.
        raise ValueError(f"need at least {period + 1} candles for ATR, got {len(clean)}")
    tr = true_range(clean).tolist()
    atr = sum(tr[:period]) / period
    for value in tr[period:]:
        atr = (atr * (period - 1) + value) / period
    if atr <= 0:
        raise ValueError("ATR is zero (no price movement)")
    return float(atr)


def find_alerts(result: SymbolLevels, atr: float, mult: float = ATR_MULT) -> list[Alert]:
    """SELL when the high is within `mult` ATRs above price; BUY when the low is
    within `mult` ATRs below it. (Unchanged from SMART-LAB-3.)"""
    last = result.last_close
    if last is None:
        return []
    checks = (
        (SELL, result.high, result.high - last),
        (BUY, result.low, last - result.low),
    )
    alerts: list[Alert] = []
    for signal, level, distance in checks:
        if distance <= mult * atr:   # <= so "exactly 2 ATR" counts
            alerts.append(Alert(result.symbol, signal, level, last, distance, atr, distance / atr))
    return alerts


def classify(result: SymbolLevels, atr: float, mult: float = ATR_MULT) -> str:
    """BUY, SELL or NEUTRAL for one symbol on one timeframe."""
    signals = {a.signal for a in find_alerts(result, atr, mult)}
    if signals == {BUY}:
        return BUY
    if signals == {SELL}:
        return SELL
    return NEUTRAL   # no signal, or both at once (contradictory)
