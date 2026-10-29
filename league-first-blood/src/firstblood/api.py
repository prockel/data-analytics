"""Authenticated Riot requests with bounded retries and resumable raw caching.

Never log headers, credentials, player identifiers, or raw response bodies.
The optional cache is private research data and must not be committed.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import random
import time

import requests


class APIError(RuntimeError):
    """A safe-to-display upstream error."""


class RiotClient:
    def __init__(self, key: str, cache: Path, *, attempts=6, session=None,
                 sleep=time.sleep, rng=None):
        if not key or key == "replace_with_your_riot_api_key":
            raise APIError("RIOT_API_KEY is missing. Set it securely or use an ignored .env file.")
        self.session = session or requests.Session()
        self.key = key
        self.cache = Path(cache)
        self.cache.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.attempts = attempts
        self.sleep = sleep
        self.rng = rng or random.Random()
        # Server-provided app/method windows are respected before the NEXT call.
        self.next_request = 0.0

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
            self.sleep(max(0, self.next_request - time.monotonic()))
            try:
                response = self.session.get(
                    f"https://{host}{path}", params=params,
                    headers={"X-Riot-Token": self.key}, timeout=(10, 60),
                    allow_redirects=False,
                )
            except (requests.ConnectionError, requests.Timeout):
                if attempt + 1 == self.attempts:
                    raise APIError("Riot network request exhausted retries") from None
                self.sleep(min(60, 2 ** attempt) + self.rng.random())
                continue
            if response.status_code in (401, 403):
                raise APIError("Riot rejected authentication/access (401/403); renew or authorize the key.")
            if response.status_code == 429 or 500 <= response.status_code < 600:
                if attempt + 1 == self.attempts:
                    raise APIError(f"Riot HTTP {response.status_code}: exhausted retries")
                delay = min(60, 2 ** attempt)
                if response.status_code == 429:
                    try:
                        delay = max(delay, float(response.headers.get("Retry-After", "1")))
                    except ValueError:
                        pass
                self.sleep(delay + self.rng.random())
                continue
            if response.status_code != 200:
                raise APIError(f"Riot returned HTTP {response.status_code}")
            for prefix in ("X-App-Rate-Limit", "X-Method-Rate-Limit"):
                try:
                    limits = dict((float(window), int(count)) for count, window in
                                  (x.split(":") for x in response.headers.get(prefix, "").split(",") if x))
                    for pair in response.headers.get(prefix + "-Count", "").split(","):
                        if not pair:
                            continue
                        count, window = pair.split(":")
                        if int(count) >= limits.get(float(window), float("inf")):
                            self.next_request = max(self.next_request, time.monotonic() + float(window) + 0.1)
                except (ValueError, TypeError):
                    # 429 handling remains authoritative if a header is malformed.
                    pass
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
