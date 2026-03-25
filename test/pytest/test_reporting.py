import json
import pathlib
from xml.etree import ElementTree

import yaml


def _make_xdist_test(testdir, content, base_url, paths=None):
    """Create a test module suitable for xdist execution.

    testdir.make_test always prepends `from test.utils import *`. That import
    works in non-xdist mode because runpytest_inprocess shares the outer
    process's sys.path (which has the project root after conftest discovery).
    Under xdist, workers are fresh subprocesses; xdist.plugin._sys_path is
    frozen at plugin import time, before conftest discovery adds the project
    root, so test.utils is unreachable. This helper builds the test file and
    conftest directly without importing test.utils at all.
    """
    if paths is None:
        paths = {"/users": {"get": {"responses": {"200": {"description": "OK"}}}}}
    schema_dict = {
        "openapi": "3.0.2",
        "info": {"title": "Test", "description": "Test", "version": "0.1.0"},
        "paths": paths,
    }
    testdir.makeconftest(
        """
def pytest_configure(config):
    config.HYPOTHESIS_CASES = 0
"""
    )
    testdir.makepyfile(
        f"""
import schemathesis
from schemathesis.config import SchemathesisConfig
from hypothesis import settings

config = SchemathesisConfig()
config.output.sanitization.update(enabled=False)

schema = schemathesis.openapi.from_dict({schema_dict!r}, config=config)
schema.config.update(base_url="{base_url}")

{content}
"""
    )


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


def test_vcr_report_written_via_xdist(testdir, openapi3_base_url):
    cassette_path = str(testdir.tmpdir.join("cassette.yaml"))
    _make_xdist_test(
        testdir,
        f"""
schema.config.reports.update(vcr_path=r"{cassette_path}")

@schema.parametrize()
@settings(max_examples=1)
def test_api(case):
    case.call()
""",
        base_url=openapi3_base_url,
    )
    result = testdir.runpytest("-n", "2")
    result.assert_outcomes(passed=1)

    with open(cassette_path) as f:
        cassette = yaml.safe_load(f)
    assert "http_interactions" in cassette
    interactions = cassette["http_interactions"]
    assert len(interactions) >= 1
    # Generation metadata must survive the worker→controller boundary
    assert all("generation" in i for i in interactions)
    assert all("phase" in i for i in interactions)


def test_har_report_written_via_xdist(testdir, openapi3_base_url):
    cassette_path = str(testdir.tmpdir.join("cassette.har"))
    _make_xdist_test(
        testdir,
        f"""
schema.config.reports.update(har_path=r"{cassette_path}")

@schema.parametrize()
@settings(max_examples=1)
def test_api(case):
    case.call()
""",
        base_url=openapi3_base_url,
    )
    result = testdir.runpytest("-n", "2")
    result.assert_outcomes(passed=1)

    with open(cassette_path) as f:
        har = json.load(f)
    assert "log" in har
    assert len(har["log"]["entries"]) >= 1


def test_junit_report_written_via_xdist(testdir, openapi3_base_url):
    report_path = str(testdir.tmpdir.join("report.xml"))
    _make_xdist_test(
        testdir,
        f"""
schema.config.reports.update(junit_path=r"{report_path}")

@schema.parametrize()
@settings(max_examples=1)
def test_api(case):
    case.call()
""",
        base_url=openapi3_base_url,
    )
    result = testdir.runpytest("-n", "2")
    result.assert_outcomes(passed=1)

    tree = ElementTree.parse(report_path)
    test_cases = tree.findall(".//testcase")
    assert len(test_cases) >= 1
    assert all(tc.find("failure") is None for tc in test_cases)


def test_junit_report_records_check_failures_via_xdist(testdir, openapi3_base_url):
    report_path = str(testdir.tmpdir.join("report.xml"))
    _make_xdist_test(
        testdir,
        f"""
schema.config.reports.update(junit_path=r"{report_path}")

@schema.parametrize()
@settings(max_examples=1)
def test_api(case):
    case.call_and_validate()
""",
        base_url=openapi3_base_url,
        paths={"/failure": {"get": {"responses": {"500": {"description": "Internal Server Error"}}}}},
    )
    result = testdir.runpytest("-n", "2")
    result.assert_outcomes(failed=1)

    tree = ElementTree.parse(report_path)
    failures = tree.findall(".//testcase/failure")
    assert len(failures) >= 1


