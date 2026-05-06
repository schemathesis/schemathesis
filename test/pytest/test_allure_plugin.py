from pathlib import Path
from textwrap import indent

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
    assert data["status"] in ("passed", "failed", "broken")


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
    assert data["status"] in ("passed", "failed", "broken")
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
