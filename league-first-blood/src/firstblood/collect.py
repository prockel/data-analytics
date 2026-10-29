"""Deterministic, bounded solo-queue sampling from official Riot endpoints."""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlparse

import requests

from .api import APIError
from .storage import database, save_match
from .transform import Excluded, GROUPS, ROLES, TIERS, patch_of, rank_group, transform_match

ROUTES = {"na1": "americas", "euw1": "europe", "kr": "asia"}


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def stable_key(seed, value):
    return hashlib.sha256(f"{seed}:{value}".encode()).hexdigest()


def completed_patches(versions, current_patch, limit):
    """DDragon enumerates versions; operator confirms the live patch separately.

    Asset publication is not a trustworthy timestamp of regional rollout.
    Only versions strictly older than the confirmed live patch can be chosen.
    """
    current = tuple(map(int, patch_of(current_patch).split(".")))
    patches = {
        patch_of(v)
        for v in versions
        if isinstance(v, str) and re.fullmatch(r"\d+\.\d+(?:\.\d+)*", v)
    }
    if patch_of(current_patch) not in patches:
        raise APIError("Confirmed live patch is absent from Data Dragon. Use the internal gameVersion major.minor, not the marketing season label.")
    return sorted((p for p in patches if tuple(map(int, p.split("."))) < current),
                  key=lambda p: tuple(map(int, p.split("."))), reverse=True)[:limit]


def validate_source(source):
    parsed = urlparse(source)
    if parsed.scheme != "https" or parsed.hostname not in {
        "www.leagueoflegends.com", "leagueoflegends.com", "support-leagueoflegends.riotgames.com",
        "www.riotgames.com", "riotgames.com"} or parsed.username or parsed.password:
        raise ValueError("Patch confirmation must cite an official Riot HTTPS page")


def fetch_versions(path):
    """Cache the catalog once per run so resume uses the same candidate set."""
    path = Path(path)
    if path.exists():
        return json.loads(path.read_text())
    try:
        response = requests.get("https://ddragon.leagueoflegends.com/api/versions.json", timeout=(10, 30))
        response.raise_for_status()
        versions = response.json()
    except (requests.RequestException, ValueError):
        raise APIError("Unable to load the official Data Dragon version catalog") from None
    if not isinstance(versions, list) or not versions:
        raise APIError("Invalid Data Dragon version catalog")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(versions))
    return versions


def discover_seeds(client, config, run_id):
    host = config["platform"] + ".api.riotgames.com"
    by_player = {}
    for tier in TIERS:
        if tier in {"MASTER", "GRANDMASTER", "CHALLENGER"}:
            path = f"/lol/league/v4/{tier.lower()}leagues/by-queue/RANKED_SOLO_5x5"
            batches = [client.get(host, path, snapshot=run_id)["entries"]]
        else:
            batches = []
            for division in ("I", "II", "III", "IV"):
                for page in range(1, config["league_pages"] + 1):
                    entries = client.get(host, f"/lol/league/v4/entries/RANKED_SOLO_5x5/{tier}/{division}",
                                         {"page": page}, snapshot=run_id)
                    batches.append(entries)
                    if not entries:
                        break
        for batch in batches:
            for entry in batch:
                puuid = entry.get("puuid")
                if not puuid:
                    raise APIError("League response lacks PUUID; verify current League-v4 API/key support. No guessed rank mapping used.")
                candidate = {"puuid": puuid, "tier": tier, "group": rank_group(tier)}
                if puuid in by_player and by_player[puuid]["tier"] != tier:
                    # A ladder move during a multi-request snapshot is ambiguous.
                    by_player[puuid] = {"puuid": puuid, "tier": "CONFLICT", "group": None}
                else:
                    by_player[puuid] = candidate
    seeds = []
    for group in GROUPS:
        pool = [s for s in by_player.values() if s["group"] == group]
        pool.sort(key=lambda s: stable_key(config["seed"], s["puuid"]))
        seeds.extend(pool[:config["players_per_group"]])
    return seeds, Counter(s["group"] for s in by_player.values() if s["group"])


def discover_matches(client, seeds, config, run_id):
    """Resolve shared games to one anchor before looking at outcomes/roles."""
    host = ROUTES[config["platform"]] + ".api.riotgames.com"
    candidates = {}
    for player in seeds:
        for offset in range(0, config["matches_per_player"], 100):
            count = min(100, config["matches_per_player"] - offset)
            ids = client.get(host, f'/lol/match/v5/matches/by-puuid/{player["puuid"]}/ids',
                             {"queue": 420, "type": "ranked", "startTime": config["start_time"],
                              "endTime": config["end_time"], "start": offset, "count": count},
                             snapshot=run_id)
            for match_id in ids:
                if not isinstance(match_id, str) or not match_id.startswith(config["platform"].upper() + "_"):
                    # A migrated player's history can include other platforms.
                    continue
                old = candidates.get(match_id)
                if old is None or stable_key(config["seed"], player["puuid"]) < stable_key(config["seed"], old["puuid"]):
                    candidates[match_id] = player
            if len(ids) < count:
                break
    return candidates


