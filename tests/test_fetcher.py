import pandas as pd
import pytest

import fetcher
from helpers import buy_pattern, make_frame


def ok_frame(tickers):
    return make_frame({t: buy_pattern(10) for t in tickers}, n=10)


def test_fetch_chunks_the_symbols():
    calls = []
    def download(chunk, period, interval):
        calls.append(list(chunk)); return ok_frame(chunk)
    frames = fetcher.fetch_ohlc(["a", "b", "c", "d", "e"], "5d", "5m", chunk_size=2, download=download, sleep=lambda s: None)
    assert calls == [["a", "b"], ["c", "d"], ["e"]] and len(frames) == 3


def test_fetch_retries_then_succeeds():
    attempts, waits = [], []
    def download(chunk, period, interval):
        attempts.append(1)
        if len(attempts) < 3:
            raise RuntimeError("boom")
        return ok_frame(chunk)
    frames = fetcher.fetch_ohlc(["a"], "5d", "5m", download=download, sleep=waits.append)
    assert len(frames) == 1 and len(attempts) == 3 and waits == [2, 4]


def test_fetch_skips_a_dead_chunk_but_keeps_the_rest(capsys):
    def download(chunk, period, interval):
        if "bad" in chunk:
            return pd.DataFrame()
        return ok_frame(chunk)
    frames = fetcher.fetch_ohlc(["good", "bad"], "5d", "5m", chunk_size=1, download=download, sleep=lambda s: None)
    assert len(frames) == 1
    assert "failed after 3 tries" in capsys.readouterr().err


def test_fetch_raises_when_everything_fails():
    with pytest.raises(fetcher.FetchError):
        fetcher.fetch_ohlc(["a"], "5d", "5m", download=lambda c, p, i: pd.DataFrame(), sleep=lambda s: None)


def test_extract_symbol_searches_all_chunks_and_handles_missing():
    f1, f2 = ok_frame(["a"]), ok_frame(["b"])
    assert fetcher.extract_symbol([f1, f2], "b") is not None
    assert fetcher.extract_symbol([f1, f2], "zzz") is None
