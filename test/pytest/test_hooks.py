from itertools import groupby
from pathlib import Path

import pytest
from flask import jsonify, request

import schemathesis
from schemathesis.generation.modes import GenerationMode


def test_map_and_flatmap_hooks_work_in_negative_mode(testdir, simple_openapi):
    # Regression for GH-3471 and GH-3652: hooks receive raw dicts, not generated-value wrappers.
    testdir.make_test(
        """
from hypothesis import strategies as st

def map_replacement(context, query):
    assert isinstance(query, dict), f"Expected dict, got {type(query).__name__}"
    return {"id": "fixed"}

@schema.hooks.apply(map_replacement, name="map_query")
@schema.parametrize()
@settings(max_examples=5)
def test_map_hook_receives_dict(case):
    pass

@st.composite
def new_query(draw, query):
    return {"id": str(draw(st.integers(min_value=1, max_value=100)))}

def flatmap_replacement(context, query):
    return new_query(query)

@schema.hooks.apply(flatmap_replacement, name="flatmap_query")
@schema.parametrize()
@settings(max_examples=5)
def test_flatmap_hook_works(case):
    pass
""",
        schema=simple_openapi,
        generation_modes=[GenerationMode.NEGATIVE],
    )
    result = testdir.runpytest()
    result.assert_outcomes(passed=2)


def test_per_test_hooks(testdir, simple_openapi):
    testdir.make_test(
        """
from hypothesis import strategies as st

def replacement(context, query):
    return {"id": "foobar"}

@schema.hooks.apply(replacement, name="map_query")
@schema.parametrize()
@settings(max_examples=1)
def test_a(case):
    if not hasattr(case.meta.phase.data, "description"):
        assert case.query["id"] == "foobar"

@schema.parametrize()
@schema.hooks.apply(replacement, name="map_query")
@settings(max_examples=1)
def test_b(case):
    if not hasattr(case.meta.phase.data, "description"):
        assert case.query["id"] == "foobar"

def another_replacement(context, query):
    return {"id": "foobaz"}

def map_headers(context, headers):
    return {"value": "spam"}

@schema.parametrize()
@schema.hooks.apply(another_replacement, name="map_query")  # Higher priority
@schema.hooks.apply(replacement, name="map_query")
@schema.hooks.apply(map_headers)
@settings(max_examples=1)
def test_c(case):
    if not hasattr(case.meta.phase.data, "description"):
        assert case.query["id"] == "foobaz"
        assert case.headers["value"] == "spam"

@schema.parametrize()
@settings(max_examples=1)
def test_d(case):
    if not hasattr(case.meta.phase.data, "description"):
        assert case.query["id"] != "foobar"
    """,
        schema=simple_openapi,
    )
    result = testdir.runpytest()
    result.assert_outcomes(passed=4)


def test_hooks_via_parametrize(testdir, simple_openapi):
    testdir.make_test(
        """
@schema.hook("filter_query")
def extra(context, query):
    return query["id"].isdigit() and query["id"].isascii() and int(query["id"]) % 2 == 0

@schema.parametrize()
@settings(max_examples=1)
def test(case):
    assert case.operation.schema.hooks.get_all_by_name("filter_query")[0] is extra
    assert int(case.query["id"]) % 2 == 0
    """,
        schema=simple_openapi,
        generation_modes=[GenerationMode.POSITIVE],
    )
    result = testdir.runpytest()
    result.assert_outcomes(passed=1)


def test_before_add_examples(testdir, simple_openapi):
    testdir.make_test(
        """
@schema.hook
def before_add_examples(context, examples):
    new = context.operation.Case(
        query={"foo": "bar"}
    )
    examples.append(new)

@schema.parametrize()
@settings(phases=[Phase.explicit])
def test_a(case):
    if not hasattr(case.meta, "phase"):
        assert case.query == {"foo": "bar"}


def another_hook(context, examples):
    new = context.operation.Case(
        query={"spam": "baz"}
    )
    examples.append(new)

IDX = 0

@schema.parametrize()
@schema.hooks.apply(another_hook, name="before_add_examples")
@settings(phases=[Phase.explicit])
def test_b(case):
    if not hasattr(case.meta, "phase"):
        global IDX
        if IDX == 0:
            assert case.query == {"spam": "baz"}
        if IDX == 1:
            assert case.query == {"foo": "bar"}
        IDX += 1
    """,
        schema=simple_openapi,
    )
    result = testdir.runpytest()
    result.assert_outcomes(passed=2)


