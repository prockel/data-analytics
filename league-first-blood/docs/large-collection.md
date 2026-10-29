# Resumable single-patch collection on macOS

This is an **opt-in, separate study**, targeting 1,667 eligible matches per anchor
rank group (5,001 total). It has not been launched by this implementation task.
The original 137-match pilot, `data/firstblood.sqlite` if present locally, and all
committed pilot reports remain unchanged. Use `collect-large`, not the legacy
`collect` command, for the new checkpoint behavior.

## What is fixed before processing

The run ID is a hash of the complete version-2 configuration: the pinned study
patch, confirmed live patch/source, explicit UTC history bounds, seed, ladder
coverage, player/history limits, and group/role thresholds. Credentials and local
paths are not part of this hash. Resume reads these settings from SQLite; it
cannot silently change the window, player sample, patch, or quota.

Discovery first records each successful ladder/history page in SQLite. Once
ladder discovery finishes, it freezes the sampled PUUIDs, tiers, and groups.
Once history discovery finishes, it freezes every unique candidate match ID,
deterministic anchor assignment, and seeded processing order, with a manifest
hash and freeze timestamps. **No matches are processed before both manifests are
frozen.** Frozen pages and manifests are not refetched on restart.

An interruption during discovery retains completed pages; unrequested pages are
retrieved later. This is a collection interval, not an instantaneous historical
ladder snapshot. Recorded page times and the raw cache document that limitation.
Promotions, demotions, API retention, and pagination can affect pages not yet
obtained. After a manifest is frozen, it is never rebuilt to add new players.

Each accepted match, its run membership, candidate outcome, exclusion counters,
and progress checkpoint are committed in one SQLite transaction. Excluded games
also get one durable outcome, so they cannot be counted twice. Group quotas mark
remaining candidates in that group as uninspected `quota` skips, not exclusions.
An insufficient sample retains accepted matches and exclusions but cannot be
analyzed as a completed study. Completed and insufficient runs are terminal;
restarting them makes no API requests and does not expand their sampling frame.

## 1. Prepare locally

The examples use Bash to avoid differences between macOS's default Zsh and Bash:

```bash
bash
cd /path/to/data-analytics/league-first-blood
python3.12 -m venv .venv
source .venv/bin/activate
source scripts/env.sh
python -m pip install -r requirements.txt
python -m pip install --no-deps -e .
python -m unittest discover -s tests -v
```

Use an existing secure `RIOT_API_KEY` binding or ignored `.env`. If creating a new
local file, copy `.env.example` **only if `.env` does not already exist**, edit it
privately, and run `chmod 600 .env`. Never put the key in commands, arguments,
logs, Git, or a notebook. An exported variable takes precedence over `.env`; renew
its secure source if that is how you authenticate. Proxy bindings are forwarded
unchanged, without attempting to extract their underlying secret.

```bash
LARGE_DB='data/large-na1/study.sqlite'
LARGE_CACHE='data/large-na1/raw'
mkdir -p data/large-na1
chmod 700 data/large-na1
firstblood auth-check --platform na1 --cache "$LARGE_CACHE"
```

Authentication must succeed before launching. Do not write the new authentication
output over `reports/authentication.json`, which belongs to the pilot history.

## 2. Confirm the patch and history window, then start

