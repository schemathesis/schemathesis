import json

import jsonschema_rs
import pytest
from _pytest.main import ExitCode
from flask import jsonify

import schemathesis
from schemathesis.checks import CHECKS
from schemathesis.cli.wfc_report import fault_category
from schemathesis.core.failures import AcceptedNegativeData, MalformedJson, ResponseTimeExceeded, ServerError
from schemathesis.engine.events import ScenarioFinished
from schemathesis.openapi.checks import (
    AllowHeaderMismatch,
    EnsureResourceAvailability,
    IgnoredAuth,
    MissingHeaderNotRejected,
    RejectedPositiveData,
    UnsupportedMethodResponse,
    UseAfterFree,
)

# WFC Report 0.7.0 (WebFuzzing/Commons e133b21) with its two known defects fixed: `FoundFault` requires
# `operationId` (upstream lists the undeclared `endpointId`) and codes 105-120 / 300-310 are allowed.
FAULT_CATEGORY = {
    "type": "object",
    "properties": {
        "code": {
            "anyOf": [
                {"type": "integer", "minimum": 100, "maximum": 120},
                {"type": "integer", "minimum": 200, "maximum": 206},
                {"type": "integer", "minimum": 300, "maximum": 310},
                {"type": "integer", "minimum": 900, "maximum": 999},
            ]
        },
        "context": {"type": ["string", "null"]},
    },
    "required": ["code"],
}
WFC_REPORT_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {
        "schemaVersion": {"type": "string"},
        "toolName": {"type": "string"},
        "toolVersion": {"type": "string"},
        "creationTime": {"type": "string", "format": "date-time"},
        "faults": {
            "type": "object",
            "properties": {
                "totalNumber": {"type": "integer", "minimum": 0},
                "foundFaults": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "operationId": {"type": "string"},
                            "testCaseId": {"type": "string"},
                            "faultCategories": {
                                "type": "array",
                                "items": FAULT_CATEGORY,
                                "minItems": 1,
                                "uniqueItems": True,
                            },
                        },
                        "required": ["operationId", "testCaseId", "faultCategories"],
                    },
                },
            },
            "required": ["totalNumber", "foundFaults"],
        },
        "problemDetails": {
            "type": "object",
            "properties": {
                "rest": {
                    "type": "object",
                    "properties": {
                        "outputHttpCalls": {"type": "integer", "minimum": 0},
                        "evaluatedHttpCalls": {"type": "integer", "minimum": 0},
                        "endpointIds": {"type": "array", "items": {"type": "string"}, "uniqueItems": True},
                        "coveredHttpStatus": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "endpointId": {"type": "string"},
                                    "testCaseId": {"type": "string"},
                                    "httpStatus": {
                                        "type": ["array", "null"],
                                        "items": {"type": ["integer", "null"], "minimum": 0, "maximum": 599},
                                        "uniqueItems": True,
                                    },
                                },
                                "required": ["endpointId", "testCaseId", "httpStatus"],
                            },
                        },
                    },
                    "required": ["outputHttpCalls", "evaluatedHttpCalls", "endpointIds", "coveredHttpStatus"],
                }
            },
        },
        "totalTests": {"type": "integer", "minimum": 0},
        "testFilePaths": {"type": "array", "items": {"type": "string"}, "uniqueItems": True},
        "testCases": {"type": "array", "items": {"type": "object"}},
        "executionTimeInSeconds": {"type": "integer", "minimum": 0},
    },
    "required": [
        "schemaVersion",
        "toolName",
        "toolVersion",
        "creationTime",
        "faults",
        "problemDetails",
        "totalTests",
        "testFilePaths",
        "testCases",
        "executionTimeInSeconds",
    ],
}


