"""Pure validation: one eligible match becomes one observation, or a reason."""
from __future__ import annotations

import re

ROLES = ("TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY")
GROUPS = ("Iron–Emerald", "Diamond–Master", "Grandmaster–Challenger")
TIERS = ("IRON", "BRONZE", "SILVER", "GOLD", "PLATINUM", "EMERALD", "DIAMOND", "MASTER", "GRANDMASTER", "CHALLENGER")


def rank_group(tier):
    if tier not in TIERS:
        raise ValueError("Unknown ranked tier")
    return GROUPS[0 if TIERS.index(tier) < 6 else 1 if TIERS.index(tier) < 8 else 2]


def patch_of(version):
    match = re.fullmatch(r"(\d+)\.(\d+)(?:\.\d+)*", str(version))
    if not match:
        raise ValueError("Expected numeric major.minor patch/version")
    return f"{int(match[1])}.{int(match[2])}"


class Excluded(ValueError):
    """Stable machine-readable exclusion reason."""


def transform_match(match, timeline, *, match_id, patch, group, anchor_puuid,
                    start_time, end_time):
    """Use timeline's earliest CHAMPION_KILL, cross-checked with killer flags.

    Reject tied first events, environmental kills, remakes, missing role/team
    assignments, malformed rosters and contradictory firstBloodKill flags.
    Never substitute seed player's team or assists for the killer's team.
    """
    try:
        info = match["info"]
        if match["metadata"]["matchId"] != match_id or timeline["metadata"]["matchId"] != match_id:
            raise Excluded("id_mismatch")
        if info["queueId"] != 420 or info["mapId"] != 11 or info.get("gameMode") != "CLASSIC":
            raise Excluded("not_ranked_solo_summoners_rift")
        if patch_of(info["gameVersion"]) != patch:
            raise Excluded("different_patch")
        started = int(info["gameStartTimestamp"]) // 1000
        if not start_time <= started < end_time:
            raise Excluded("outside_sampling_window")
        duration = int(info["gameDuration"])
        players = info["participants"]
        if duration < 300 or any(p.get("gameEndedInEarlySurrender", False) for p in players):
            raise Excluded("remake_or_early_surrender")
        if len(players) != 10 or {p["participantId"] for p in players} != set(range(1, 11)):
            raise Excluded("invalid_roster")
        if anchor_puuid not in {p["puuid"] for p in players}:
            raise Excluded("anchor_not_in_match")
        for team_id in (100, 200):
            team = [p for p in players if p["teamId"] == team_id]
            if len(team) != 5 or sorted(p["teamPosition"] for p in team) != sorted(ROLES):
                raise Excluded("ambiguous_role_assignment")
        teams = info["teams"]
        if len(teams) != 2 or {t["teamId"] for t in teams} != {100, 200}:
            raise Excluded("invalid_teams")
        if any(type(t["win"]) is not bool for t in teams) or sum(t["win"] for t in teams) != 1:
            raise Excluded("invalid_winner")
        winner = next(t["teamId"] for t in teams if t["win"])
        if any(type(p["win"]) is not bool or p["win"] != (p["teamId"] == winner) for p in players):
            raise Excluded("contradictory_winner")
        kills = [e for frame in timeline["info"]["frames"] for e in frame["events"]
                 if e["type"] == "CHAMPION_KILL"]
        if not kills:
            raise Excluded("no_champion_kill")
        first_timestamp = min(e["timestamp"] for e in kills)
        first = [e for e in kills if e["timestamp"] == first_timestamp]
        if len(first) != 1:
            raise Excluded("tied_first_kills")
        event = first[0]
        by_id = {p["participantId"]: p for p in players}
        if event["killerId"] not in by_id or event["victimId"] not in by_id:
            raise Excluded("environmental_or_unknown_kill")
        killer, victim = by_id[event["killerId"]], by_id[event["victimId"]]
        if killer["teamId"] == victim["teamId"] or not 0 < first_timestamp <= duration * 1000:
            raise Excluded("invalid_kill_event")
        flagged = [p["participantId"] for p in players if p["firstBloodKill"] is True]
        if flagged != [killer["participantId"]]:
            raise Excluded("contradictory_first_blood")
        if group not in GROUPS:
            raise Excluded("unknown_rank_group")
        return dict(match_id=match_id, patch=patch, rank_group=group,
                    role=killer["teamPosition"], killer_team_id=killer["teamId"],
                    winner_team_id=winner, first_blood_win=int(killer["teamId"] == winner),
                    first_blood_timestamp_ms=first_timestamp, game_start_utc=started,
                    duration_seconds=duration)
    except Excluded:
        raise
    except (KeyError, TypeError, ValueError, StopIteration):
        raise Excluded("malformed_response") from None
