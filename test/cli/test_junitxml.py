import platform
import re
import sys
from xml.etree import ElementTree

import pytest
from _pytest.main import ExitCode


@pytest.mark.operations("success")
def test_junitxml_option(cli, schema_url, hypothesis_max_examples, tmp_path):
    # When option with a path to junit.xml is provided
    xml_path = tmp_path / "junit.xml"
    cli.run_and_assert(
        schema_url,
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
@pytest.mark.operations("success", "failure", "unsatisfiable", "empty_string")
def test_junitxml_file(cli, schema_url, hypothesis_max_examples, tmp_path, path, server_host, in_config):
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
    cli.run_and_assert(schema_url, *args, exit_code=ExitCode.TESTS_FAILED, **kwargs)
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
    failure = testcases_by_name["GET /failure"]
    assert failure.tag == "testcase"
    assert failure[0].tag == "failure"
    assert failure[0].attrib["type"] == "failure"
    assert (
        extract_message(failure[0], server_host)
        == "1. Test Case ID: <PLACEHOLDER>  - Server error  - Undocumented Content-Type      Received: text/plain; charset=utf-8     Documented: application/json  [500] Internal Server Error:      `500: Internal Server Error`  Reproduce with:       curl -X GET http://localhost/api/failure"
    )

    # Inspect passed testcase
    success = testcases_by_name["GET /success"]
    assert success.tag == "testcase"

    # Inspect testcase with an error
    error = testcases_by_name["POST /unsatisfiable"]
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
@pytest.mark.operations("success")
@pytest.mark.openapi_version("3.0")
def test_error_with_traceback(with_error, cli, schema_url, tmp_path):
    xml_path = tmp_path / "junit.xml"
    cli.main(
        "run",
        schema_url,
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


def test_binary_response(ctx, cli, openapi3_base_url, tmp_path, server_host):
    xml_path = tmp_path / "junit.xml"
    schema_path = ctx.openapi.write_schema(
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
    cli.run(
        str(schema_path),
        f"--url={openapi3_base_url}",
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
        extract_message(testcases[0][0], server_host)
        == "1. Test Case ID: <PLACEHOLDER>  - Server error  [500] Internal Server Error:      <BINARY>  Reproduce with:       curl -X GET http://localhost/api/binary"
    )


@pytest.mark.operations("slow")
@pytest.mark.openapi_version("3.0")
def test_timeout(cli, tmp_path, schema_url, hypothesis_max_examples):
    xml_path = tmp_path / "junit.xml"
    cli.run(
        schema_url,
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
    assert testcases[0].attrib["name"] == "GET /slow"
    assert testcases[0][0].tag == "error"
    assert testcases[0][0].attrib["type"] == "error"
    assert "Read timed out after 0.01 seconds" in testcases[0][0].text


@pytest.mark.operations("success")
@pytest.mark.openapi_version("3.0")
def test_skipped(cli, tmp_path, schema_url, server_host):
    xml_path = tmp_path / "junit.xml"
    cli.run(
        schema_url,
        f"--report-junit-path={xml_path}",
        "--seed=1",
        "--phases=examples",
        "--checks=all",
    )
    tree = ElementTree.parse(xml_path)
    testsuite = tree.getroot()[0]
    testcases = list(testsuite)
    assert testcases[0].tag == "testcase"
    assert testcases[0].attrib["name"] == "GET /success"
    assert testcases[0][0].tag == "skipped"
    assert testcases[0][0].attrib["type"] == "skipped"
    assert extract_message(testcases[0][0], server_host) == "No examples in schema"


@pytest.mark.parametrize("path", ["junit.xml", "does-not-exist/junit.xml"])
@pytest.mark.openapi_version("3.0")
@pytest.mark.skipif(platform.system() == "Windows", reason="Unclear how to trigger the permission error on Windows")
def test_permission_denied(cli, tmp_path, schema_url, path):
    dir_path = tmp_path / "output"
    dir_path.mkdir(mode=0o555)
    xml_path = dir_path / path
    result = cli.run_and_assert(schema_url, f"--report-junit-path={xml_path}", exit_code=ExitCode.INTERRUPTED)

    assert "Permission denied" in result.stdout or "Permission denied" in result.stderr
