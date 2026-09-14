from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from contextlib import suppress
from pathlib import Path

import pytest

from scripts.coverage import audit as cli_audit
from tools.coverage.audit import AuditError, PhaseName, audit_schema


def test_record_crash_preserves_requested_phase():
    results = []
    reporter = cli_audit._Reporter(total=1)
    with reporter:
        cli_audit._record_crash(
            ("openapi-3.0", "acme.json"),
            AuditError(stage="worker_crashed", exception=None, message="killed worker in isolation"),
            results,
            phase=PhaseName.COVERAGE,
            reporter=reporter,
        )

    assert results[0].phase == "coverage"


@pytest.mark.parametrize(
    ("bucket", "prior", "expected_fragments"),
    [
        (
            {"covered": 1100, "total": 2000, "pct": 55.0},
            {"covered": 577, "total": 2000, "pct": 28.9},
            ["+523", "+26.1%"],
        ),
        (
            {"covered": 900, "total": 2000, "pct": 45.0},
            {"covered": 1000, "total": 2000, "pct": 50.0},
            ["-100", "-5.0%"],
        ),
        ({"covered": 500, "total": 2000, "pct": 25.0}, {"covered": 500, "total": 2000, "pct": 25.0}, ["+0", "+0.0%"]),
        (
            {"covered": 600, "total": 2500, "pct": 24.0},
            {"covered": 500, "total": 2000, "pct": 25.0},
            ["totals shifted", "-1.0%"],
        ),
    ],
    ids=["gain", "loss", "no-change", "totals-shifted"],
)
def test_delta_cell_renders_absolute_and_percent(bucket, prior, expected_fragments):
    cell = cli_audit._delta_cell(bucket, prior)
    for fragment in expected_fragments:
        assert fragment in cell, cell


def test_is_complete_treats_error_only_schema_as_complete(ctx):
    raw = ctx.openapi.build_schema(
        {
            "/items": {"get": {"responses": {"200": {"description": "OK"}}}},
            "/broken": {
                "post": {
                    "parameters": [
                        {
                            "in": "query",
                            "name": "filter",
                            "required": True,
                            "schema": {"$ref": "#/components/schemas/Missing"},
                        }
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            },
        }
    )
    outcome = audit_schema(raw, api="t", corpus="external", phase=PhaseName.COVERAGE)
    assert outcome.result.errors
    assert cli_audit._is_complete(outcome.result) is True


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0, "0 B"),
        (512, "512 B"),
        (2048, "2.0 KB"),
        (-2048, "-2.0 KB"),
        (5 * 1024 * 1024, "5.0 MB"),
        (-3 * 1024**3, "-3.0 GB"),
    ],
)
def test_format_bytes_renders_signed_units(value, expected):
    assert cli_audit._format_bytes(value) == expected


def test_is_complete_still_false_when_load_failed():
    outcome = audit_schema({"not": "valid"}, api="t", corpus="external", phase=PhaseName.COVERAGE)
    assert outcome.result.errors and outcome.result.errors[0].stage == "load_failed"
    assert cli_audit._is_complete(outcome.result) is False


def test_stopping_the_driver_leaves_no_worker_processes(tmp_path):
    # Spawn workers outlive a killed parent forever, pinning ~1 GB each.
    driver = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "scripts.coverage.audit",
            "--phase=coverage",
            "--corpus=swagger-2.0",
            "--limit=24",
            "--workers=2",
            f"--out={tmp_path}",
        ],
        cwd=Path(__file__).resolve().parents[3],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    group = os.getpgid(driver.pid)
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline and len(_group_members(group)) < 3:
        time.sleep(0.1)
    assert len(_group_members(group)) >= 3, "workers never started"

    driver.send_signal(signal.SIGTERM)
    driver.wait(timeout=60)

    deadline = time.monotonic() + 15
    while time.monotonic() < deadline and _group_members(group):
        time.sleep(0.1)
    survivors = _group_members(group)
    for pid in survivors:
        with suppress(ProcessLookupError):
            os.kill(pid, signal.SIGKILL)
    assert survivors == [], f"orphaned workers: {survivors}"


def _group_members(group: int) -> list[int]:
    members = []
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            if os.getpgid(int(entry)) == group:
                members.append(int(entry))
        except (ProcessLookupError, PermissionError):
            continue
    return members
