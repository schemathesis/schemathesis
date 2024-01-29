import platform
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
@pytest.mark.operations("success", "failure", "unsatisfiable")
def test_junitxml_file(cli, schema_url, hypothesis_max_examples, tmp_path, path):
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
    assert root.attrib["failures"] == "1"
    assert root.attrib["tests"] == "3"
    # Inspect the nested element `testsuite`
    testsuite = root[0]
    assert testsuite.tag == "testsuite"
    assert testsuite.attrib["name"] == "schemathesis"
    assert testsuite.attrib["errors"] == "1"
    assert testsuite.attrib["failures"] == "1"
    assert testsuite.attrib["tests"] == "3"
    # Inspected nested `testcase`s
    testcases = list(testsuite)
    assert len(testcases) == 3
    # Inspected testcase with a failure
    assert testcases[0].tag == "testcase"
    assert testcases[0].attrib["name"] == "GET /api/failure"
    assert testcases[0][0].tag == "failure"
    assert testcases[0][0].attrib["type"] == "failure"
    assert testcases[0][0].attrib["message"] == "1. Undocumented Content-Type"
    # Inspect passed testcase
    assert testcases[1].attrib["name"] == "GET /api/success"
    # Inspect testcase with an error
    assert testcases[2].attrib["name"] == "POST /api/unsatisfiable"
    assert testcases[2][0].tag == "error"
    assert testcases[2][0].attrib["type"] == "error"
    assert "Failed to generate test cases for this API operation" in testcases[2][0].attrib["message"]


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
