# Imported lazily from `__main__` only when --charts is passed; seaborn's import is heavy.
from __future__ import annotations

from pathlib import Path

import seaborn
from matplotlib import figure, pyplot

from .metrics import RunMetrics


def _save(fig: figure.Figure, base: Path) -> None:
    base.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(base.with_suffix(".png"), bbox_inches="tight")
    pyplot.close(fig)


def _bucket_chart(run: RunMetrics, charts_dir: Path) -> None:
    buckets = run.buckets
    rows = [
        ("positive_accepted", buckets.positive_accepted),
        ("negative_rejected", buckets.negative_rejected),
        ("positive_drift", buckets.positive_drift),
        ("negative_drift", buckets.negative_drift),
        ("server_error", buckets.server_error),
        ("route_rejected", buckets.route_rejected),
        ("auth_rejected", buckets.auth_rejected),
        ("other", buckets.other),
    ]
    labels = [label for label, _ in rows]
    counts = [count for _, count in rows]
    fig, ax = pyplot.subplots(figsize=(9, 4))
    seaborn.barplot(x=labels, y=counts, ax=ax, color="#3b82f6")
    ax.set_title("Call buckets")
    ax.set_ylabel("Count")
    ax.tick_params(axis="x", rotation=30)
    _save(fig, charts_dir / "buckets")


def _phases_chart(run: RunMetrics, charts_dir: Path) -> None:
    if not run.phases:
        return
    names = [phase.name for phase in run.phases]
    durations = [phase.duration_seconds for phase in run.phases]
    fig, ax = pyplot.subplots(figsize=(8, 4))
    seaborn.barplot(x=names, y=durations, ax=ax, color="#8b5cf6")
    ax.set_title("Phase wall-clock duration")
    ax.set_ylabel("Seconds")
    _save(fig, charts_dir / "phases")


def _mutation_grid_chart(run: RunMetrics, charts_dir: Path) -> None:
    grid = run.mutations.grid
    if not grid:
        return
    locations = sorted({key.split("|", 1)[0] for key in grid})
    operators = sorted({key.split("|", 1)[1] for key in grid})
    matrix = []
    for location in locations:
        row = []
        for operator in operators:
            cell = grid.get(f"{location}|{operator}")
            if cell is None or cell.count == 0:
                row.append(0.0)
            else:
                row.append(cell.accepted / cell.count)
        matrix.append(row)
    fig, ax = pyplot.subplots(figsize=(max(6, len(operators) * 0.8), max(3, len(locations) * 0.5)))
    seaborn.heatmap(
        matrix,
        xticklabels=operators,
        yticklabels=locations,
        ax=ax,
        annot=True,
        fmt=".2f",
        cmap="rocket_r",
    )
    ax.set_title("Mutation acceptance_rate (accepted / count)")
    ax.set_xlabel("Operator")
    ax.set_ylabel("Location")
    _save(fig, charts_dir / "mutation_grid")


def _coverage_scenarios_chart(run: RunMetrics, charts_dir: Path) -> None:
    by_kind = run.coverage_scenarios.by_kind
    if not by_kind:
        return
    rows = sorted(by_kind.items(), key=lambda item: -item[1].count)
    kinds = [name for name, _ in rows]
    counts = [cell.count for _, cell in rows]
    accept_ratio = [(cell.accepted / cell.count) if cell.count else 0.0 for _, cell in rows]
    fig, ax = pyplot.subplots(figsize=(max(7, len(kinds) * 0.5), 4))
    seaborn.barplot(x=kinds, y=counts, hue=accept_ratio, ax=ax, palette="rocket_r", legend=False)
    ax.set_title("Coverage scenarios (bar height = count, color = acceptance_rate)")
    ax.set_ylabel("Count")
    ax.tick_params(axis="x", rotation=60)
    _save(fig, charts_dir / "coverage_scenarios")


def _new_operation_timeline_chart(run: RunMetrics, charts_dir: Path) -> None:
    timeline = run.rates.new_operation_per_minute_timeline
    if not timeline:
        return
    minutes = [row["minute"] for row in timeline]
    covered = [row["covered"] for row in timeline]
    fig, ax = pyplot.subplots(figsize=(8, 4))
    seaborn.lineplot(x=minutes, y=covered, ax=ax, marker="o", color="#10b981")
    ax.set_title("Operation discovery curve")
    ax.set_xlabel("Minute since EngineStarted")
    ax.set_ylabel("Distinct operations with >= 1 2xx")
    _save(fig, charts_dir / "new_operation_timeline")


_PRODUCERS = (
    _bucket_chart,
    _phases_chart,
    _mutation_grid_chart,
    _coverage_scenarios_chart,
    _new_operation_timeline_chart,
)


def write_charts(run: RunMetrics, charts_dir: Path) -> None:
    seaborn.set_theme(style="whitegrid", palette="muted")
    charts_dir.mkdir(parents=True, exist_ok=True)
    for producer in _PRODUCERS:
        producer(run, charts_dir)
