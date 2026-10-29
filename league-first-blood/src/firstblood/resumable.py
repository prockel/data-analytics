"""Single-patch, single-worker collection with durable discovery and outcomes.

This opt-in mode uses a separate database. The legacy pilot collector is unchanged.
SQLite is the authority for manifests and progress; private raw JSON remains an
additional cache for a response downloaded just before a process interruption.
"""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import signal
import sqlite3
import time

from .api import APIError
from .collect import (ROUTES, completed_patches, discover_matches, discover_seeds,
                      fetch_versions, stable_key, utc_now, validate_source)
from .storage import SCHEMA, save_match
from .transform import Excluded, GROUPS, ROLES, patch_of, transform_match

LARGE_SCHEMA = """
CREATE TABLE IF NOT EXISTS collection_state (
 run_id TEXT PRIMARY KEY REFERENCES runs(run_id), stage TEXT NOT NULL,
 players_frozen_at TEXT, candidates_frozen_at TEXT
);
CREATE TABLE IF NOT EXISTS discovery_pages (
 run_id TEXT NOT NULL REFERENCES runs(run_id), request_key TEXT NOT NULL,
 recorded_at TEXT NOT NULL, body_json TEXT NOT NULL,
 PRIMARY KEY(run_id,request_key)
);
CREATE TABLE IF NOT EXISTS sampled_players (
 run_id TEXT NOT NULL REFERENCES runs(run_id), puuid TEXT NOT NULL,
 tier TEXT NOT NULL, rank_group TEXT NOT NULL,
 PRIMARY KEY(run_id,puuid)
);
CREATE TABLE IF NOT EXISTS candidates (
 run_id TEXT NOT NULL REFERENCES runs(run_id), match_id TEXT NOT NULL,
 anchor_puuid TEXT NOT NULL, rank_group TEXT NOT NULL, sort_key TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','accepted','excluded','quota')),
 reason TEXT, PRIMARY KEY(run_id,match_id),
 FOREIGN KEY(run_id,anchor_puuid) REFERENCES sampled_players(run_id,puuid)
);
CREATE INDEX IF NOT EXISTS candidates_pending ON candidates(run_id,state,sort_key,match_id);
CREATE INDEX IF NOT EXISTS candidates_group ON candidates(run_id,rank_group,state);
"""


@contextmanager
def large_database(path):
    """Initialize both schemas atomically, including across startup interruption."""
    con = sqlite3.connect(path)
    os.chmod(path, 0o600)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA foreign_keys=ON")
        con.executescript("BEGIN;\n" + SCHEMA + LARGE_SCHEMA + "\nCOMMIT;")
        yield con
        con.commit()
    except BaseException:
        con.rollback()
        raise
    finally:
        con.close()


def validate_config(config):
    required = {"schema_version", "platform", "patch", "current_patch", "patch_source",
                "start_time", "end_time", "window_confirmed", "seed", "league_pages",
                "players_per_group", "matches_per_player", "quota", "min_matches_per_cell"}
    if set(config) != required or config["schema_version"] != 2 or config["platform"] != "na1":
        raise ValueError("Expected a version-2, single-patch NA1 collection configuration")
    validate_source(config["patch_source"])
    for name in ("patch", "current_patch"):
        if config[name] != patch_of(config[name]):
            raise ValueError("Use internal major.minor patch identifiers")
    if tuple(map(int, config["patch"].split('.'))) >= tuple(map(int, config["current_patch"].split('.'))):
        raise ValueError("Study patch must be older than the confirmed live patch")
    if (type(config["start_time"]) is not int or type(config["end_time"]) is not int
            or not 0 <= config["start_time"] < config["end_time"] <= time.time()
            or config["window_confirmed"] is not True):
        raise ValueError("Confirm the completed-patch UTC window; start < end <= now is required")
    if type(config["seed"]) is not int:
        raise ValueError("Sampling seed must be an integer")
    for name in ("league_pages", "players_per_group", "matches_per_player", "quota", "min_matches_per_cell"):
        if type(config[name]) is not int or config[name] < 1:
            raise ValueError(f"{name} must be a positive integer")
    if config["min_matches_per_cell"] * 5 > config["quota"]:
        raise ValueError("Per-cell minimums exceed the group quota")


