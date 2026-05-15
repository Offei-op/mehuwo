"""Print-ready chart helpers styled to match the UI palette.

We use matplotlib (not Plotly) for two reasons:
  - The cluster_summary heatmap teachers will look at on screen should
    look the same as the PDF version they'll print.
  - The pipeline already emits matplotlib PNGs in `--debug-dir`. Using
    the same library and style here makes the on-screen and on-paper
    deliverables visually consistent.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np
import pandas as pd
from matplotlib.colors import LinearSegmentedColormap


# Project palette - matched to styles.css and the skill-graph image
PALETTE = {
    "bg":      "#FBF8F2",
    "ink":     "#1F1A14",
    "ink_soft": "#5C5346",
    "green":   "#2D4A2B",
    "green_3": "#A8C97A",  # mastered
    "amber":   "#D49C3C",
    "amber_3": "#E5C067",  # foundation band
    "plum":    "#6E4B7D",
    "plum_3":  "#9B7AB8",  # leaf band
    "red":     "#B14A3A",
    "rule":    "#E0D7C5",
}


# Custom diverging colormap: red (low mastery) → amber → green (high mastery)
MASTERY_CMAP = LinearSegmentedColormap.from_list(
    "mehuwo_mastery",
    [
        (0.00, "#C25C4A"),
        (0.50, PALETTE["amber_3"]),
        (1.00, PALETTE["green_3"]),
    ],
    N=256,
)


def _apply_base_style(ax) -> None:
    ax.set_facecolor(PALETTE["bg"])
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    for spine in ("left", "bottom"):
        ax.spines[spine].set_color(PALETTE["rule"])
        ax.spines[spine].set_linewidth(0.8)
    ax.tick_params(colors=PALETTE["ink_soft"], labelsize=9)
    ax.title.set_color(PALETTE["ink"])
    ax.title.set_fontweight("600")
    ax.title.set_fontsize(11)


# --- Cluster centroid heatmap --------------------------------------------

def cluster_centroid_heatmap(
    summary: pd.DataFrame, skills: list[str],
) -> plt.Figure:
    """summary: rows = clusters, with `centroid_<skill>` columns."""
    cent_cols = [f"centroid_{s}" for s in skills]
    M = summary[cent_cols].to_numpy()
    k = len(summary)

    fig, ax = plt.subplots(
        figsize=(max(6.5, 0.65 * len(skills) + 2.5), 0.6 * k + 2.2),
        facecolor=PALETTE["bg"],
    )

    im = ax.imshow(M, aspect="auto", cmap=MASTERY_CMAP, vmin=0, vmax=1)

    ax.set_yticks(range(k))
    labels = [f"cluster {row['cluster']}\n({row['label']}, n={row['size']})"
              for _, row in summary.iterrows()]
    ax.set_yticklabels(labels, fontsize=9)

    ax.set_xticks(range(len(skills)))
    ax.set_xticklabels(skills, rotation=40, ha="right", fontsize=9)

    # Numeric value annotations
    for i in range(M.shape[0]):
        for j in range(M.shape[1]):
            v = M[i, j]
            colour = PALETTE["ink"] if 0.25 < v < 0.78 else "white"
            ax.text(j, i, f"{v:.2f}", ha="center", va="center",
                    color=colour, fontsize=8.5, fontweight="500")

    ax.set_title("Mastery by cluster and skill")
    cbar = fig.colorbar(im, ax=ax, fraction=0.025, pad=0.02)
    cbar.ax.tick_params(labelsize=8, colors=PALETTE["ink_soft"])
    cbar.outline.set_edgecolor(PALETTE["rule"])
    cbar.set_label("mastery", color=PALETTE["ink_soft"], fontsize=9)

    _apply_base_style(ax)
    fig.tight_layout()
    return fig


# --- Cluster size bar -----------------------------------------------------

def cluster_size_bar(summary: pd.DataFrame) -> plt.Figure:
    fig, ax = plt.subplots(figsize=(6.5, 2.8), facecolor=PALETTE["bg"])

    # Colour each bar by its label
    label_colours = {
        "all-mastered":   PALETTE["green_3"],
        "foundation-gap": PALETTE["amber_3"],
        "middle-gap":     PALETTE["amber"],
        "leaf-gap":       PALETTE["plum_3"],
        "broad-gap":      PALETTE["red"],
    }
    bar_colours = [label_colours.get(l, PALETTE["green_3"])
                   for l in summary["label"]]

    ys = np.arange(len(summary))
    ax.barh(ys, summary["size"].to_numpy(),
            color=bar_colours, edgecolor=PALETTE["ink"], linewidth=0.6)

    ax.set_yticks(ys)
    ax.set_yticklabels([f"cluster {c}" for c in summary["cluster"]],
                       fontsize=9)
    ax.invert_yaxis()
    ax.set_xlabel("students", color=PALETTE["ink_soft"], fontsize=9)
    ax.set_title("Cluster sizes")

    # Number labels inside or beside each bar
    xmax = float(summary["size"].max())
    for y, v in zip(ys, summary["size"]):
        if v > xmax * 0.15:
            ax.text(v - xmax * 0.02, y, str(int(v)),
                    color="white", ha="right", va="center",
                    fontsize=9, fontweight="600")
        else:
            ax.text(v + xmax * 0.02, y, str(int(v)),
                    color=PALETTE["ink"], ha="left", va="center",
                    fontsize=9, fontweight="500")

    # Legend
    seen = []
    handles = []
    for l in summary["label"]:
        if l not in seen:
            seen.append(l)
            handles.append(mpatches.Patch(
                color=label_colours.get(l, PALETTE["green_3"]), label=l))
    if handles:
        ax.legend(handles=handles, loc="lower right", frameon=False,
                  fontsize=8, ncol=min(len(handles), 3))

    _apply_base_style(ax)
    fig.tight_layout()
    return fig


# --- Mastery distribution -------------------------------------------------

def mastery_distribution(mastery: pd.DataFrame, skills: list[str]) -> plt.Figure:
    """Per-skill bar of mean mastery across all scored students."""
    means = mastery[skills].mean()
    fig, ax = plt.subplots(figsize=(max(6.5, 0.55 * len(skills) + 2.5), 3),
                           facecolor=PALETTE["bg"])

    colours = []
    for v in means:
        if v >= 0.75:    colours.append(PALETTE["green_3"])
        elif v >= 0.5:   colours.append(PALETTE["amber_3"])
        elif v >= 0.25:  colours.append(PALETTE["amber"])
        else:            colours.append(PALETTE["red"])

    xs = np.arange(len(skills))
    ax.bar(xs, means.to_numpy(),
           color=colours, edgecolor=PALETTE["ink"], linewidth=0.6)

    ax.axhline(0.5, color=PALETTE["ink_soft"], linewidth=0.7,
               linestyle="--", alpha=0.5)
    ax.text(len(skills) - 0.5, 0.51, "partial threshold",
            ha="right", va="bottom", fontsize=8,
            color=PALETTE["ink_soft"], style="italic")

    ax.set_xticks(xs)
    ax.set_xticklabels(skills, rotation=40, ha="right", fontsize=9)
    ax.set_ylim(0, 1.05)
    ax.set_ylabel("mean mastery", fontsize=9, color=PALETTE["ink_soft"])
    ax.set_title("Class-wide mastery by skill")

    _apply_base_style(ax)
    fig.tight_layout()
    return fig


# --- Mastery histogram ----------------------------------------------------

def per_student_mean_hist(mastery: pd.DataFrame) -> plt.Figure:
    """Distribution of per-student mean mastery."""
    fig, ax = plt.subplots(figsize=(6.5, 2.8), facecolor=PALETTE["bg"])
    vals = mastery["mean_mastery"].dropna().to_numpy()
    if len(vals) == 0:
        ax.text(0.5, 0.5, "No scored students", ha="center", va="center",
                color=PALETTE["ink_soft"], transform=ax.transAxes)
    else:
        ax.hist(vals, bins=12, color=PALETTE["green_3"],
                edgecolor=PALETTE["ink"], linewidth=0.7)
        ax.axvline(float(np.mean(vals)), color=PALETTE["green"],
                   linewidth=1.5, label=f"mean = {np.mean(vals):.2f}")
        ax.legend(loc="upper left", frameon=False, fontsize=9)

    ax.set_xlim(0, 1)
    ax.set_xlabel("mean mastery (per student)",
                  color=PALETTE["ink_soft"], fontsize=9)
    ax.set_ylabel("students", color=PALETTE["ink_soft"], fontsize=9)
    ax.set_title("Distribution of per-student mean mastery")

    _apply_base_style(ax)
    fig.tight_layout()
    return fig
