"""Command-line collection and analysis. Run from league-first-blood/."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import sys

from dotenv import load_dotenv

from .api import APIError, RiotClient
from .collect import ROUTES, collect, validate_source
from .storage import database, read_database, run_config
from .transform import patch_of


def timestamp(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError
        return int(parsed.timestamp())
    except ValueError:
        raise argparse.ArgumentTypeError("Use ISO-8601 with timezone, e.g. 2026-10-29T00:00:00Z") from None


def positive(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("Must be at least 1")
    return number


def parser():
    root = argparse.ArgumentParser(description=__doc__)
    subs = root.add_subparsers(dest="command", required=True)
    auth = subs.add_parser("auth-check", help="Verify a live HTTPS credential binding without exposing it")
    auth.add_argument("--platform", choices=ROUTES, default="na1")
    auth.add_argument("--cache", type=Path, default=Path("data/raw"))
    auth.add_argument("--output", type=Path, help="Write sanitized diagnostic JSON (no response body or key)")
    c = subs.add_parser("collect", help="Collect a versioned official Riot API sample")
    c.add_argument("--db", type=Path, default=Path("data/firstblood.sqlite"))
    c.add_argument("--cache", type=Path, default=Path("data/raw"))
    c.add_argument("--platform", choices=ROUTES, default="na1")
    c.add_argument("--current-patch", required=True, help="Confirmed currently live major.minor; never select this patch")
    c.add_argument("--patch-source", required=True, help="Official Riot patch announcement/schedule URL you checked")
    c.add_argument("--as-of", type=timestamp, help="Freeze sampling cutoff in UTC; print and reuse to resume")
    c.add_argument("--lookback-days", type=positive, default=42)
    c.add_argument("--patch-candidates", type=positive, default=3)
    c.add_argument("--seed", type=int, default=20261028)
    c.add_argument("--league-pages", type=positive, default=1)
    c.add_argument("--players-per-group", type=positive, default=30)
    c.add_argument("--matches-per-player", type=positive, default=300)
    c.add_argument("--max-matches-per-group", type=positive, default=1000)
    c.add_argument("--min-matches-per-group", type=positive, default=100)
    c.add_argument("--min-matches-per-cell", type=positive, default=10)
    large = subs.add_parser("collect-large", help="Start a resumable, explicitly pinned NA1 patch study")
    large.add_argument("--db", type=Path, required=True)
    large.add_argument("--cache", type=Path, required=True)
    large.add_argument("--patch", required=True)
    large.add_argument("--current-patch", required=True)
    large.add_argument("--patch-source", required=True)
    large.add_argument("--window-start", type=timestamp, required=True)
    large.add_argument("--window-end", type=timestamp, required=True)
    large.add_argument("--confirm-window", action="store_true", required=True,
                       help="Confirm NA1 patch rollout dates and the completed-patch history window")
    large.add_argument("--seed", type=int, default=20261028)
    large.add_argument("--league-pages", type=positive, default=2)
    large.add_argument("--players-per-group", type=positive, default=1000)
    large.add_argument("--matches-per-player", type=positive, default=200)
    large.add_argument("--quota", type=positive, default=1667)
    large.add_argument("--min-matches-per-cell", type=positive, default=10)
    resume = subs.add_parser("resume", help="Resume a version-2 run from its saved configuration")
    resume.add_argument("--db", type=Path, required=True)
    resume.add_argument("--cache", type=Path, required=True)
    resume.add_argument("--run-id", required=True)
    for name in ("analyze", "status"):
        a = subs.add_parser(name)
        a.add_argument("--db", type=Path, default=Path("data/firstblood.sqlite"))
        a.add_argument("--run-id", required=True)
        if name == "analyze":
            a.add_argument("--output", type=Path, default=Path("reports"))
    return root


def main(argv=None):
    args = parser().parse_args(argv)
    # Explicit local path, with process environment taking precedence.
    load_dotenv(Path.cwd() / ".env", override=False)
    try:
        if args.command == "auth-check":
            from .auth import authentication_report
            report = authentication_report(os.environ.get("RIOT_API_KEY", ""), args.cache, args.platform)
            rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(rendered)
            print(rendered, end="")
            return 0 if report["authentication_verified"] else 1
        elif args.command in ("collect-large", "resume"):
            from .resumable import collect_resumable, identity, protect_paths, validate_config
            protect_paths(args.db, args.cache)
            if args.command == "collect-large":
                config = {key: getattr(args, key) for key in (
                    "patch", "current_patch", "patch_source", "seed", "league_pages",
                    "players_per_group", "matches_per_player", "quota", "min_matches_per_cell")}
                config.update(schema_version=2, platform="na1", start_time=args.window_start,
                              end_time=args.window_end, window_confirmed=args.confirm_window)
            else:
                if not args.db.exists():
                    raise ValueError("Resume database does not exist")
                with read_database(args.db) as con:
                    _, config = run_config(con, args.run_id)
                if identity(config) != args.run_id:
                    raise ValueError("Saved configuration does not match the requested run ID")
            validate_config(config)
            print("Run ID:", identity(config), flush=True)
            client = RiotClient(os.environ.get("RIOT_API_KEY", ""), args.cache,
                                state_path=args.cache / "rate-limit.json")
            run_id = collect_resumable(client, args.db, config)
            with read_database(args.db) as con:
                run, _ = run_config(con, run_id)
            print("Collection status:", run["status"])
            return 0 if run["status"] == "complete" else 2
        elif args.command == "collect":
            validate_source(args.patch_source)
            if args.current_patch != patch_of(args.current_patch):
                raise ValueError("--current-patch must be major.minor")
            if args.min_matches_per_group > args.max_matches_per_group or args.min_matches_per_cell * 5 > args.max_matches_per_group:
                raise ValueError("Sampling minimums exceed the per-group cap")
            end = args.as_of if args.as_of is not None else int(datetime.now(timezone.utc).timestamp())
            if end > int(datetime.now(timezone.utc).timestamp()):
                raise ValueError("--as-of cannot be in the future")
            config = {key: getattr(args, key) for key in (
                "platform", "current_patch", "patch_source", "lookback_days", "patch_candidates", "seed",
                "league_pages", "players_per_group", "matches_per_player", "max_matches_per_group",
                "min_matches_per_group", "min_matches_per_cell")}
            config.update(start_time=end - args.lookback_days * 86400, end_time=end, schema_version=1)
            print("Sampling cutoff (reuse --as-of to resume):", datetime.fromtimestamp(end, timezone.utc).isoformat())
            client = RiotClient(os.environ.get("RIOT_API_KEY", ""), args.cache)
            run_id = collect(client, args.db, config)
            print("Run ID:", run_id)
            with database(args.db) as con:
                run, _ = run_config(con, run_id)
            print("Collection status:", run["status"])
            if run["status"] != "complete":
                print("No patch met all configured minimums. Inspect status/audit, widen the sampling frame and start a new run.")
                return 2
        elif args.command == "analyze":
            from .analysis import analyze
            summary = analyze(args.db, args.run_id, args.output)
            print(f"Wrote {len(summary)} role/group estimates and charts to {args.output}")
        else:
            with read_database(args.db) as con:
                run, config = run_config(con, args.run_id)
            run["config"] = config
            del run["config_json"]
            run["audit"] = json.loads(run.pop("audit_json"))
            print(json.dumps(run, indent=2))
    except KeyboardInterrupt:
        print("Interrupted. Saved checkpoints remain available; resume using the printed run ID.", file=sys.stderr)
        return 130
    except sqlite3.Error:
        print("SQLite operation failed. Keep the database and cache; inspect storage before resuming.", file=sys.stderr)
        return 1
    except (APIError, ValueError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