def test_vcr_report_records_check_failures_via_xdist(testdir, openapi3_base_url):
    cassette_path = str(testdir.tmpdir.join("cassette.yaml"))
    _make_xdist_test(
        testdir,
        f"""
schema.config.reports.update(vcr_path=r"{cassette_path}")

@schema.parametrize()
@settings(max_examples=1)
def test_api(case):
    case.call_and_validate()
""",
        base_url=openapi3_base_url,
        paths={"/failure": {"get": {"responses": {"500": {"description": "Internal Server Error"}}}}},
    )
    result = testdir.runpytest("-n", "2")
    result.assert_outcomes(failed=1)

    with open(cassette_path) as f:
        cassette = yaml.safe_load(f)
    all_checks = [c for i in cassette["http_interactions"] for c in i["checks"]]
    assert any(c["status"] == "FAILURE" for c in all_checks)


def test_vcr_report_examples_phase_via_xdist(testdir, openapi3_base_url):
    cassette_path = str(testdir.tmpdir.join("cassette.yaml"))
    paths = {
        "/users/": {
            "post": {
                "requestBody": {
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "first_name": {"type": "string"},
                                    "last_name": {"type": "string"},
                                },
                                "required": ["first_name", "last_name"],
                            },
                            "example": {"first_name": "John", "last_name": "Doe"},
                        }
                    },
                    "required": True,
                },
                "responses": {"201": {"description": "Created"}},
            }
        }
    }
    _make_xdist_test(
        testdir,
        f"""
schema.config.reports.update(vcr_path=r"{cassette_path}")

@schema.parametrize()
@settings(max_examples=1)
def test_api(case):
    case.call()
""",
        base_url=openapi3_base_url,
        paths=paths,
    )
    result = testdir.runpytest("-n", "2")
    result.assert_outcomes(passed=1)

    with open(cassette_path) as f:
        cassette = yaml.safe_load(f)
    interactions = cassette["http_interactions"]
    assert len(interactions) >= 1
    # ExamplesPhaseData round-trip via worker→controller boundary
    assert any(i.get("phase", {}).get("name") == "examples" for i in interactions)
    # Request body preserved through serialization
    assert all(i["request"]["body"] is not None for i in interactions)


def test_vcr_report_via_directory_via_xdist(testdir, openapi3_base_url):
    # enable VCR via `formats=` without an explicit path so get_stable_path()
    # falls through to the directory-based filename branch
    report_dir = str(testdir.tmpdir.mkdir("reports"))
    _make_xdist_test(
        testdir,
        f"""
from schemathesis.config._report import ReportFormat
from pathlib import Path

schema.config.reports.update(formats=[ReportFormat.VCR], directory=Path(r"{report_dir}"))

@schema.parametrize()
@settings(max_examples=1)
def test_api(case):
    case.call()
""",
        base_url=openapi3_base_url,
    )
    result = testdir.runpytest("-n", "2")
    result.assert_outcomes(passed=1)

    cassette_path = testdir.tmpdir.join("reports", "vcr.yaml")
    with open(str(cassette_path)) as f:
        cassette = yaml.safe_load(f)
    assert "http_interactions" in cassette
    assert len(cassette["http_interactions"]) >= 1


def test_xdist_no_report_configured(testdir, openapi3_base_url):
    # workers always send data via workeroutput even with no reports configured;
    # the controller must silently skip processing when no writers are opened
    _make_xdist_test(
        testdir,
        """
@schema.parametrize()
@settings(max_examples=1)
def test_api(case):
    case.call()
""",
        base_url=openapi3_base_url,
    )
    result = testdir.runpytest("-n", "2")
    result.assert_outcomes(passed=1)


def test_vcr_report_multiple_operations_via_xdist(testdir, openapi3_base_url):
    # two operations share the same schema_id; the second pytest_testnodedown call
    # must reuse already-opened writers rather than opening new ones
    cassette_path = str(testdir.tmpdir.join("cassette.yaml"))
    _make_xdist_test(
        testdir,
        f"""
schema.config.reports.update(vcr_path=r"{cassette_path}")

@schema.parametrize()
@settings(max_examples=1)
def test_api(case):
    case.call()
""",
        base_url=openapi3_base_url,
        paths={
            "/users": {"get": {"responses": {"200": {"description": "OK"}}}},
            "/health": {"get": {"responses": {"200": {"description": "OK"}}}},
        },
    )
    result = testdir.runpytest("-n", "2")
    result.assert_outcomes(passed=2)

    with open(cassette_path) as f:
        cassette = yaml.safe_load(f)
    assert len(cassette["http_interactions"]) >= 2


