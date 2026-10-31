"""Tiny Supabase (PostgREST) client built on `requests`.

Why not the official supabase-py package: we only need three calls, and plain
HTTP keeps the install small and fast on every GitHub run.

Safety rules in this file:
  * every request has a timeout (a hung network call must not eat the job)
  * 5xx / 429 / network errors are retried a few times; other 4xx are not
    (retrying a bad request never helps)
  * the key is only ever put in request headers, never in error messages
"""
from __future__ import annotations

import time
from typing import Any, Callable

import requests


class SupabaseError(RuntimeError):
    """A database call failed (message never contains the key)."""


class SupabaseClient:
    BATCH = 200   # rows per request; also stays far below PostgREST size limits

    def __init__(self, url: str, key: str, session: Any = None, timeout: int = 30,
                 retries: int = 3, sleep: Callable = time.sleep):
        if not url.startswith("https://"):
            raise ValueError("SUPABASE_URL must start with https://")
        if not key:
            raise ValueError("Supabase key is empty")
        self.base = url.rstrip("/") + "/rest/v1"
        self._session = session or requests.Session()
        self._timeout = timeout
        self._retries = retries
        self._sleep = sleep
        self._headers = {"apikey": key, "Content-Type": "application/json"}
        # Legacy keys are JWTs and also go in Authorization. The newer "secret"
        # keys are NOT JWTs: sending them as a Bearer token is rejected.
        if key.startswith("eyJ"):
            self._headers["Authorization"] = f"Bearer {key}"

    def _request(self, method: str, path: str, *, params: dict | None = None,
                 body: Any = None, prefer: str | None = None) -> Any:
        headers = dict(self._headers)
        if prefer:
            headers["Prefer"] = prefer
        problem = "unknown"
        for attempt in range(1, self._retries + 1):
            try:
                resp = self._session.request(method, self.base + path, params=params, json=body,
                                             headers=headers, timeout=self._timeout)
            except requests.RequestException as exc:
                problem = f"network error ({type(exc).__name__})"
            else:
                if resp.status_code < 300:
                    return resp
                if resp.status_code < 500 and resp.status_code != 429:
                    raise SupabaseError(f"{method} {path} failed: HTTP {resp.status_code} {resp.text[:300]}")
                problem = f"HTTP {resp.status_code}"
            if attempt < self._retries:
                self._sleep(2 * attempt)
        raise SupabaseError(f"{method} {path} failed after {self._retries} tries ({problem})")

    def upsert_levels(self, rows: list[dict]) -> None:
        """Insert-or-update rows of the `levels` table (key: ticker + timeframe).
        broker_name is left out on purpose: a database trigger fills it in."""
        for start in range(0, len(rows), self.BATCH):
            self._request("POST", "/levels", params={"on_conflict": "ticker,timeframe"},
                          body=rows[start:start + self.BATCH],
                          prefer="resolution=merge-duplicates,return=minimal")

    def rpc(self, name: str, payload: dict) -> Any:
        """Call a database function (see supabase/functions.sql)."""
        return self._request("POST", f"/rpc/{name}", body=payload).json()

    def inactive_tickers(self) -> set[str]:
        """Tickers switched off in the database (symbols.active = false)."""
        resp = self._request("GET", "/symbols", params={"select": "ticker", "active": "eq.false"})
        return {row["ticker"] for row in resp.json()}
