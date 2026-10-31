"""Shared helpers: build fake Yahoo-style data without the internet."""
import numpy as np
import pandas as pd


def make_frame(tickers_to_ohlc: dict, n: int, freq: str = "5min") -> pd.DataFrame:
    """tickers_to_ohlc: {ticker: DataFrame with High/Low/Close (length n)} -> one
    combined frame whose columns are (ticker, field), like yf.download(group_by='ticker')."""
    index = pd.date_range("2026-01-01", periods=n, freq=freq, tz="UTC")
    parts = {}
    for ticker, df in tickers_to_ohlc.items():
        df = df.copy()
        df.index = index[-len(df):]
        if "Open" not in df.columns:
            df["Open"] = df["Close"]
        if "Volume" not in df.columns:
            df["Volume"] = 1000.0
        parts[ticker] = df[["Open", "High", "Low", "Close", "Volume"]]
    return pd.concat(parts, axis=1)


def buy_pattern(n: int = 150) -> pd.DataFrame:
    """Price sitting near its LOW, far from its HIGH.

    Oldest 60 candles trade high (125-130), the next 40 mid (105-110), the newest
    50 low (100-105). Candle ranges are only 5, so the ATR stays small (~5-6) while
    the high level (130) is ~25 away: near the low only -> BUY.
    """
    high = np.r_[np.full(60, 130.0), np.full(40, 110.0), np.full(50, 105.0)][-n:]
    low = high - 5.0
    close = low + 3.0
    return pd.DataFrame({"High": high, "Low": low, "Close": close})


def sell_pattern(n: int = 150) -> pd.DataFrame:
    """Mirror image of buy_pattern: price near its HIGH, far from its LOW -> SELL."""
    b = buy_pattern(n)
    return pd.DataFrame({"High": 230.0 - b["Low"], "Low": 230.0 - b["High"], "Close": 230.0 - b["Close"]})
