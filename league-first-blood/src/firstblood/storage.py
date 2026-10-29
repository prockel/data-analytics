"""SQLite storage with explicit run membership and integrity constraints."""
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3

SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS runs (
 run_id TEXT PRIMARY KEY, started_at TEXT NOT NULL, completed_at TEXT,
 status TEXT NOT NULL, selected_patch TEXT, config_json TEXT NOT NULL,
 audit_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS matches (
 match_id TEXT PRIMARY KEY, patch TEXT NOT NULL,
 rank_group TEXT NOT NULL CHECK(rank_group IN ('Iron–Emerald','Diamond–Master','Grandmaster–Challenger')),
 role TEXT NOT NULL CHECK(role IN ('TOP','JUNGLE','MIDDLE','BOTTOM','UTILITY')),
 killer_team_id INTEGER NOT NULL CHECK(killer_team_id IN (100,200)),
 winner_team_id INTEGER NOT NULL CHECK(winner_team_id IN (100,200)),
 first_blood_win INTEGER NOT NULL CHECK(first_blood_win IN (0,1)),
 first_blood_timestamp_ms INTEGER NOT NULL CHECK(first_blood_timestamp_ms>0),
 game_start_utc INTEGER NOT NULL, duration_seconds INTEGER NOT NULL,
 CHECK(first_blood_win = (killer_team_id=winner_team_id))
);
CREATE TABLE IF NOT EXISTS run_matches (
 run_id TEXT NOT NULL REFERENCES runs(run_id),
 match_id TEXT NOT NULL REFERENCES matches(match_id),
 rank_group TEXT NOT NULL CHECK(rank_group IN ('Iron–Emerald','Diamond–Master','Grandmaster–Challenger')),
 PRIMARY KEY(run_id,match_id)
);
CREATE INDEX IF NOT EXISTS matches_patch ON matches(patch);
"""


@contextmanager
def read_database(path):
    """Read without initializing schemas or touching a pilot database."""
    con = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        yield con
    finally:
        con.close()


@contextmanager
def database(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    try:
        yield con
        con.commit()
    except BaseException:
        con.rollback()
        raise
    finally:
        con.close()


def save_match(con, run_id, row):
    columns = ",".join(row)
    con.execute(f"INSERT OR IGNORE INTO matches ({columns}) VALUES ({','.join('?' for _ in row)})", tuple(row.values()))
    existing = dict(con.execute("SELECT * FROM matches WHERE match_id=?", (row["match_id"],)).fetchone())
    # Rank is a run-specific seed-snapshot label. All game facts must agree.
    if any(existing[k] != v for k, v in row.items() if k != "rank_group"):
        raise ValueError("Cached match facts disagree with existing SQLite record")
    con.execute("INSERT OR IGNORE INTO run_matches VALUES (?,?,?)",
                (run_id, row["match_id"], row["rank_group"]))


def run_config(con, run_id):
    row = con.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
    if row is None:
        raise ValueError("Unknown run ID")
    return dict(row), json.loads(row["config_json"])
