from __future__ import annotations

import pytest

from changelog import _issue_numbers

CHANGELOG = """# Changelog

## [Unreleased](https://github.com/schemathesis/schemathesis/compare/v2.0.0...HEAD) - TBD

### :bug: Fixed

- Not released yet. [#4000](https://github.com/schemathesis/schemathesis/issues/4000)

## [2.0.0](https://github.com/schemathesis/schemathesis/compare/v1.1.0...v2.0.0) - 2026-01-03

### :rocket: Added

- A feature with no reference.
- A feature with a reference. [#312](https://github.com/schemathesis/schemathesis/issues/312)

### :bug: Fixed

- A fix sharing the same reference. [#312](https://github.com/schemathesis/schemathesis/issues/312)
- A fix mentioning #999 in prose.
- A fix with a lower reference. [#57](https://github.com/schemathesis/schemathesis/issues/57)
- A fix crediting another project. [#4](https://github.com/encode/starlette/issues/4)
- A fix linking a discussion. [#88](https://github.com/schemathesis/schemathesis/discussions/88)

## [1.1.0](https://github.com/schemathesis/schemathesis/compare/v1.0.0...v1.1.0) - 2026-01-02

### :bug: Fixed

- A fix nobody reported.

## [1.0.0](https://github.com/schemathesis/schemathesis/compare/v0.9.0...v1.0.0) - 2026-01-01

### :bug: Fixed

- A fix from an older release. [#7](https://github.com/schemathesis/schemathesis/issues/7)
"""


@pytest.mark.parametrize(
    ("version", "expected"),
    [
        ("2.0.0", [57, 312]),
        ("1.1.0", []),
        ("1.0.0", [7]),
    ],
    ids=["deduplicated-and-sorted", "no-references", "last-block"],
)
def test_issue_numbers(version, expected):
    assert _issue_numbers(CHANGELOG.splitlines(keepends=True), version) == expected


def test_issue_numbers_for_unknown_version():
    with pytest.raises(RuntimeError, match="Changelog misses the 3.0.0 version"):
        _issue_numbers(CHANGELOG.splitlines(keepends=True), "3.0.0")
