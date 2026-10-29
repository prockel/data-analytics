# First blood, role, and victory in League of Legends

**Does the role that secures first blood have an association with that team's
probability of winning, and does that relationship differ across ranked skill levels?**

A reproducible Python → Riot API → SQLite → SQL/pandas → notebook and chart
portfolio project for ranked solo/duo Summoner's Rift. Each eligible match contributes
**one observation**: the first-blood killer's role and whether that killer's team won.

> **Research status: upstream access blocked.** A Riot HTTPS secret is now
> configured, with a runtime binding for the allowed Riot hosts. The live check
> at **2026-10-29 03:36 UTC** received a **Cloudflare HTML block page (HTTP 403)**,
> rather than a Riot JSON API response. The key's validity remains unverified.
> No matches, empirical results, or charts have been fabricated. The exact study
> patch and collection dates remain unset until a successful live collection.

## Findings and visualizations

[Observed findings / current collection status](reports/findings.md) is the
canonical results page. There are currently **no empirical findings or charts**.
[The sanitized authentication check](reports/authentication.json) records the
actual network outcome without credentials, raw HTML, or player identifiers.
After collection, the same page contains all fifteen estimates, sample sizes,
95% intervals, exact patch, collection timestamps, and these generated figures:

| Output | Purpose |
|---|---|
| `reports/figures/win_rates.png` | Win rate by role and rank group, with Wilson intervals and sample sizes |
| `reports/figures/sample_sizes.png` | Fifteen-cell sample-composition heatmap |
| `reports/win_rates.csv` | Wins, games, win rates, and 95% Wilson interval bounds |
| `reports/interaction_contrasts.csv` | Exploratory differences in role-vs-TOP win-rate differences across ranks |
| `reports/collection.json` | Exact patch, UTC dates, settings, seed, and exclusion/coverage audit |
| [Analysis notebook](notebooks/analysis.ipynb) | SQL, pandas exploration, diagnostic tables, and figures |

When results exist, the findings page renders the actual charts. The chart
directory deliberately contains no synthetic or decorative results images.

## Methodology

### Population and patch

The target is **ranked solo/duo (`queueId=420`), Summoner's Rift (`mapId=11`),
`CLASSIC` matches** on one platform per run (NA1 by default; EUW1 and KR supported).
Flex queue, normals, ARAM, other maps, and mixed patches are excluded.

Before collecting, confirm the **current live major.minor patch** for the chosen
region using an official Riot patch announcement/schedule and the actual rollout.
Supply it and its official URL with `--current-patch` and `--patch-source`.
The CLI validates the URL's official hostname; it cannot verify the page's meaning
or regional rollout. This confirmation is an explicit operator responsibility.
Data Dragon asset releases alone cannot establish whether a regional patch has
finished rolling out.

