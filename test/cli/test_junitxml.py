import platform
import re
from xml.etree import ElementTree

import pytest
from _pytest.main import ExitCode
from flask import Response, jsonify

from schemathesis.cli.commands.run import executor
from test.apps.catalog.openapi import basic
from test.utils import allure_results


def test_junitxml_option(ctx, cli, hypothesis_max_examples, tmp_path):
    api = ctx.openapi.apps.success()
    xml_path = tmp_path / "junit.xml"
    cli.run_and_assert(
        api.schema_url,
        f"--report-junit-path={xml_path}",
        f"--max-examples={hypothesis_max_examples or 2}",
        "--checks=not_a_server_error",
        "--seed=1",
    )
    assert xml_path.exists()
    ElementTree.parse(xml_path)


@pytest.mark.parametrize("in_config", [True, False])
@pytest.mark.parametrize("path", ["junit.xml", "does-not-exist/junit.xml"])
def test_junitxml_file(ctx, cli, hypothesis_max_examples, tmp_path, path, in_config):
    api = ctx.openapi.serve(basic.success_failure_unsatisfiable_empty_string())
    server_host = api.base_url.removeprefix("http://")
    xml_path = tmp_path / path
    args = [
        f"--max-examples={hypothesis_max_examples or 1}",
        "--seed=1",
        "--checks=all",
        "--exclude-checks=positive_data_acceptance",
        "--mode=positive",
    ]
    kwargs = {}
    if in_config:
        kwargs["config"] = {"reports": {"junit": {"path": str(xml_path)}}}
        args.append("--report=junit")
    else:
        args.append(f"--report-junit-path={xml_path}")
    cli.run_and_assert(api.schema_url, *args, exit_code=ExitCode.TESTS_FAILED, **kwargs)
    tree = ElementTree.parse(xml_path)
    root = tree.getroot()
    assert root.tag == "testsuites"
    assert root.attrib["errors"] == "1"
    assert root.attrib["failures"] == "2"
    assert root.attrib["tests"] == "4"
    testsuite = root[0]
    assert testsuite.tag == "testsuite"
    assert testsuite.attrib["name"] == "schemathesis"
    assert testsuite.attrib["errors"] == "1"
    assert testsuite.attrib["failures"] == "2"
    assert testsuite.attrib["tests"] == "4"
    testcases = list(testsuite)
    assert len(testcases) == 4

    testcases_by_name = {tc.attrib["name"]: tc for tc in testcases}

    failure = testcases_by_name["GET /api/failure"]
    assert failure.tag == "testcase"
    assert failure[0].tag == "failure"
    assert failure[0].attrib == {"type": "failure", "message": "ST101"}
    message = extract_message(failure[0], server_host)
    assert "Server error" in message
    assert "[500] Internal Server Error" in message
    assert "curl -X GET http://localhost/api/failure" in message

    success = testcases_by_name["GET /api/success"]
    assert success.tag == "testcase"

    error = testcases_by_name["POST /api/unsatisfiable"]
    assert error.tag == "testcase"
    assert error[0].tag == "error"
    assert error[0].attrib["type"] == "error"
    assert (
        error[0]
        .text.replace("\n", " ")
        .startswith("Schema Error  Cannot generate test data for request body (application/json) Schema:")
    )


INVALID_ITEMS = {"type": "array", "items": {"type": "string", "required": True}}
NO_INTEGER = {"type": "integer", "minimum": 5, "maximum": 1}
CREATED = {
    "201": {
        "description": "Created",
        "content": {"application/json": {"schema": {"type": "object", "properties": {"id": {"type": "integer"}}}}},
    }
}


def _create(schema):
    return {
        "post": {
            "requestBody": {"required": True, "content": {"application/json": {"schema": schema}}},
            "responses": CREATED,
        }
    }


def _read(name):
    return {
        "get": {
            "parameters": [{"name": name, "in": "path", "required": True, "schema": {"type": "integer"}}],
            "responses": {"200": {"description": "OK"}},
        }
    }


