from pathlib import Path
from textwrap import indent
from xml.etree import ElementTree

import pytest

from test.utils import load_json_or_fail


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
    marker = ""
    if outcome == "check_failure":
        api = ctx.openapi.apps.failure()
        schema = f'schemathesis.openapi.from_url("{api.schema_url}")'
        config = ""
        body = "case.call_and_validate()"
        label = "GET /api/failure"
    elif outcome == "unsatisfiable":
        api = ctx.openapi.apps.unsatisfiable()
        schema = f'schemathesis.openapi.from_url("{api.schema_url}")'
        config = "schema.config.generation.update(modes=[GenerationMode.POSITIVE])"
        body = "case.call_and_validate()"
        label = "POST /api/unsatisfiable"
    elif outcome == "network_error":
        schema_dict = ctx.openapi.build_schema({"/network": {"get": {"responses": {"200": {"description": "OK"}}}}})
        schema = f"schemathesis.openapi.from_dict({schema_dict!r})"
        config = 'schema.config.update(base_url="http://127.0.0.1:1")'
        body = "case.call()"
        label = "GET /network"
    else:
        api = ctx.openapi.apps.success()
        schema = f'schemathesis.openapi.from_url("{api.schema_url}")'
        config = ""
        body = {
            "assertion": 'case.call(); assert False, "boom"',
            "runtime_error": 'case.call(); raise RuntimeError("bug")',
            "skip": 'pytest.skip("why")',
            "mark_skip": "case.call()",
        }[outcome]
        if outcome == "mark_skip":
            marker = '@pytest.mark.skip(reason="why")'
        label = "GET /api/success"

    testdir.make_test(
        f"""
schema = {schema}
{config}
schema.config.reports.update(allure_path=r"{allure_dir}")

{marker}
@schema.parametrize()
@settings(max_examples=1, phases=[Phase.generate])
def test_api(case):
    {body}
"""
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