def load_report(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_server_error_report(ctx, cli, tmp_path):
    api = ctx.openapi.apps.failure()
    path = tmp_path / "report.json"

    cli.run_and_assert(
        api.schema_url,
        f"--report-wfc-path={path}",
        "--max-examples=1",
        "--phases=fuzzing",
        "--checks=not_a_server_error",
        exit_code=ExitCode.TESTS_FAILED,
    )

    report = load_report(path)
    jsonschema_rs.validate(WFC_REPORT_SCHEMA, report)
    [fault] = report["faults"]["foundFaults"]
    assert fault == {
        "operationId": "GET:/api/failure",
        "testCaseId": fault["testCaseId"],
        "faultCategories": [{"code": 100, "context": "GET:/api/failure -> ServerError: Server error (status 500)"}],
    }
    assert report["faults"]["totalNumber"] == 1
    assert report["testFilePaths"] == []
    assert report["testCases"] == [{"id": fault["testCaseId"], "name": "Server error"}]
    assert report["problemDetails"]["rest"]["coveredHttpStatus"] == [
        {"endpointId": "GET:/api/failure", "testCaseId": "GET:/api/failure", "httpStatus": [500]}
    ]


def test_same_failure_on_two_operations_is_two_faults(ctx, cli, tmp_path):
    # WFC identifies a fault by code and context, so the context has to name the operation.
    app, _ = ctx.openapi.make_flask_app(
        {
            "/first": {"get": {"responses": {"200": {"description": "OK"}}}},
            "/second": {"get": {"responses": {"200": {"description": "OK"}}}},
        }
    )

    @app.route("/first")
    def first():
        return jsonify({}), 500

    @app.route("/second")
    def second():
        return jsonify({}), 500

    path = tmp_path / "report.json"
    cli.run_openapi_app(
        app, f"--report-wfc-path={path}", "--max-examples=1", "--phases=fuzzing", "--checks=not_a_server_error"
    )

    assert load_report(path)["faults"]["totalNumber"] == 2


def test_endpoint_ids_list_declared_operations(ctx, cli, tmp_path):
    # Probes for undeclared methods are requests, not endpoints; filtered-out operations are still endpoints.
    api = ctx.openapi.apps.success_and_failure()
    path = tmp_path / "report.json"

    cli.run(
        api.schema_url,
        f"--report-wfc-path={path}",
        "--max-examples=1",
        "--phases=coverage",
        "--include-path=/api/success",
    )

    rest = load_report(path)["problemDetails"]["rest"]
    assert rest["endpointIds"] == ["GET:/api/failure", "GET:/api/success"]
    assert [entry["endpointId"] for entry in rest["coveredHttpStatus"]] == ["GET:/api/success"]


def test_one_case_can_carry_several_faults(ctx, cli, tmp_path):
    app, _ = ctx.openapi.make_flask_app({"/items": {"get": {"responses": {"200": {"description": "OK"}}}}})

    @app.route("/items")
    def items():
        return jsonify({}), 500

    path = tmp_path / "report.json"
    cli.run_openapi_app(
        app,
        f"--report-wfc-path={path}",
        "--max-examples=1",
        "--phases=fuzzing",
        "--checks=not_a_server_error,status_code_conformance",
    )

    report = load_report(path)
    assert {
        (fault["testCaseId"], fault["faultCategories"][0]["code"]) for fault in report["faults"]["foundFaults"]
    } == {(case["id"], code) for case in report["testCases"] for code in (100, 200)}
    assert len(report["testCases"]) == 1


def test_identical_failures_on_one_case_are_one_fault(ctx, cli, tmp_path):
    app, _ = ctx.openapi.make_flask_app(
        {
            "/items": {
                "get": {
                    "responses": {
                        "200": {
                            "description": "OK",
                            "headers": {
                                "X-First": {"schema": {"type": "integer"}},
                                "X-Second": {"schema": {"type": "integer"}},
                            },
                        }
                    }
                }
            }
        }
    )

    @app.route("/items")
    def items():
        return jsonify({}), 200, {"X-First": "text", "X-Second": "text"}

    path = tmp_path / "report.json"
    cli.run_openapi_app(
        app,
        f"--report-wfc-path={path}",
        "--max-examples=1",
        "--phases=fuzzing",
        "--checks=response_headers_conformance",
    )

    assert len(load_report(path)["faults"]["foundFaults"]) == 1


@pytest.fixture
def unmapped_check():
    @schemathesis.check
    def unmapped_check(ctx, response, case):
        raise AssertionError("Custom finding")

    yield unmapped_check

    CHECKS.unregister(unmapped_check.__name__)


def test_failures_without_wfc_category_are_left_out(ctx, cli, tmp_path, unmapped_check):
    api = ctx.openapi.apps.success()
    path = tmp_path / "report.json"

    cli.run(
        api.schema_url,
        f"--report-wfc-path={path}",
        "--max-examples=1",
        "--phases=fuzzing",
        "--checks=unmapped_check,not_a_server_error",
    )

    report = load_report(path)
    assert (report["faults"], report["testCases"]) == ({"totalNumber": 0, "foundFaults": []}, [])


def test_path_extensions_are_not_endpoints(ctx, cli, tmp_path):
    raw = ctx.openapi.build_schema({"/items": {"get": {"responses": {"200": {"description": "OK"}}}}})
    raw["paths"]["x-internal"] = "Not an operation"
    schema = tmp_path / "schema.json"
    schema.write_text(json.dumps(raw), encoding="utf-8")
    path = tmp_path / "report.json"

    cli.run(str(schema), "--url=http://127.0.0.1:1", f"--report-wfc-path={path}", "--phases=examples")

    assert load_report(path)["problemDetails"]["rest"]["endpointIds"] == ["GET:/items"]


def test_report_when_schema_never_loads(cli, tmp_path):
    schema = tmp_path / "schema.yaml"
    schema.write_text("{{{ not yaml", encoding="utf-8")
    path = tmp_path / "report.json"

    cli.run(str(schema), f"--report-wfc-path={path}")

    report = load_report(path)
    jsonschema_rs.validate(WFC_REPORT_SCHEMA, report)
    assert (report["executionTimeInSeconds"], report["problemDetails"]["rest"]["endpointIds"]) == (0, [])


def test_report_when_run_stops_early(ctx, cli, tmp_path):
    # An engine that started but never finished still has a running time to report.
    @schemathesis.cli.handler()
    class StopAfterFirstScenario(schemathesis.cli.EventHandler):
        def handle_event(self, run_ctx, event) -> None:
            if isinstance(event, ScenarioFinished):
                raise RuntimeError("stop")

    api = ctx.openapi.apps.success()
    path = tmp_path / "report.json"

    cli.main("run", api.schema_url, f"--report-wfc-path={path}", "--max-examples=1")

    jsonschema_rs.validate(WFC_REPORT_SCHEMA, load_report(path))


def test_report_format_uses_default_path(ctx, cli, tmp_path):
    api = ctx.openapi.apps.success()
    cli.run_and_assert(
        api.schema_url,
        "--report=wfc",
        f"--report-dir={tmp_path}",
        "--max-examples=1",
        "--phases=fuzzing",
    )
    [report] = tmp_path.glob("wfc-*.json")
    jsonschema_rs.validate(WFC_REPORT_SCHEMA, load_report(report))


OPERATION = "GET /users/{id}"


@pytest.mark.parametrize(
    ("failure", "code"),
    [
        (ServerError(operation=OPERATION, status_code=500), 100),
        (ServerError(operation=OPERATION, status_code=503), 100),
        (ServerError(operation=OPERATION, status_code=501), 205),
        (
            MalformedJson(
                operation=OPERATION, message="", validation_message="", document="", position=0, lineno=1, colno=1
            ),
            200,
        ),
        (
            AllowHeaderMismatch(
                operation=OPERATION, allow_header="GET", missing_methods=[], undocumented_methods=["PUT"], message=""
            ),
            201,
        ),
        (RejectedPositiveData(operation=OPERATION, message="", status_code=406, allowed_statuses=["2xx"]), 204),
        (RejectedPositiveData(operation=OPERATION, message="", status_code=400, allowed_statuses=["2xx"]), 901),
        (AcceptedNegativeData(operation=OPERATION, message="", status_code=200, expected_statuses=["400"]), 206),
        (AcceptedNegativeData(operation=OPERATION, message="", status_code=302, expected_statuses=["400"]), 904),
        (
            MissingHeaderNotRejected(
                operation=OPERATION, header_name="X", status_code=201, expected_statuses=[400], message=""
            ),
            206,
        ),
        (
            MissingHeaderNotRejected(
                operation=OPERATION, header_name="X", status_code=500, expected_statuses=[400], message=""
            ),
            904,
        ),
        (
            UnsupportedMethodResponse(
                operation=OPERATION, method="TRACE", status_code=405, failure_reason="missing_allow_header", message=""
            ),
            110,
        ),
        (
            UnsupportedMethodResponse(
                operation=OPERATION, method="TRACE", status_code=200, failure_reason="wrong_status", message=""
            ),
            310,
        ),
        (
            UnsupportedMethodResponse(
                operation=OPERATION, method="TRACE", status_code=400, failure_reason="wrong_status", message=""
            ),
            905,
        ),
        (UseAfterFree(operation=OPERATION, message="", free="DELETE /users/1", usage="GET /users/1"), 113),
        (IgnoredAuth(operation="DELETE /users/{id}", message=""), 308),
        (IgnoredAuth(operation=OPERATION, message=""), 905),
        (
            EnsureResourceAvailability(
                operation=OPERATION, message="", created_with="POST /users", not_available_with="GET /users/1"
            ),
            902,
        ),
        (ResponseTimeExceeded(operation=OPERATION, elapsed=2.0, deadline=1.0, message=""), 903),
    ],
    ids=lambda value: type(value).__name__ if not isinstance(value, int) else str(value),
)
def test_fault_category(failure, code):
    assert fault_category(failure)[0] == code