def test_vcr_report_network_error_via_xdist(testdir):
    cassette_path = str(testdir.tmpdir.join("cassette.yaml"))
    _make_xdist_test(
        testdir,
        f"""
import requests

schema.config.reports.update(vcr_path=r"{cassette_path}")

@schema.parametrize()
@settings(max_examples=1)
def test_api(case):
    try:
        case.call()
    except requests.ConnectionError:
        pass
""",
        base_url="http://127.0.0.1:1",
    )
    result = testdir.runpytest("-n", "2")
    result.assert_outcomes(passed=1)

    with open(cassette_path) as f:
        cassette = yaml.safe_load(f)
    interactions = cassette["http_interactions"]
    assert len(interactions) >= 1
    assert all(i["response"] is None for i in interactions)
    assert all(i["status"] == "ERROR" for i in interactions)


def test_two_schemas_same_vcr_path_via_xdist(testdir, openapi3_base_url):
    # Two schemas both enabling VCR without an explicit path share the same
    # default filename; the controller adds a schema-id suffix so they don't
    # overwrite each other.
    report_dir = str(testdir.tmpdir.mkdir("reports"))
    schema_a = {
        "openapi": "3.0.2",
        "info": {"title": "A", "description": "", "version": "0.1.0"},
        "paths": {"/users": {"get": {"responses": {"200": {"description": "OK"}}}}},
    }
    schema_b = {
        "openapi": "3.0.2",
        "info": {"title": "B", "description": "", "version": "0.1.0"},
        "paths": {"/health": {"get": {"responses": {"200": {"description": "OK"}}}}},
    }
    testdir.makeconftest("def pytest_configure(config):\n    config.HYPOTHESIS_CASES = 0\n")
    testdir.makepyfile(
        f"""
import schemathesis
from schemathesis.config import SchemathesisConfig
from schemathesis.config._report import ReportFormat
from pathlib import Path
from hypothesis import settings

config_a = SchemathesisConfig()
config_a.output.sanitization.update(enabled=False)
schema_a = schemathesis.openapi.from_dict({schema_a!r}, config=config_a)
schema_a.config.update(base_url="{openapi3_base_url}")
schema_a.config.reports.update(formats=[ReportFormat.VCR], directory=Path(r"{report_dir}"))

config_b = SchemathesisConfig()
config_b.output.sanitization.update(enabled=False)
schema_b = schemathesis.openapi.from_dict({schema_b!r}, config=config_b)
schema_b.config.update(base_url="{openapi3_base_url}")
schema_b.config.reports.update(formats=[ReportFormat.VCR], directory=Path(r"{report_dir}"))

@schema_a.parametrize()
@settings(max_examples=1)
def test_schema_a(case):
    case.call()

@schema_b.parametrize()
@settings(max_examples=1)
def test_schema_b(case):
    case.call()
"""
    )
    result = testdir.runpytest("-n", "2")
    result.assert_outcomes(passed=2)

    suffixed = sorted(pathlib.Path(report_dir).glob("vcr-*.yaml"))
    assert len(suffixed) == 2
    for f in suffixed:
        cassette = yaml.safe_load(f.read_text())
        assert "http_interactions" in cassette
        assert len(cassette["http_interactions"]) >= 1


def test_xdist_writer_open_failure(testdir, openapi3_base_url):
    # VCR opens successfully, then HAR fails (path is a directory);
    # the except block must close the already-opened VCR writer before re-raising
    vcr_path = str(testdir.tmpdir.join("cassette.yaml"))
    har_path = str(testdir.tmpdir.join("cassette.har"))
    testdir.tmpdir.mkdir("cassette.har")
    _make_xdist_test(
        testdir,
        f"""
schema.config.reports.update(vcr_path=r"{vcr_path}", har_path=r"{har_path}")

@schema.parametrize()
@settings(max_examples=1)
def test_api(case):
    case.call()
""",
        base_url=openapi3_base_url,
    )
    result = testdir.runpytest("-n", "2")
    # the writer open failure propagates from pytest_testnodedown as a session error
    assert result.ret != 0
    # VCR writer was opened before HAR failed — file exists with the cassette header
    with open(vcr_path) as f:
        content = f.read()
    assert "recorded_with" in content
