import json
import warnings

import pytest


def pytest_configure(config):
    warnings.filterwarnings("ignore", category=pytest.PytestDeprecationWarning)


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    """Report `KNOWN_BODY_VIOLATIONS` entries that never fired so the list doesn't rot.

    Skipped under `-k` filters: partial runs naturally leave entries undrained.
    """
    if config.getoption("keyword"):
        return
    if not any(
        report.nodeid.startswith("test_corpus.py::test_coverage_phase")
        for report in terminalreporter.getreports("passed") + terminalreporter.getreports("failed")
    ):
        return
    try:
        from test_corpus import _PENDING_BODY_VIOLATIONS
    except ImportError:
        return
    if not _PENDING_BODY_VIOLATIONS:
        return
    terminalreporter.section("Stale KNOWN_BODY_VIOLATIONS entries", sep="-", red=True)
    terminalreporter.write_line(
        f"{len(_PENDING_BODY_VIOLATIONS)} entries did not fire — bodies are now valid; remove them:"
    )
    for schema_id, label in sorted(_PENDING_BODY_VIOLATIONS):
        terminalreporter.write_line(f"  - ({schema_id!r}, {label!r})")


def clean_schema(obj):
    # A helper to display schemas without fields that make too much noise and are irrelevant to dependency analysis
    if isinstance(obj, dict):
        return {k: clean_schema(v) for k, v in obj.items() if k not in ("description", "title", "summary")}
    elif isinstance(obj, list):
        return [clean_schema(item) for item in obj]
    else:
        return obj


@pytest.fixture
def save_schema():
    def save_schema(schema, filename="schema.json"):
        with open(filename, "w") as fd:
            json.dump(clean_schema(schema), fd, indent=4)

    return save_schema
