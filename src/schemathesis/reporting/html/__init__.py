from __future__ import annotations

from importlib.resources import files
from pathlib import Path
from typing import TYPE_CHECKING

from schemathesis.reporting.html.render import render_index

if TYPE_CHECKING:
    from schemathesis.reporting.html.model import ReportData

__all__ = ["write_report"]


def write_report(data: ReportData, output_dir: Path) -> None:
    assets_dir = output_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    assets = files("schemathesis.reporting.html") / "assets"
    (assets_dir / "report.css").write_text((assets / "report.css").read_text(encoding="utf-8"), encoding="utf-8")
    (output_dir / "index.html").write_text(render_index(data), encoding="utf-8")
