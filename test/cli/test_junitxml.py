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
    result = cli.run(
        schema_url,
        f"--junit-xml={xml_path}",
        f"--hypothesis-max-examples={hypothesis_max_examples or 2}",
        "--hypothesis-seed=1",
    )
    # Command executed successfully
    assert result.exit_code == ExitCode.OK, result.stdout
    # File is created
    assert xml_path.exists()
    # And contains valid xml
    ElementTree.parse(xml_path)


@pytest.mark.parametrize("path", ("junit.xml", "does-not-exist/junit.xml"))
@pytest.mark.operations("success", "failure", "unsatisfiable", "empty_string")
def test_junitxml_file(cli, schema_url, hypothesis_max_examples, tmp_path, path, server_host):
    xml_path = tmp_path / path
    cli.run(
        schema_url,
        f"--junit-xml={xml_path}",
        f"--hypothesis-max-examples={hypothesis_max_examples or 1}",
        "--hypothesis-seed=1",
        "--checks=all",
    )
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
    # Inspected testcase with a failure
    assert testcases[1].tag == "testcase"
    assert testcases[1].attrib["name"] == "GET /api/failure"
    assert testcases[1][0].tag == "failure"
    assert testcases[1][0].attrib["type"] == "failure"
    assert (
        extract_message(testcases[1][0], server_host)
        == "1. Test Case ID: <PLACEHOLDER>  - Server error  - Undocumented Content-Type      Received: text/plain; charset=utf-8     Documented: application/json  [500] Internal Server Error:      `500: Internal Server Error`  Reproduce with:       curl -X GET http://localhost/api/failure"
    )
    # Inspect passed testcase
    assert testcases[2].attrib["name"] == "GET /api/success"
    # Inspect testcase with an error
    assert testcases[3].attrib["name"] == "POST /api/unsatisfiable"
    assert testcases[3][0].tag == "error"
    assert testcases[3][0].attrib["type"] == "error"
    assert (
        testcases[3][0].attrib["message"]
        == "Schema Error  Failed to generate test cases for this API operation. Possible reasons:      - Contradictory schema constraints, such as a minimum value exceeding the maximum.     - Invalid schema definitions for headers or cookies, for example allowing for non-ASCII characters.     - Excessive schema complexity, which hinders parameter generation.  Tip: Examine the schema for inconsistencies and consider simplifying it."
    )


@pytest.mark.parametrize(
    "args, expected",
    (
        ((), "Runtime Error  division by zero"),
        (("--show-trace",), "Runtime Error  division by zero      Traceback (most recent call last): "),
    ),
)
@pytest.mark.skipif(
    sys.version_info < (3, 11) or platform.system() == "Windows",
    reason="Cover only tracebacks that highlight error positions in every line",
)
@pytest.mark.operations("success")
@pytest.mark.openapi_version("3.0")
def test_error_with_traceback(cli, schema_url, tmp_path, testdir, args, expected):
    module = testdir.make_importable_pyfile(
        hook="""
import schemathesis


@schemathesis.check
def with_error(response, case):
    1 / 0
"""
    )
    xml_path = tmp_path / "junit.xml"
    cli.main("run", schema_url, "-c", "with_error", f"--junit-xml={xml_path}", *args, hooks=module.purebasename)
    tree = ElementTree.parse(xml_path)
    root = tree.getroot()
    testcases = list(root[0])
    assert testcases[0][0].attrib["message"].startswith(expected)


def extract_message(testcase, server_host):
    return re.sub("Test Case ID: (.+?) ", "Test Case ID: <PLACEHOLDER> ", testcase.attrib["message"]).replace(
        server_host, "localhost"
    )


def test_binary_response(empty_open_api_3_schema, testdir, cli, openapi3_base_url, tmp_path, server_host):
    xml_path = tmp_path / "junit.xml"
    empty_open_api_3_schema["paths"] = {
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
    schema_file = testdir.make_openapi_schema_file(empty_open_api_3_schema)
    cli.run(str(schema_file), f"--base-url={openapi3_base_url}", "--checks=all", f"--junit-xml={xml_path}")
    tree = ElementTree.parse(xml_path)
    testsuite = tree.getroot()[0]
    testcases = list(testsuite)
    assert testcases[0].tag == "testcase"
    assert testcases[0].attrib["name"] == "GET /api/binary"
    assert testcases[0][0].tag == "failure"
    assert testcases[0][0].attrib["type"] == "failure"
    assert (
        extract_message(testcases[0][0], server_host)
        == "1. Test Case ID: <PLACEHOLDER>  - Server error  [500] Internal Server Error:      <BINARY>  Reproduce with:       curl -X GET http://localhost/api/binary"
    )


@pytest.mark.operations("slow")
@pytest.mark.openapi_version("3.0")
def test_timeout(cli, tmp_path, schema_url, hypothesis_max_examples, server_host):
    xml_path = tmp_path / "junit.xml"
    cli.run(
        schema_url,
        f"--junit-xml={xml_path}",
        f"--hypothesis-max-examples={hypothesis_max_examples or 1}",
        "--hypothesis-seed=1",
        "--request-timeout=10",
        "--checks=all",
    )
    tree = ElementTree.parse(xml_path)
    testsuite = tree.getroot()[0]
    testcases = list(testsuite)
    assert testcases[0].tag == "testcase"
    assert testcases[0].attrib["name"] == "GET /api/slow"
    assert testcases[0][0].tag == "failure"
    assert testcases[0][0].attrib["type"] == "failure"
    assert (
        extract_message(testcases[0][0], server_host)
        == "1. Test Case ID: <PLACEHOLDER>  - Response timeout      The server failed to respond within the specified limit of 10.00ms  Reproduce with:       curl -X GET http://localhost/api/slow"
    )


@pytest.mark.operations("success")
@pytest.mark.openapi_version("3.0")
def test_skipped(cli, tmp_path, schema_url, server_host):
    xml_path = tmp_path / "junit.xml"
    cli.run(
        schema_url,
        f"--junit-xml={xml_path}",
        "--hypothesis-seed=1",
        "--hypothesis-phases=explicit",
        "--checks=all",
    )
    tree = ElementTree.parse(xml_path)
    testsuite = tree.getroot()[0]
    testcases = list(testsuite)
    assert testcases[0].tag == "testcase"
    assert testcases[0].attrib["name"] == "GET /api/success"
    assert testcases[0][0].tag == "skipped"
    assert testcases[0][0].attrib["type"] == "skipped"
    assert extract_message(testcases[0][0], server_host) == "Hypothesis has been told to run no examples for this test."


@pytest.mark.parametrize("path", ("junit.xml", "does-not-exist/junit.xml"))
@pytest.mark.openapi_version("3.0")
@pytest.mark.skipif(platform.system() == "Windows", reason="Unclear how to trigger the permission error on Windows")
def test_permission_denied(cli, tmp_path, schema_url, path):
    dir_path = tmp_path / "output"
    dir_path.mkdir(mode=0o555)
    xml_path = dir_path / path
    result = cli.run(schema_url, f"--junit-xml={xml_path}")
    assert result.exit_code == ExitCode.INTERRUPTED, result.stdout
    assert "Permission denied" in result.stdout
