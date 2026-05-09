from __future__ import annotations

import sys
from pathlib import Path

import click

from .metrics import analyze
from .report import render_json, render_markdown


@click.command()
@click.argument("ndjson_path", type=click.Path(exists=False, dir_okay=False, path_type=Path))
@click.option(
    "-o",
    "--output",
    "output_dir",
    type=click.Path(file_okay=False, path_type=Path),
    required=True,
    help="Directory to write metrics.json + report.md (created if missing).",
)
@click.option(
    "--charts",
    is_flag=True,
    default=False,
    help="Also write a charts/ subdirectory with PNG visualizations (requires seaborn).",
)
def cli(ndjson_path: Path, output_dir: Path, charts: bool) -> None:
    """Extract metrics from a schemathesis NDJSON shard."""
    if not ndjson_path.exists():
        click.echo(f"error: file not found: {ndjson_path}", err=True)
        sys.exit(2)
    output_dir.mkdir(parents=True, exist_ok=True)
    run = analyze(ndjson_path)
    (output_dir / "metrics.json").write_text(render_json(run), encoding="utf-8")
    (output_dir / "report.md").write_text(render_markdown(run), encoding="utf-8")
    if charts:
        # Lazy import — seaborn is heavy and only loaded when requested.
        from .charts import write_charts

        write_charts(run, output_dir / "charts")


if __name__ == "__main__":
    cli()
