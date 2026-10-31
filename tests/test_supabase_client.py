import pytest
import requests

from supabase_client import SupabaseClient, SupabaseError

URL = "https://example.supabase.co"


class Resp:
    def __init__(self, status=200, data=None, text=""):
        self.status_code, self._data, self.text = status, data, text
    def json(self):
        return self._data


class Session:
    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []
    def request(self, method, url, **kw):
        self.calls.append((method, url, kw))
        r = self.responses.pop(0) if self.responses else Resp()
        if isinstance(r, Exception):
            raise r
        return r


def client(session, key="sb_secret_abc", **kw):
    return SupabaseClient(URL, key, session=session, sleep=lambda s: None, **kw)


def test_upsert_levels_batches_and_headers():
    s = Session()
    client(s).upsert_levels([{"ticker": str(i)} for i in range(450)])
    assert len(s.calls) == 3
    method, url, kw = s.calls[0]
    assert method == "POST" and url == URL + "/rest/v1/levels"
    assert kw["params"] == {"on_conflict": "ticker,timeframe"} and kw["timeout"] == 30
    assert kw["headers"]["Prefer"] == "resolution=merge-duplicates,return=minimal"
    assert len(kw["json"]) == 200 and len(s.calls[2][2]["json"]) == 50


def test_new_style_secret_key_is_not_sent_as_bearer_token():
    s = Session(); client(s, key="sb_secret_abc").rpc("apply_prices", {"payload": []})
    assert s.calls[0][2]["headers"]["apikey"] == "sb_secret_abc"
    assert "Authorization" not in s.calls[0][2]["headers"]


def test_legacy_jwt_key_also_goes_in_authorization():
    s = Session(); client(s, key="eyJabc.def.ghi").rpc("apply_prices", {"payload": []})
    assert s.calls[0][2]["headers"]["Authorization"] == "Bearer eyJabc.def.ghi"


def test_client_error_is_not_retried_and_never_leaks_the_key():
    s = Session(Resp(401, text="Invalid API key"))
    with pytest.raises(SupabaseError) as exc:
        client(s, key="sb_secret_TOPSECRET").rpc("apply_scan", {})
    assert len(s.calls) == 1 and "401" in str(exc.value) and "TOPSECRET" not in str(exc.value)


def test_server_error_is_retried_then_succeeds():
    s = Session(Resp(503), Resp(200, data=5))
    assert client(s).rpc("apply_scan", {}) == 5 and len(s.calls) == 2


def test_network_errors_retry_then_fail_cleanly():
    s = Session(*[requests.ConnectionError("x")] * 3)
    with pytest.raises(SupabaseError, match="after 3 tries"):
        client(s).rpc("apply_scan", {})
    assert len(s.calls) == 3


def test_inactive_tickers():
    s = Session(Resp(200, data=[{"ticker": "AAA"}, {"ticker": "BBB"}]))
    assert client(s).inactive_tickers() == {"AAA", "BBB"}
    assert s.calls[0][2]["params"] == {"select": "ticker", "active": "eq.false"}


def test_url_must_be_https():
    with pytest.raises(ValueError):
        SupabaseClient("http://example.com", "k")
