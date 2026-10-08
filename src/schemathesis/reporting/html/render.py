from __future__ import annotations

from html import escape
from importlib.resources import files

from schemathesis.core.output import escape_surrogates
from schemathesis.core.version import SCHEMATHESIS_VERSION

_LOGO = (files("schemathesis.reporting.html") / "assets" / "logo.svg").read_text(encoding="utf-8").strip()


def esc(value: object) -> str:
    return escape(escape_surrogates(str(value)), quote=True)


def page(*, title: str, body: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{esc(title)}</title>
  <link rel="stylesheet" href="assets/report.css">
</head>
<body>
  <main class="container">
{body}
  </main>
</body>
</html>
"""


def report_top(*, generated_at: str) -> str:
    brand = (
        f'<span class="logo">{_LOGO}</span>'
        '<span class="brand-name">Report</span>'
        f'<span class="version-chip">v{esc(SCHEMATHESIS_VERSION)}</span>'
    )
    return (
        '<header class="report-top">'
        f'<div class="brand">{brand}</div>'
        '<div class="top-meta"><span class="tm-eyebrow">Generated at</span> '
        f"<span><b>{esc(generated_at)}</b></span></div>"
        "</header>"
    )


def render_index(*, generated_at: str) -> str:
    return page(title="Schemathesis Report", body=report_top(generated_at=generated_at))