def collect(client, db_path, config, *, versions=None):
    """Resume is idempotent for the same config/as-of and retained raw cache.

    A completed run's manifest, roster snapshot, and responses must be retained
    for exact replay; refetching a live ladder later is a new sample.
    """
    serialized = json.dumps(config, sort_keys=True)
    run_id = hashlib.sha256(serialized.encode()).hexdigest()[:20]
    with database(db_path) as con:
        previous = con.execute("SELECT status FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if previous and previous["status"] == "complete":
            return run_id
        con.execute("INSERT OR IGNORE INTO runs(run_id,started_at,status,config_json) VALUES (?,?,?,?)",
                    (run_id, utc_now(), "collecting", serialized))
        con.execute("UPDATE runs SET status='collecting',completed_at=NULL WHERE run_id=?", (run_id,))
        con.commit()
        audit = {}
        try:
            versions = versions if versions is not None else fetch_versions(client.cache / f"versions-{run_id}.json")
            patches = completed_patches(versions, config["current_patch"], config["patch_candidates"])
            audit["candidate_patches"] = patches
            if not patches:
                raise APIError("No completed candidate patches found")
            seeds, pool_sizes = discover_seeds(client, config, run_id)
            audit["seed_pool_sizes"] = dict(pool_sizes)
            audit["sampled_players"] = dict(Counter(s["group"] for s in seeds))
            if any(audit["sampled_players"].get(g, 0) == 0 for g in GROUPS):
                raise APIError("At least one rank group has no usable sampled players")
            candidates = discover_matches(client, seeds, config, run_id)
            audit["unique_candidate_matches"] = len(candidates)
            audit["candidate_manifest_sha256"] = hashlib.sha256(json.dumps(
                sorted((mid, stable_key(config["seed"], s["puuid"]), s["group"]) for mid, s in candidates.items())
            ).encode()).hexdigest()
            ordered = sorted(candidates, key=lambda mid: stable_key(config["seed"], mid))
            host = ROUTES[config["platform"]] + ".api.riotgames.com"
            audit["patches"] = {}
            selected_rows = []
            for patch in patches:
                rows, exclusions = [], Counter()
                totals, cells = Counter(), Counter()
                for mid in ordered:
                    anchor = candidates[mid]
                    group = anchor["group"]
                    if totals[group] >= config["max_matches_per_group"]:
                        continue
                    match = client.get(host, f"/lol/match/v5/matches/{mid}")
                    try:
                        if patch_of(match["info"]["gameVersion"]) != patch:
                            exclusions["different_patch"] += 1
                            continue
                    except (KeyError, ValueError, TypeError):
                        exclusions["malformed_response"] += 1
                        continue
                    timeline = client.get(host, f"/lol/match/v5/matches/{mid}/timeline")
                    try:
                        row = transform_match(match, timeline, match_id=mid, patch=patch,
                                              group=group, anchor_puuid=anchor["puuid"],
                                              start_time=config["start_time"], end_time=config["end_time"])
                    except Excluded as exc:
                        exclusions[str(exc)] += 1
                        continue
                    rows.append(row)
                    totals[group] += 1
                    cells[(group, row["role"])] += 1
                sufficient = (all(totals[g] >= config["min_matches_per_group"] for g in GROUPS)
                              and all(cells[g, r] >= config["min_matches_per_cell"] for g in GROUPS for r in ROLES))
                audit["patches"][patch] = {
                    "eligible_by_group": dict(totals),
                    "eligible_by_cell": {f"{g}/{r}": cells[g, r] for g in GROUPS for r in ROLES},
                    "exclusions": dict(exclusions), "sufficient": sufficient,
                }
                if sufficient:
                    selected_rows = rows
                    break
            con.execute("DELETE FROM run_matches WHERE run_id=?", (run_id,))
            for row in selected_rows:
                save_match(con, run_id, row)
            selected_patch = selected_rows[0]["patch"] if selected_rows else None
            con.execute("UPDATE runs SET status=?,selected_patch=?,completed_at=?,audit_json=? WHERE run_id=?",
                        ("complete" if selected_rows else "insufficient_data", selected_patch, utc_now(),
                         json.dumps(audit, sort_keys=True), run_id))
            con.commit()
        except Exception:
            con.rollback()
            con.execute("UPDATE runs SET status='failed',completed_at=?,audit_json=? WHERE run_id=?",
                        (utc_now(), json.dumps(audit, sort_keys=True), run_id))
            con.commit()
            raise
    return run_id
