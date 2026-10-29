"""Offline integration tests. Synthetic fixtures never enter portfolio reports."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from firstblood.analysis import analyze
from firstblood.api import APIError
from firstblood.collect import collect, completed_patches, discover_matches, discover_seeds, stable_key, validate_source
from firstblood.storage import database
from firstblood.transform import GROUPS, ROLES
from test_transform import fixture


def config():
    return dict(platform="na1", current_patch="16.21", patch_source="https://www.leagueoflegends.com/en-us/news/game-updates/",
                start_time=500, end_time=1500, seed=123, league_pages=1, players_per_group=3,
                matches_per_player=101, max_matches_per_group=10, min_matches_per_group=5,
                min_matches_per_cell=1, patch_candidates=2)


class CollectionTests(unittest.TestCase):
    def test_patch_selection_no_current_or_future(self):
        self.assertEqual(completed_patches(["16.21.1", "16.20.3", "16.20.2", "16.19.1", "16.22.1"], "16.21", 2),
                         ["16.20", "16.19"])
        with self.assertRaises(APIError):
            completed_patches(["16.21.1", "16.20.1"], "26.21", 2)
        with self.assertRaises(ValueError):
            validate_source("https://untrusted.example/patch")

    def test_discovery_dedup_anchor_is_order_independent(self):
        client = Mock()
        client.get.return_value = ["NA1_A", "NA1_B", "EUW1_MIGRATED"]
        seeds = [dict(puuid="a", group=GROUPS[0]), dict(puuid="b", group=GROUPS[1])]
        forward = discover_matches(client, seeds, config(), "run")
        backward = discover_matches(client, list(reversed(seeds)), config(), "run")
        self.assertEqual(forward, backward)
        self.assertEqual(len(forward), 2)
        self.assertEqual(forward["NA1_A"]["puuid"], min(("a", "b"), key=lambda p: stable_key(123, p)))
        self.assertEqual(client.get.call_args.args[2]["queue"], 420)

    def test_match_list_pagination(self):
        client = Mock()
        client.get.side_effect = [[f"NA1_{i}" for i in range(100)], ["NA1_100"]]
        result = discover_matches(client, [dict(puuid="a", group=GROUPS[0])], config(), "run")
        self.assertEqual(len(result), 101)
        self.assertEqual(client.get.call_args.args[2]["start"], 100)
        self.assertEqual(client.get.call_args.args[2]["count"], 1)

    def test_conflicting_ladder_snapshot_is_excluded(self):
        client = Mock()
        def get(host, path, *args, **kwargs):
            entries = [{"puuid": "moving-player"}]
            return {"entries": entries} if "leagues/by-queue" in path else entries
        client.get.side_effect = get
        seeds, counts = discover_seeds(client, config(), "run")
        self.assertEqual(seeds, [])
        self.assertEqual(sum(counts.values()), 0)

    def test_full_collection_sql_analysis_and_resume(self):
        self.run_pipeline(sufficient=True)

    def test_insufficient_data_does_not_publish_results(self):
        self.run_pipeline(sufficient=False)

    def test_fallback_to_newest_sufficient_completed_patch(self):
        self.run_pipeline(sufficient=True, fallback=True)

    def run_pipeline(self, sufficient, fallback=False):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            db = directory / "firstblood.sqlite"
            client = Mock(cache=directory / "cache")
            players = [dict(puuid="synthetic-9", group=group) for group in GROUPS]
            candidates = {f"NA1_TEST_{g}_{r}": players[g] for g in range(3) for r in range(5)}
            def get(host, path):
                mid = path.split("/")[-2 if path.endswith("/timeline") else -1]
                match, timeline = fixture()
                role = int(mid.rsplit("_", 1)[1])
                match["metadata"]["matchId"] = mid
                timeline["metadata"]["matchId"] = mid
                for player in match["info"]["participants"]:
                    player["firstBloodKill"] = player["participantId"] == role + 1
                timeline["info"]["frames"][0]["events"][0]["killerId"] = role + 1
                if not sufficient:
                    match["info"]["queueId"] = 440
                return timeline if path.endswith("/timeline") else match
            client.get.side_effect = get
            settings = config()
            versions = ["16.21.1", "16.20.1", "16.19.1"]
            if fallback:
                settings["current_patch"] = "16.22"
                versions = ["16.22.1", "16.21.1", "16.20.1"]
            with patch("firstblood.collect.discover_seeds", return_value=(players, {})), \
                 patch("firstblood.collect.discover_matches", return_value=candidates):
                run_id = collect(client, db, settings, versions=versions)
                with database(db) as con:
                    run = dict(con.execute("SELECT * FROM runs").fetchone())
                    count = con.execute("SELECT COUNT(*) FROM matches").fetchone()[0]
                    self.assertEqual(run["status"], "complete" if sufficient else "insufficient_data")
                    self.assertEqual(count, 15 if sufficient else 0)
                    audit = json.loads(run["audit_json"])
                    self.assertEqual(audit["patches"]["16.20"]["sufficient"], sufficient)
                    if sufficient:
                        self.assertEqual(run["selected_patch"], "16.20")
                        if fallback:
                            self.assertFalse(audit["patches"]["16.21"]["sufficient"])
                        sql = (Path(__file__).parents[1] / "sql/analysis.sql").read_text().split(";")[0]
                        sql_rows = con.execute(sql, {"run_id": run_id}).fetchall()
                        self.assertEqual(len(sql_rows), 15)
                        self.assertTrue(all(row["n"] == 1 and row["wins"] == 0 for row in sql_rows))
                if sufficient:
                    calls = client.get.call_count
                    self.assertEqual(collect(client, db, settings, versions=[]), run_id)
                    self.assertEqual(client.get.call_count, calls)
                    output = directory / "reports"
                    summary = analyze(db, run_id, output)
                    self.assertEqual(summary.n.sum(), 15)
                    self.assertTrue((output / "figures/win_rates.png").stat().st_size > 1000)
                    self.assertTrue((output / "collection.json").exists())
                else:
                    with self.assertRaisesRegex(ValueError, "completed"):
                        analyze(db, run_id, directory / "reports")

    def test_failure_marks_run_failed(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory)/"db.sqlite"
            client = Mock(cache=Path(directory))
            with patch("firstblood.collect.discover_seeds", side_effect=APIError("blocked")):
                with self.assertRaises(APIError):
                    collect(client, db, config(), versions=["16.21.1", "16.20.1"])
            with database(db) as con:
                self.assertEqual(con.execute("SELECT status FROM runs").fetchone()[0], "failed")
