import pathlib
import re
from xml.etree import ElementTree

import pytest

from test.utils import allure_outcomes, allure_results, load_json_or_fail, load_yaml_or_fail, make_pytest_outcome_test


def _make_xdist_test(ctx, testdir, content, base_url, paths=None):
    if paths is None:
        paths = {"/users": {"get": {"responses": {"200": {"description": "OK"}}}}}
    schema_dict = ctx.openapi.build_schema(paths)
    testdir.makepyfile(
        f"""
import schemathesis
from hypothesis import settings

schema = schemathesis.openapi.from_dict({schema_dict!r})
schema.config.update(base_url="{base_url}")

{content}
"""
    )


def _junit_outcomes(report_path):
    outcomes = {}
    for test_case in ElementTree.parse(report_path).findall(".//testcase"):
        if len(test_case) == 0:
            kind = "passed"
            message = ""
        else:
            kind = test_case[0].tag
            raw_message = test_case[0].attrib.get("message", "")
            message = raw_message.splitlines()[0] if raw_message else ""
            if message.startswith("ConnectionError: "):
                message = "ConnectionError"
        outcomes[test_case.attrib["name"]] = (kind, message)
    return outcomes


@pytest.mark.parametrize(
    ("outcome", "junit", "allure", "xdist"),
    [
        ("check_failure", ("failure", "ST101"), ("failed", ""), False),
        ("assertion", ("failure", "boom"), ("failed", "boom"), False),
        ("runtime_error", ("error", "RuntimeError: bug"), ("broken", "RuntimeError: bug"), False),
        (
            "unsatisfiable",
            ("error", "Unsatisfiable: Cannot generate test data for request body (application/json)"),
            ("broken", "Unsatisfiable: Cannot generate test data for request body (application/json)"),
            False,
        ),
        ("network_error", ("error", "ConnectionError"), ("broken", "ConnectionError"), False),
        (
            "schema_error",
            ("error", "InvalidSchema: Unresolvable reference in the schema"),
            ("broken", "InvalidSchema: Unresolvable reference in the schema"),
            False,
        ),
        ("skip", ("skipped", "why"), ("skipped", "why"), False),
        ("mark_skip", ("skipped", "why"), ("skipped", "why"), False),
        # Workers forward the outcome as a status name and message, the same for every outcome.
        ("runtime_error", ("error", "RuntimeError: bug"), ("broken", "RuntimeError: bug"), True),
        ("skip", ("skipped", "why"), ("skipped", "why"), True),
    ],
    ids=[
        "check_failure",
        "assertion",
        "runtime_error",
        "unsatisfiable",
        "network_error",
        "schema_error",
        "skip",
        "mark_skip",
        "runtime_error-xdist",
        "skip-xdist",
    ],
)
def test_reports_use_pytest_outcome(testdir, tmp_path, ctx, outcome, junit, allure, xdist):
    junit_path = tmp_path / "report.xml"
    allure_dir = tmp_path / "allure-results"
    label = make_pytest_outcome_test(
        testdir,
        ctx,
        outcome,
        f'schema.config.reports.update(junit_path=r"{junit_path}", allure_path=r"{allure_dir}")',
    )
    args = ("-n", "2") if xdist else ()

    testdir.runpytest(*args)

    assert (_junit_outcomes(junit_path), allure_outcomes(allure_dir)) == ({label: junit}, {label: allure})


def test_reports_written_via_config(testdir, ctx):
    api = ctx.openapi.apps.success()
    cassette_path = str(testdir.tmpdir.join("cassette.yaml"))
    har_path = str(testdir.tmpdir.join("cassette.har"))
    report_path = str(testdir.tmpdir.join("report.xml"))
    testdir.make_test(
        f"""
schema.config.update(base_url="{api.base_url}/api")
schema.config.reports.update(vcr_path=r"{cassette_path}", har_path=r"{har_path}", junit_path=r"{report_path}")

@schema.parametrize()
@settings(max_examples=1)
def test_api(case):
    case.call()
""",
    )
    result = testdir.runpytest("-s")
    result.assert_outcomes(passed=1)

    assert load_yaml_or_fail(cassette_path)["http_interactions"]
    assert load_json_or_fail(har_path)["log"]["entries"]
    test_cases = ElementTree.parse(report_path).findall(".//testcase")
    assert test_cases
    assert all(tc.find("failure") is None for tc in test_cases)


