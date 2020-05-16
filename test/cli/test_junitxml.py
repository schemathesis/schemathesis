from xml.etree import ElementTree

import pytest
from _pytest.main import ExitCode


@pytest.mark.endpoints("success")
def test_junitxml_option(cli, schema_url, tmp_path):
    # When option with a path to junit.xml is provided
    xml_path = tmp_path / "junit.xml"
    result = cli.run(schema_url, f"--junit-xml={xml_path}", "--hypothesis-max-examples=2", "--hypothesis-seed=1")
    # Command executed successfully
    assert result.exit_code == ExitCode.OK, result.stdout
    # File is created
    assert xml_path.exists()
    # And contains valid xml
    ElementTree.parse(xml_path)


@pytest.mark.endpoints("success", "failure", "malformed_json")
def test_junitxml_file(cli, schema_url, tmp_path):
    xml_path = tmp_path / "junit.xml"
    cli.run(schema_url, f"--junit-xml={xml_path}", "--hypothesis-max-examples=1", "--hypothesis-seed=1", "--checks=all")
    tree = ElementTree.parse(xml_path)
    # Inspect root element `testsuites`
    root = tree.getroot()
    assert root.tag == "testsuites"
    assert root.attrib["errors"] == "1"
    assert root.attrib["failures"] == "1"
    assert root.attrib["tests"] == "3"
    # Inspect nested element `testsuite`
    testsuite = root[0]
    assert testsuite.tag == "testsuite"
    assert testsuite.attrib["name"] == "schemathesis"
    assert testsuite.attrib["errors"] == "1"
    assert testsuite.attrib["failures"] == "1"
    assert testsuite.attrib["tests"] == "3"
    # Inpected nested `testcase`s
    testcases = list(testsuite)
    assert len(testcases) == 3
    # Inspected testcase with a failure
    assert testcases[0].tag == "testcase"
    assert testcases[0].attrib["name"] == "GET /api/failure"
    assert testcases[0][0].tag == "failure"
    assert testcases[0][0].attrib["type"] == "failure"
    assert (
        testcases[0][0].attrib["message"] == "1. Received a response with 'text/plain; charset=utf-8' Content-Type, "
        "but it is not declared in the schema.  Defined content types: application/json"
    )
    # Inspect testcase with an error
    assert testcases[1].attrib["name"] == "GET /api/malformed_json"
    assert testcases[1][0].tag == "error"
    assert testcases[1][0].attrib["type"] == "error"
    assert "Expecting property name enclosed in double quotes" in testcases[1][0].attrib["message"]
    # Inspect passed testcase
    assert testcases[2].attrib["name"] == "GET /api/success"