def identity(config):
    return hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()[:20]


def protect_existing_database(path):
    """Inspect read-only before any schema operation; never migrate a pilot DB."""
    path = Path(path).resolve()
    if not path.exists() or path.stat().st_size == 0:
        return
    con = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    try:
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if tables and ("collection_state" not in tables or "runs" not in tables):
            raise ValueError("Use a separate database for large collection; existing database is protected")
        if "runs" in tables:
            for (encoded,) in con.execute("SELECT config_json FROM runs"):
                if json.loads(encoded).get("schema_version") != 2:
                    raise ValueError("Pilot/legacy database is protected; choose a new database path")
    finally:
        con.close()


def protect_paths(db_path, cache):
    project = Path(__file__).resolve().parents[2]
    pilot_cache = project / "data/raw"
    if (Path(db_path).resolve() == project / "data/firstblood.sqlite"
            or Path(cache).resolve().is_relative_to(pilot_cache)):
        raise ValueError("Pilot database/cache paths are protected; choose separate large-run paths")
    protect_existing_database(db_path)


@contextmanager
def exclusive(path):
    """Advisory single-writer lock, released by the OS even on a hard kill."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open("a") as stream:
        os.chmod(path, 0o600)
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("Another collector owns this database or cache; use one worker") from None
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


@contextmanager
def interruptible():
    # SIGINT already raises KeyboardInterrupt; make SIGTERM equally recoverable.
    previous = signal.getsignal(signal.SIGTERM)
    def stop(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, stop)
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous)


class FrozenDiscovery:
    """Every successful discovery page survives even deletion of the raw cache."""
    def __init__(self, client, con, run_id, audit):
        self.client, self.con, self.run_id = client, con, run_id
        self.audit = audit
        self.saved_pages = con.execute("SELECT COUNT(*) FROM discovery_pages WHERE run_id=?", (run_id,)).fetchone()[0]

    def get(self, host, path, params=None, *, snapshot=""):
        key = hashlib.sha256(json.dumps([host, path, params or {}, snapshot], sort_keys=True).encode()).hexdigest()
        row = self.con.execute("SELECT body_json FROM discovery_pages WHERE run_id=? AND request_key=?",
                               (self.run_id, key)).fetchone()
        if row:
            return json.loads(row[0])
        body = self.client.get(host, path, params, snapshot=snapshot)
        with self.con:
            self.con.execute("INSERT INTO discovery_pages VALUES (?,?,?,?)",
                             (self.run_id, key, utc_now(), json.dumps(body)))
            self.saved_pages += 1
            self.audit["discovery_pages_saved"] = self.saved_pages
            self.audit["last_checkpoint_at"] = utc_now()
            update_run(self.con, self.run_id, self.audit)
        return body


def update_run(con, run_id, audit, *, status="collecting", patch=None):
    con.execute("UPDATE runs SET status=?,selected_patch=?,audit_json=?,completed_at=? WHERE run_id=?",
                (status, patch, json.dumps(audit, sort_keys=True),
                 utc_now() if status != "collecting" else None, run_id))


def rebuild_progress(con, run_id, audit, config):
    """Reconcile from committed rows once per restart, never trust process memory."""
    groups = {g: 0 for g in GROUPS}
    cells = {f"{g}/{r}": 0 for g in GROUPS for r in ROLES}
    for row in con.execute("""SELECT rm.rank_group,m.role,COUNT(*) n FROM run_matches rm
                              JOIN matches m USING(match_id) WHERE rm.run_id=?
                              GROUP BY rm.rank_group,m.role""", (run_id,)):
        groups[row["rank_group"]] += row["n"]
        cells[f"{row['rank_group']}/{row['role']}"] = row["n"]
    exclusions = dict(con.execute("SELECT reason,COUNT(*) FROM candidates WHERE run_id=? AND state='excluded' GROUP BY reason", (run_id,)))
    states = dict(con.execute("SELECT state,COUNT(*) FROM candidates WHERE run_id=? GROUP BY state", (run_id,)))
    audit["patches"] = {config["patch"]: {"eligible_by_group": groups, "eligible_by_cell": cells,
                       "exclusions": exclusions, "sufficient": False}}
    audit["progress"] = {"accepted": states.get("accepted", 0), "excluded": states.get("excluded", 0),
                         "pending": states.get("pending", 0), "quota_skipped": states.get("quota", 0)}


def collect_resumable(client, db_path, config, *, versions=None):
    validate_config(config)
    db_path = Path(db_path).resolve()
    run_id = identity(config)
    # Check before opening SQLite in write mode, even when the CLI is bypassed.
    protect_paths(db_path, client.cache)
    with exclusive(db_path.with_suffix(db_path.suffix + ".lock")), \
         exclusive(Path(client.cache).resolve() / ".collector.lock"), interruptible():
        protect_existing_database(db_path)
        with large_database(db_path) as con:
            previous = con.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
            if previous and previous["config_json"] != json.dumps(config, sort_keys=True):
                raise ValueError("Run identity/configuration mismatch")
            if previous and previous["status"] in ("complete", "insufficient_data"):
                return run_id
            with con:
                con.execute("INSERT OR IGNORE INTO runs(run_id,started_at,status,config_json) VALUES (?,?,?,?)",
                            (run_id, utc_now(), "collecting", json.dumps(config, sort_keys=True)))
                con.execute("INSERT OR IGNORE INTO collection_state(run_id,stage) VALUES (?,'ladder')", (run_id,))
            audit = json.loads(previous["audit_json"]) if previous else {}
            audit.setdefault("stage", "ladder")
            audit.pop("last_error_kind", None)
            try:
                with con:
                    update_run(con, run_id, audit)
                stage = con.execute("SELECT stage FROM collection_state WHERE run_id=?", (run_id,)).fetchone()[0]
                discovery = FrozenDiscovery(client, con, run_id, audit)
                if stage == "ladder":
                    # Only discovery needs the catalog; resume of frozen processing does not.
                    if "candidate_patches" not in audit:
                        catalog = versions if versions is not None else fetch_versions(Path(client.cache) / f"versions-{run_id}.json")
                        if config["patch"] not in completed_patches(catalog, config["current_patch"], len(catalog)):
                            raise APIError("Pinned completed patch is absent from the frozen catalog")
                        audit["candidate_patches"] = [config["patch"]]
                        with con:
                            update_run(con, run_id, audit)
                    seeds, pool_sizes = discover_seeds(discovery, config, run_id)
                    audit["seed_pool_sizes"] = dict(pool_sizes)
                    audit["sampled_players"] = {g: sum(s["group"] == g for s in seeds) for g in GROUPS}
                    if any(audit["sampled_players"][g] == 0 for g in GROUPS):
                        raise APIError("At least one rank group has no sampled players")
                    with con:
                        con.executemany("INSERT INTO sampled_players VALUES (?,?,?,?)",
                                        [(run_id, s["puuid"], s["tier"], s["group"]) for s in seeds])
                        con.execute("UPDATE collection_state SET stage='history',players_frozen_at=? WHERE run_id=?", (utc_now(), run_id))
                        audit["stage"] = "history"
                        update_run(con, run_id, audit)
                    stage = "history"
                if stage == "history":
                    seeds = [dict(puuid=r["puuid"], tier=r["tier"], group=r["rank_group"])
                             for r in con.execute("SELECT * FROM sampled_players WHERE run_id=? ORDER BY puuid", (run_id,))]
                    candidates = discover_matches(discovery, seeds, config, run_id)
                    audit["unique_candidate_matches"] = len(candidates)
                    audit["candidate_manifest_sha256"] = hashlib.sha256(json.dumps(
                        sorted((mid, stable_key(config["seed"], s["puuid"]), s["group"]) for mid, s in candidates.items())
                    ).encode()).hexdigest()
                    with con:
                        con.executemany("INSERT INTO candidates(run_id,match_id,anchor_puuid,rank_group,sort_key) VALUES (?,?,?,?,?)",
                                        [(run_id, mid, s["puuid"], s["group"], stable_key(config["seed"], mid)) for mid, s in candidates.items()])
                        con.execute("UPDATE collection_state SET stage='processing',candidates_frozen_at=? WHERE run_id=?", (utc_now(), run_id))
                        audit["stage"] = "processing"
                        update_run(con, run_id, audit)
                rebuild_progress(con, run_id, audit, config)
                with con:
                    update_run(con, run_id, audit)
                patch_audit = audit["patches"][config["patch"]]
                progress = audit["progress"]
                host = ROUTES[config["platform"]] + ".api.riotgames.com"
                while True:
                    candidate = con.execute("SELECT * FROM candidates WHERE run_id=? AND state='pending' ORDER BY sort_key,match_id LIMIT 1", (run_id,)).fetchone()
                    if candidate is None:
                        break
                    mid, group = candidate["match_id"], candidate["rank_group"]
                    try:
                        match = client.get(host, f"/lol/match/v5/matches/{mid}")
                        try:
                            if patch_of(match["info"]["gameVersion"]) != config["patch"]:
                                raise Excluded("different_patch")
                        except (KeyError, TypeError, ValueError) as exc:
                            if isinstance(exc, Excluded):
                                raise
                            raise Excluded("malformed_response") from None
                        timeline = client.get(host, f"/lol/match/v5/matches/{mid}/timeline")
                        row = transform_match(match, timeline, match_id=mid, patch=config["patch"], group=group,
                                              anchor_puuid=candidate["anchor_puuid"], start_time=config["start_time"], end_time=config["end_time"])
                        state, reason = "accepted", None
                    except Excluded as exc:
                        state, reason = "excluded", str(exc)
                    with con:
                        if state == "accepted":
                            save_match(con, run_id, row)
                            patch_audit["eligible_by_group"][group] += 1
                            patch_audit["eligible_by_cell"][f"{group}/{row['role']}"] += 1
                        else:
                            counts = patch_audit["exclusions"]
                            counts[reason] = counts.get(reason, 0) + 1
                        con.execute("UPDATE candidates SET state=?,reason=? WHERE run_id=? AND match_id=? AND state='pending'",
                                    (state, reason, run_id, mid))
                        progress[state] += 1
                        progress["pending"] -= 1
                        if patch_audit["eligible_by_group"][group] >= config["quota"]:
                            skipped = con.execute("UPDATE candidates SET state='quota' WHERE run_id=? AND rank_group=? AND state='pending'", (run_id, group)).rowcount
                            progress["quota_skipped"] += skipped
                            progress["pending"] -= skipped
                        audit["last_checkpoint_at"] = utc_now()
                        update_run(con, run_id, audit)
                sufficient = (all(n >= config["quota"] for n in patch_audit["eligible_by_group"].values())
                              and all(n >= config["min_matches_per_cell"] for n in patch_audit["eligible_by_cell"].values()))
                patch_audit["sufficient"] = sufficient
                audit["stage"] = "done"
                with con:
                    con.execute("UPDATE collection_state SET stage='done' WHERE run_id=?", (run_id,))
                    update_run(con, run_id, audit, status="complete" if sufficient else "insufficient_data", patch=config["patch"])
                return run_id
            except BaseException as exc:
                con.rollback()
                # A signal/hard failure cannot undo previously committed matches.
                # Reconcile in-memory counters before recording the failure.
                if "patches" in audit:
                    rebuild_progress(con, run_id, audit, config)
                audit["discovery_pages_saved"] = con.execute(
                    "SELECT COUNT(*) FROM discovery_pages WHERE run_id=?", (run_id,)).fetchone()[0]
                audit["stage"] = con.execute(
                    "SELECT stage FROM collection_state WHERE run_id=?", (run_id,)).fetchone()[0]
                audit["last_error_kind"] = exc.kind if isinstance(exc, APIError) else type(exc).__name__
                with con:
                    update_run(con, run_id, audit, status="interrupted" if isinstance(exc, KeyboardInterrupt) else "failed", patch=config["patch"])
                raise