Use the **internal `gameVersion`/Data Dragon major.minor**, which can differ
from the public season/patch label (for example, 2025 marketing patch 25.x used
internal version 15.x). Check the [official version catalog](https://ddragon.leagueoflegends.com/api/versions.json)
as well as the official patch announcement. The collector rejects a confirmation
that does not occur in the catalog; this prevents a season label from accidentally
classifying the current game patch as an older patch. Exported patch identifiers
always use the internal gameVersion namespace.

The collector freezes the official Data Dragon version catalog for the run,
considers up to three distinct major.minor patches **strictly older** than the
confirmed live patch, and evaluates them newest first. It selects the first with
at least **100 eligible matches in every rank group and 10 in every role/group
cell**, using a fixed maximum of 1,000 eligible games per group. These thresholds
are transparent feasibility minimums, not a statistical power calculation.
Change them before collecting, never in response to observed win rates.

The default match-history window is the 42 days before an explicit UTC cutoff.
The latest completed patch means **the newest completed patch meeting the
thresholds within this bounded sampling frame**, not a claim about all available
Riot games. If none qualify, status is `insufficient_data`; analysis refuses to
produce findings. Increase the history window, ladder-page coverage, sampled
players, or match-history cap in a new run. Do not silently pool patches.
The output manifest records the exact selected patch, candidate coverage,
collection start/end dates, UTC game window, and all settings.

### Rank-group assignment: a sampling label, not historical lobby rank

Riot Match-v5 does not expose participant ranks at match time. The sampling frame
comes from **League-v4 current ranked solo/duo ladder entries**:

| Anchor player's tier at collection | Match sampling group |
|---|---|
| IRON, BRONZE, SILVER, GOLD, PLATINUM, EMERALD | Iron–Emerald |
| DIAMOND, MASTER | Diamond–Master |
| GRANDMASTER, CHALLENGER | Grandmaster–Challenger |

For ordinary tiers, collect the first configured number of pages for each of
divisions I–IV. For Master, Grandmaster, and Challenger, use the apex league
endpoints. Exclude players observed in conflicting tiers during the snapshot.
Select up to 30 players per group by seeded SHA-256 ordering of PUUIDs, then
request up to 300 solo/duo match IDs per player with paginated Match-v5 histories.
Do not use a summoner name as a stable player identifier. Responses without PUUID
fail explicitly instead of inventing a rank mapping.

A game reached through multiple sampled players is deduplicated **by match ID**.
Choose one anchor by the lowest seeded hash of its PUUID before inspecting
first blood or outcome. The anchor's group labels the match; the first-blood
killer may be on either team and in a different tier. Match order is also a
seeded hash; the fixed per-group eligible-game cap does not depend on wins.

These groups describe **current rank of a sampled anchor**, not the killer's
rank, an average lobby rank, or rank at game time. A player's promotions,
demotions, cross-tier matchmaking, region transfers, and delayed collection can
misclassify historical games. Use this operational definition when interpreting
any differences; a stronger historical-rank study needs a prospective design or
an independently validated historical ranking source.

### First blood, roles, outcomes, and exclusions

1. Validate response IDs, patch, queue, map, mode, game-start window, ten unique
   participant IDs, and two five-player teams.
2. Require exactly one of each `teamPosition`: **TOP, JUNGLE, MIDDLE, BOTTOM,
   UTILITY** on each team. Reject missing, duplicate, or ambiguous roles rather
   than guessing from lane, champion, or summoner spells.
3. Find the earliest `CHAMPION_KILL` in the Match-v5 timeline, regardless of frame
   ordering. Exclude no-kill games, simultaneous tied first kills, environmental
   kills (`killerId=0`), unknown participants, and inconsistent timestamps.
4. Cross-check the killer against the single `firstBloodKill=true` participant.
   Assists do not count. The victim must be on the opposing team.
5. Resolve the winner from `info.teams[].win`, verify participant win flags, and
   compare **the killer's team ID** with **the winning team ID**. The anchor's
   team never substitutes for the killer's team.
6. Exclude games under 300 seconds and any `gameEndedInEarlySurrender=true`
   participant. This is a conservative remake/early-surrender filter and may
   remove some real short games. Malformed or contradictory records are excluded.

Every exclusion reason is counted by candidate patch. Threshold-based capping
means the audit describes inspected candidates, not a census of all games.
HTTP failures (including inaccessible matches or timelines) stop the run and mark
it `failed`; they are never silently treated as losing/winning games or missing
observations. Resume after resolving access problems.

### Statistics and interpretation

For each of 5 roles × 3 rank groups:

- `n`: eligible unique matches with that first-blood role;
- `wins`: matches where the first-blood killer's team won;
- `win_rate = wins / n`;
- a two-sided **95% Wilson score interval**, with `z=1.959963984540054`.

Empty cells have undefined rates/intervals, never 0% estimates. Wilson intervals
assume independent binomial observations. Games often share players; these
intervals can be too narrow. They are descriptive uncertainty summaries, not
population-corrected estimates. No significance is inferred from CI overlap.

Exploratory interaction contrasts compare `(role − TOP)` risk differences
between each upper group and Iron–Emerald. Their CSV includes unpooled Wald 95%
intervals; these are approximate, can be unreliable for sparse/extreme cells,
and do not adjust for dependence or multiple comparisons. They help describe
whether role patterns differ; they do not by themselves prove an interaction.

The 50% chart line is a visual reference, **not a causal counterfactual**.
Conditioning on first blood, skill, champion picks, matchup, team strength,
side, premades, game time, and selection can confound associations. This study
does not estimate how awarding first blood would change a team's chance of victory.

## Reproduce the project

### 1. Install (Python 3.12)

From the repository root:

```bash
cd league-first-blood
python3.12 -m venv .venv
source .venv/bin/activate
source scripts/env.sh
python -m pip install -r requirements.txt
python -m pip install --no-deps -e .
python -m ipykernel install --sys-prefix --name firstblood --display-name 'First Blood (Python 3.12)'
python -m unittest discover -s tests -v
```

The existing `requirements.txt` pins the analysis/notebook environment. The
project uses the standard library's `unittest` and SQLite; no database service
or paid dependency is needed. `pyproject.toml` defines the package/CLI. The
optional shell helper puts writable Jupyter and plotting directories in ignored
`.local/`, which also supports cloud machines with a read-only home directory.

### 2. Authenticate securely

Obtain a Riot key at <https://developer.riotgames.com/>. Development keys expire;
renew as needed and respect your application's Riot-approved usage and limits.

```bash
cp .env.example .env
# Edit .env locally to replace its placeholder; never commit this file.
chmod 600 .env
```

Alternatively configure `RIOT_API_KEY` as an HTTPS secret in environment
settings, with the target variable named `RIOT_API_KEY` and the Riot destinations
below. In this cloud environment the runtime variable can be an **opaque binding**
that the HTTPS proxy substitutes on those destinations. It need not be a raw
API key. Forward the binding unchanged in `X-Riot-Token` through the provided
HTTPS proxy; do not inspect its format, extract a secret from proxy storage, or
reject it merely because it looks like a placeholder. A process variable takes
precedence over `.env`. Never put a real key on a command line, in a notebook
cell, or in Git. Required HTTPS destinations are
`ddragon.leagueoflegends.com`, the selected platform's `*.api.riotgames.com`
host, and its regional match host (NA1→americas, EUW1→europe, KR→asia).

### Verify authentication before collecting

```bash
firstblood auth-check --platform na1 --output reports/authentication.json
```

This makes an **uncached** request to the authenticated League-v4 Challenger
endpoint, checks its response schema, and emits only safe diagnostic fields.
It never prints the binding, headers, response body, or player identifiers.
Exit code 0 means authentication was verified; 1 means it remains unverified.
The command does not equate an HTML/proxy denial with an expired key.

**Observed blocker (2026-10-29 UTC):** the NA1 authenticated League request and
NA1/EUW1 status checks received HTTP 403 Cloudflare pages containing “Sorry,
you have been blocked.” Data Dragon and GitHub API controls returned HTTP 200.
The environment configuration reports unrestricted network access, a saved
`RIOT_API_KEY` binding, and the correct Riot destination hosts. This is not
evidence that the key is missing or invalid; a Riot authentication response
has not been obtained.

**Required next change:** arrange a network/egress path that Riot's upstream
protection permits, or request an access review/allowlisting of the cloud egress
through the environment provider and Riot. Adding the already-configured binding
or repeating the same requests will not diagnose this denial further. Do not
bypass Cloudflare or disable TLS verification. After a routing/access change,
rerun `auth-check`. If it then returns a **Riot JSON** 401/403, renew or authorize
the existing key binding. No duplicate secret requirement is needed now.

If a different runtime supplies no binding at all, configure the HTTPS secret's
**target environment variable** and allowed Riot hostnames, then reconnect/apply
the environment settings. Do not paste the secret into chat or code.

### 3. Collect a frozen sample

Check the region's live patch in [official patch notes](https://www.leagueoflegends.com/en-us/news/tags/patch-notes/).
Replace the placeholders below with verified values; no example patch is
claimed to be the current live patch.

```bash
firstblood collect \
  --platform na1 \
  --current-patch '<confirmed-major.minor>' \
  --patch-source 'https://www.leagueoflegends.com/<official-patch-announcement-path>' \
  --as-of '<UTC-cutoff-in-ISO-8601-with-timezone>' \
  --seed 20261028
```

The command prints a run ID. Save that ID and reuse **exactly the same arguments,
cutoff, database, and raw cache** to resume. If `--as-of` is omitted, the current
UTC cutoff is printed; copy it for subsequent retries. A completed run is a
no-op on rerun. Never reuse an older current-patch confirmation with a later
cutoff that crosses another rollout.

Default sampling can require thousands of requests and substantial private cache
space; developer-key rate limits can make a run take hours. Start with the
published defaults, monitor disk usage, and resume rather than discarding the
cache. Raising caps increases cost and changes the sampling frame.

Requests use Riot's documented API authentication header, TLS verification,
timeouts, no authentication-bearing redirects, bounded retry/backoff with jitter,
`Retry-After` on 429, and proactive pauses when app/method rate windows are
exhausted. Persistent 401/403 errors fail immediately. Raw JSON is cached
atomically with private permissions. Successful responses survive interruptions;
SQLite records run status and commits only a sufficiently sampled selection.

Retain `data/raw/` and SQLite privately for exact replay. The raw ladder and
match-history pages are frozen per run; immutable match/timeline responses are
reused across runs. A seed alone cannot recreate a historical live ladder.
No credentials are stored in cache keys, manifests, or SQLite. Raw responses
contain player identifiers, so `data/` is ignored and should not be published.
Share aggregate results, charts, and collection metadata after review instead.

```bash
firstblood status --run-id '<printed-run-id>'
firstblood analyze --run-id '<printed-run-id>'
```

Analysis writes the real findings page, aggregate CSVs, charts, and manifest.
It refuses failed or insufficient runs. Source/API request failures return exit
code 1; an insufficient sample returns 2; success returns 0. Use a new database
and cache directory for independent studies or schema upgrades.

### 4. Explore SQL and the notebook

[sql/analysis.sql](sql/analysis.sql) contains named-parameter queries for win
rates, role frequency, duration/window coverage, and outcome integrity. Example
using Python, without installing the SQLite CLI:

```python
import sqlite3
from pathlib import Path
with sqlite3.connect('data/firstblood.sqlite') as con:
    sql = Path('sql/analysis.sql').read_text().split(';')[0]
    rows = con.execute(sql, {'run_id': '<printed-run-id>'}).fetchall()
    print(rows)
```

```bash
# Execute from league-first-blood/; no browser required.
# Set FIRSTBLOOD_RUN_ID to select a run, otherwise the newest completed run is used.
jupyter nbconvert --to notebook --execute notebooks/analysis.ipynb \
  --output analysis.executed.ipynb --output-dir .local
# Optional interactive exploration, with Jupyter's default token authentication:
jupyter lab --no-browser --ip=127.0.0.1 --notebook-dir=.
```

The notebook clearly reports **pending live data** if no completed run exists.
It does not create synthetic research observations or overwrite the canonical
reports while merely opening the notebook.

## Validation

```bash
python -m unittest discover -s tests -v
python -m pip check
firstblood --help
```

Tests cover upstream-versus-Riot authentication failures, opaque proxy binding handling,
credential-safe diagnostics, killer-team/winner mapping, rank boundaries, patch/mode filtering,
role ambiguity, remakes, malformed and tied first kills, retries, rate-limit
handling, credential-safe errors, deterministic sampling and shared-match anchor
assignment, pagination, SQL deduplication/integrity, run-specific rank labels,
Wilson intervals, interaction contrasts, collection status, and offline
collector→SQLite→SQL→charts integration. All test fixtures are explicitly
synthetic and temporary; they are not Riot API records or portfolio findings.

## Limitations and next collection review

This is a **ladder-seeded convenience sample**, not a random draw of ranked games.
Partial ladder pages, capped histories, active/repeated players, rank-group
mixtures, shared-match anchor resolution, API retention, exclusion rules, and
per-group caps can affect both sample composition and win rates. The combined
Iron–Emerald group is especially heterogeneous. Findings apply to the sampled
platform/patch and cannot be generalized automatically across regions or patches.
No prospective power or dependence-adjusted significance claim is made.

Before interpreting a live run, inspect the manifest's exclusions, dates,
thresholds and coverage, role counts, group sizes, interval widths, and notebook
duration diagnostics. Consider sensitivity analyses with wider ladder coverage,
independent seeds, alternate caps, and participant-aware uncertainty. Confirm the
live patch rather than assuming asset version order equals deployment order.

This project uses Riot Games' official APIs and is not endorsed by Riot Games.