Check the [official patch notes](https://www.leagueoflegends.com/en-us/news/tags/patch-notes/)
and regional rollout information for **NA1**, and the
[internal version catalog](https://ddragon.leagueoflegends.com/api/versions.json).
Identify one fully completed internal patch, the currently live internal patch,
and a UTC history interval covering the completed patch. Allow a documented
rollout buffer; exact `gameVersion` filtering still excludes other patches.
The end is exclusive. The CLI rejects future or reversed bounds and a study
patch that is not older than the confirmed live version.

Do not reuse the pilot's old confirmation or guess dates from asset publication.
`--confirm-window` records an explicit operator confirmation; the software cannot
establish the meaning of a patch-notes page or infer regional deployment times.
The URL and exact bounds are saved in the run configuration.

The following prompts contain no credential values. Enter the verified values
before executing the start command:

```bash
read -r -p 'Completed internal study patch (major.minor): ' STUDY_PATCH
read -r -p 'Confirmed live internal patch (major.minor): ' LIVE_PATCH
read -r -p 'Official Riot patch confirmation HTTPS URL: ' PATCH_SOURCE
read -r -p 'Confirmed window start (ISO-8601 UTC, ending Z): ' PATCH_WINDOW_START
read -r -p 'Confirmed window end (ISO-8601 UTC, ending Z): ' PATCH_WINDOW_END

caffeinate -i firstblood collect-large \
  --db "$LARGE_DB" --cache "$LARGE_CACHE" \
  --patch "$STUDY_PATCH" --current-patch "$LIVE_PATCH" \
  --patch-source "$PATCH_SOURCE" \
  --window-start "$PATCH_WINDOW_START" --window-end "$PATCH_WINDOW_END" \
  --confirm-window --seed 20261028 \
  --league-pages 2 --players-per-group 1000 --matches-per-player 200 \
  --quota 1667 --min-matches-per-cell 10
```

The command prints the run ID **before discovery**. Save it. If stopped before
initial database/run creation finishes, repeat this same start command; there is
no saved run to resume yet, and its unchanged settings produce the same ID. `caffeinate -i`
reduces idle-sleep interruptions; keep the laptop open, connected, and powered.
Only one worker may use the database/cache. Never run another collector or API
job concurrently with the same Riot application key, including in another folder.

This configuration samples **up to** 1,000 players per group; apex availability,
shared games, inactive players, and history truncation may prevent filling a
quota. Each player's 200-history cap can truncate a busy player's patch history.
There is no guarantee of 5,001 eligible matches or equal role frequencies. Do not
alter the sample after observing outcomes to force a desired result.

## 3. Monitor and stop

In a second terminal, enter the project directory, activate the same environment,
and set the database path and the printed run ID:

```bash
source .venv/bin/activate
source scripts/env.sh
LARGE_DB='data/large-na1/study.sqlite'
read -r -p 'Printed run ID: ' RUN_ID
firstblood status --db "$LARGE_DB" --run-id "$RUN_ID"
```

Monitoring is read-only and makes no Riot requests. During processing,
`audit.progress` reports accepted/excluded/pending/quota-skipped candidates;
`audit.patches` reports group/role counts and exclusion reasons. `audit.stage`
shows `ladder`, `history`, `processing`, or `done`. During discovery,
`audit.discovery_pages_saved` and `audit.last_checkpoint_at` update after each
saved page; processing counters appear once discovery finishes.
The CLI can be idle during a rate-limit wait without being stuck.

Press **Ctrl-C once** in the collection terminal to stop cleanly. SIGTERM is also
handled. An abrupt kill or power loss may leave status `collecting`; after the old
process is gone, resume normally. The OS releases its file locks. SQLite rolls
back an unfinished transaction, while earlier checkpoints remain committed.

## 4. Resume, including after key expiration

Retain **both** the SQLite database and the entire private raw-cache directory,
including `rate-limit.json`. Back up the database while the collector is stopped
(or use SQLite's backup facility); do not copy it mid-transaction as an ordinary
file. Discovery pages and finished results are durable in SQLite; raw responses
also avoid repeat downloads for a match interrupted between download and commit.
If the cache is lost, finished results remain, but an unfinished match may need
to be downloaded again. Never delete rate-limit state to shorten a cooldown.

After a Riot JSON 401/403, renew the key through its existing secure source or
edit the ignored `.env` privately. Do not change the run configuration or store
the key in SQLite. An upstream HTML denial requires network/access diagnosis
rather than an assumption that the key expired.

```bash
# In Bash, from the project directory with the environment activated:
LARGE_DB='data/large-na1/study.sqlite'
LARGE_CACHE='data/large-na1/raw'
read -r -p 'Original run ID: ' RUN_ID
caffeinate -i firstblood resume \
  --db "$LARGE_DB" --cache "$LARGE_CACHE" --run-id "$RUN_ID"
```

Resume loads the original settings and continues only pending work. It does not
rerun completed matches or double-count exclusions. API requests use the renewed
runtime binding; the binding is excluded from cache identities and run hashes.
A failed request is left pending, not treated as a data exclusion.

The collector honors both application and method limits and numeric or HTTP-date
`Retry-After` values. It saves cooldowns even when the final retry fails. A fresh
rate-state file starts with a conservative 120-second wait; after a restart it
waits through the full longest known window since the last request, plus any
longer saved cooldown. This is intentionally slower than optimistic quota reuse.
429/5xx/timeouts use bounded retries; persistent failures leave a resumable run.
No key rotation, extra workers, or alternate routes are used to bypass limits.

Exit codes: **0** completed, **2** fixed sample exhausted/insufficient,
**1** failure requiring diagnosis/resume, **130** interrupted. An insufficient
run retains data but is not silently extended; a different configuration is a
separate study. Per-cell minimums are feasibility checks, not a power calculation.

## 5. Analyze only after completion

```bash
firstblood analyze --db "$LARGE_DB" --run-id "$RUN_ID" \
  --output "reports/large-na1-$RUN_ID"
```

The analysis refuses incomplete runs and output directories belonging to another
run. Never point it at the pilot report directory. The same first-blood/role
validation, anchor-current-rank definition, Wilson intervals, and exploratory
statistical caveats apply. Raw player identifiers and discovery pages stay in
ignored private storage; publish only reviewed aggregate reports and charts.
