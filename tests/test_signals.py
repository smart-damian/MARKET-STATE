import random

import pandas as pd
import pytest

from levels import SymbolLevels, find_level
from signals import BUY, NEUTRAL, SELL, classify, compute_atr, find_alerts


def levels(high, low, last):
    return SymbolLevels("X", high=high, low=low, last_close=last, bars=150)


def test_find_level_picks_most_frequent_running_max():
    # newest-first highs [2,2,2,3,1] -> running max [2,2,2,3,3] -> 2 appears most
    assert find_level(pd.Series([1, 3, 2, 2, 2]), "high") == 2.0


def test_compute_atr_constant_range():
    df = pd.DataFrame({"High": [11.0] * 5, "Low": [9.0] * 5, "Close": [10.0] * 5})
    assert compute_atr(df, period=3) == pytest.approx(2.0)


def test_compute_atr_needs_enough_candles():
    df = pd.DataFrame({"High": [11.0] * 3, "Low": [9.0] * 3, "Close": [10.0] * 3})
    with pytest.raises(ValueError, match="at least 4 candles"):
        compute_atr(df, period=3)


def test_compute_atr_rejects_zero_movement():
    df = pd.DataFrame({"High": [10.0] * 6, "Low": [10.0] * 6, "Close": [10.0] * 6})
    with pytest.raises(ValueError, match="zero"):
        compute_atr(df, period=3)


def test_compute_atr_wilder_smoothing_value():
    # period 2: seed = mean(TR0, TR1) = 3; then atr = (3*1 + 5)/2 = 4
    df = pd.DataFrame({"High": [12.0, 13.0, 18.0], "Low": [9.0, 10.0, 13.0], "Close": [10.0, 11.0, 17.0]})
    # TR0 = 3, TR1 = max(3, |13-10|, |10-10|) = 3, TR2 = max(5, |18-11|=7, |13-11|=2) = 7
    assert compute_atr(df, period=2) == pytest.approx((3.0 * 1 + 7.0) / 2)


@pytest.mark.parametrize("last, expected", [
    (107.0, SELL),    # high - last = 3 <= 4
    (102.0, BUY),     # last - low = 2 <= 4
    (105.0, NEUTRAL), # 5 and 5: neither within 4
    (106.0, SELL),    # exactly 2 ATR counts (<=)
    (104.0, BUY),     # exactly 2 ATR counts (<=)
])
def test_classify_levels(last, expected):
    assert classify(levels(110.0, 100.0, last), atr=2.0) == expected


def test_classify_both_near_is_neutral():
    # range narrower than 4 ATR: near the high AND the low -> contradictory -> NEUTRAL
    r = levels(104.0, 100.0, 102.0)
    assert {a.signal for a in find_alerts(r, 2.0)} == {BUY, SELL}
    assert classify(r, 2.0) == NEUTRAL


def test_classify_without_price_is_neutral():
    assert classify(levels(110.0, 100.0, None), 2.0) == NEUTRAL


def test_classify_matches_the_written_rule_on_random_data():
    rng = random.Random(7)
    for _ in range(500):
        low = rng.uniform(1, 100)
        high = low + rng.uniform(0.1, 50)
        last = rng.uniform(low, high)
        atr = rng.uniform(0.05, 10)
        near_high = high - last <= 2 * atr
        near_low = last - low <= 2 * atr
        want = SELL if near_high and not near_low else BUY if near_low and not near_high else NEUTRAL
        assert classify(levels(high, low, last), atr) == want
