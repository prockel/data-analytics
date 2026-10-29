# Exploratory pilot results

Run `204395aef6dd58c82454` · patch **16.20** · platform **NA1**.
Collection: 2026-10-29T04:04:17.826574+00:00 through 2026-10-29T04:19:50.975959+00:00 (UTC).

Analyzed **137 unique eligible matches** from a ladder-seeded convenience sample
collected locally. Thirty anchor players were sampled: ten per current-rank group.
The resulting match counts are 20 Iron–Emerald, 83 Diamond–Master, and
34 Grandmaster–Challenger. These groups describe **the sampled anchor player's
current rank at collection**, not the first-blood killer's historical rank or an
average lobby rank.

This pilot used completion minimums of **one match per group and one per cell**,
not the CLI defaults of 100 per group and ten per cell. `complete` in the manifest
means those pilot settings were met; it does not establish statistical adequacy.
Nine of fifteen cells have fewer than ten games. These are exploratory descriptive
estimates, not representative population estimates or significance findings.

| Anchor rank group | First-blood role | Games | Wins | Win rate | 95% Wilson CI |
|---|---|---:|---:|---:|---|
| Iron–Emerald | TOP | 7 | 4 | 57.1% | 25.0%–84.2% |
| Iron–Emerald | JUNGLE | 3 | 2 | 66.7% | 20.8%–93.9% |
| Iron–Emerald | MIDDLE | 2 | 0 | 0.0% | 0.0%–65.8% |
| Iron–Emerald | BOTTOM | 6 | 4 | 66.7% | 30.0%–90.3% |
| Iron–Emerald | UTILITY | 2 | 1 | 50.0% | 9.5%–90.5% |
| Diamond–Master | TOP | 22 | 12 | 54.5% | 34.7%–73.1% |
| Diamond–Master | JUNGLE | 13 | 6 | 46.2% | 23.2%–70.9% |
| Diamond–Master | MIDDLE | 12 | 6 | 50.0% | 25.4%–74.6% |
| Diamond–Master | BOTTOM | 23 | 17 | 73.9% | 53.5%–87.5% |
| Diamond–Master | UTILITY | 13 | 6 | 46.2% | 23.2%–70.9% |
| Grandmaster–Challenger | TOP | 5 | 4 | 80.0% | 37.6%–96.4% |
| Grandmaster–Challenger | JUNGLE | 5 | 2 | 40.0% | 11.8%–76.9% |
| Grandmaster–Challenger | MIDDLE | 8 | 3 | 37.5% | 13.7%–69.4% |
| Grandmaster–Challenger | BOTTOM | 14 | 9 | 64.3% | 38.8%–83.7% |
| Grandmaster–Challenger | UTILITY | 2 | 0 | 0.0% | 0.0%–65.8% |

![Win rates and 95% intervals](figures/win_rates.png)

![Sample composition](figures/sample_sizes.png)

See `interaction_contrasts.csv` for exploratory role-versus-TOP differences between rank groups.
Individual CI overlap is not a test of interaction. Contrast intervals are unadjusted Wald approximations;
they do not account for repeated players or multiple comparisons. Sparse or
all-win/all-loss cells can make the Wald approximations unreliable. In particular,
the Diamond–Master MIDDLE contrast's interval excluding zero is not interpreted
as a validated significance finding. Contrast values are probability differences,
not win probabilities. No statistically significant rank/role differences or
causal effects are claimed.

The 0/2 cells have Wilson upper bounds of 65.8%; their observed 0% rates do not
imply a zero underlying probability. Likewise, Grandmaster–Challenger TOP's 80%
rate comes from only 4/5 wins (95% Wilson CI 37.6%–96.4%). See the
[project README](../README.md) for sampling, cleaning, and interpretation limits.
The aggregate CSVs and manifest are committed; private match records and SQLite
are not included, so this review checks aggregate consistency rather than
independently revalidating individual games.
