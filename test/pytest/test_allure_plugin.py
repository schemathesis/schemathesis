from textwrap import indent
from xml.etree import ElementTree

import pytest

from test.utils import allure_outcomes, allure_results, make_pytest_outcome_test


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


def _first_allure_result(allure_dir):
    return allure_results(allure_dir)[0]


def _allure_result_summaries(allure_dir):
    return sorted(
        (
            result["name"],
            result["status"],
            sorted(attachment["name"] for attachment in result.get("attachments", [])),
            [step["status"] for step in result.get("steps", [])],
        )
        for result in allure_results(allure_dir)
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

    assert allure_outcomes(allure_dir) == {
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


@pytest.mark.parametrize(
    ("outcome", "expected", "xdist"),
    [
        ("check_failure", ("failed", "failure"), False),
        ("assertion", ("failed", "failure"), False),
        ("runtime_error", ("broken", "error"), False),
        ("unsatisfiable", ("broken", "error"), False),
        ("network_error", ("broken", "error"), False),
        ("schema_error", ("broken", "error"), False),
        ("skip", ("skipped", "skipped"), False),
        # Workers forward the outcome as a status name and message, the same for every outcome.
        ("runtime_error", ("broken", "error"), True),
        ("skip", ("skipped", "skipped"), True),
    ],
    ids=[
        "check_failure",
        "assertion",
        "runtime_error",
        "unsatisfiable",
        "network_error",
        "schema_error",
        "skip",
        "runtime_error-xdist",
        "skip-xdist",
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

    allure_status = {name: status for name, (status, _) in allure_outcomes(allure_dir).items()}
    junit_kind = {
        test_case.get("name"): (test_case[0].tag if len(test_case) else "passed")
        for test_case in ElementTree.parse(junit_path).getroot().iter("testcase")
    }
    assert (allure_status, junit_kind) == ({label: expected[0]}, {label: expected[1]})


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


def test_allure_report_written_via_xdist(testdir, tmp_path, ctx):
    api = ctx.openapi.apps.success()
    schema_dict = ctx.openapi.build_schema(
        {"/users": {"get": {"tags": ["users", "readonly"], "responses": {"200": {"description": "OK"}}}}},
    )
    _make_allure_xdist_test(
        testdir,
        schema_dict=schema_dict,
        base_url=api.base_url,
        reports_config=f'schema.config.reports.update(formats=[ReportFormat.ALLURE], directory=Path(r"{tmp_path}"))',
        body="""
allure.dynamic.title("xdist title")
allure.dynamic.description("xdist description")
allure.dynamic.link("https://example.com", name="xdist link")
allure.attach("xdist body", name="xdist note", attachment_type=allure.attachment_type.TEXT)
case.call()
""",
        imports=("import allure", "from pathlib import Path", "from schemathesis.config import ReportFormat"),
    )
    result = testdir.runpytest("-n", "2")
    result.assert_outcomes(passed=1)
    # Without an explicit path, results go to a stable `allure` directory, not a timestamped one.
    allure_dir = tmp_path / "allure"
    data = _first_allure_result(allure_dir)
    assert data["status"] == "passed"
    assert {lbl["value"] for lbl in data["labels"] if lbl["name"] == "feature"} == {"users", "readonly"}
    assert data["name"] == "xdist title"
    assert data["description"] == "xdist description"
    assert any(lnk["name"] == "xdist link" for lnk in data.get("links", []))
    attachment = next((a for a in data.get("attachments", []) if a["name"] == "xdist note"), None)
    assert attachment is not None
    assert (allure_dir / attachment["source"]).read_text() == "xdist body"


def test_allure_report_written_via_plugin(testdir, tmp_path, ctx):
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
    allure.dynamic.link("https://example.com/docs", name="API Docs")
    allure.dynamic.description("Custom description")
    allure.dynamic.title("My Custom Title")
    case.call()
""",
    )
    result = testdir.runpytest("-s")
    result.assert_outcomes(passed=1)
    data = _first_allure_result(allure_dir)
    assert data["status"] == "passed"
    assert data["testCaseId"] == data["historyId"]
    assert any(a["name"] == "my-note" for a in data.get("attachments", []))
    assert any(lnk["name"] == "API Docs" for lnk in data.get("links", []))
    assert data.get("description") == "Custom description"
    assert data["name"] == "My Custom Title"
