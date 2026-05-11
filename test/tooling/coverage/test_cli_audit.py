from __future__ import annotations

from scripts.coverage import audit as cli_audit
from tools.coverage.audit import PhaseName


def test_record_crash_preserves_requested_phase():
    results = []

    cli_audit._record_crash(
        "corpus://openapi-3.0/acme.json",
        "killed worker in isolation",
        results,
        phase=PhaseName.COVERAGE,
    )

    assert results[0].phase == "coverage"
