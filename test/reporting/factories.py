from schemathesis.engine import Status
from schemathesis.engine.run import PhaseName
from schemathesis.reporting.html.model import (
    CaseEntry,
    ErrorEntry,
    FailureEntry,
    FailureTick,
    OperationEntry,
    PhaseCases,
    PhaseTiming,
    ReportData,
    TickItem,
)


def build_case(case_id, checks):
    return CaseEntry(
        case_id=case_id,
        phase=PhaseName.FUZZING,
        failures=[FailureEntry(check_name=name, title=name, message="msg") for name in checks],
        curl="curl -X GET http://x",
        response_status=500,
        response_message="Internal Server Error",
        response_body=None,
        response_content_type=None,
        elapsed_ms=10,
        parent_steps=[],
    )


def build_operation(
    label, status, *, failing_cases=(), cases_per_phase=None, skip_reason=None, error_count=0, summary=None, definition=None
):
    return OperationEntry(
        label=label,
        status=status,
        summary=summary,
        definition=definition,
        skip_reason=skip_reason,
        elapsed=1.0,
        cases_per_phase=cases_per_phase or {PhaseName.FUZZING: PhaseCases(total=10, failed=0)},
        failing_cases=list(failing_cases),
        error_count=error_count,
    )


def build_report(operations, **kwargs):
    defaults = {
        "generated_at": "2026-07-09 12:00:00 UTC",
        "location": "openapi.yaml",
        "base_url": "http://127.0.0.1",
        "command": "st run openapi.yaml",
        "seed": 42,
        "phases": {},
        "operations": {entry.label: entry for entry in operations},
        "ticks": [],
        "warnings": None,
        "errors": [],
        "fatal_errors": [],
        "running_time": 134.0,
        "stop_reason": None,
    }
    defaults.update(kwargs)
    return ReportData(**defaults)


def failing_report():
    case = build_case("abc123", ["server_error"])
    entry = build_operation("POST /orders", Status.FAILURE, failing_cases=[case])
    return build_report(
        [entry],
        phases={PhaseName.FUZZING: PhaseTiming(started_at=100.0, finished_at=140.0)},
        ticks=[FailureTick(at=120.0, items=[TickItem("server_error", "POST /orders", "abc123")])],
    )


def passing_report():
    entry = build_operation("GET /health", Status.SUCCESS)
    return build_report([entry], phases={PhaseName.FUZZING: PhaseTiming(started_at=100.0, finished_at=110.0)})


def errored_report():
    entry = build_operation("GET /flaky", Status.ERROR, error_count=1)
    return build_report(
        [entry],
        fatal_errors=[ErrorEntry(label="GET /flaky", title="RequestError", message="boom", traceback="tb", phase="fuzzing")],
    )


def graphql_report():
    entry = build_operation("Query.getBooks", Status.FAILURE, failing_cases=[build_case("g1", ["server_error"])])
    return build_report([entry])


def skipped_report():
    entry = build_operation("GET /skipped", Status.SKIP, skip_reason="no test data")
    return build_report([entry])


def timeline_report():
    return failing_report()


def empty_report():
    return build_report([])
