from pathlib import Path
from textwrap import indent
from xml.etree import ElementTree

import pytest

from test.utils import load_json_or_fail, make_pytest_outcome_test


def _make_allure_xdist_test(testdir, *, schema_dict, base_url, reports_config, body="case.call()", imports=()):
    imports_block = "\n".join(imports)
    if imports_block:
        imports_block = f"{imports_block}\n"
    testdir.makepyfile(
        f"""
{imports_block}import schemathesis
from hypothesis import settings

schema = schemathesis.openapi.from_dict({schema_dict!r})
schema.config.update(base_url="{base_url}/api")
{reports_config}

@schema.parametrize()
@settings(max_examples=1)
def test_api(case):
{indent(body.strip(), "    ")}
"""
    )


def _allure_results(allure_dir):
    result_files = list(Path(allure_dir).glob("*-result.json"))
    assert result_files
    return [load_json_or_fail(path) for path in result_files]


def _first_allure_result(allure_dir):
    return _allure_results(allure_dir)[0]


def _allure_outcomes(allure_dir):
    outcomes = {}
    for result in _allure_results(allure_dir):
        raw_message = result.get("statusDetails", {}).get("message", "")
        message = raw_message.splitlines()[0] if raw_message else ""
        if message.startswith("ConnectionError: "):
            message = "ConnectionError"
        outcomes[result["name"]] = (result["status"], message)
    return outcomes


def _allure_result_summaries(allure_dir):
    return sorted(
        (
            result["name"],
            result["status"],
            sorted(attachment["name"] for attachment in result.get("attachments", [])),
            [step["status"] for step in result.get("steps", [])],
        )
        for result in _allure_results(allure_dir)
    )


@pytest.mark.parametrize("xdist", [False, True], ids=["in-process", "xdist"])
def test_from_fixture_writes_allure_and_junit_reports(testdir, tmp_path, ctx, xdist):
    api = ctx.openapi.apps.success_and_failure()
    allure_dir = tmp_path / "allure-results"
    junit_path = tmp_path / "junit.xml"
    testdir.makepyfile(
        f"""
import pytest
import schemathesis
from hypothesis import Phase, settings

@pytest.fixture
def api_schema():
    schema = schemathesis.openapi.from_url("{api.schema_url}")
    schema.config.reports.update(allure_path=r"{allure_dir}", junit_path=r"{junit_path}")
    return schema

lazy_schema = schemathesis.pytest.from_fixture("api_schema")

@lazy_schema.parametrize()
@settings(max_examples=1, phases=[Phase.generate])
def test_api(case):
    case.call_and_validate()
"""
    )
    args = ("-n", "2") if xdist else ()

    testdir.runpytest(*args)

    assert _allure_outcomes(allure_dir) == {
        "GET /api/failure": ("failed", ""),
        "GET /api/success": ("passed", ""),
    }
    assert {
        test_case.get("name"): (test_case[0].tag if len(test_case) else "passed")
        for test_case in ElementTree.parse(junit_path).getroot().iter("testcase")
    } == {
        "GET /api/failure": "failure",
        "GET /api/success": "passed",
    }


@pytest.mark.parametrize("xdist", [False, True], ids=["in-process", "xdist"])
@pytest.mark.parametrize(
    ("outcome", "expected"),
    [
        ("check_failure", ("failed", "failure")),
        ("assertion", ("failed", "failure")),
        ("runtime_error", ("broken", "error")),
        ("unsatisfiable", ("broken", "error")),
        ("network_error", ("broken", "error")),
        ("skip", ("skipped", "skipped")),
    ],
)
def test_from_fixture_reports_use_pytest_outcome(testdir, tmp_path, ctx, xdist, outcome, expected):
    allure_dir = tmp_path / "allure-results"
    junit_path = tmp_path / "junit.xml"
    label = make_pytest_outcome_test(
        testdir,
        ctx,
        outcome,
        f'schema.config.reports.update(allure_path=r"{allure_dir}", junit_path=r"{junit_path}")',
        lazy=True,
    )
    args = ("-n", "2") if xdist else ()

    testdir.runpytest(*args)

    allure_status = {name: status for name, (status, _) in _allure_outcomes(allure_dir).items()}
    junit_kind = {
        test_case.get("name"): (test_case[0].tag if len(test_case) else "passed")
        for test_case in ElementTree.parse(junit_path).getroot().iter("testcase")
    }
    assert (allure_status, junit_kind) == ({label: expected[0]}, {label: expected[1]})


