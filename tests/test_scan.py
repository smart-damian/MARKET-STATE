import json
import re
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import scan
from helpers import buy_pattern, make_frame, sell_pattern
from timeframes import TIMEFRAMES, get_timeframe, resample_ohlc

ROOT = Path(__file__).resolve().parent.parent


def fake_fetch_factory(frame):
    def fake(symbols, period, interval):
        return [frame]
    return fake


def build_frame():
    short = buy_pattern(150).tail(50)   # only 50 candles: too few for ATR 100
    return make_frame({"AAA": buy_pattern(), "BBB": sell_pattern(), "CCC": short}, n=150)


def test_run_scan_states_and_skips():
    tf = get_timeframe("5m")
    readings, skipped = scan.run_scan(tf, ["AAA", "BBB", "CCC", "DDD"], fetch=fake_fetch_factory(build_frame()))
    by = {r.ticker: r for r in readings}
    assert by["AAA"].state == "BUY"
    assert by["BBB"].state == "SELL"
    assert sorted(skipped) == ["CCC", "DDD"]       # too few candles / missing entirely
    assert by["AAA"].high > by["AAA"].last > by["AAA"].low
    assert by["AAA"].candle_ts.endswith("+00:00")


def test_run_scan_4h_resamples():
    tf = get_timeframe("4h")
    # 150*... need > 100 four-hour candles: 600 hourly candles
    idx = pd.date_range("2026-01-01", periods=600, freq="1h", tz="UTC")
    rng = np.random.default_rng(1)
    close = 100 + rng.normal(0, 1, 600).cumsum()
    df = pd.DataFrame({"High": close + 1, "Low": close - 1, "Close": close, "Open": close, "Volume": 1.0}, index=idx)
    frame = pd.concat({"ZZZ": df}, axis=1)
    readings, skipped = scan.run_scan(tf, ["ZZZ"], fetch=fake_fetch_factory(frame))
    assert not skipped and readings[0].state in {"BUY", "SELL", "NEUTRAL"}


def test_resample_ohlc_rules():
    idx = pd.date_range("2026-01-01", periods=8, freq="1h", tz="UTC")
    df = pd.DataFrame({"Open": range(8), "High": [1, 5, 2, 3, 4, 9, 1, 2], "Low": [0, 1, 1, 1, 2, 3, 0, 1],
                       "Close": range(10, 18)}, index=idx).astype(float)
    out = resample_ohlc(df, "4h")
    assert len(out) == 2
    assert out["High"].iloc[0] == 5 and out["Low"].iloc[0] == 0   # max of highs, min of lows
    assert out["Close"].iloc[0] == 13 and out["Open"].iloc[0] == 0  # last close, first open


def test_all_six_timeframes_defined_with_enough_history():
    assert list(TIMEFRAMES) == ["5m", "15m", "30m", "1h", "4h", "1d"]
    assert TIMEFRAMES["4h"].resample_to == "4h" and TIMEFRAMES["4h"].interval == "1h"
    assert all(t.lookback == 150 for t in TIMEFRAMES.values())


def test_row_builders_are_json_safe():
    r = scan.Reading("AAA", "BUY", 130.0, 100.0, float("nan"), 5.0, "2026-01-01T00:00:00+00:00")
    rows = scan.levels_rows("5m", [r], "2026-01-02T00:00:00+00:00")
    assert rows[0]["last_price"] is None and rows[0]["timeframe"] == "5m"
    assert "broker_name" not in rows[0]               # the database fills it in
    json.dumps(rows, allow_nan=False)                 # raises if NaN/inf slipped through
    assert scan.state_rows([r], "t") == [{"ticker": "AAA", "state": "BUY", "ts": "t"}]
    prices = scan.price_rows([r], {"AAA": 12.5}, "t")
    assert prices[0]["volume_1d"] == 12.5 and prices[0]["last_price"] is None


def test_daily_volumes_uses_latest_real_day_and_nulls_missing():
    idx = pd.date_range("2026-01-01", periods=3, freq="1D", tz="UTC")
    stock = pd.DataFrame({"High": 1.0, "Low": 1.0, "Close": [10.0, 11.0, np.nan], "Volume": [5.0, 7.0, 99.0]}, index=idx)
    forex = pd.DataFrame({"High": 1.0, "Low": 1.0, "Close": [1.1, 1.1, 1.1], "Volume": [0.0, 0.0, 0.0]}, index=idx)
    frame = pd.concat({"STK": stock, "FX": forex}, axis=1)
    vols = scan.daily_volumes(["STK", "FX", "NONE"], fetch=fake_fetch_factory(frame))
    assert vols == {"STK": 7.0, "FX": None, "NONE": None}   # row with NaN Close is ignored; 0 -> None


def test_load_symbols_validation(tmp_path):
    good = tmp_path / "a.csv"; good.write_text("ticker,broker_name,asset_class\nAAA,AAA,stock\nBBB,BBB,stock\n")
    assert scan.load_symbols(good) == ["AAA", "BBB"]
    dup = tmp_path / "d.csv"; dup.write_text("ticker,broker_name,asset_class\nAAA,A,stock\nAAA,A,stock\n")
    with pytest.raises(ValueError, match="duplicate"):
        scan.load_symbols(dup)
    bad = tmp_path / "b.csv"; bad.write_text("name\nAAA\n")
    with pytest.raises(ValueError, match="ticker"):
        scan.load_symbols(bad)


def test_real_symbols_csv_is_consistent():
    import csv
    rows = list(csv.DictReader(open(ROOT / "symbols.csv", newline="")))
    assert len(rows) == 200
    assert len({r["ticker"] for r in rows}) == 200
    assert len({r["broker_name"] for r in rows}) == 200
    counts = {}
    for r in rows:
        counts[r["asset_class"]] = counts.get(r["asset_class"], 0) + 1
    assert counts == {"crypto": 40, "forex": 41, "metal": 7, "energy": 2, "index": 8, "stock": 102}


def test_sql_functions_are_locked_down_and_workflow_has_guards():
    sql = (ROOT / "supabase" / "functions.sql").read_text()
    assert "revoke all on function public.apply_scan" in sql and "revoke all on function public.apply_prices" in sql
    assert "to service_role" in sql
    wf = (ROOT / ".github" / "workflows" / "scan.yml").read_text()
    for needle in ["workflow_dispatch", "timeout-minutes", "concurrency:", "permissions:", "contents: read"]:
        assert needle in wf
    # GitHub's own schedule is deliberately NOT used (comments may mention it, keys may not)
    assert not re.search(r"^\s*schedule:", wf, re.M)
