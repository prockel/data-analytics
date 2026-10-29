"""Authenticated Riot requests with bounded retries and resumable raw caching.

Never log headers, credentials, player identifiers, or raw response bodies.
The optional cache is private research data and must not be committed.
"""
from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import json
import math
import os
from pathlib import Path
import random
import time

import requests


class APIError(RuntimeError):
    """A safe-to-display upstream error."""

    def __init__(self, message, *, kind="api_error", status_code=None, evidence=None):
        super().__init__(message)
        self.kind = kind
        self.status_code = status_code
        # Only explicitly selected booleans, never headers or response bodies.
        self.evidence = evidence or {}


class RiotClient:
    def __init__(self, key: str, cache: Path, *, attempts=6, session=None,
                 sleep=time.sleep, rng=None, state_path=None):
        if not key or key == "replace_with_your_riot_api_key":
            raise APIError(
                "RIOT_API_KEY has no runtime binding. Configure the HTTPS secret's "
                "target variable or use an ignored local .env; never expose its value.",
                kind="credential_binding_missing",
            )
        self.session = session or requests.Session()
        self.key = key
        self.cache = Path(cache)
        self.cache.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.attempts = attempts
        self.sleep = sleep
        self.rng = rng or random.Random()
        # Server-provided app/method windows are respected before the NEXT call.
        self.next_request = 0.0
        self.not_before = 0.0
        self.longest_window = 120.0
        self.state_path = Path(state_path) if state_path is not None else None
        if self.state_path:
            if self.state_path.exists():
                try:
                    state = json.loads(self.state_path.read_text())
                    self.not_before = float(state["not_before_utc"])
                    self.longest_window = float(state["longest_window_seconds"])
                    if not math.isfinite(self.not_before) or not math.isfinite(self.longest_window) or self.longest_window <= 0:
                        raise ValueError
                except (ValueError, KeyError, TypeError):
                    raise APIError("Invalid saved rate-limit state; restore it before resuming") from None
            else:
                # Cold starts have no counters from other clients: be conservative.
                self.not_before = time.time() + self.longest_window

    def _save_rate_state(self):
        if self.state_path:
            self.state_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            temporary = self.state_path.with_suffix(".tmp")
            with temporary.open("w") as stream:
                os.chmod(temporary, 0o600)
                json.dump({"not_before_utc": max(self.not_before, time.time() + self.longest_window),
                           "longest_window_seconds": self.longest_window}, stream)
            temporary.replace(self.state_path)

    def _cooldown(self, seconds):
        self.next_request = max(self.next_request, time.monotonic() + seconds)
        self.not_before = max(self.not_before, time.time() + seconds)
        self._save_rate_state()

    @staticmethod
    def _retry_after(value):
        try:
            seconds = float(value)
        except (ValueError, TypeError):
            try:
                seconds = parsedate_to_datetime(value).timestamp() - time.time()
            except (ValueError, TypeError, OverflowError):
                return 0.0
        return max(0.0, seconds) if math.isfinite(seconds) else 0.0

    def _observe_limits(self, response):
        # Observe both application and method windows, including error responses.
        for prefix in ("X-App-Rate-Limit", "X-Method-Rate-Limit"):
            try:
                limits = dict((float(window), int(count)) for count, window in
                              (x.split(":") for x in response.headers.get(prefix, "").split(",") if x))
                limits = {window: count for window, count in limits.items()
                          if math.isfinite(window) and window > 0 and count > 0}
                if limits:
                    self.longest_window = max(self.longest_window, max(limits))
                for pair in response.headers.get(prefix + "-Count", "").split(","):
                    if not pair:
                        continue
                    count, window = pair.split(":")
                    if int(count) >= limits.get(float(window), float("inf")):
                        self._cooldown(float(window) + 0.1)
            except (ValueError, TypeError):
                pass  # 429 handling remains authoritative.
        # On restart, wait a full known window since the last request. This is
        # deliberately conservative, not a way to reset an application's quota.
        self._save_rate_state()

    def get(self, host, path, params=None, *, snapshot="", cache=True):
        if host not in {"na1.api.riotgames.com", "euw1.api.riotgames.com",
                        "kr.api.riotgames.com", "americas.api.riotgames.com",
                        "europe.api.riotgames.com", "asia.api.riotgames.com"}:
            raise ValueError("Unsupported Riot routing host")
        identity = json.dumps([host, path, params or {}, snapshot], sort_keys=True)
        filename = self.cache / (hashlib.sha256(identity.encode()).hexdigest() + ".json")
        if cache and filename.exists():
            return json.loads(filename.read_text())["body"]
        for attempt in range(self.attempts):
            self.sleep(max(0, self.next_request - time.monotonic(), self.not_before - time.time()))
            # Also guard a restart if the process dies during the HTTP request.
            self._save_rate_state()
            try:
                response = self.session.get(
                    f"https://{host}{path}", params=params,
                    headers={"X-Riot-Token": self.key}, timeout=(10, 60),
                    allow_redirects=False,
                )
            except (requests.ConnectionError, requests.Timeout):
                self._cooldown(min(60, 2 ** attempt) + self.rng.random())
                if attempt + 1 == self.attempts:
                    raise APIError("Riot network request exhausted retries") from None
                continue
            except requests.RequestException:
                raise APIError("HTTPS request setup failed; check local request configuration") from None
            self._observe_limits(response)
            if response.status_code in (401, 403):
                try:
                    error_body = response.json()
                    riot_error = (isinstance(error_body, dict)
                                  and isinstance(error_body.get("status"), dict)
                                  and error_body["status"].get("status_code") == response.status_code)
                except ValueError:
                    riot_error = False
                if riot_error:
                    raise APIError(
                        "Riot rejected authentication/access (401/403); renew or authorize the key.",
                        kind="riot_authentication_or_access_denied", status_code=response.status_code,
                    )
                content_type = response.headers.get("Content-Type", "").lower()
                text = response.text.lower() if isinstance(response.text, str) else ""
                cloudflare = "sorry, you have been blocked" in text and "cloudflare" in text
                raise APIError(
                    f"HTTPS upstream returned non-Riot HTTP {response.status_code}"
                    + (" (Cloudflare block page)" if cloudflare else "")
                    + ". Authentication could not be verified. Resolve upstream/egress access "
                      "before treating the key as invalid.",
                    kind="upstream_access_denied", status_code=response.status_code,
                    evidence={"html_response": "text/html" in content_type,
                              "cloudflare_block_page": cloudflare,
                              "upstream_error_header_present": "x-at-upstream-error" in response.headers},
                )
            if response.status_code == 429 or 500 <= response.status_code < 600:
                delay = max(min(60, 2 ** attempt), self._retry_after(response.headers.get("Retry-After")))
                self._cooldown(delay + self.rng.random())
                if attempt + 1 == self.attempts:
                    raise APIError(f"Riot HTTP {response.status_code}: exhausted retries",
                                   kind="retry_exhausted", status_code=response.status_code)
                continue
            if response.status_code != 200:
                raise APIError(f"Riot returned HTTP {response.status_code}")
            try:
                body = response.json()
            except ValueError:
                raise APIError("Riot returned invalid JSON") from None
            if cache:
                temporary = filename.with_suffix(".tmp")
                with temporary.open("w", encoding="utf-8") as stream:
                    os.chmod(temporary, 0o600)
                    json.dump({"fetched_at_utc": datetime.now(timezone.utc).isoformat(), "body": body}, stream)
                temporary.replace(filename)
            return body
        raise APIError("No request attempts configured")
