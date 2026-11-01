import sys
from pathlib import Path

import pytest

import scan
from supabase_client import SupabaseError

ROOT = Path(__file__).resolve().parent.parent


class FakeClient:
    """Stands in for SupabaseClient: records every call, talks to nobody."""
    instances = []

    def __init__(self, url, key):
        self.calls = []
        type(self).instances.append(self)

    def inactive_tickers(self):
        return set()

    def upsert_levels(self, rows):
        self.calls.append(("levels", len(rows)))

    def rpc(self, name, payload):
        self.calls.append((name, payload))
        return {"apply_scan": len(payload.get("payload", [])), "prune_signal_events": 7}.get(name, 0)


class PruneFailsClient(FakeClient):
    instances = []

    def rpc(self, name, payload):
        if name == "prune_signal_events":
            raise SupabaseError("rpc/prune_signal_events failed: HTTP 404 function not found")
        return super().rpc(name, payload)


def run_main(monkeypatch, tmp_path, tf, *extra, client=FakeClient):
    csv_file = tmp_path / "s.csv"
    csv_file.write_text("ticker,broker_name,asset_class\nAAA,AAA,stock\n")
    reading = scan.Reading("AAA", "BUY", 130.0, 100.0, 103.0, 5.0, "2026-01-01T00:00:00+00:00")
    monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "sb_secret_test")
    monkeypatch.setattr(scan, "SupabaseClient", client)
    monkeypatch.setattr(scan, "run_scan", lambda *a, **k: ([reading], []))
    monkeypatch.setattr(scan, "daily_volumes", lambda *a, **k: {"AAA": 1.0})
    monkeypatch.setattr(sys, "argv", ["scan.py", tf, "--symbols-file", str(csv_file), *extra])
    code = scan.main()
    return code, client.instances[-1].calls


def test_prune_old_events_returns_the_count():
    assert scan.prune_old_events(FakeClient("u", "k"), 30) == 7


def test_prune_old_events_failure_is_only_a_warning(capsys):
    assert scan.prune_old_events(PruneFailsClient("u", "k"), 30) is None
    assert "could not prune" in capsys.readouterr().err


def test_daily_run_prunes_with_default_30_days(monkeypatch, tmp_path):
    code, calls = run_main(monkeypatch, tmp_path, "1d")
    assert code == 0
    assert ("prune_signal_events", {"keep_days": 30}) in calls


def test_keep_days_option_is_passed_through(monkeypatch, tmp_path):
    _, calls = run_main(monkeypatch, tmp_path, "1d", "--keep-days", "7")
    assert ("prune_signal_events", {"keep_days": 7}) in calls


def test_other_timeframes_never_prune(monkeypatch, tmp_path):
    for tf in ["5m", "15m", "30m", "1h", "4h"]:
        _, calls = run_main(monkeypatch, tmp_path, tf)
        assert not any(c[0] == "prune_signal_events" for c in calls), tf


def test_a_failed_prune_does_not_fail_the_scan(monkeypatch, tmp_path, capsys):
    code, calls = run_main(monkeypatch, tmp_path, "1d", client=PruneFailsClient)
    assert code == 0                                   # data was saved; cleanup can retry tomorrow
    assert any(c[0] == "apply_scan" for c in calls)
    assert "could not prune" in capsys.readouterr().err


def test_keep_days_must_be_positive(monkeypatch, tmp_path):
    with pytest.raises(SystemExit):
        run_main(monkeypatch, tmp_path, "1d", "--keep-days", "0")


def test_prune_sql_is_locked_down_and_limits_realtime_to_inserts():
    sql = (ROOT / "supabase" / "prune.sql").read_text()
    assert "revoke all on function public.prune_signal_events" in sql
    assert "to service_role" in sql
    assert "publish = 'insert'" in sql and "make_interval" in sql
