"""Synthetic unit fixtures ONLY: never collected data or research evidence."""
from copy import deepcopy
import unittest

from firstblood.transform import Excluded, GROUPS, ROLES, patch_of, rank_group, transform_match


def fixture():
    participants = [dict(participantId=i+1, puuid=f"synthetic-{i}",
                         teamId=100 if i < 5 else 200, teamPosition=ROLES[i % 5],
                         win=i >= 5, firstBloodKill=i == 1) for i in range(10)]
    match = {"metadata": {"matchId": "NA1_TEST"}, "info": {
        "queueId": 420, "mapId": 11, "gameMode": "CLASSIC", "gameVersion": "16.20.123.456",
        "gameStartTimestamp": 1000000, "gameDuration": 1800, "participants": participants,
        "teams": [{"teamId": 100, "win": False}, {"teamId": 200, "win": True}]}}
    timeline = {"metadata": {"matchId": "NA1_TEST"}, "info": {"frames": [{"events": [
        {"type": "CHAMPION_KILL", "timestamp": 90000, "killerId": 2, "victimId": 7}]}]}}
    return match, timeline


def transform(match, timeline):
    return transform_match(match, timeline, match_id="NA1_TEST", patch="16.20", group=GROUPS[0],
                           anchor_puuid="synthetic-9", start_time=500, end_time=1500)


class TransformTests(unittest.TestCase):
    def test_killer_team_not_anchor_team(self):
        row = transform(*fixture())
        self.assertEqual((row["role"], row["killer_team_id"], row["winner_team_id"], row["first_blood_win"]),
                         ("JUNGLE", 100, 200, 0))

    def test_killer_winning_team(self):
        match, timeline = fixture()
        for player in match["info"]["participants"]:
            player["firstBloodKill"] = player["participantId"] == 7
        timeline["info"]["frames"][0]["events"][0].update(killerId=7, victimId=2)
        self.assertEqual(transform(match, timeline)["first_blood_win"], 1)

    def test_reject_ineligible_variants(self):
        for field, value, reason in [("queueId", 440, "not_ranked_solo_summoners_rift"),
                                     ("mapId", 12, "not_ranked_solo_summoners_rift"),
                                     ("gameVersion", "16.19.1", "different_patch"),
                                     ("gameDuration", 200, "remake_or_early_surrender"),
                                     ("gameStartTimestamp", 1500000, "outside_sampling_window")]:
            with self.subTest(field=field):
                match, timeline = fixture()
                match["info"][field] = value
                with self.assertRaisesRegex(Excluded, reason):
                    transform(match, timeline)

    def test_role_and_winner_ambiguity(self):
        for variant, reason in [("role", "ambiguous_role_assignment"), ("winner", "contradictory_winner"),
                                ("flags", "contradictory_first_blood"), ("remake", "remake_or_early_surrender")]:
            with self.subTest(variant=variant):
                match, timeline = fixture()
                player = match["info"]["participants"][1]
                if variant == "role":
                    player["teamPosition"] = ""
                if variant == "winner":
                    player["win"] = True
                if variant == "flags":
                    player["firstBloodKill"] = False
                if variant == "remake":
                    player["gameEndedInEarlySurrender"] = True
                with self.assertRaisesRegex(Excluded, reason):
                    transform(match, timeline)

    def test_event_ambiguity(self):
        for variant, reason in [("tie", "tied_first_kills"), ("environment", "environmental_or_unknown_kill"),
                                ("none", "no_champion_kill"), ("friendly", "invalid_kill_event"),
                                ("time", "invalid_kill_event")]:
            with self.subTest(variant=variant):
                match, timeline = fixture()
                events = timeline["info"]["frames"][0]["events"]
                if variant == "tie":
                    events.append(deepcopy(events[0]))
                if variant == "environment":
                    events[0]["killerId"] = 0
                if variant == "none":
                    events.clear()
                if variant == "friendly":
                    events[0]["victimId"] = 3
                if variant == "time":
                    events[0]["timestamp"] = 1900000
                with self.assertRaisesRegex(Excluded, reason):
                    transform(match, timeline)

    def test_sort_events_by_time(self):
        match, timeline = fixture()
        timeline["info"]["frames"][0]["events"].insert(0, {
            "type": "CHAMPION_KILL", "timestamp": 200000, "killerId": 4, "victimId": 7})
        self.assertEqual(transform(match, timeline)["first_blood_timestamp_ms"], 90000)

    def test_id_and_malformed_data(self):
        match, timeline = fixture()
        timeline["metadata"]["matchId"] = "NA1_OTHER"
        with self.assertRaisesRegex(Excluded, "id_mismatch"):
            transform(match, timeline)
        with self.assertRaisesRegex(Excluded, "malformed_response"):
            transform({}, {})

    def test_rank_boundaries_and_patch(self):
        for tier, expected in [("IRON", GROUPS[0]), ("EMERALD", GROUPS[0]), ("DIAMOND", GROUPS[1]),
                               ("MASTER", GROUPS[1]), ("GRANDMASTER", GROUPS[2]), ("CHALLENGER", GROUPS[2])]:
            self.assertEqual(rank_group(tier), expected)
        with self.assertRaises(ValueError):
            rank_group("UNRANKED")
        self.assertEqual(patch_of("16.20.123.456"), "16.20")
        with self.assertRaises(ValueError):
            patch_of("16.2bad")
