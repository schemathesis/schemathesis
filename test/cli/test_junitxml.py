import platform
import re
import sys
from xml.etree import ElementTree

import pytest
from _pytest.main import ExitCode
from flask import Response, jsonify


def test_junitxml_option(ctx, cli, hypothesis_max_examples, tmp_path):
    api = ctx.openapi.apps.success()
    # When option with a path to junit.xml is provided
    xml_path = tmp_path / "junit.xml"
    cli.run_and_assert(
        api.schema_url,
        f"--report-junit-path={xml_path}",
        f"--max-examples={hypothesis_max_examples or 2}",
        "--checks=not_a_server_error",
        "--seed=1",
    )
    # File is created
    assert xml_path.exists()
    # And contains valid xml
    ElementTree.parse(xml_path)


@pytest.mark.parametrize("in_config", [True, False])
@pytest.mark.parametrize("path", ["junit.xml", "does-not-exist/junit.xml"])
def test_junitxml_file(ctx, cli, hypothesis_max_examples, tmp_path, path, in_config):
    api = ctx.openapi.apps.success_failure_unsatisfiable_empty_string()
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
    else:
        args.append(f"--report-junit-path={xml_path}")
    cli.run_and_assert(api.schema_url, *args, exit_code=ExitCode.TESTS_FAILED, **kwargs)
    tree = ElementTree.parse(xml_path)
    # Inspect root element `testsuites`
    root = tree.getroot()
    assert root.tag == "testsuites"
    assert root.attrib["errors"] == "1"
    assert root.attrib["failures"] == "2"
    assert root.attrib["tests"] == "4"
    # Inspect the nested element `testsuite`
    testsuite = root[0]
    assert testsuite.tag == "testsuite"
    assert testsuite.attrib["name"] == "schemathesis"
    assert testsuite.attrib["errors"] == "1"
    assert testsuite.attrib["failures"] == "2"
    assert testsuite.attrib["tests"] == "4"
    # Inspected nested `testcase`s
    testcases = list(testsuite)
    assert len(testcases) == 4

    # Create a mapping from testcase name to element
    testcases_by_name = {tc.attrib["name"]: tc for tc in testcases}

    # Inspected testcase with a failure
    failure = testcases_by_name["GET /api/failure"]
    assert failure.tag == "testcase"
    assert failure[0].tag == "failure"
    assert failure[0].attrib["type"] == "failure"
    message = extract_message(failure[0], server_host)
    assert "Server error" in message
    assert "[500] Internal Server Error" in message
    assert "curl -X GET http://localhost/api/failure" in message

    # Inspect passed testcase
    success = testcases_by_name["GET /api/success"]
    assert success.tag == "testcase"

    # Inspect testcase with an error
    error = testcases_by_name["POST /api/unsatisfiable"]
    assert error.tag == "testcase"
    assert error[0].tag == "error"
    assert error[0].attrib["type"] == "error"
    assert (
        error[0]
        .text.replace("\n", " ")
        .startswith("Schema Error  Cannot generate test data for request body (application/json) Schema:")
    )


@pytest.fixture
def with_error(ctx):
    with ctx.check("""
@schemathesis.check
def with_error(ctx, response, case):
    1 / 0
""") as module:
        yield module


@pytest.mark.skipif(
    sys.version_info < (3, 11) or platform.system() == "Windows",
    reason="Cover only tracebacks that highlight error positions in every line",
)
def test_error_with_traceback(ctx, with_error, cli, tmp_path):
    api = ctx.openapi.apps.success()
    xml_path = tmp_path / "junit.xml"
    cli.main(
        "run",
        api.schema_url,
        "-c",
        "with_error",
        f"--report-junit-path={xml_path}",
        hooks=with_error,
    )
    tree = ElementTree.parse(xml_path)
    root = tree.getroot()
    testcases = list(root[0])
    assert (
        testcases[0][0]
        .text.replace("\n", " ")
        .startswith("Runtime Error  division by zero      Traceback (most recent call last): ")
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
        from flask import Response

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
        == "1. Test Case ID: <PLACEHOLDER>  - Server error  [500] Internal Server Error:      <BINARY>  Reproduce with:      curl -X GET http://localhost/api/binary"
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
        == "1. Test Case ID: <PLACEHOLDER>  - Server error  [500] Internal Server Error:      `boom`  Reproduce with:      curl -X GET http://localhost/api/boom"
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
    # When the Examples phase skips an operation (schema has no inline examples)
    # but the Coverage phase subsequently runs and produces real results,
    # the JUnit report must NOT mark the test case as skipped.
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
    # Coverage ran real requests — the earlier skip must have been cleared
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


def test_coverage_unspecified_method_in_junit(cli, ctx, tmp_path):
    # See GH-3699
    # When coverage phase triggers an `unsupported_method` failure via UNSPECIFIED_HTTP_METHOD,
    # the failure must appear in JUnit XML
    xml_path = tmp_path / "junit.xml"
    app, _ = ctx.openapi.make_flask_app({"/users": {"get": {"responses": {"200": {"description": "OK"}}}}})

    # Server accepts all methods — returns 200 instead of 405 for undocumented methods
    @app.route("/users", methods=["GET", "DELETE", "POST", "PUT", "PATCH", "HEAD"])
    def users():
        return jsonify([])

    result = cli.run_openapi_app(
        app,
        "--phases=coverage",
        f"--report-junit-path={xml_path}",
        "--checks=unsupported_method",
    )

    # Exit code is 1: the check detected that undocumented methods were accepted
    assert result.exit_code == 1

    # JUnit must reflect that failure
    tree = ElementTree.parse(xml_path)
    root = tree.getroot()
    assert int(root.attrib.get("failures", 0)) > 0


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
