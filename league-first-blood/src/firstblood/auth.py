"""Safe, uncached authentication check using an authenticated League-v4 API.

An HTTPS proxy may substitute an opaque runtime binding only on allowed hosts.
Forward that binding unchanged; never inspect its format, print it, or assume
it is a usable raw key outside the configured network route.
"""
from datetime import datetime, timezone

from .api import APIError, RiotClient


def authentication_report(key, cache, platform, *, client_factory=RiotClient):
    report = {
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "platform": platform,
        "host": platform + ".api.riotgames.com",
        "endpoint": "/lol/league/v4/challengerleagues/by-queue/RANKED_SOLO_5x5",
        "credential_binding_present": bool(key),
        "authentication_verified": False,
        "cache_used": False,
    }
    try:
        client = client_factory(key, cache)
        body = client.get(report["host"], report["endpoint"], cache=False)
        if (not isinstance(body, dict) or body.get("queue") != "RANKED_SOLO_5x5"
                or body.get("tier") != "CHALLENGER" or not isinstance(body.get("entries"), list)):
            raise APIError("HTTP success did not contain the expected League-v4 response; authentication remains unverified.",
                           kind="unexpected_response", status_code=200)
        report.update(authentication_verified=True, outcome="authenticated", http_status=200,
                      ladder_entry_count=len(body["entries"]))
    except APIError as exc:
        report.update(outcome=exc.kind, http_status=exc.status_code,
                      message=str(exc), evidence=exc.evidence)
    return report