@pytest.mark.parametrize("xdist", [False, True], ids=["in-process", "xdist"])
@pytest.mark.parametrize(
    ("outcome", "expected"),
    [
        ("check_failure", ("failed", "")),
        ("assertion", ("failed", "boom")),
        ("runtime_error", ("broken", "RuntimeError: bug")),
        (
            "unsatisfiable",
            ("broken", "Unsatisfiable: Cannot generate test data for request body (application/json)"),
        ),
        ("network_error", ("broken", "ConnectionError")),
        ("skip", ("skipped", "why")),
        ("mark_skip", ("skipped", "why")),
    ],
)
def test_allure_report_uses_pytest_outcome(testdir, tmp_path, ctx, xdist, outcome, expected):
    allure_dir = tmp_path / "allure-results"
    label = make_pytest_outcome_test(
        testdir, ctx, outcome, f'schema.config.reports.update(allure_path=r"{allure_dir}")'
    )
    args = ("-n", "2") if xdist else ()

    testdir.runpytest(*args)

    assert _allure_outcomes(allure_dir) == {label: expected}


@pytest.mark.parametrize("xdist", [False, True], ids=["in-process", "xdist"])
def test_allure_merges_results_from_same_schema_source(testdir, tmp_path, ctx, xdist):
    api = ctx.openapi.apps.failure()
    allure_dir = tmp_path / "allure-results"
    junit_path = tmp_path / "junit.xml"
    testdir.makepyfile(
        f"""
import allure
import schemathesis
from hypothesis import Phase, settings

schema = schemathesis.openapi.from_url("{api.schema_url}")
schema.config.reports.update(allure_path=r"{allure_dir}", junit_path=r"{junit_path}")
filtered_schema = schema.include(name="GET /api/failure")

@filtered_schema.parametrize()
@settings(max_examples=1, phases=[Phase.generate])
def test_passing(case):
    allure.attach("passing", name="passing test", attachment_type=allure.attachment_type.TEXT)
    case.call()

@schema.parametrize()
@settings(max_examples=1, phases=[Phase.generate])
def test_failing(case):
    allure.attach("failing", name="failing test", attachment_type=allure.attachment_type.TEXT)
    case.call_and_validate()
"""
    )
    args = ("-n", "2") if xdist else ()

    result = testdir.runpytest(*args)

    result.assert_outcomes(passed=1, failed=1)
    assert _allure_result_summaries(allure_dir) == [
        ("GET /api/failure", "failed", ["failing test", "passing test"], ["failed"])
    ]
    testcases = ElementTree.parse(junit_path).getroot().iter("testcase")
    assert [(case.get("name"), [child.tag for child in case]) for case in testcases] == [
        ("GET /api/failure", ["failure"])
    ]


@pytest.mark.parametrize("xdist", [False, True], ids=["in-process", "xdist"])
def test_allure_keeps_results_from_different_schema_sources_separate(testdir, tmp_path, ctx, xdist):
    api = ctx.openapi.apps.success()
    allure_dir = tmp_path / "allure-results"
    paths = {"/success": {"get": {"responses": {"200": {"description": "OK"}}}}}
    first_schema = ctx.openapi.write_schema(paths, filename="first")
    second_schema = ctx.openapi.write_schema(paths, filename="second")
    testdir.makepyfile(
        f"""
import allure
import schemathesis
from hypothesis import Phase, settings

first_schema = schemathesis.openapi.from_path(r"{first_schema}")
first_schema.config.update(base_url="{api.base_url}/api")
first_schema.config.reports.update(allure_path=r"{allure_dir}")

second_schema = schemathesis.openapi.from_path(r"{second_schema}")
second_schema.config.update(base_url="{api.base_url}/api")
second_schema.config.reports.update(allure_path=r"{allure_dir}")

@first_schema.parametrize()
@settings(max_examples=1, phases=[Phase.generate])
def test_first(case):
    allure.attach("first", name="first schema", attachment_type=allure.attachment_type.TEXT)
    case.call()

@second_schema.parametrize()
@settings(max_examples=1, phases=[Phase.generate])
def test_second(case):
    allure.attach("second", name="second schema", attachment_type=allure.attachment_type.TEXT)
    case.call()
"""
    )
    args = ("-n", "2") if xdist else ()

    result = testdir.runpytest(*args)

    result.assert_outcomes(passed=2)
    assert _allure_result_summaries(allure_dir) == [
        ("GET /success", "passed", ["first schema"], []),
        ("GET /success", "passed", ["second schema"], []),
    ]


