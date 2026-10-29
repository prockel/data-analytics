"""Offline failure/restart tests. All records are synthetic temporary fixtures."""
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.parse import urlsplit

from firstblood.analysis import analyze
from firstblood.api import APIError, RiotClient
from firstblood.collect import stable_key
from firstblood.resumable import collect_resumable, exclusive, identity, validate_config
from firstblood.storage import database
from firstblood.transform import GROUPS
from test_transform import fixture


def settings():
    return dict(schema_version=2, platform="na1", patch="16.20", current_patch="16.21",
                patch_source="https://www.leagueoflegends.com/en-us/news/game-updates/",
                start_time=500, end_time=1500, window_confirmed=True, seed=123,
                league_pages=1, players_per_group=1, matches_per_player=100,
                quota=5, min_matches_per_cell=1)


class SyntheticRiot:
    def __init__(self):
        self.bodies = {}
        self.ids = {g: [] for g in range(3)}
        self.calls = Counter()
        self.credentials = []
        self.fail = None
        self.details = 0
        for group in range(3):
            for role in range(5):
                self.add(f"NA1_SYN_{group}_{role}", group, role)
        # Two exclusions that are sorted before all valid candidates, so they
        # are definitely checkpointed before later simulated failures.
        smallest = min(stable_key(123, mid) for mid in self.bodies)
        for kind in ("patch", "role"):
            index = 0
            while stable_key(123, f"NA1_BAD_{kind}_{index}") >= smallest:
                index += 1
            mid = f"NA1_BAD_{kind}_{index}"
            self.add(mid, 0, 0)
            match, _ = self.bodies[mid]
            if kind == "patch":
                match["info"]["gameVersion"] = "16.19.1"
            else:
                match["info"]["participants"][0]["teamPosition"] = ""

    def add(self, mid, group, role):
        match, timeline = fixture()
        match["metadata"]["matchId"] = timeline["metadata"]["matchId"] = mid
        match["info"]["participants"][9]["puuid"] = f"anchor-{group}"
        for player in match["info"]["participants"]:
            player["firstBloodKill"] = player["participantId"] == role + 1
        timeline["info"]["frames"][0]["events"][0]["killerId"] = role + 1
        self.bodies[mid] = match, timeline
        self.ids[group].append(mid)

    def get(self, url, *, params=None, headers=None, **kwargs):
        path = urlsplit(url).path
        key = (path, json.dumps(params, sort_keys=True))
        self.calls[key] += 1
        self.credentials.append(headers["X-Riot-Token"])
        if self.fail:
            outcome = self.fail(path)
            if isinstance(outcome, BaseException):
                raise outcome
            if outcome == 401:
                return Mock(status_code=401, headers={}, text="", json=Mock(return_value={"status": {"status_code": 401}}))
        if "/league/v4/" in path:
            entries = []
            if path.endswith("/IRON/I"):
                entries = [{"puuid": "anchor-0"}]
            if path.endswith("/DIAMOND/I"):
                entries = [{"puuid": "anchor-1"}]
            if "/challengerleagues/" in path:
                entries = [{"puuid": "anchor-2"}]
            body = {"entries": entries} if "leagues/by-queue" in path else entries
        elif "/by-puuid/" in path:
            group = int(path.split('/')[-2].split('-')[-1])
            body = self.ids[group][params["start"]:params["start"]+params["count"]]
        else:
            mid = path.split('/')[-2 if path.endswith('/timeline') else -1]
            body = self.bodies[mid][1 if path.endswith('/timeline') else 0]
        return Mock(status_code=200, headers={}, text="", json=Mock(return_value=deepcopy(body)))


class ResumableTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = self.root / 'large.sqlite'
        self.cache = self.root / 'raw'
        self.config = settings()
        self.riot = SyntheticRiot()

    def client(self, key="synthetic-old-binding"):
        return RiotClient(key, self.cache, session=Mock(get=Mock(side_effect=self.riot.get)), sleep=lambda _: None)

    def collect(self, key="synthetic-old-binding"):
        return collect_resumable(self.client(key), self.db, self.config, versions=["16.21.1", "16.20.1"])

    def snapshot(self):
        with sqlite3.connect(self.db) as con:
            con.row_factory = sqlite3.Row
            run = dict(con.execute("SELECT * FROM runs").fetchone())
            candidates = [tuple(r) for r in con.execute("SELECT match_id,anchor_puuid,rank_group,sort_key,state,reason FROM candidates ORDER BY sort_key,match_id")]
            players = [tuple(r) for r in con.execute("SELECT puuid,tier,rank_group FROM sampled_players ORDER BY puuid")]
            matches = con.execute("SELECT COUNT(*) FROM run_matches").fetchone()[0]
        return run, candidates, players, matches

    def fail_on_fifth_detail(self, exception):
        seen = 0
        def fail(path):
            nonlocal seen
            if '/matches/' in path and not path.endswith('/timeline') and '/by-puuid/' not in path:
                seen += 1
                if seen == 5:
                    return exception
        self.riot.fail = fail

    def assert_finished(self):
        run, candidates, players, matches = self.snapshot()
        self.assertEqual(run['status'], 'complete')
        self.assertEqual(matches, 15)
        self.assertEqual(len(players), 3)
        self.assertEqual(Counter(c[4] for c in candidates), {'accepted': 15, 'excluded': 2})
        audit = json.loads(run['audit_json'])
        self.assertEqual(audit['patches']['16.20']['exclusions'], {'different_patch': 1, 'ambiguous_role_assignment': 1})
        self.assertEqual(audit['progress'], {'accepted': 15, 'excluded': 2, 'pending': 0, 'quota_skipped': 0})
        self.assertEqual(audit['patches']['16.20']['eligible_by_group'], dict.fromkeys(GROUPS, 5))

    def test_keyboard_interrupt_checkpoints_and_idempotent_restart(self):
        self.fail_on_fifth_detail(KeyboardInterrupt())
        with self.assertRaises(KeyboardInterrupt):
            self.collect()
        run, candidates, players, matches = self.snapshot()
        self.assertEqual(run['status'], 'interrupted')
        self.assertEqual(matches, 2)
        frozen = [c[:4] for c in candidates]
        calls = self.riot.calls.copy()
        self.riot.fail = None
        self.assertEqual(self.collect(), identity(self.config))
        self.assert_finished()
        self.assertEqual(frozen, [c[:4] for c in self.snapshot()[1]])
        self.assertEqual(players, self.snapshot()[2])
        for key, count in calls.items():
            if '/league/' in key[0] or '/by-puuid/' in key[0]:
                self.assertEqual(self.riot.calls[key], count)
        completed = self.snapshot()
        calls = self.riot.calls.copy()
        self.collect()
        self.assertEqual(self.riot.calls, calls)
        self.assertEqual(self.snapshot(), completed)

    def test_expired_credentials_resume_with_new_binding_same_run(self):
        self.fail_on_fifth_detail(401)
        with self.assertRaises(APIError):
            self.collect()
        self.assertEqual(self.snapshot()[0]['status'], 'failed')
        self.assertEqual(self.snapshot()[3], 2)
        self.riot.fail = None
        self.assertEqual(self.collect('synthetic-renewed-binding'), identity(self.config))
        self.assert_finished()
        self.assertIn('synthetic-renewed-binding', self.riot.credentials)
        self.assertNotIn('synthetic-old-binding', self.snapshot()[0]['config_json'])

    def test_transient_failures_exhaust_retries_then_resume_without_losing_progress(self):
        import requests
        seen, failed_path = 0, None
        def fail(path):
            nonlocal seen, failed_path
            if '/matches/' in path and '/by-puuid/' not in path and not path.endswith('/timeline'):
                seen += 1
                if seen == 5:
                    failed_path = path
                if path == failed_path:
                    return requests.Timeout('synthetic temporary failure')
        self.riot.fail = fail
        with self.assertRaisesRegex(APIError, 'exhausted retries'):
            self.collect()
        self.assertEqual(self.snapshot()[3], 2)
        self.assertEqual(self.riot.calls[(failed_path, 'null')], 6)
        self.riot.fail = None
        self.collect()
        self.assert_finished()

    def test_sigterm_is_checkpointed_and_handler_is_restored(self):
        import signal
        original = signal.getsignal(signal.SIGTERM)
        def fail(path):
            signal.raise_signal(signal.SIGTERM)
        self.riot.fail = fail
        with self.assertRaises(KeyboardInterrupt):
            self.collect()
        self.assertEqual(self.snapshot()[0]['status'], 'interrupted')
        self.assertEqual(signal.getsignal(signal.SIGTERM), original)
        self.riot.fail = None
        self.collect()
        self.assert_finished()

    def test_stale_collecting_status_does_not_prevent_recovery(self):
        self.fail_on_fifth_detail(KeyboardInterrupt())
        with self.assertRaises(KeyboardInterrupt):
            self.collect()
        # A hard kill cannot run the exception handler and leaves this status.
        with sqlite3.connect(self.db) as con:
            con.execute("UPDATE runs SET status='collecting',completed_at=NULL")
        self.riot.fail = None
        self.collect()
        self.assert_finished()

    def test_cached_detail_not_refetched_after_timeline_interruption(self):
        failed_path = None
        def fail(path):
            nonlocal failed_path
            if path.endswith('/timeline') and failed_path is None:
                failed_path = path
                return KeyboardInterrupt()
        self.riot.fail = fail
        with self.assertRaises(KeyboardInterrupt):
            self.collect()
        detail_key = (failed_path.removesuffix('/timeline'), 'null')
        self.assertEqual(self.riot.calls[detail_key], 1)
        self.riot.fail = None
        self.collect()
        self.assertEqual(self.riot.calls[detail_key], 1)
        self.assert_finished()

    def test_transaction_rolls_back_result_but_keeps_cached_responses(self):
        from firstblood.storage import save_match as real_save
        def interrupted_save(con, run_id, row):
            real_save(con, run_id, row)
            raise KeyboardInterrupt
        with patch('firstblood.resumable.save_match', side_effect=interrupted_save):
            with self.assertRaises(KeyboardInterrupt):
                self.collect()
        self.assertEqual(self.snapshot()[3], 0)
        completed_http = {k: n for k, n in self.riot.calls.items() if '/matches/' in k[0] and '/by-puuid/' not in k[0]}
        self.collect()
        self.assert_finished()
        for key, count in completed_http.items():
            self.assertEqual(self.riot.calls[key], count)

    def test_discovery_page_checkpoints_survive_cache_loss(self):
        count = 0
        def fail(path):
            nonlocal count
            if '/league/v4/' in path:
                count += 1
                if count == 6:
                    return KeyboardInterrupt()
        self.riot.fail = fail
        with self.assertRaises(KeyboardInterrupt):
            self.collect()
        old = self.riot.calls.copy()
        self.assertEqual(json.loads(self.snapshot()[0]['audit_json'])['discovery_pages_saved'], 5)
        for path in self.cache.glob('*.json'):
            path.unlink()
        self.riot.fail = None
        self.collect()
        self.assert_finished()
        for key, count in list(old.items())[:5]:
            self.assertEqual(self.riot.calls[key], count)

    def test_discovery_transaction_rollback_reconciles_progress(self):
        from firstblood.resumable import update_run as real_update
        def fail(con, run_id, audit, **kwargs):
            if audit.get('discovery_pages_saved') == 1 and kwargs.get('status', 'collecting') == 'collecting':
                raise KeyboardInterrupt
            real_update(con, run_id, audit, **kwargs)
        with patch('firstblood.resumable.update_run', side_effect=fail):
            with self.assertRaises(KeyboardInterrupt):
                self.collect()
        self.assertEqual(json.loads(self.snapshot()[0]['audit_json'])['discovery_pages_saved'], 0)
        first_request = next(iter(self.riot.calls))
        self.collect()
        self.assertEqual(self.riot.calls[first_request], 1)
        self.assert_finished()

    def test_history_failure_keeps_frozen_players_and_completed_pages(self):
        count = 0
        def fail(path):
            nonlocal count
            if '/by-puuid/' in path:
                count += 1
                if count == 2:
                    return KeyboardInterrupt()
        self.riot.fail = fail
        with self.assertRaises(KeyboardInterrupt):
            self.collect()
        players = self.snapshot()[2]
        calls = self.riot.calls.copy()
        self.riot.fail = None
        self.collect()
        self.assertEqual(players, self.snapshot()[2])
        for key, count in calls.items():
            if '/league/' in key[0]:
                self.assertEqual(self.riot.calls[key], count)
        self.assert_finished()

    def test_insufficient_run_keeps_matches_and_is_a_noop_on_restart(self):
        self.config['quota'] = 6
        self.collect()
        self.assertEqual(self.snapshot()[0]['status'], 'insufficient_data')
        self.assertEqual(self.snapshot()[3], 15)
        calls = self.riot.calls.copy()
        self.collect()
        self.assertEqual(self.riot.calls, calls)
        with self.assertRaises(ValueError):
            analyze(self.db, identity(self.config), self.root/'reports')

    def test_quotas_skip_unneeded_requests(self):
        for i in range(10):
            self.riot.add(f'NA1_EXTRA_{i}', 0, i % 5)
        self.collect()
        run, candidates, _, matches = self.snapshot()
        self.assertEqual(matches, 15)
        skipped = [c[0] for c in candidates if c[4] == 'quota']
        self.assertGreaterEqual(len(skipped), 10)
        for mid in skipped:
            self.assertFalse(any(mid in key[0] for key in self.riot.calls))

    def test_existing_pilot_database_and_reports_are_protected(self):
        with database(self.db) as con:
            con.execute("INSERT INTO runs(run_id,started_at,status,config_json) VALUES ('pilot','date','complete',?)", (json.dumps({'schema_version': 1}),))
        digest = hashlib.sha256(self.db.read_bytes()).hexdigest()
        with self.assertRaisesRegex(ValueError, 'protected'):
            self.collect()
        self.assertEqual(hashlib.sha256(self.db.read_bytes()).hexdigest(), digest)
        output = self.root/'reports'
        output.mkdir()
        (output/'collection.json').write_text(json.dumps({'run_id': 'pilot'}))
        (output/'findings.md').write_text('original pilot')
        with self.assertRaisesRegex(ValueError, 'another run'):
            analyze(self.db, 'new-run', output)
        self.assertEqual((output/'findings.md').read_text(), 'original pilot')
        self.assertFalse(self.riot.calls)

    def test_exclusive_lock_rejects_second_worker(self):
        with exclusive(self.db.with_suffix('.sqlite.lock')):
            with self.assertRaisesRegex(ValueError, 'Another collector'):
                self.collect()
        self.assertFalse(self.riot.calls)

    def test_config_requires_explicit_completed_window_and_allows_1667_quota(self):
        self.config['quota'] = 1667
        validate_config(self.config)
        for field, value in [('window_confirmed', False), ('patch', '16.21'),
                             ('start_time', 1500), ('platform', 'euw1')]:
            bad = {**self.config, field: value}
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_config(bad)
