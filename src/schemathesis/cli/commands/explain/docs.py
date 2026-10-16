"""Generate `docs/reference/failures.md` from the `st explain` explanations.

Run `python -m schemathesis.cli.commands.explain.docs > docs/reference/failures.md`.
"""

from __future__ import annotations

import sys
import textwrap

from schemathesis.cli.commands.explain.explanations import EXPLANATIONS, Explanation
from schemathesis.cli.commands.explain.render import (
    DEFAULT_EXPECTED_STATUSES,
    GROUPS,
    VERIFY_TEXT,
    also_disables,
    example_response_text,
    example_schema_text,
    prose_blocks,
)
from schemathesis.failure_codes import iter_failure_classes

INTRO = """\
# Failure codes

Every failure Schemathesis reports carries a stable code, such as `ST108`. Run `st explain <code>` to read the
section below in your terminal, or `st explain --list` for all codes. A code never changes meaning and is never
reused.

Terms used on this page:

- **Case ID**: the `Test Case ID:` line above each failure in the run output. `st replay` takes it.
- **Positive and negative cases**: requests that follow the schema, and requests that break it on purpose.
- **Phases**: the stages of a run. The coverage phase sends targeted boundary and invalid values, the fuzzing
  phase sends random data, and the stateful phase chains operations through links between them.
- **Severity**: how much the failure is likely to hurt API consumers, from LOW to CRITICAL.
- **WFC**: [Web Fuzzing Commons](https://github.com/WebFuzzing/Commons) fault categories, which
  `--report wfc` exports.
"""


def generate_docs_page() -> str:
    parts = [INTRO]
    current_group = ""
    for cls in iter_failure_classes():
        group = GROUPS[cls.code[2]]
        if group != current_group:
            parts.append(f"## {group}\n")
            current_group = group
        parts.append(
            _section(cls.code, EXPLANATIONS[cls.code], cls.kind_title, cls.severity_default.value.upper(), cls.wfc)
        )
    return "\n".join(parts)


def _section(code: str, explanation: Explanation, title: str, severity: str, wfc: tuple[str, ...]) -> str:
    checks = ", ".join(
        f"`{name}` (GraphQL only)" if name in explanation.graphql_only else f"`{name}`" for name in explanation.checks
    )
    meta = f"**Severity:** {severity}. **Checks:** {checks or 'user-defined'}."
    if wfc:
        meta += f" **WFC:** {', '.join(wfc)}."
    lines = [f"### {code} {title} {{#{code.lower()}}}", "", meta, "", *_prose(explanation.summary)]
    for heading, body in (
        ("Why it matters", _prose(explanation.why)),
        ("Example", _example(explanation)),
        ("How Schemathesis found it", _prose(explanation.how_found)),
        ("How to fix", _prose(explanation.fix)),
        ("False positives", _prose(explanation.false_positives)),
        ("Verify the fix", _prose(explanation.verify or VERIFY_TEXT)),
        ("Tuning", _tuning(explanation)),
        ("Turn it off", _turn_off(code, explanation)),
    ):
        if body:
            lines += ["", f"#### {heading}", "", *body]
    return "\n".join(lines) + "\n"


def _prose(text: str) -> list[str]:
    lines: list[str] = []
    for block in prose_blocks(text):
        # Markdown renders a list only when a blank line separates it from the paragraph before it.
        if block.startswith("- ") and lines and lines[-1] and not lines[-1].startswith("- "):
            lines.append("")
        lines.append(block)
    return lines


def _example(explanation: Explanation) -> list[str]:
    if explanation.example is not None:
        return [
            "```yaml",
            example_schema_text(explanation.example),
            "```",
            "",
            "```http",
            example_response_text(explanation.example),
            "```",
        ]
    return ["```text", textwrap.dedent(explanation.scenario).strip("\n"), "```"]


def _tuning(explanation: Explanation) -> list[str]:
    lines: list[str] = []
    for name in explanation.tuning:
        statuses = ", ".join(f'"{status}"' for status in DEFAULT_EXPECTED_STATUSES[name])
        lines += [
            "Set the status codes this check expects. Your list replaces the default, so copy it and add your "
            "status. For one operation only, put the same key, `checks.<check>.expected-statuses`, in an "
            "`[[operations]]` entry with `include-name`:",
            "",
            "```toml",
            f"[checks.{name}]",
            f"expected-statuses = [{statuses}]",
            "```",
        ]
    return lines


def _turn_off(code: str, explanation: Explanation) -> list[str]:
    if not explanation.checks:
        return [
            "Run with `--exclude-checks` and the custom check named in the failure, for example "
            "`--exclude-checks check_audit_trail`, or set:",
            "",
            "```toml",
            "[checks.check_audit_trail]",
            "enabled = false",
            "```",
        ]
    if explanation.checks == ("max_response_time",):
        return ["Off unless `--max-response-time` or `checks.max_response_time` is set."]
    regular = tuple(name for name in explanation.checks if name not in explanation.graphql_only)
    lines = _disable_block(code, regular)
    if explanation.graphql_only:
        lines += ["", "For GraphQL schemas:", "", *_disable_block(code, explanation.graphql_only)]
    return lines


def _disable_block(code: str, checks: tuple[str, ...]) -> list[str]:
    lines = [f"Run with `--exclude-checks {','.join(checks)}`, or set:", "", "```toml"]
    for index, name in enumerate(checks):
        if index:
            lines.append("")
        lines += [f"[checks.{name}]", "enabled = false"]
    lines.append("```")
    also = also_disables(code, checks)
    if also:
        lines += ["", "This also disables:", ""]
        lines += [f"- [{other}](#{other.lower()}) {title}" for other, title in also]
    return lines


if __name__ == "__main__":  # pragma: no cover
    sys.stdout.write(generate_docs_page())
