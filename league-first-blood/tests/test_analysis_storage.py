import math
from pathlib import Path
import sqlite3
import tempfile
import unittest

import pandas as pd

from firstblood.analysis import interaction_contrasts, summarize, wilson
from firstblood.storage import database, save_match
from firstblood.transform import GROUPS, ROLES
from test_transform import fixture, transform


class AnalysisStorageTests(unittest.TestCase):
    def test_wilson_known_values(self):
        lo, hi = wilson(50, 100)
        self.assertAlmostEqual(lo, 0.4038315304)
        self.assertAlmostEqual(hi, 0.5961684696)
        self.assertAlmostEqual(wilson(0, 10)[0], 0)
        self.assertAlmostEqual(wilson(10, 10)[1], 1)
        self.assertTrue(math.isnan(wilson(0, 0)[0]))
        with self.assertRaises(ValueError):
            wilson(11, 10)

    def test_empty_cells_and_contrasts(self):
        frame = pd.DataFrame([dict(rank_group=GROUPS[0], role="TOP", first_blood_win=1)])
        summary = summarize(frame)
        self.assertEqual(len(summary), 15)
        self.assertEqual(summary.n.sum(), 1)
        self.assertEqual(summary.win_rate.isna().sum(), 14)
        self.assertTrue(interaction_contrasts(summary).difference_in_risk_differences.isna().all())

    def test_interaction_difference_in_differences(self):
        rows = []
        for group in GROUPS:
            for role in ROLES:
                wins = 8 if (group == GROUPS[1] and role == "JUNGLE") else 5
                rows.extend(dict(rank_group=group, role=role, first_blood_win=int(i < wins)) for i in range(10))
        contrasts = interaction_contrasts(summarize(pd.DataFrame(rows)))
        cell = contrasts[(contrasts.rank_group == GROUPS[1]) & (contrasts.role == "JUNGLE")].iloc[0]
        self.assertAlmostEqual(cell.difference_in_risk_differences, 0.3)

    def test_dedup_run_labels_and_integrity(self):
        with tempfile.TemporaryDirectory() as directory, database(Path(directory)/"db.sqlite") as con:
            for run in ("a", "b"):
                con.execute("INSERT INTO runs(run_id,started_at,status,config_json) VALUES (?, 'test', 'collecting', '{}')", (run,))
            row = transform(*fixture())
            save_match(con, "a", row)
            save_match(con, "a", row)
            row["rank_group"] = GROUPS[1]
            save_match(con, "b", row)
            self.assertEqual(con.execute("SELECT COUNT(*) FROM matches").fetchone()[0], 1)
            self.assertEqual(con.execute("SELECT COUNT(*) FROM run_matches").fetchone()[0], 2)
            self.assertEqual(con.execute("SELECT rank_group FROM run_matches WHERE run_id='b'").fetchone()[0], GROUPS[1])
            with self.assertRaises(sqlite3.IntegrityError):
                con.execute("UPDATE matches SET first_blood_win=1")