def test_allure_xdist_stable_path_without_explicit_path(testdir, tmp_path, ctx):
    api = ctx.openapi.apps.success()
    schema_dict = ctx.openapi.build_schema({"/users": {"get": {"responses": {"200": {"description": "OK"}}}}})
    _make_allure_xdist_test(
        testdir,
        schema_dict=schema_dict,
        base_url=api.base_url,
        reports_config=f'schema.config.reports.update(formats=[ReportFormat.ALLURE], directory=Path(r"{tmp_path}"))',
        imports=("from pathlib import Path", "from schemathesis.config._report import ReportFormat"),
    )
    result = testdir.runpytest("-n", "2")
    result.assert_outcomes(passed=1)
    # get_stable_path without suffix returns directory/allure (no timestamp)
    assert list((tmp_path / "allure").glob("*-result.json"))


def test_allure_feature_labels_via_xdist(testdir, tmp_path, ctx):
    api = ctx.openapi.apps.success()
    allure_dir = tmp_path / "allure-results"
    schema_dict = ctx.openapi.build_schema(
        {"/users": {"get": {"tags": ["users", "readonly"], "responses": {"200": {"description": "OK"}}}}},
    )
    _make_allure_xdist_test(
        testdir,
        schema_dict=schema_dict,
        base_url=api.base_url,
        reports_config=f'schema.config.reports.update(allure_path=r"{allure_dir}")',
    )
    result = testdir.runpytest("-n", "2")
    result.assert_outcomes(passed=1)
    data = _first_allure_result(allure_dir)
    feature_labels = [lbl["value"] for lbl in data["labels"] if lbl["name"] == "feature"]
    assert set(feature_labels) == {"users", "readonly"}


def test_allure_dynamic_calls_via_xdist(testdir, tmp_path, ctx):
    api = ctx.openapi.apps.success()
    allure_dir = tmp_path / "allure-results"
    schema_dict = ctx.openapi.build_schema({"/users": {"get": {"responses": {"200": {"description": "OK"}}}}})
    _make_allure_xdist_test(
        testdir,
        schema_dict=schema_dict,
        base_url=api.base_url,
        reports_config=f'schema.config.reports.update(allure_path=r"{allure_dir}")',
        body="""
allure.dynamic.title("xdist title")
allure.dynamic.description("xdist description")
allure.dynamic.link("https://example.com", name="xdist link")
allure.attach("xdist body", name="xdist note", attachment_type=allure.attachment_type.TEXT)
case.call()
""",
        imports=("import allure",),
    )
    result = testdir.runpytest("-n", "2")
    result.assert_outcomes(passed=1)
    data = _first_allure_result(allure_dir)
    assert data["name"] == "xdist title"
    assert data["description"] == "xdist description"
    assert any(lnk["name"] == "xdist link" for lnk in data.get("links", []))
    attachment = next((a for a in data.get("attachments", []) if a["name"] == "xdist note"), None)
    assert attachment is not None
    assert (allure_dir / attachment["source"]).read_text() == "xdist body"


def test_allure_report_written_via_xdist(testdir, ctx):
    api = ctx.openapi.apps.success()
    allure_dir = str(testdir.tmpdir.join("allure-results"))
    schema_dict = ctx.openapi.build_schema({"/users": {"get": {"responses": {"200": {"description": "OK"}}}}})
    _make_allure_xdist_test(
        testdir,
        schema_dict=schema_dict,
        base_url=api.base_url,
        reports_config=f'schema.config.reports.update(allure_path=r"{allure_dir}")',
    )
    result = testdir.runpytest("-n", "2")
    result.assert_outcomes(passed=1)

    data = _first_allure_result(allure_dir)
    assert "name" in data
    assert data["status"] == "passed"


