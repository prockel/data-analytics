-- Bind :run_id to a completed run. The run's label overrides the first-ever
-- stored label: a player's ladder snapshot can change between collection runs.
-- One row per game, never a row for each of its ten participants.
SELECT rm.rank_group, m.role,
       COUNT(*) AS n, SUM(m.first_blood_win) AS wins,
       ROUND(100.0 * AVG(m.first_blood_win), 2) AS win_rate_percent
FROM matches AS m
JOIN run_matches AS rm USING (match_id)
JOIN runs AS r ON r.run_id = rm.run_id
WHERE rm.run_id = :run_id AND r.status = 'complete'
  AND m.patch = r.selected_patch
GROUP BY rm.rank_group, m.role;

-- Check whether role frequency differs between the rank-group samples.
SELECT rm.rank_group, m.role, COUNT(*) AS first_blood_count,
       ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (PARTITION BY rm.rank_group), 2)
         AS share_within_group_percent
FROM matches AS m JOIN run_matches AS rm USING (match_id)
WHERE rm.run_id = :run_id
GROUP BY rm.rank_group, m.role;

-- Composition diagnostics: very short games / collection-window coverage.
SELECT rm.rank_group, COUNT(*) AS games,
       MIN(datetime(m.game_start_utc,'unixepoch')) AS first_game_utc,
       MAX(datetime(m.game_start_utc,'unixepoch')) AS last_game_utc,
       ROUND(AVG(m.duration_seconds)/60.0,2) AS mean_minutes,
       SUM(m.duration_seconds < 900) AS games_under_15_minutes
FROM matches AS m JOIN run_matches AS rm USING (match_id)
WHERE rm.run_id = :run_id
GROUP BY rm.rank_group;

-- Integrity audit: this query must return zero.
SELECT COUNT(*) AS invalid_outcomes
FROM matches
WHERE first_blood_win <> (killer_team_id = winner_team_id);
