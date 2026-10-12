from __future__ import annotations

from typing import TYPE_CHECKING

from schemathesis.reporting.html.render import render_index

if TYPE_CHECKING:
    from pathlib import Path

    from schemathesis.reporting.html.model import ReportData

__all__ = ["write_report"]


def write_report(data: ReportData, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "index.html").write_text(render_index(data), encoding="utf-8")
