"""Exercise macOS-compatible start/status/resume commands without live requests."""
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch

from firstblood.cli import main
from firstblood.resumable import identity
import test_resumable as fixtures


class LargeCLITests(unittest.TestCase):
    def test_start_monitor_expired_key_resume_and_completed_noop(self):
        helper = fixtures.ResumableTests()
        helper.setUp()
        self.addCleanup(helper.doCleanups)
        helper.fail_on_fifth_detail(401)
        arguments = ['collect-large', '--db', str(helper.db), '--cache', str(helper.cache),
                     '--patch', '16.20', '--current-patch', '16.21',
                     '--patch-source', helper.config['patch_source'],
                     '--window-start', '1970-01-01T00:08:20Z',
                     '--window-end', '1970-01-01T00:25:00Z', '--confirm-window',
                     '--seed', '123', '--league-pages', '1', '--players-per-group', '1',
                     '--matches-per-player', '100', '--quota', '5', '--min-matches-per-cell', '1']
        stdout, stderr = StringIO(), StringIO()
        def client_factory(key, cache, **kwargs):
            return helper.client(key)
        with patch.dict(os.environ, {'RIOT_API_KEY': 'synthetic-old'}), \
             patch('firstblood.cli.RiotClient', side_effect=client_factory), \
             patch('firstblood.resumable.fetch_versions', return_value=['16.21.1', '16.20.1']), \
             redirect_stdout(stdout), redirect_stderr(stderr):
            self.assertEqual(main(arguments), 1)
        run_id = identity(helper.config)
        self.assertIn(run_id, stdout.getvalue())
        monitor = StringIO()
        with redirect_stdout(monitor):
            self.assertEqual(main(['status', '--db', str(helper.db), '--run-id', run_id]), 0)
        report = json.loads(monitor.getvalue())
        self.assertEqual(report['status'], 'failed')
        self.assertEqual(report['audit']['progress']['accepted'], 2)
        helper.riot.fail = None
        resume = ['resume', '--db', str(helper.db), '--cache', str(helper.cache), '--run-id', run_id]
        with patch.dict(os.environ, {'RIOT_API_KEY': 'synthetic-renewed'}), \
             patch('firstblood.cli.RiotClient', side_effect=client_factory), \
             redirect_stdout(stdout):
            self.assertEqual(main(resume), 0)
            calls = helper.riot.calls.copy()
            self.assertEqual(main(resume), 0)
            self.assertEqual(helper.riot.calls, calls)
        helper.assert_finished()
        self.assertNotIn('synthetic-old', stdout.getvalue() + stderr.getvalue())
        self.assertNotIn('synthetic-renewed', stdout.getvalue() + stderr.getvalue())

    def test_status_is_read_only_and_missing_run_does_not_create_database(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'missing.sqlite'
            with redirect_stderr(StringIO()):
                self.assertEqual(main(['status', '--db', str(path), '--run-id', 'missing']), 1)
            self.assertFalse(path.exists())

    def test_resume_refuses_modified_configuration(self):
        helper = fixtures.ResumableTests()
        helper.setUp()
        self.addCleanup(helper.doCleanups)
        run_id = helper.collect()
        with sqlite3.connect(helper.db) as con:
            modified = {**helper.config, 'seed': 999}
            con.execute('UPDATE runs SET config_json=?', (json.dumps(modified, sort_keys=True),))
        with redirect_stderr(StringIO()):
            result = main(['resume', '--db', str(helper.db), '--cache', str(helper.cache), '--run-id', run_id])
        self.assertEqual(result, 1)
