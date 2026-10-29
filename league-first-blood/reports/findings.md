# Findings pending upstream access

**No Riot match data has been collected for this project yet.** The published
environment now has a saved HTTPS secret binding named `RIOT_API_KEY` for the
correct Riot hosts. The runtime binding was forwarded unchanged through the
provided HTTPS proxy; no credential was printed or stored in the report.

## Actual authentication check

At **2026-10-29T03:36:25Z**, the uncached NA1 League-v4 Challenger request returned
**HTTP 403 with a Cloudflare HTML block page**. It did not return a Riot JSON
authentication response. Therefore **key validity remains unverified**; this is
not a finding that the key is missing or expired. Separate NA1 and EUW1 status
checks encountered the same Cloudflare block. Data Dragon and GitHub API control
requests succeeded with HTTP 200.

See [the sanitized machine-readable check](authentication.json). It contains no
key, header values, raw HTML, player identifiers, or match records. Its timestamp
is an authentication attempt, **not a data-collection date**.

The configured network policy is unrestricted and the secret's allowed Riot
destinations are correct. An environment-provider/Riot review or permitted egress
route is needed to resolve the upstream block. After that change, rerun
`firstblood auth-check`; only a verified authenticated response should be followed
by patch confirmation and live collection. Do not add a duplicate key or assume
that making a raw key available as an ordinary environment variable fixes this
upstream response.

## Research outputs

No exact study patch, collection dates, win rates, confidence intervals, or
empirical conclusions can be reported. There are **zero collected matches**;
no analytical sample exists and no empirical charts have been generated.

`firstblood analyze` generates `figures/win_rates.png`,
`figures/sample_sizes.png`, `win_rates.csv`, exploratory
`interaction_contrasts.csv`, and a dated `collection.json` only after a live
collection satisfies the configured sample minimums. It replaces this document
with observed results and exact patch/collection dates.

Offline tests use explicitly synthetic fixtures, write temporary outputs,
and delete them. Those fixtures and outputs are not Riot data or findings.
