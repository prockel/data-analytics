"""Descriptive win rates and Wilson intervals; never assert causal effects."""
from __future__ import annotations

from itertools import product
import json
import math
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns

from .storage import read_database, run_config
from .transform import GROUPS, ROLES


def wilson(wins, n, z=1.959963984540054):
    """Two-sided 95% Wilson score CI; empty cells have no defined estimate."""
    if n < 0 or wins < 0 or wins > n:
        raise ValueError("Require 0 <= wins <= n")
    if n == 0:
        return math.nan, math.nan
    p = wins / n
    denominator = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denominator
    half = z * math.sqrt(p * (1-p) / n + z*z / (4*n*n)) / denominator
    return max(0.0, center-half), min(1.0, center+half)


def summarize(frame):
    rows = []
    for group, role in product(GROUPS, ROLES):
        cell = frame[(frame["rank_group"] == group) & (frame["role"] == role)]
        n, wins = len(cell), int(cell["first_blood_win"].sum())
        low, high = wilson(wins, n)
        rows.append(dict(rank_group=group, role=role, n=n, wins=wins,
                         win_rate=wins/n if n else math.nan, ci_low=low, ci_high=high))
    return pd.DataFrame(rows)


def interaction_contrasts(summary):
    """Exploratory differences in role-vs-TOP risk differences between groups.

    Unpooled Wald 95% intervals are a descriptive interaction diagnostic.
    Not multiplicity-adjusted; repeated-player dependence is not modeled.
    """
    indexed = summary.set_index(["rank_group", "role"])
    rows = []
    for group in GROUPS[1:]:
        for role in ROLES[1:]:
            cells = [indexed.loc[(group, role)], indexed.loc[(group, "TOP")],
                     indexed.loc[(GROUPS[0], role)], indexed.loc[(GROUPS[0], "TOP")]]
            if any(c["n"] == 0 for c in cells):
                estimate = low = high = math.nan
            else:
                estimate = sum(sign * c["win_rate"] for sign, c in zip((1,-1,-1,1), cells))
                se = math.sqrt(sum(c["win_rate"]*(1-c["win_rate"])/c["n"] for c in cells))
                low, high = estimate - 1.959963984540054*se, estimate + 1.959963984540054*se
            rows.append(dict(rank_group=group, role=role, reference_group=GROUPS[0], reference_role="TOP",
                             difference_in_risk_differences=estimate, ci_low=low, ci_high=high))
    return pd.DataFrame(rows)


def make_charts(summary, directory, subtitle):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    sns.set_theme(style="whitegrid", context="notebook", palette="colorblind")
    colors = sns.color_palette("colorblind", 3)
    fig, ax = plt.subplots(figsize=(11, 6), layout="constrained")
    for index, group in enumerate(GROUPS):
        cells = summary[summary.rank_group == group].set_index("role").reindex(ROLES)
        x = np.arange(5) + (index-1)*0.22
        valid = cells.n.to_numpy() > 0
        y = cells.win_rate.to_numpy()*100
        ax.errorbar(x[valid], y[valid],
                    yerr=np.array([y-cells.ci_low.to_numpy()*100,
                                   cells.ci_high.to_numpy()*100-y])[:, valid],
                    fmt="o", capsize=4, color=colors[index], label=group)
        for xpos, (_, cell) in zip(x, cells.iterrows()):
            if cell.n:
                ax.annotate(f"n={int(cell.n):,}", (xpos, cell.ci_high*100),
                            xytext=(0, 7), textcoords="offset points", ha="center", fontsize=7)
    ax.axhline(50, color="gray", linestyle="--", linewidth=1, label="50% reference (not causal baseline)")
    ax.set(xticks=range(5), xticklabels=ROLES, ylim=(0, 105), xlabel="First-blood killer's role",
           ylabel="First-blood team's win rate (%)",
           title="First blood and victory by role and sampled rank\n" + subtitle)
    ax.legend(loc="lower center", ncols=2, fontsize=9)
    fig.savefig(directory / "win_rates.png", dpi=180)
    plt.close(fig)
    counts = summary.pivot(index="rank_group", columns="role", values="n").reindex(index=GROUPS, columns=ROLES)
    fig, ax = plt.subplots(figsize=(9, 4), layout="constrained")
    sns.heatmap(counts, annot=True, fmt="d", cmap="Blues", cbar_kws={"label": "Matches"}, ax=ax)
    ax.set(xlabel="First-blood killer's role", ylabel="Sampled anchor rank group",
           title="Sample composition\n" + subtitle)
    fig.savefig(directory / "sample_sizes.png", dpi=180)
    plt.close(fig)


def analyze(db_path, run_id, output):
    output = Path(output)
    existing_manifest = output / "collection.json"
    if existing_manifest.exists() and json.loads(existing_manifest.read_text()).get("run_id") != run_id:
        raise ValueError("Output directory belongs to another run; use a separate report directory")
    with read_database(db_path) as con:
        run, config = run_config(con, run_id)
        if run["status"] != "complete":
            raise ValueError("Analysis requires a completed, sufficiently sampled live collection run")
        frame = pd.read_sql_query("""
            SELECT m.match_id,m.patch,rm.rank_group,m.role,m.first_blood_win,m.duration_seconds
            FROM matches m JOIN run_matches rm USING(match_id)
            WHERE rm.run_id=? AND m.patch=? ORDER BY m.match_id
        """, con, params=(run_id, run["selected_patch"]))
    if frame.empty or frame.match_id.duplicated().any():
        raise ValueError("Empty or duplicate match sample")
    summary = summarize(frame)
    contrasts = interaction_contrasts(summary)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    summary.to_csv(output / "win_rates.csv", index=False)
    contrasts.to_csv(output / "interaction_contrasts.csv", index=False)
    make_charts(summary, output / "figures", f"Patch {run['selected_patch']} · {config['platform'].upper()} · {len(frame):,} unique games")
    metadata = {k: run[k] for k in ("run_id", "started_at", "completed_at", "status", "selected_patch")}
    metadata.update(config=config, audit=json.loads(run["audit_json"]))
    (output / "collection.json").write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n")
    lines = ["# Observed results", "", f"Run `{run_id}` · patch **{run['selected_patch']}** · platform **{config['platform'].upper()}**.",
             f"Collection: {run['started_at']} through {run['completed_at']} (UTC).", "",
             f"Analyzed **{len(frame):,} unique eligible matches**. These are associations in a ladder-seeded convenience sample.", "",
             "| Anchor rank group | First-blood role | Games | Wins | Win rate | 95% Wilson CI |",
             "|---|---|---:|---:|---:|---|" ]
    for row in summary.itertuples():
        estimate = f"{row.win_rate:.1%}" if row.n else "Unavailable"
        interval = f"{row.ci_low:.1%}–{row.ci_high:.1%}" if row.n else "Unavailable"
        lines.append(f"| {row.rank_group} | {row.role} | {row.n:,} | {row.wins:,} | {estimate} | {interval} |")
    lines.extend(["", "![Win rates and 95% intervals](figures/win_rates.png)", "",
                  "![Sample composition](figures/sample_sizes.png)", "",
                  "See `interaction_contrasts.csv` for exploratory role-versus-TOP differences between rank groups.",
                  "Individual CI overlap is not a test of interaction. Contrast intervals are unadjusted Wald approximations;",
                  "they do not account for repeated players or multiple comparisons. No causal interpretation is supported.", ""])
    (output / "findings.md").write_text("\n".join(lines))
    return summary
