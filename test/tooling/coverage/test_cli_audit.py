from __future__ import annotations

from scripts.coverage import audit as cli_audit
from tools.coverage.audit import PhaseName


def test_record_crash_preserves_requested_phase():
    results = []
    reporter = cli_audit._Reporter(total=1)
    with reporter:
        cli_audit._record_crash(
            ("openapi-3.0", "acme.json"),
            "killed worker in isolation",
            results,
            phase=PhaseName.COVERAGE,
            reporter=reporter,
        )

    assert results[0].phase == "coverage"
