import json
from xml.etree import ElementTree

import yaml


def test_vcr_report_written_via_config(testdir, openapi3_base_url):
    cassette_path = str(testdir.tmpdir.join("cassette.yaml"))
    testdir.make_test(
        f"""
schema.config.update(base_url="{openapi3_base_url}")
schema.config.reports.update(vcr_path=r"{cassette_path}")

@schema.parametrize()
@settings(max_examples=1)
def test_api(case):
    case.call()
""",
    )
    result = testdir.runpytest("-s")
    result.assert_outcomes(passed=1)

    with open(cassette_path) as f:
        cassette = yaml.safe_load(f)
    assert "http_interactions" in cassette
    assert len(cassette["http_interactions"]) >= 1


def test_vcr_report_records_check_failures(testdir, openapi3_base_url):
    cassette_path = str(testdir.tmpdir.join("cassette.yaml"))
    testdir.make_test(
        f"""
schema.config.update(base_url="{openapi3_base_url}")
schema.config.reports.update(vcr_path=r"{cassette_path}")

@schema.parametrize()
@settings(max_examples=1)
def test_api(case):
    case.call_and_validate()
""",
        schema_name="simple_openapi.yaml",
        paths={"/failure": {"get": {"responses": {"500": {"description": "Internal Server Error"}}}}},
    )
    result = testdir.runpytest("-s")
    result.assert_outcomes(failed=2)

    with open(cassette_path) as f:
        cassette = yaml.safe_load(f)
    all_checks = [c for i in cassette["http_interactions"] for c in i["checks"]]
    assert any(c["status"] == "FAILURE" for c in all_checks)


def test_vcr_report_no_interactions_when_call_raises(testdir, openapi3_base_url):
    cassette_path = str(testdir.tmpdir.join("cassette.yaml"))
    testdir.make_test(
        f"""
schema.config.update(base_url="{openapi3_base_url}")
schema.config.reports.update(vcr_path=r"{cassette_path}")

@schema.parametrize()
@settings(max_examples=1)
def test_api(case, monkeypatch):
    def fail(*args, **kwargs):
        raise ConnectionError("simulated network failure")
    monkeypatch.setattr("schemathesis.generation.case.Case.call", fail)
    case.call()
""",
    )
    result = testdir.runpytest("-s")
    result.assert_outcomes(failed=1)

    with open(cassette_path) as f:
        cassette = yaml.safe_load(f)
    assert cassette["http_interactions"] is None


def test_har_report_written_via_config(testdir, openapi3_base_url):
    cassette_path = str(testdir.tmpdir.join("cassette.har"))
    testdir.make_test(
        f"""
schema.config.update(base_url="{openapi3_base_url}")
schema.config.reports.update(har_path=r"{cassette_path}")

@schema.parametrize()
@settings(max_examples=1)
def test_api(case):
    case.call()
""",
    )
    result = testdir.runpytest("-s")
    result.assert_outcomes(passed=1)

    with open(cassette_path) as f:
        har = json.load(f)
    assert "log" in har
    assert len(har["log"]["entries"]) >= 1


def test_junit_report_written_via_config(testdir, openapi3_base_url):
    report_path = str(testdir.tmpdir.join("report.xml"))
    testdir.make_test(
        f"""
schema.config.update(base_url="{openapi3_base_url}")
schema.config.reports.update(junit_path=r"{report_path}")

@schema.parametrize()
@settings(max_examples=1)
def test_api(case):
    case.call()
""",
    )
    result = testdir.runpytest("-s")
    result.assert_outcomes(passed=1)

    tree = ElementTree.parse(report_path)
    test_cases = tree.findall(".//testcase")
    assert len(test_cases) >= 1
    assert all(tc.find("failure") is None for tc in test_cases)


def test_junit_report_records_check_failures(testdir, openapi3_base_url):
    report_path = str(testdir.tmpdir.join("report.xml"))
    testdir.make_test(
        f"""
schema.config.update(base_url="{openapi3_base_url}")
schema.config.reports.update(junit_path=r"{report_path}")

@schema.parametrize()
@settings(max_examples=1)
def test_api(case):
    case.call_and_validate()
""",
        schema_name="simple_openapi.yaml",
        paths={"/failure": {"get": {"responses": {"500": {"description": "Internal Server Error"}}}}},
    )
    result = testdir.runpytest("-s")
    result.assert_outcomes(failed=2)

    tree = ElementTree.parse(report_path)
    failures = tree.findall(".//testcase/failure")
    assert len(failures) >= 1
