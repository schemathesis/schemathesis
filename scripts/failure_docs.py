"""Generates `docs/reference/failures.md` from the same data as `st explain`."""

from __future__ import annotations

import sys
import textwrap

from schemathesis.cli.commands.explain.explanations import EXPLANATIONS
from schemathesis.cli.commands.explain.render import GROUPS, config_snippet, turn_off_text
from schemathesis.failure_codes import FAILURE_CLASSES

HEADER = """\
# Failure Codes

Every failure Schemathesis reports starts with a code, such as `ST101`. `st explain <code>` prints the same
information in the terminal.
"""


def generate_docs_page() -> str:
    sections = [HEADER]
    group = ""
    for cls in FAILURE_CLASSES:
        if GROUPS[cls.code[2]] != group:
            group = GROUPS[cls.code[2]]
            sections.append(f"## {group}\n")
        explanation = EXPLANATIONS[cls.code]
        checks = ", ".join(f"[`{name}`](checks.md#{name})" for name in cls.checks) or "custom checks"
        lines = [f"### {cls.code} {cls.kind_title} {{#{cls.code.lower()}}}", "", f"Reported by {checks}.", ""]
        lines.append(explanation.summary)
        scenario = textwrap.dedent(explanation.scenario).strip("\n")
        if scenario:
            lines += ["", "```text", scenario, "```"]
        if explanation.notes:
            lines += ["", " ".join(explanation.notes.split())]
        snippet = config_snippet(cls.code, width=100)
        if snippet:
            lines += ["", "```toml", *snippet, "```"]
        lines += ["", turn_off_text(cls.code)]
        sections.append("\n".join(lines) + "\n")
    return "\n".join(sections)


if __name__ == "__main__":
    sys.stdout.write(generate_docs_page())
