from schemathesis.cli.summary import (
    ErrorGroup,
    FailureGroup,
    OperationsSummary,
    SummaryData,
    TestCasesSummary,
    WarningData,
)
from schemathesis.core.failures import Severity
from schemathesis.engine import Status, StopReason
from schemathesis.engine.run import PhaseName
from schemathesis.generation.stateful import STATEFUL_TESTS_LABEL
from schemathesis.reporting.html.model import ErrorEntry, ReportData, ReportMeta


def failure_group(title, *operations, count=None, type="ServerError"):
    return FailureGroup(
        type=type,
        title=title,
        severity=Severity.CRITICAL,
        count=count or len(operations),
        operations=sorted(operations),
    )


def error_entry(title, *, count=1, operations=("GET /flaky",), message="boom", traceback=None, phase="fuzzing"):
    return ErrorEntry(
        operations=list(operations), title=title, message=message, traceback=traceback, phase=phase, count=count
    )


def build_summary(
    *,
    tested=1,
    skipped=0,
    errored=0,
    failures=(),
    errors=(),
    warnings=None,
    generated=10,
    with_failures=0,
    phases=None,
):
    return SummaryData(
        operations=OperationsSummary(
            total=tested + skipped + errored,
            selected=tested + skipped + errored,
            tested=tested,
            errored=errored,
            skipped=skipped,
            skip_reasons=[],
        ),
        phases=phases if phases is not None else {PhaseName.FUZZING: (Status.SUCCESS, None)},
        test_cases=TestCasesSummary(
            generated=generated, with_failures=with_failures, unique_failures=len(failures), without_checks=0
        ),
        failures=list(failures),
        errors=[ErrorGroup(title=entry.title, count=entry.count) for entry in errors],
        warnings=warnings or WarningData(),
    )


def build_report(summary=None, **kwargs):
    defaults = {
        "meta": ReportMeta(
            generated_at="2026-09-07 12:00:00 UTC",
            location="openapi.yaml",
            base_url="http://127.0.0.1",
            command="st run openapi.yaml",
            seed=42,
        ),
        "summary": summary or build_summary(),
        "errors": [],
        "fatal_error": None,
        "running_time": 134.0,
        "stop_reason": StopReason.COMPLETED,
        "started": True,
        "complete": True,
        "exit_code": 0,
    }
    defaults.update(kwargs)
    return ReportData(**defaults)


def passing_report():
    return build_report()


def failing_report():
    errors = [error_entry("Connection error")]
    summary = build_summary(
        tested=3,
        with_failures=3,
        failures=[
            failure_group("Server error", "POST /orders", count=3),
            failure_group(
                "Undocumented HTTP status code", "POST /orders", "GET /orders/{id}", type="UndefinedStatusCode"
            ),
        ],
        errors=errors,
    )
    return build_report(summary, errors=errors, exit_code=1)


def errored_report():
    errors = [error_entry("Connection error", count=2, traceback="Traceback <b>")]
    return build_report(build_summary(tested=0, errored=1, errors=errors), errors=errors, exit_code=1)


def fatal_report():
    # A fatal error means `EngineFinished` never arrived, so the handler reports `INTERRUPTED`.
    return build_report(
        fatal_error=error_entry("Schema loading error", operations=[], message="boom <b>", phase=None),
        stop_reason=StopReason.INTERRUPTED,
        complete=False,
        exit_code=1,
    )


def empty_report():
    return build_report(build_summary(tested=0, generated=0))


def interrupted_report():
    # In-engine Ctrl-C: the engine still emits `EngineFinished` and the CLI exits 0.
    return build_report(stop_reason=StopReason.INTERRUPTED)


def never_finished_report():
    return build_report(stop_reason=StopReason.INTERRUPTED, complete=False, running_time=12.0, exit_code=1)


def stateful_report():
    summary = build_summary(
        tested=2, with_failures=2, failures=[failure_group("Server error", "POST /orders", STATEFUL_TESTS_LABEL)]
    )
    return build_report(summary, exit_code=1)


def stateful_only_report():
    summary = build_summary(tested=2, with_failures=1, failures=[failure_group("Server error", STATEFUL_TESTS_LABEL)])
    return build_report(summary, exit_code=1)


def warnings_report():
    warnings = WarningData(
        missing_auth={401: {"GET /users"}, 403: {"DELETE /users/{id}"}},
        missing_test_data={"PUT /users/{id}"},
        base_url_mismatch={"PATCH /users/{id}"},
        base_url_suggestion="http://127.0.0.1/api",
        validation_mismatch={"POST /users"},
        missing_deserializer={"application/xml": {"HEAD /users": {"200"}}},
        unused_openapi_auth={"apiKey"},
        unsupported_regex={"OPTIONS /users": {"(?<=x)y"}},
        method_not_allowed={"TRACE /users"},
        constants_extraction={"myapp"},
        unmatched_filter={"--include-name=GET /nope"},
        unresolvable_reference={"GET /users": {"#/components/schemas/Missing"}},
    )
    return build_report(build_summary(warnings=warnings))
