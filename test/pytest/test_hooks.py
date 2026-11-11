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
    # Should show hook error message, not schema error
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