def test_before_init_operation(testdir, simple_openapi):
    testdir.make_test(
        """
@schema.hook
def before_init_operation(context, operation):
    operation.query[0].definition["schema"] = {"enum": [42]}

@schema.parametrize()
def test_a(case):
    if not hasattr(case.meta.phase.data, "description"):
        assert case.query == {"id": 42}
    """,
        schema=simple_openapi,
        generation_modes=[GenerationMode.POSITIVE],
    )
    result = testdir.runpytest()
    result.assert_outcomes(passed=1)


def test_hook_error_not_converted_to_schema_error(testdir, simple_openapi):
    testdir.make_test(
        """
@schema.hook
def before_init_operation(context, operation):
    raise AttributeError("test hook error")

@schema.parametrize()
def test_(case):
    pass
""",
        schema=simple_openapi,
    )
    result = testdir.runpytest("-v")
    result.assert_outcomes(errors=1)
    # A hook error, not a schema error.
    result.stdout.re_match_lines([r".*Error in.*before_init_operation.*hook.*AttributeError.*test hook error.*"])


def test_test_scoped_case_hooks_apply_in_coverage_phase(testdir):
    testdir.make_test(
        """
schema.config.phases.examples.enabled = False
schema.config.phases.fuzzing.enabled = False

filtered = []

def local_filter_case(context, case):
    filtered.append(case)
    return True

def local_map_case(context, case):
    case.headers = {**(case.headers or {}), "X-Mapped": "yes"}
    return case

@schema.hooks.apply(local_map_case, name="map_case")
@schema.hooks.apply(local_filter_case, name="filter_case")
@schema.parametrize()
def test_api(case):
    assert case.meta.phase.name.value == "coverage"
    assert case.headers["X-Mapped"] == "yes"
    assert filtered
""",
        paths={
            "/users": {
                "get": {
                    "parameters": [{"name": "id", "in": "query", "required": True, "schema": {"type": "integer"}}],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
    )
    result = testdir.runpytest()
    result.assert_outcomes(passed=1)


def test_schema_filter_case_sees_global_map_case_in_coverage_phase(testdir):
    testdir.make_test(
        """
schema.config.phases.examples.enabled = False
schema.config.phases.fuzzing.enabled = False

seen = []

@schemathesis.hook
def map_case(context, case):
    case.headers = {**(case.headers or {}), "X-Mapped": "yes"}
    return case

@schema.hook
def filter_case(context, case):
    return (case.headers or {}).get("X-Mapped") == "yes"

@schema.parametrize()
def test_api(case):
    seen.append(case.meta.phase.name.value)

def test_coverage_cases_generated():
    assert "coverage" in seen
""",
        paths={
            "/users": {
                "get": {
                    "parameters": [{"name": "id", "in": "query", "required": True, "schema": {"type": "integer"}}],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
    )
    result = testdir.runpytest("-p", "no:randomly")
    result.assert_outcomes(passed=2)


def test_test_scoped_after_validate_hook(testdir, ctx):
    api = ctx.openapi.apps.success()
    testdir.make_test(
        f"""
schema.config.update(base_url="{api.base_url}/api")

validated = []

def local_after_validate(context, case, response, results):
    validated.extend((result.name, result.status.name) for result in results)

@schema.hooks.apply(local_after_validate, name="after_validate")
@schema.include(path_regex="success").parametrize()
@settings(max_examples=1, phases=[Phase.generate])
def test_api(case):
    validated.clear()
    case.call_and_validate(checks=[schemathesis.checks.not_a_server_error])
    assert validated == [("not_a_server_error", "SUCCESS")]
""",
        paths={"/success": {"get": {"responses": {"200": {"description": "OK"}}}}},
        schema_name="simple_openapi.yaml",
    )
    result = testdir.runpytest()
    result.assert_outcomes(passed=1)


LIFECYCLE_HOOKS = """
@schemathesis.hook
def setup(context, schema):
    note("\\nEVENT setup")

@schemathesis.hook
def teardown(context, schema):
    note("\\nEVENT teardown")
"""
SINGLE_OPERATION = {"/users": {"get": {"responses": {"200": {"description": "OK"}}}}}


def _events(result):
    lines = [line.removeprefix("EVENT ") for line in result.outlines if line.startswith("EVENT ")]
    return [event for event, _ in groupby(lines)]


def test_setup_and_teardown_hooks_wrap_parametrized_tests(testdir):
    testdir.make_test(
        LIFECYCLE_HOOKS
        + """
@pytest.fixture(scope="session")
def resource():
    note("\\nEVENT resource up")
    yield
    note("\\nEVENT resource down")

@schema.parametrize()
@settings(max_examples=1)
def test_a(case, resource):
    note("\\nEVENT test")

@schema.parametrize()
@settings(max_examples=1)
def test_b(case):
    note("\\nEVENT test")
"""
    )
    assert _events(testdir.runpytest("-s")) == ["resource up", "setup", "test", "teardown", "resource down"]


@pytest.mark.parametrize(
    ("scope", "expected"),
    [
        ("function", ["setup", "test", "teardown", "setup", "test", "teardown"]),
        ("session", ["setup", "test", "teardown"]),
    ],
)
def test_setup_and_teardown_hooks_follow_schema_fixture_scope(testdir, scope, expected):
    testdir.make_test(
        LIFECYCLE_HOOKS
        + f"""
@pytest.fixture(scope="{scope}")
def api_schema():
    return schema

lazy_schema = schemathesis.pytest.from_fixture("api_schema")

@lazy_schema.parametrize()
@settings(max_examples=1)
def test_a(case):
    note("\\nEVENT test")

@lazy_schema.parametrize()
@settings(max_examples=1)
def test_b(case):
    note("\\nEVENT test")
"""
    )
    assert _events(testdir.runpytest("-s")) == expected


def test_setup_and_teardown_hooks_wrap_state_machine(ctx, testdir):
    api = ctx.openapi.apps.users_crud()
    testdir.make_test(
        LIFECYCLE_HOOKS
        + f"""
@schemathesis.hook
def after_call(context, case, response):
    note("\\nEVENT call")

schema = schemathesis.openapi.from_url("{api.schema_url}")
TestCase = schema.as_state_machine().TestCase
TestCase.settings = settings(max_examples=1, stateful_step_count=2, deadline=None, derandomize=True)
"""
    )
    assert _events(testdir.runpytest("-s")) == ["setup", "call", "teardown"]


def test_failing_setup_hook_errors_tests_and_still_tears_down(ctx, testdir):
    testdir.make_test(
        """
@schemathesis.hook
def setup(context, schema):
    raise ValueError("setup failed")

@schemathesis.hook
def teardown(context, schema):
    note("\\nEVENT teardown")

@schema.parametrize()
def test_a(case):
    note("\\nEVENT test")
""",
        schema=ctx.openapi.build_schema(SINGLE_OPERATION),
    )
    result = testdir.runpytest("-s")
    result.assert_outcomes(errors=1)
    result.stdout.fnmatch_lines(["*Error in `setup` hook: ValueError: setup failed"])
    assert _events(result) == ["teardown"]


def test_teardown_hooks_all_run_and_report_failures(ctx, testdir):
    testdir.make_test(
        """
@schemathesis.hook("teardown")
def first(context, schema):
    note("\\nEVENT first")

@schemathesis.hook("teardown")
def second(context, schema):
    raise ValueError("teardown failed")

@schema.parametrize()
@settings(max_examples=1)
def test_a(case):
    pass
""",
        schema=ctx.openapi.build_schema(SINGLE_OPERATION),
    )
    result = testdir.runpytest("-s")
    result.assert_outcomes(passed=1, errors=1)
    result.stdout.fnmatch_lines(["*Error in `teardown` hook: ValueError: teardown failed"])
    assert _events(result) == ["first"]


def test_each_xdist_worker_tears_down_what_it_set_up(testdir):
    calls = testdir.mkdir("lifecycle_calls")
    testdir.make_test(
        f"""
import os
from pathlib import Path

@schemathesis.hook
def setup(context, schema):
    Path({str(calls)!r}, f"setup-{{os.getpid()}}").touch()

@schemathesis.hook
def teardown(context, schema):
    Path({str(calls)!r}, f"teardown-{{os.getpid()}}").touch()

@schema.parametrize()
@settings(max_examples=1)
def test_a(case):
    pass
"""
    )
    testdir.runpytest("-n", "2").assert_outcomes(passed=1)
    names = [path.name.split("-") for path in Path(calls).iterdir()]
    workers = {pid for kind, pid in names if kind == "setup"}
    assert workers and workers == {pid for kind, pid in names if kind == "teardown"}


def test_extension_spans_the_pytest_session(ctx, testdir):
    api = ctx.openapi.apps.success()
    testdir.makefile(".toml", schemathesis='[extensions.recorder]\nlabel = "staging"')
    testdir.make_test(
        f"""
@schemathesis.extension("recorder")
class Recorder:
    def __init__(self, settings):
        self.label = settings["label"]
        self.seen = []

    def setup(self, context, schema):
        self.seen.append("setup")

    def after_call(self, context, case, response):
        if self.seen[-1] != "call":
            self.seen.append("call")

    def teardown(self, context, schema):
        note(f"\\nEVENT {{self.label}}: {{', '.join(self.seen)}}")

schema = schemathesis.openapi.from_url("{api.schema_url}")

@schema.parametrize()
@settings(max_examples=1)
def test_a(case):
    case.call()

@schema.parametrize()
@settings(max_examples=1)
def test_b(case):
    case.call()
"""
    )
    assert _events(testdir.runpytest("-s")) == ["staging: setup, call"]


def test_extension_methods_fire_for_schemas_loaded_after_registration(testdir):
    testdir.make_test(
        """
@schemathesis.extension("loads")
class Loads:
    def __init__(self, settings):
        pass

    def after_load_schema(self, context, schema):
        note("\\nEVENT loaded")

loaded_later = schemathesis.openapi.from_dict(raw_schema)

@loaded_later.parametrize()
@settings(max_examples=1)
def test_a(case):
    pass
"""
    )
    assert _events(testdir.runpytest("-s")) == ["loaded"]


def test_extension_is_unbound_when_the_pytest_session_ends(testdir, simple_openapi):
    calls = testdir.mkdir("load_calls")
    testdir.make_test(
        f"""
import uuid
from pathlib import Path

@schemathesis.extension("loads")
class Loads:
    def __init__(self, settings):
        pass

    def after_load_schema(self, context, schema):
        Path({str(calls)!r}, uuid.uuid4().hex).touch()

loaded_later = schemathesis.openapi.from_dict(raw_schema)
"""
    )
    testdir.runpytest()
    schemathesis.openapi.from_dict(simple_openapi)
    assert len(list(Path(calls).iterdir())) == 1


@pytest.mark.parametrize(
    ("table", "source", "expected"),
    [
        (
            "[extensions.tiant]",
            "",
            "`[extensions.tiant]` matches no loaded extension; loaded: `taint`",
        ),
        (
            "[extensions.taint]\ndepth = 0",
            'if settings.get("depth", 1) < 1:\n            raise ValueError("`depth` must be positive")',
            "Extension `taint` rejected its settings: `depth` must be positive",
        ),
    ],
    ids=["unknown-table", "rejected-settings"],
)
def test_extension_settings_errors_stop_the_pytest_session(testdir, table, source, expected):
    testdir.makefile(".toml", schemathesis=table)
    testdir.make_test(
        f"""
@schemathesis.extension("taint")
class Taint:
    def __init__(self, settings):
        {source or "pass"}

@schema.parametrize()
def test_a(case):
    note("\\nEVENT test")
"""
    )
    result = testdir.runpytest("-s")
    assert expected in result.stdout.str() + result.stderr.str()
    assert (result.ret != 0, _events(result)) == (True, [])


def test_usage_error_raised_before_schemathesis_configures_stays_a_usage_error(testdir):
    testdir.makepyfile(
        early="""
import pytest

@pytest.hookimpl(tryfirst=True)
def pytest_configure(config):
    raise pytest.UsageError("bad option from another plugin")
""",
        test_a="def test_a():\n    pass\n",
    )
    testdir.syspathinsert()
    result = testdir.runpytest("-p", "early")
    assert (result.ret, "bad option from another plugin" in result.stderr.str()) == (pytest.ExitCode.USAGE_ERROR, True)


def test_two_failing_teardown_hooks_are_both_reported(ctx, testdir):
    testdir.make_test(
        """
@schemathesis.hook("teardown")
def first(context, schema):
    raise ValueError("first failed")

@schemathesis.hook("teardown")
def second(context, schema):
    raise ValueError("second failed")

@schema.parametrize()
@settings(max_examples=1)
def test_a(case):
    pass
""",
        schema=ctx.openapi.build_schema(SINGLE_OPERATION),
    )
    result = testdir.runpytest()
    result.assert_outcomes(passed=1, errors=1)
    result.stdout.fnmatch_lines(["*ValueError: second failed", "*ValueError: first failed"])


def test_unknown_schema_fixture_is_reported_by_pytest(testdir):
    testdir.make_test(
        """
lazy_schema = schemathesis.pytest.from_fixture("missing")

@lazy_schema.parametrize()
def test_a(case):
    pass
"""
    )
    result = testdir.runpytest()
    result.assert_outcomes(errors=1)
    result.stdout.fnmatch_lines(["*fixture 'missing' not found"])


def test_extension_registered_before_the_session_is_configured(testdir):
    testdir.makepyfile(
        early="""
import schemathesis

@schemathesis.extension("early")
class Early:
    def __init__(self, settings):
        pass

    def teardown(self, context, schema):
        print("\\nEVENT early teardown")
"""
    )
    testdir.make_test(
        """
@schema.parametrize()
@settings(max_examples=1)
def test_a(case):
    pass
"""
    )
    testdir.syspathinsert()
    assert _events(testdir.runpytest("-s", "-p", "early")) == ["early teardown"]


def test_invalid_config_file_is_not_reported_at_pytest_startup(testdir):
    testdir.makefile(".toml", schemathesis="unknown = 1")
    testdir.make_test(
        """
@schema.parametrize()
@settings(max_examples=1)
def test_a(case):
    pass
""",
        schema_name="simple_openapi.yaml",
    )
    result = testdir.runpytest()
    assert "INTERNALERROR" not in result.stdout.str() + result.stderr.str()


def test_values_suggested_by_a_hook_reach_later_requests(ctx, testdir, app_runner):
    app, _ = ctx.openapi.make_flask_app(
        {
            "/check": {
                "get": {
                    "parameters": [{"name": "code", "in": "query", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    seen = []

    @app.route("/check")
    def check():
        seen.append(request.args.get("code"))
        return jsonify({})

    testdir.make_test(
        f"""
@schemathesis.hook
def after_call(context, case, response):
    context.suggest(["SECRET42"], parameter="query.code")

schema = schemathesis.openapi.from_url("{app_runner.openapi_url(app)}")

@schema.parametrize()
@settings(max_examples=20)
def test_a(case):
    case.call()
"""
    )
    testdir.runpytest()
    assert "SECRET42" in seen