def test_allure_report_written_via_plugin(testdir, tmp_path, ctx):
    api = ctx.openapi.apps.success()
    allure_dir = tmp_path / "allure-results"
    testdir.make_test(
        f"""
schema.config.update(base_url="{api.base_url}/api")
schema.config.reports.update(allure_path=r"{allure_dir}")

@schema.parametrize()
@settings(max_examples=1)
def test_api(case):
    case.call()
""",
    )
    result = testdir.runpytest("-s")
    result.assert_outcomes(passed=1)
    data = _first_allure_result(allure_dir)
    assert "name" in data
    assert data["status"] == "passed"
    assert data["testCaseId"] == data["historyId"]


def test_allure_report_failure_written_via_plugin(testdir, tmp_path, ctx):
    api = ctx.openapi.apps.failure()
    allure_dir = tmp_path / "allure-results"
    testdir.make_test(
        f"""
schema.config.update(base_url="{api.base_url}/api")
schema.config.reports.update(allure_path=r"{allure_dir}")

@schema.parametrize()
@settings(max_examples=1)
def test_api(case):
    case.call_and_validate()
""",
        paths={"/failure": {"get": {"responses": {"500": {"description": "Internal Server Error"}}}}},
    )
    result = testdir.runpytest("-s")
    result.assert_outcomes(failed=2)
    failed_results = _allure_results(allure_dir)
    assert any(r["status"] == "failed" for r in failed_results)
    failed = next(r for r in failed_results if r["status"] == "failed")
    step_messages = [s["statusDetails"]["message"] for s in failed.get("steps", [])]
    assert step_messages
    assert any("curl" in m.lower() for m in step_messages)


def test_allure_attachment_via_forwarder(testdir, tmp_path, ctx):
    api = ctx.openapi.apps.success()
    allure_dir = tmp_path / "allure-results"
    testdir.make_test(
        f"""
import allure
schema.config.update(base_url="{api.base_url}/api")
schema.config.reports.update(allure_path=r"{allure_dir}")

@schema.parametrize()
@settings(max_examples=1)
def test_api(case):
    allure.attach("extra context", name="my-note", attachment_type=allure.attachment_type.TEXT)
    case.call()
""",
    )
    result = testdir.runpytest("-s")
    result.assert_outcomes(passed=1)
    data = _first_allure_result(allure_dir)
    assert any(a["name"] == "my-note" for a in data.get("attachments", []))


def test_allure_link_via_forwarder(testdir, tmp_path, ctx):
    api = ctx.openapi.apps.success()
    allure_dir = tmp_path / "allure-results"
    testdir.make_test(
        f"""
import allure
schema.config.update(base_url="{api.base_url}/api")
schema.config.reports.update(allure_path=r"{allure_dir}")

@schema.parametrize()
@settings(max_examples=1)
def test_api(case):
    allure.dynamic.link("https://example.com/docs", name="API Docs")
    case.call()
""",
    )
    result = testdir.runpytest("-s")
    result.assert_outcomes(passed=1)
    data = _first_allure_result(allure_dir)
    assert any(lnk["name"] == "API Docs" for lnk in data.get("links", []))


def test_allure_description_via_forwarder(testdir, tmp_path, ctx):
    api = ctx.openapi.apps.success()
    allure_dir = tmp_path / "allure-results"
    testdir.make_test(
        f"""
import allure
schema.config.update(base_url="{api.base_url}/api")
schema.config.reports.update(allure_path=r"{allure_dir}")

@schema.parametrize()
@settings(max_examples=1)
def test_api(case):
    allure.dynamic.description("Custom description")
    case.call()
""",
    )
    result = testdir.runpytest("-s")
    result.assert_outcomes(passed=1)
    data = _first_allure_result(allure_dir)
    assert data.get("description") == "Custom description"


def test_allure_title_override_via_forwarder(testdir, tmp_path, ctx):
    api = ctx.openapi.apps.success()
    allure_dir = tmp_path / "allure-results"
    testdir.make_test(
        f"""
import allure
schema.config.update(base_url="{api.base_url}/api")
schema.config.reports.update(allure_path=r"{allure_dir}")

@schema.parametrize()
@settings(max_examples=1)
def test_api(case):
    allure.dynamic.title("My Custom Title")
    case.call()
""",
    )
    result = testdir.runpytest("-s")
    result.assert_outcomes(passed=1)
    data = _first_allure_result(allure_dir)
    assert data["name"] == "My Custom Title"