@pytest.mark.snapshot(replace_reproduce_with=True)
@pytest.mark.parametrize(
    ("mode", "version", "body", "expected"),
    [
        ("positive", "3.0.2", INVALID_ITEMS, "Invalid `items` definition"),
        ("all", "3.1.0", INVALID_ITEMS, "Invalid `required` definition"),
        ("positive", "3.0.2", NO_INTEGER, "Cannot generate test data for request body (application/json)"),
    ],
    ids=["invalid-schema", "invalid-schema-openapi-3.1", "unsatisfiable"],
)
def test_operation_error_reported_once_across_phases(ctx, cli, tmp_path, snapshot_cli, mode, version, body, expected):
    xml_path = tmp_path / "junit.xml"
    html_dir = tmp_path / "report"
    app, _ = ctx.openapi.make_flask_app(
        {
            "/users": _create({"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}),
            "/users/{userId}": _read("userId"),
            "/items": _create(body),
            "/items/{itemId}": _read("itemId"),
        },
        version=version,
    )

    @app.route("/users", methods=["POST"])
    @app.route("/items", methods=["POST"])
    def create():
        return jsonify({"id": 1}), 201

    @app.route("/users/<int:user_id>")
    @app.route("/items/<int:item_id>")
    def read(**_):
        return jsonify({"id": 1})

    result = cli.run_openapi_app(
        app,
        "--phases=coverage,fuzzing,stateful",
        f"--mode={mode}",
        "--checks=not_a_server_error",
        "--max-examples=10",
        "--seed=1",
        f"--report-junit-path={xml_path}",
        f"--report-html-path={html_dir}",
    )
    testcases = {testcase.attrib["name"]: testcase for testcase in ElementTree.parse(xml_path).getroot()[0]}
    errors = [child.text.splitlines()[2] for child in testcases["POST /items"] if child.tag == "error"]
    assert errors == [expected]
    assert (html_dir / "index.html").read_text(encoding="utf-8").count('<article class="case case-error">') == 1
    assert result == snapshot_cli


@pytest.mark.skipif(
    platform.system() == "Windows",
    reason="Cover only tracebacks that highlight error positions in every line",
)
@pytest.mark.parametrize(
    ("hooks", "args", "name", "text"),
    [
        (
            "@schemathesis.check\ndef with_error(ctx, response, case):\n    1 / 0\n",
            ("-c", "with_error"),
            "GET /api/success",
            "Runtime Error  division by zero      Traceback (most recent call last): ",
        ),
        (
            "@schemathesis.hook\ndef before_load_schema(context, raw_schema):\n    1 / 0\n",
            (),
            "Hook Error",
            "Hook Error  Error in `before_load_schema` hook: ZeroDivisionError: division by zero      Traceback (most recent call last): ",
        ),
    ],
    ids=["check", "schema-loading-hook"],
)
def test_error_with_traceback(ctx, cli, tmp_path, hooks, args, name, text):
    api = ctx.openapi.apps.success()
    xml_path = tmp_path / "junit.xml"
    cli.main("run", api.schema_url, *args, f"--report-junit-path={xml_path}", hooks=ctx.write_pymodule(hooks))
    testsuite = ElementTree.parse(xml_path).getroot()[0]
    testcase = testsuite[0]
    assert (testsuite.attrib["errors"], testcase.attrib["name"], testcase[0].tag) == ("1", name, "error")
    assert testcase[0].text.replace("\n", " ").startswith(text)


def test_internal_error(ctx, cli, tmp_path, monkeypatch):
    def crash(schema):
        raise RuntimeError("Engine crashed")

    monkeypatch.setattr(executor, "from_schema", crash)
    xml_path = tmp_path / "junit.xml"
    cli.main("run", ctx.openapi.apps.success().schema_url, f"--report-junit-path={xml_path}")
    testsuite = ElementTree.parse(xml_path).getroot()[0]
    testcase = testsuite[0]
    assert (testsuite.attrib["errors"], testcase.attrib["name"], testcase[0].tag) == (
        "1",
        "Test Execution Error",
        "error",
    )
    # The traceback below carries machine-specific paths
    assert testcase[0].text.startswith("Runtime Error\n\nEngine crashed\n\n    Traceback (most recent call last):\n")


@pytest.mark.parametrize("command", ["run", "fuzz"])
@pytest.mark.parametrize(
    ("location", "error"),
    [
        (
            "{malformed}",
            "API schema does not appear syntactically valid\n\n"
            "    Expecting property name enclosed in double quotes: line 1 column 31 (char 30)",
        ),
        (
            "{base_url}/missing.json",
            "Failed to load schema due to client error (HTTP 404 Not Found)\n\n"
            "Tip: Verify that the URL points directly to the Open API schema or GraphQL endpoint",
        ),
    ],
    ids=["malformed-schema", "missing-schema"],
)
def test_schema_loading_error(ctx, cli, tmp_path, command, location, error):
    api = ctx.openapi.apps.success()
    malformed = tmp_path / "broken.json"
    malformed.write_text('{"openapi": "3.0.0", "info": {', encoding="utf-8")
    xml_path = tmp_path / "junit.xml"
    location = location.format(malformed=malformed, base_url=api.base_url)
    cli.main(command, location, f"--report-junit-path={xml_path}")
    counts = 'errors="1" failures="0" skipped="0" tests="1" time="0.000000"'
    assert xml_path.read_text(encoding="utf-8") == (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        f"<testsuites {counts}>\n"
        f'  <testsuite name="schemathesis" hostname="{platform.node()}" {counts}>\n'
        '    <testcase name="Schema Loading Error" time="0.000000">\n'
        f'      <error type="error">{error}</error>\n'
        "    </testcase>\n"
        "  </testsuite>\n"
        "</testsuites>"
    )


def extract_message(testcase, server_host):
    return (
        re.sub(r"Test Case ID: (\w+)", "Test Case ID: <PLACEHOLDER>", testcase.text or testcase.attrib["message"])
        .replace(server_host, "localhost")
        .replace("\n", " ")
    )


def test_binary_response(ctx, cli, app_runner, tmp_path):
    xml_path = tmp_path / "junit.xml"
    app, _ = ctx.openapi.make_flask_app(
        {
            "/binary": {
                "get": {
                    "responses": {
                        "default": {
                            "description": "text",
                            "content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}},
                        }
                    }
                },
            },
        }
    )

    @app.route("/api/binary")
    def binary():
        return Response(
            b"\xa7\xf5=\x18H\xc7\xff'\xf0\xeep\x06M-RX",
            content_type="application/octet-stream",
            status=500,
        )

    base_url = app_runner.openapi_url(app, path="")
    cli.run(
        f"{base_url}/openapi.json",
        f"--url={base_url}/api",
        "--checks=all",
        f"--report-junit-path={xml_path}",
        "--exclude-checks=positive_data_acceptance",
    )
    tree = ElementTree.parse(xml_path)
    testsuite = tree.getroot()[0]
    testcases = list(testsuite)
    assert testcases[0].tag == "testcase"
    assert testcases[0].attrib["name"] == "GET /binary"
    assert testcases[0][0].tag == "failure"
    assert testcases[0][0].attrib["type"] == "failure"
    assert (
        extract_message(testcases[0][0], base_url.removeprefix("http://"))
        == "1. Test Case ID: <PLACEHOLDER>  - ST101 Server error  [500] Internal Server Error:      <BINARY>  Reproduce with:      curl -X GET http://localhost/api/binary"
    )


@pytest.mark.parametrize("charset", ["bogus-xyz", "undefined"], ids=["unknown-charset", "undefined-codec"])
def test_bad_charset_response(ctx, cli, app_runner, tmp_path, charset):
    xml_path = tmp_path / "junit.xml"
    app, _ = ctx.openapi.make_flask_app({"/boom": {"get": {"responses": {"500": {"description": "Error"}}}}})

    @app.route("/api/boom")
    def boom():
        return Response(b"boom", content_type=f"text/plain; charset={charset}", status=500)

    base_url = app_runner.openapi_url(app, path="")
    cli.run(
        f"{base_url}/openapi.json",
        f"--url={base_url}/api",
        "--checks=not_a_server_error",
        f"--report-junit-path={xml_path}",
    )
    tree = ElementTree.parse(xml_path)
    testsuite = tree.getroot()[0]
    testcases = list(testsuite)
    assert testcases[0].tag == "testcase"
    assert testcases[0][0].tag == "failure"
    assert (
        extract_message(testcases[0][0], base_url.removeprefix("http://"))
        == "1. Test Case ID: <PLACEHOLDER>  - ST101 Server error  [500] Internal Server Error:      `boom`  Reproduce with:      curl -X GET http://localhost/api/boom"
    )


def test_control_characters_in_failure_message(ctx, cli, app_runner, tmp_path):
    xml_path = tmp_path / "junit.xml"
    app, _ = ctx.openapi.make_flask_app({"/boom": {"get": {"responses": {"500": {"description": "Error"}}}}})

    @app.route("/api/boom")
    def boom():
        return Response("bad token \x12\x00\ufffe in body", content_type="text/plain; charset=utf-8", status=500)

    base_url = app_runner.openapi_url(app, path="")
    cli.run(
        f"{base_url}/openapi.json",
        f"--url={base_url}/api",
        "--checks=not_a_server_error",
        f"--report-junit-path={xml_path}",
    )
    testcase = ElementTree.parse(xml_path).getroot()[0][0]
    assert (
        extract_message(testcase[0], base_url.removeprefix("http://"))
        == r"1. Test Case ID: <PLACEHOLDER>  - ST101 Server error  [500] Internal Server Error:      `bad token \x12\x00\ufffe in body`  Reproduce with:      curl -X GET http://localhost/api/boom"
    )


def test_timeout(ctx, cli, tmp_path, hypothesis_max_examples):
    api = ctx.openapi.apps.slow()
    xml_path = tmp_path / "junit.xml"
    cli.run(
        api.schema_url,
        f"--report-junit-path={xml_path}",
        f"--max-examples={hypothesis_max_examples or 1}",
        "--seed=1",
        "--request-timeout=0.01",
        "--checks=all",
    )
    tree = ElementTree.parse(xml_path)
    testsuite = tree.getroot()[0]
    testcases = list(testsuite)
    assert testcases[0].tag == "testcase"
    assert testcases[0].attrib["name"] == "GET /api/slow"
    assert testcases[0][0].tag == "error"
    assert testcases[0][0].attrib["type"] == "error"
    assert "Read timed out after 0.01 seconds" in testcases[0][0].text


def test_skipped(ctx, cli, tmp_path):
    api = ctx.openapi.apps.success()
    xml_path = tmp_path / "junit.xml"
    cli.run(
        api.schema_url,
        f"--report-junit-path={xml_path}",
        "--seed=1",
        "--phases=examples",
        "--checks=all",
    )
    tree = ElementTree.parse(xml_path)
    testsuite = tree.getroot()[0]
    testcases = list(testsuite)
    assert testcases[0].tag == "testcase"
    assert testcases[0].attrib["name"] == "GET /api/success"
    assert testcases[0][0].tag == "skipped"
    assert testcases[0][0].attrib["type"] == "skipped"
    assert extract_message(testcases[0][0], f"127.0.0.1:{api.port}") == "No examples in schema"


def test_examples_phase_skip_cleared_when_coverage_runs(ctx, cli, tmp_path):
    # `success` has no inline examples, so the Examples phase skips it while the Coverage phase runs.
    api = ctx.openapi.apps.success()
    xml_path = tmp_path / "junit.xml"
    cli.run(
        api.schema_url,
        f"--report-junit-path={xml_path}",
        "--phases=examples,coverage",
        "--checks=all",
    )
    tree = ElementTree.parse(xml_path)
    testsuite = tree.getroot()[0]
    testcases = list(testsuite)
    assert testcases[0].tag == "testcase"
    assert testcases[0].attrib["name"] == "GET /api/success"
    assert not testcases[0].findall("skipped")


@pytest.mark.parametrize("path", ["junit.xml", "does-not-exist/junit.xml"])
@pytest.mark.skipif(platform.system() == "Windows", reason="Unclear how to trigger the permission error on Windows")
def test_permission_denied(ctx, cli, tmp_path, path):
    api = ctx.openapi.apps.success()
    dir_path = tmp_path / "output"
    dir_path.mkdir(mode=0o555)
    xml_path = dir_path / path
    result = cli.run_and_assert(api.schema_url, f"--report-junit-path={xml_path}", exit_code=ExitCode.INTERRUPTED)

    assert "Permission denied" in result.stdout or "Permission denied" in result.stderr


@pytest.mark.skipif(platform.system() != "Linux", reason="Requires /dev/full")
def test_report_write_error(ctx, cli, snapshot_cli):
    api = ctx.openapi.apps.success()
    result = cli.run(
        api.schema_url,
        "--phases=fuzzing",
        "--max-examples=1",
        "--checks=not_a_server_error",
        "--report-junit-path=/dev/full",
    )
    assert result.exit_code == ExitCode.OK
    assert result == snapshot_cli


def test_after_run_failure_in_junit(ctx, cli, ensure_reachability_module, tmp_path):
    api = ctx.openapi.apps.success_and_failure()
    xml_path = tmp_path / "junit.xml"
    cli.main(
        "run",
        api.schema_url,
        "-c",
        "EnsureReachability",
        "--max-examples=5",
        "--phases=fuzzing",
        f"--report-junit-path={xml_path}",
        hooks=ensure_reachability_module,
    )
    tree = ElementTree.parse(xml_path)
    testsuite = tree.getroot()[0]
    testcases_by_name = {tc.attrib["name"]: tc for tc in testsuite}
    run_checks = testcases_by_name.get("Run checks")
    assert run_checks is not None, list(testcases_by_name)
    assert run_checks[0].tag == "failure"
    assert "never returned 2xx" in (run_checks[0].text or run_checks[0].attrib.get("message", ""))


_UNSORTED_RUN_CHECK_FAILURES = """
from schemathesis.core.failures import CustomFailure, FailureGroup


@schemathesis.check
class ZRunCheck:
    def after_response(self, ctx, response, case):
        pass

    def after_run(self, ctx):
        raise AssertionError("zzz run-level failure")


@schemathesis.check
class ARunCheck:
    def after_response(self, ctx, response, case):
        pass

    def after_run(self, ctx):
        raise FailureGroup(
            [
                CustomFailure(operation=None, title="dup", message="aaa duplicated", exception=AssertionError("dup"))
                for _ in range(2)
            ]
        )
"""


@pytest.fixture
def unsorted_run_check_failures_module(ctx):
    return ctx.write_pymodule(_UNSORTED_RUN_CHECK_FAILURES)


def test_after_run_failures_in_reports_are_sorted_and_deduplicated(
    ctx, cli, unsorted_run_check_failures_module, tmp_path
):
    api = ctx.openapi.apps.success()
    xml_path = tmp_path / "junit.xml"
    allure_dir = tmp_path / "allure-results"
    cli.main(
        "run",
        api.schema_url,
        "-c",
        "ZRunCheck,ARunCheck",
        "--max-examples=1",
        "--phases=fuzzing",
        f"--report-junit-path={xml_path}",
        f"--report-allure-path={allure_dir}",
        hooks=unsorted_run_check_failures_module,
    )
    testcases_by_name = {testcase.attrib["name"]: testcase for testcase in ElementTree.parse(xml_path).getroot()[0]}
    assert testcases_by_name["Run checks"][0].text == (
        "\n- ST901 dup\n\n    aaa duplicated\n\n- ST901 Custom check failed: `ZRunCheck`\n\n    zzz run-level failure"
    )
    results = allure_results(allure_dir)
    [run_checks] = [result for result in results if result["name"] == "Run checks"]
    assert [step["statusDetails"]["message"] for step in run_checks["steps"]] == [
        "- ST901 dup\n\n    aaa duplicated\n\n- ST901 Custom check failed: `ZRunCheck`\n\n    zzz run-level failure"
    ]
