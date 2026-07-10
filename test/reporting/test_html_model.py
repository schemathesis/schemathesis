from schemathesis.engine import Status
from schemathesis.engine.run import PhaseName
from schemathesis.reporting.html.model import (
    CaseEntry,
    FailureEntry,
    OperationEntry,
    PhaseCases,
    PhaseTiming,
    ReportData,
)


def make_operation(label, status, *, failing_cases=(), cases_per_phase=None, error_count=0):
    return OperationEntry(
        label=label,
        status=status,
        summary=None,
        definition=None,
        skip_reason=None,
        elapsed=0.0,
        cases_per_phase=cases_per_phase or {},
        failing_cases=list(failing_cases),
        error_count=error_count,
    )


def make_case(case_id, checks):
    return CaseEntry(
        case_id=case_id,
        phase=PhaseName.FUZZING,
        failures=[FailureEntry(check_name=name, title=name, message="") for name in checks],
        curl=None,
        response_status=500,
        response_message="Internal Server Error",
        response_body=None,
        response_content_type=None,
        elapsed_ms=None,
        parent_steps=[],
    )


def make_report(operations):
    return ReportData(
        generated_at="2026-07-09 12:00:00 UTC",
        location=None,
        base_url=None,
        command=None,
        seed=None,
        phases={},
        operations={entry.label: entry for entry in operations},
        ticks=[],
        warnings=None,
        errors=[],
        fatal_errors=[],
        running_time=None,
        stop_reason=None,
    )


def test_operation_entry_method_and_path():
    entry = make_operation("GET /users/{id}", Status.SUCCESS)
    assert (entry.method, entry.path) == ("GET", "/users/{id}")


def test_operation_entry_counts():
    entry = make_operation(
        "POST /orders",
        Status.FAILURE,
        failing_cases=[make_case("a", ["server_error", "status_code_conformance"]), make_case("b", ["server_error"])],
        cases_per_phase={
            PhaseName.COVERAGE: PhaseCases(total=14, failed=0),
            PhaseName.FUZZING: PhaseCases(total=473, failed=2),
        },
    )
    assert entry.total_cases == 487
    assert entry.failed_checks_count == 3
    assert entry.check_counts == [("server_error", 2), ("status_code_conformance", 1)]


def test_report_data_groups_operations_by_status():
    failed = make_operation("POST /orders", Status.FAILURE)
    passed = make_operation("GET /health", Status.SUCCESS)
    skipped = make_operation("GET /internal", Status.SKIP)
    data = make_report([failed, passed, skipped])
    assert data.failed_operations == [failed]
    assert data.passed_operations == [passed]
    assert data.skipped_operations == [skipped]


def test_report_data_top_failures_counts_affected_operations():
    data = make_report(
        [
            make_operation("POST /a", Status.FAILURE, failing_cases=[make_case("x", ["server_error"])]),
            make_operation(
                "POST /b",
                Status.FAILURE,
                # Same check failing twice within one operation counts once.
                failing_cases=[make_case("y", ["server_error"]), make_case("z", ["server_error", "ignored_auth"])],
            ),
        ]
    )
    assert data.top_failures == [("server_error", 2), ("ignored_auth", 1)]


def test_report_data_executed_phases_ordered_and_complete():
    data = make_report([])
    data.phases[PhaseName.FUZZING] = PhaseTiming(started_at=10.0, finished_at=20.0)
    data.phases[PhaseName.EXAMPLES] = PhaseTiming(started_at=1.0, finished_at=2.0)
    data.phases[PhaseName.COVERAGE] = PhaseTiming(started_at=5.0, finished_at=None)
    assert [phase for phase, _ in data.executed_phases] == [PhaseName.EXAMPLES, PhaseName.FUZZING]