def test_reports_record_check_failures(testdir, tmp_path, ctx):
    api = ctx.openapi.apps.failure()
    cassette_path = str(testdir.tmpdir.join("cassette.yaml"))
    report_path = str(testdir.tmpdir.join("report.xml"))
    allure_dir = tmp_path / "allure-results"
    testdir.make_test(
        f"""
schema.config.update(base_url="{api.base_url}/api")
schema.config.reports.update(vcr_path=r"{cassette_path}", junit_path=r"{report_path}", allure_path=r"{allure_dir}")

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

    cassette = load_yaml_or_fail(cassette_path)
    all_checks = [c for i in cassette["http_interactions"] for c in i["checks"]]
    assert any(c["status"] == "FAILURE" for c in all_checks)
    assert ElementTree.parse(report_path).findall(".//testcase/failure")
    failed = next(r for r in allure_results(allure_dir) if r["status"] == "failed")
    assert any("curl" in step["statusDetails"]["message"].lower() for step in failed.get("steps", []))


def test_vcr_report_hand_built_case_has_null_metadata(testdir, ctx):
    api = ctx.openapi.apps.success()
    cassette_path = str(testdir.tmpdir.join("cassette.yaml"))
    testdir.make_test(
        f"""
schema.config.update(base_url="{api.base_url}/api")
schema.config.reports.update(vcr_path=r"{cassette_path}")

@schema.parametrize()
@settings(max_examples=1)
def test_api(case):
    case.operation.Case().call()
""",
        paths={"/success": {"get": {"responses": {"200": {"description": "OK"}}}}},
    )
    testdir.runpytest().assert_outcomes(passed=2)

    cassette = load_yaml_or_fail(cassette_path)
    assert cassette["http_interactions"]
    assert {(interaction["status"], interaction["metadata"]) for interaction in cassette["http_interactions"]} == {
        ("SKIP", None)
    }


def test_vcr_report_no_interactions_when_call_raises(testdir, ctx):
    api = ctx.openapi.apps.success()
    cassette_path = str(testdir.tmpdir.join("cassette.yaml"))
    testdir.make_test(
        f"""
schema.config.update(base_url="{api.base_url}/api")
schema.config.reports.update(vcr_path=r"{cassette_path}")

@schemathesis.hook
def before_call(context, case, kwargs):
    raise ConnectionError("simulated network failure")

@schema.parametrize()
@settings(max_examples=1)
def test_api(case):
    case.call()
""",
    )
    result = testdir.runpytest("-s")
    result.assert_outcomes(failed=1)

    cassette = load_yaml_or_fail(cassette_path)
    assert cassette["http_interactions"] is None


def test_reports_written_via_xdist(testdir, ctx):
    api = ctx.openapi.apps.success()
    report_dir = testdir.tmpdir.mkdir("reports")
    har_path = str(testdir.tmpdir.join("cassette.har"))
    junit_path = str(testdir.tmpdir.join("report.xml"))
    ok = {"responses": {"200": {"description": "OK"}}}
    _make_xdist_test(
        ctx,
        testdir,
        f"""
from pathlib import Path
from schemathesis.config import ReportFormat

schema.config.reports.update(
    formats=[ReportFormat.VCR], directory=Path(r"{report_dir}"), har_path=r"{har_path}", junit_path=r"{junit_path}"
)

@schema.parametrize()
@settings(max_examples=1)
def test_api(case):
    case.call()
""",
        base_url=f"{api.base_url}/api",
        paths={
            "/users": {
                "get": ok,
                "post": {
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"first_name": {"type": "string"}},
                                    "required": ["first_name"],
                                },
                                "example": {"first_name": "John"},
                            }
                        },
                        "required": True,
                    },
                    **ok,
                },
            },
            "/health": {"get": ok},
        },
    )
    testdir.runpytest("-n", "2").assert_outcomes(passed=3)

    interactions = load_yaml_or_fail(str(report_dir.join("vcr.yaml")))["http_interactions"]
    # The coverage phase also records probes with undeclared methods.
    assert {(i["request"]["method"], i["request"]["uri"]) for i in interactions} >= {
        ("GET", f"{api.base_url}/api/users"),
        ("POST", f"{api.base_url}/api/users"),
        ("GET", f"{api.base_url}/api/health"),
    }
    assert all("generation" in i and "phase" in i for i in interactions)
    examples = [i for i in interactions if i["phase"]["name"] == "examples"]
    assert examples
    assert all(i["request"]["body"] is not None for i in examples)
    assert len(load_json_or_fail(har_path)["log"]["entries"]) == len(interactions)
    assert _junit_outcomes(junit_path) == {
        "GET /users": ("passed", ""),
        "POST /users": ("passed", ""),
        "GET /health": ("passed", ""),
    }


def test_lazy_vcr_report_via_xdist_skips_tests_without_calls(testdir, ctx):
    api = ctx.openapi.apps.success_and_failure()
    cassette_path = str(testdir.tmpdir.join("cassette.yaml"))
    testdir.makepyfile(
        f"""
import pytest
import schemathesis
from hypothesis import Phase, settings

@pytest.fixture
def api_schema():
    schema = schemathesis.openapi.from_url("{api.schema_url}")
    schema.config.reports.update(vcr_path=r"{cassette_path}")
    return schema

lazy_schema = schemathesis.pytest.from_fixture("api_schema")

@lazy_schema.parametrize()
@settings(max_examples=1, phases=[Phase.generate])
def test_api(case):
    assert case.path != "/api/failure"
    case.call()
"""
    )
    result = testdir.runpytest("-n", "2")
    result.assert_outcomes(failed=1, passed=1)

    cassette = load_yaml_or_fail(cassette_path)
    assert [interaction["request"]["uri"] for interaction in cassette["http_interactions"]] == [
        f"{api.base_url}/api/success"
    ]


def test_reports_record_check_failures_via_xdist(testdir, ctx):
    api = ctx.openapi.apps.failure()
    report_path = str(testdir.tmpdir.join("report.xml"))
    cassette_path = str(testdir.tmpdir.join("cassette.yaml"))
    _make_xdist_test(
        ctx,
        testdir,
        f"""
schema.config.reports.update(junit_path=r"{report_path}", vcr_path=r"{cassette_path}")

@schema.parametrize()
@settings(max_examples=1)
def test_api(case):
    case.call_and_validate()
""",
        base_url=f"{api.base_url}/api",
        paths={"/failure": {"get": {"responses": {"500": {"description": "Internal Server Error"}}}}},
    )
    testdir.runpytest("-n", "2").assert_outcomes(failed=1)

    assert ElementTree.parse(report_path).findall(".//testcase/failure")
    checks = [check for i in load_yaml_or_fail(cassette_path)["http_interactions"] for check in i["checks"]]
    assert any(check["status"] == "FAILURE" for check in checks)


def test_xdist_no_report_configured(testdir, ctx):
    # workers always send data via workeroutput even with no reports configured;
    # the controller must silently skip processing when no writers are opened
    api = ctx.openapi.apps.success()
    _make_xdist_test(
        ctx,
        testdir,
        """
@schema.parametrize()
@settings(max_examples=1)
def test_api(case):
    case.call()
""",
        base_url=f"{api.base_url}/api",
    )
    result = testdir.runpytest("-n", "2")
    result.assert_outcomes(passed=1)


def test_xdist_worker_crash_does_not_break_controller(testdir, ctx):
    # Crashed workers don't report results; the hook must skip them, not raise.
    api = ctx.openapi.apps.success()
    _make_xdist_test(
        ctx,
        testdir,
        """
import os

@schema.parametrize()
@settings(max_examples=1)
def test_api(case):
    os._exit(1)
""",
        base_url=f"{api.base_url}/api",
    )
    result = testdir.runpytest("-n", "1")
    assert "INTERNALERROR" not in "\n".join(result.outlines + result.errlines)


@pytest.mark.parametrize("explicit_seed", [None, 42])
def test_xdist_workers_share_reported_seed(testdir, ctx, explicit_seed):
    api = ctx.openapi.apps.success()
    cassette_path = testdir.tmpdir.join("cassette.yaml")
    seeds_dir = testdir.mkdir("seeds")
    schema_dict = ctx.openapi.build_schema({"/success": {"get": {"responses": {"200": {"description": "OK"}}}}})
    seed_setup = "" if explicit_seed is None else f"schema.config.seed = {explicit_seed}"
    content = f"""
import os
import pathlib

import schemathesis
from hypothesis import settings

schema = schemathesis.openapi.from_dict({schema_dict!r})
schema.config.update(base_url="{api.base_url}/api")
{seed_setup}
schema.config.reports.update(vcr_path=r"{cassette_path}")

@schema.parametrize()
@settings(max_examples=1)
def test_api(case):
    case.call()
    worker = os.environ["PYTEST_XDIST_WORKER"]
    pathlib.Path(r"{seeds_dir}", worker).write_text(str(schema.config.seed))
"""
    # `loadfile` sends one file to each worker
    testdir.makepyfile(test_first=content, test_second=content)

    result = testdir.runpytest("-n", "2", "--dist=loadfile")
    result.assert_outcomes(passed=2)

    reported_seed = load_yaml_or_fail(str(cassette_path))["seed"]
    worker_seeds = {path.basename: int(path.read_text("utf-8")) for path in seeds_dir.listdir()}
    assert worker_seeds == {"gw0": reported_seed, "gw1": reported_seed}
    if explicit_seed is not None:
        assert reported_seed == explicit_seed


def test_vcr_report_network_error_via_xdist(testdir, ctx):
    cassette_path = str(testdir.tmpdir.join("cassette.yaml"))
    _make_xdist_test(
        ctx,
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

    cassette = load_yaml_or_fail(cassette_path)
    interactions = cassette["http_interactions"]
    assert len(interactions) >= 1
    assert all(i["response"] is None for i in interactions)
    assert all(i["status"] == "ERROR" for i in interactions)


def test_two_schemas_same_vcr_path_via_xdist(testdir, ctx):
    # Two schemas both enabling VCR without an explicit path share the same
    # default filename; the controller adds a schema-id suffix so they don't
    # overwrite each other.
    api = ctx.openapi.apps.success()
    report_dir = str(testdir.tmpdir.mkdir("reports"))
    schema_a = ctx.openapi.build_schema(
        {"/users": {"get": {"responses": {"200": {"description": "OK"}}}}},
        info={"title": "A", "description": "", "version": "0.1.0"},
    )
    schema_b = ctx.openapi.build_schema(
        {"/health": {"get": {"responses": {"200": {"description": "OK"}}}}},
        info={"title": "B", "description": "", "version": "0.1.0"},
    )
    testdir.makepyfile(
        f"""
import schemathesis
from schemathesis.config import ReportFormat
from pathlib import Path
from hypothesis import settings

schema_a = schemathesis.openapi.from_dict({schema_a!r})
schema_a.config.update(base_url="{api.base_url}/api")
schema_a.config.reports.update(formats=[ReportFormat.VCR], directory=Path(r"{report_dir}"))

schema_b = schemathesis.openapi.from_dict({schema_b!r})
schema_b.config.update(base_url="{api.base_url}/api")
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
        cassette = load_yaml_or_fail(f)
        assert "http_interactions" in cassette
        assert len(cassette["http_interactions"]) >= 1


def test_xdist_writer_open_failure(testdir, ctx):
    # VCR opens successfully, then HAR fails (path is a directory);
    # the except block must close the already-opened VCR writer before re-raising
    api = ctx.openapi.apps.success()
    vcr_path = str(testdir.tmpdir.join("cassette.yaml"))
    har_path = str(testdir.tmpdir.join("cassette.har"))
    testdir.tmpdir.mkdir("cassette.har")
    _make_xdist_test(
        ctx,
        testdir,
        f"""
schema.config.reports.update(vcr_path=r"{vcr_path}", har_path=r"{har_path}")

@schema.parametrize()
@settings(max_examples=1)
def test_api(case):
    case.call()
""",
        base_url=f"{api.base_url}/api",
    )
    result = testdir.runpytest("-n", "2")
    # the writer open failure propagates from pytest_testnodedown as a session error
    assert result.ret != 0
    # VCR writer was opened before HAR failed — file exists with the cassette header
    with open(vcr_path) as f:
        content = f.read()
    assert "recorded_with" in content


def test_reports_written_for_stateful_test(ctx, testdir, tmp_path):
    api = ctx.openapi.apps.users_crud()
    cassette_path = str(testdir.tmpdir.join("cassette.yaml"))
    har_path = str(testdir.tmpdir.join("cassette.har"))
    report_path = str(testdir.tmpdir.join("report.xml"))
    allure_dir = tmp_path / "allure-results"
    testdir.make_test(
        f"""
schema = schemathesis.openapi.from_url("{api.schema_url}")
schema.config.update(base_url="{api.base_url}")
schema.config.reports.update(
    vcr_path=r"{cassette_path}", har_path=r"{har_path}", junit_path=r"{report_path}", allure_path=r"{allure_dir}"
)
TestCase = schema.as_state_machine().TestCase
""",
    )
    result = testdir.runpytest("-s")
    result.assert_outcomes(failed=1)

    interactions = load_yaml_or_fail(cassette_path)["http_interactions"]
    assert "request" in interactions[0]
    assert "response" in interactions[0]
    entries = load_json_or_fail(har_path)["log"]["entries"]
    assert "request" in entries[0]
    assert "response" in entries[0]
    tree = ElementTree.parse(report_path)
    assert tree.findall(".//testcase")
    assert tree.findall(".//failure")
    assert allure_outcomes(allure_dir) == {"Stateful tests": ("failed", "")}


def test_vcr_report_written_for_stateful_test_via_xdist(ctx, testdir):
    api = ctx.openapi.apps.users_crud()
    cassette_path = str(testdir.tmpdir.join("cassette.yaml"))
    testdir.makepyfile(
        f"""
import schemathesis

schema = schemathesis.openapi.from_url("{api.schema_url}")
schema.config.update(base_url="{api.base_url}")
schema.config.reports.update(vcr_path=r"{cassette_path}")

TestCase = schema.as_state_machine().TestCase
"""
    )
    result = testdir.runpytest("-n", "2", "--dist=load")
    result.assert_outcomes(failed=1)

    assert pathlib.Path(cassette_path).exists()
    cassette = load_yaml_or_fail(cassette_path)
    interactions = cassette["http_interactions"]
    assert len(interactions) >= 1
    assert "request" in interactions[0]
    assert "response" in interactions[0]


def test_junit_report_for_lazy_schema_without_calls(testdir):
    report_path = str(testdir.tmpdir.join("report.xml"))
    testdir.make_test(
        f"""
schema.config.reports.update(junit_path=r"{report_path}")
lazy_schema = schemathesis.pytest.from_fixture("simple_schema")

@lazy_schema.parametrize()
def test_api(case):
    pass
""",
    )
    testdir.runpytest().assert_outcomes(passed=1)

    assert _junit_outcomes(report_path) == {}


@pytest.mark.parametrize("xdist", [False, True], ids=["in-process", "xdist"])
def test_junit_reports_for_schemas_with_identical_content(testdir, ctx, xdist):
    first_report_path = str(testdir.tmpdir.join("first.xml"))
    second_report_path = str(testdir.tmpdir.join("second.xml"))
    api = ctx.openapi.apps.success()
    testdir.make_test(
        f"""
schema.config.update(base_url="{api.base_url}/api")
schema.config.reports.update(junit_path=r"{first_report_path}")
other = schemathesis.openapi.from_dict(raw_schema)
other.config.update(base_url="{api.base_url}/api")
other.config.reports.update(junit_path=r"{second_report_path}")
unreported = schemathesis.openapi.from_dict(raw_schema)

@schema.include(path="/success").parametrize()
@settings(max_examples=1)
def test_first_report(case):
    case.call()

@other.include(path="/users").parametrize()
@settings(max_examples=1)
def test_second_report(case):
    case.call()

@unreported.parametrize()
def test_without_report(case):
    raise AssertionError("not reported")
""",
        paths={"/success": {"get": {"responses": {"200": {"description": "OK"}}}}},
    )
    args = ("-n", "1") if xdist else ()
    testdir.runpytest("-p", "no:randomly", *args).assert_outcomes(passed=2, failed=2)

    assert _junit_outcomes(first_report_path) == {"GET /success": ("passed", "")}
    assert _junit_outcomes(second_report_path) == {"GET /users": ("passed", "")}


def test_stateful_test_via_xdist_without_reports(ctx, testdir):
    api = ctx.openapi.apps.users_crud()
    testdir.makepyfile(
        f"""
import schemathesis

schema = schemathesis.openapi.from_url("{api.schema_url}")
schema.config.update(base_url="{api.base_url}")

TestCase = schema.as_state_machine().TestCase
"""
    )
    result = testdir.runpytest("-n", "1")
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*Undocumented HTTP status code*"])


def test_html_report_failed(testdir, ctx, snapshot_html):
    api = ctx.openapi.apps.failure()
    report_dir = testdir.tmpdir.join("report")
    testdir.make_test(
        f"""
schema.config.update(base_url="{api.base_url}/api")
schema.config.seed = 42
schema.config.reports.update(html_path=r"{report_dir}")

@schema.parametrize()
@settings(max_examples=1, phases=[Phase.generate])
def test_api(case):
    case.call_and_validate()
""",
        schema=ctx.openapi.build_schema(
            {"/failure": {"get": {"responses": {"500": {"description": "Internal Server Error"}}}}}
        ),
    )
    testdir.runpytest().assert_outcomes(failed=1)

    assert report_dir.join("index.html").read_text("utf-8") == snapshot_html
    assert report_dir.listdir() == [report_dir.join("index.html")]


def test_html_report_passed(testdir, ctx, snapshot_html):
    api = ctx.openapi.apps.success()
    report_dir = testdir.tmpdir.join("report")
    testdir.make_test(
        f"""
schema.config.update(base_url="{api.base_url}/api")
schema.config.seed = 42
schema.config.reports.update(html_path=r"{report_dir}")

@schema.parametrize()
@settings(max_examples=1, phases=[Phase.generate])
def test_api(case):
    case.call_and_validate()
""",
        schema=ctx.openapi.build_schema({"/success": {"get": {"responses": {"200": {"description": "OK"}}}}}),
    )
    testdir.runpytest().assert_outcomes(passed=1)

    assert report_dir.join("index.html").read_text("utf-8") == snapshot_html


@pytest.mark.parametrize("outcome", ["assertion", "runtime_error", "skip"])
def test_html_report_uses_pytest_outcome(testdir, ctx, outcome, snapshot_html):
    report_dir = testdir.tmpdir.join("report")
    make_pytest_outcome_test(
        testdir, ctx, outcome, f'schema.config.seed = 42\nschema.config.reports.update(html_path=r"{report_dir}")'
    )

    testdir.runpytest()

    assert report_dir.join("index.html").read_text("utf-8") == snapshot_html


def test_html_report_repeated_check_failure_across_items(testdir, ctx):
    api = ctx.openapi.apps.failure()
    report_dir = testdir.tmpdir.join("report")
    testdir.make_test(
        f"""
schema.config.update(base_url="{api.base_url}/api")
schema.config.reports.update(html_path=r"{report_dir}")

@pytest.mark.parametrize("user", ["alice", "bob"])
@schema.parametrize()
@settings(max_examples=1, phases=[Phase.generate])
def test_api(case, user):
    case.call_and_validate(headers={{"X-User": user}})
""",
        schema=ctx.openapi.build_schema({"/failure": {"get": {"responses": {"200": {"description": "OK"}}}}}),
    )
    testdir.runpytest().assert_outcomes(failed=2)

    assert re.findall(r'class="tf-name"[^>]* title="([^"]+)"', report_dir.join("index.html").read_text("utf-8")) == [
        "Server error",
        "Undocumented HTTP status code",
    ]


def test_html_report_merged_via_xdist(testdir, ctx, snapshot_html):
    api = ctx.openapi.apps.success_and_failure()
    testdir.makefile(".toml", schemathesis='seed = 42\n\n[reports.html]\npath = "report"\n')
    testdir.makepyfile(
        f"""
import schemathesis
from hypothesis import settings, Phase

schema = schemathesis.openapi.from_url("{api.schema_url}")

@schema.parametrize()
@settings(max_examples=1, phases=[Phase.generate])
def test_api(case):
    case.call_and_validate()
"""
    )
    testdir.runpytest("-n", "2").assert_outcomes(passed=1, failed=1)

    assert testdir.tmpdir.join("report", "index.html").read_text("utf-8") == snapshot_html


def reports_section(lines):
    start = next((index for index, line in enumerate(lines) if "Schemathesis reports" in line), len(lines))
    end = next((index for index in range(start + 1, len(lines)) if lines[index].startswith("=")), len(lines))
    return lines[start:end]


@pytest.mark.parametrize("args", [(), ("-n", "2")], ids=["single-process", "xdist"])
def test_report_paths_in_terminal_summary(testdir, ctx, args):
    api = ctx.openapi.apps.success()
    testdir.makefile(
        ".toml",
        schemathesis='[reports.junit]\npath = "junit.xml"\n\n[reports.html]\nenabled = true\npath = "report"\n',
    )
    testdir.makepyfile(
        f"""
import schemathesis
from hypothesis import settings, Phase

schema = schemathesis.openapi.from_url("{api.schema_url}")

@schema.parametrize()
@settings(max_examples=1, phases=[Phase.generate])
def test_api(case):
    case.call()
"""
    )
    result = testdir.runpytest(*args)
    result.assert_outcomes(passed=1)

    header, *entries = reports_section(result.outlines)
    # The separator width follows the terminal, which is one column narrower on Windows
    assert header.strip("= ") == "Schemathesis reports"
    # Paths are printed with native separators, as in the CLI's Reports section
    assert entries == [
        "  - JUnit: junit.xml",
        f"  - HTML: {pathlib.Path('report', 'index.html')}",
    ]


@pytest.mark.parametrize("xdist", [False, True], ids=["single-process", "xdist"])
def test_junit_failure_names_failure_code(testdir, ctx, xdist):
    api = ctx.openapi.apps.failure()
    junit_path = str(testdir.tmpdir.join("report.xml"))
    _make_xdist_test(
        ctx,
        testdir,
        f"""
schema.config.reports.update(junit_path=r"{junit_path}")

@schema.parametrize()
@settings(max_examples=1)
def test_api(case):
    case.call_and_validate()
""",
        base_url=f"{api.base_url}/api",
        paths={"/failure": {"get": {"responses": {"200": {"description": "OK"}}}}},
    )
    testdir.runpytest(*(("-n", "2") if xdist else ())).assert_outcomes(failed=1)
    assert _junit_outcomes(junit_path) == {"GET /failure": ("failure", "ST101, ST103")}
