import pytest
from hypothesis import HealthCheck, given, settings

import schemathesis
from schemathesis.hooks import HookContext, HookDispatcher, HookScope
from schemathesis.utils import PARAMETRIZE_MARKER


@pytest.fixture(autouse=True)
def reset_hooks():
    yield
    schemathesis.hooks.unregister_all()


@pytest.fixture(params=["direct", "named"])
def global_hook(request):
    if request.param == "direct":

        @schemathesis.hook
        def before_generate_query(context, strategy):
            return strategy.filter(lambda x: x["id"].isdigit())

    if request.param == "named":

        @schemathesis.hook("before_generate_query")
        def hook(context, strategy):
            return strategy.filter(lambda x: x["id"].isdigit())


@pytest.fixture
def schema(flask_app):
    return schemathesis.from_wsgi("/schema.yaml", flask_app)


@pytest.fixture()
def dispatcher():
    return HookDispatcher(scope=HookScope.SCHEMA)


@pytest.mark.hypothesis_nested
@pytest.mark.operations("custom_format")
@pytest.mark.usefixtures("global_hook")
def test_global_query_hook(schema, schema_url):
    strategy = schema["/custom_format"]["GET"].as_strategy()

    @given(case=strategy)
    @settings(max_examples=3, suppress_health_check=[HealthCheck.filter_too_much, HealthCheck.too_slow])
    def test(case):
        assert case.query["id"].isdigit()

    test()


@pytest.mark.hypothesis_nested
@pytest.mark.operations("payload")
def test_global_body_hook(schema):
    @schemathesis.hook
    def before_generate_body(context, strategy):
        return strategy.filter(lambda x: len(x["name"]) == 5)

    strategy = schema["/payload"]["POST"].as_strategy()

    @given(case=strategy)
    @settings(max_examples=3, suppress_health_check=[HealthCheck.filter_too_much, HealthCheck.too_slow])
    def test(case):
        assert len(case.body["name"]) == 5

    test()


@pytest.mark.hypothesis_nested
@pytest.mark.operations("create_user")
def test_case_hook(schema):
    dispatcher = HookDispatcher(scope=HookScope.TEST)

    @dispatcher.register
    def before_generate_case(context, strategy):
        def tune_case(case):
            case.body["extra"] = 42
            return case

        return strategy.map(tune_case)

    @schemathesis.hook
    def before_generate_case(context, strategy):  # noqa: F811
        def tune_case(case):
            case.body["first_name"] = case.body["last_name"]
            return case

        return strategy.map(tune_case)

    strategy = schema["/users/"]["POST"].as_strategy(hooks=dispatcher)

    @given(case=strategy)
    @settings(max_examples=10, suppress_health_check=[HealthCheck.filter_too_much])
    def test(case):
        assert case.body["first_name"] == case.body["last_name"]
        assert case.body["extra"] == 42

    test()


@pytest.mark.hypothesis_nested
@pytest.mark.operations("custom_format")
def test_schema_query_hook(schema, schema_url):
    @schema.hook
    def before_generate_query(context, strategy):
        return strategy.filter(lambda x: x["id"].isdigit())

    strategy = schema["/custom_format"]["GET"].as_strategy()

    @given(case=strategy)
    @settings(max_examples=3)
    def test(case):
        assert case.query["id"].isdigit()

    test()


@pytest.mark.hypothesis_nested
@pytest.mark.usefixtures("global_hook")
@pytest.mark.operations("custom_format")
def test_hooks_combination(schema, schema_url):
    @schema.hook("before_generate_query")
    def extra(context, st):
        assert context.operation == schema["/custom_format"]["GET"]
        return st.filter(lambda x: int(x["id"]) % 2 == 0)

    strategy = schema["/custom_format"]["GET"].as_strategy()

    @given(case=strategy)
    @settings(max_examples=3)
    def test(case):
        assert case.query["id"].isdigit()
        assert int(case.query["id"]) % 2 == 0

    test()


def test_per_test_hooks(testdir, simple_openapi):
    testdir.make_test(
        """
from hypothesis import strategies as st

def replacement(context, strategy):
    return st.just({"id": "foobar"})

@schema.hooks.apply(replacement, name="before_generate_query")
@schema.parametrize()
@settings(max_examples=1)
def test_a(case):
    assert case.query["id"] == "foobar"

@schema.parametrize()
@schema.hooks.apply(replacement, name="before_generate_query")
@settings(max_examples=1)
def test_b(case):
    assert case.query["id"] == "foobar"

def another_replacement(context, strategy):
    return st.just({"id": "foobaz"})

def before_generate_headers(context, strategy):
    return st.just({"value": "spam"})

@schema.parametrize()
@schema.hooks.apply(another_replacement, name="before_generate_query")  # Higher priority
@schema.hooks.apply(replacement, name="before_generate_query")
@schema.hooks.apply(before_generate_headers)
@settings(max_examples=1)
def test_c(case):
    assert case.query["id"] == "foobaz"
    assert case.headers["value"] == "spam"

@schema.parametrize()
@settings(max_examples=1)
def test_d(case):
    assert case.query["id"] != "foobar"
    """,
        schema=simple_openapi,
    )
    result = testdir.runpytest()
    result.assert_outcomes(passed=4)


def test_hooks_via_parametrize(testdir, simple_openapi):
    testdir.make_test(
        """
@schema.hook("before_generate_query")
def extra(context, st):
    return st.filter(lambda x: x["id"].isdigit() and int(x["id"]) % 2 == 0)

@schema.parametrize()
@settings(max_examples=1)
def test(case):
    assert case.operation.schema.hooks.get_all_by_name("before_generate_query")[0] is extra
    assert int(case.query["id"]) % 2 == 0
    """,
        schema=simple_openapi,
    )
    result = testdir.runpytest()
    result.assert_outcomes(passed=1)


def test_register_invalid_hook_name(dispatcher):
    with pytest.raises(TypeError, match="There is no hook with name 'hook'"):

        @dispatcher.register
        def hook():
            pass


def test_register_invalid_hook_spec(dispatcher):
    with pytest.raises(TypeError, match="Hook 'before_generate_query' takes 2 arguments but 3 is defined"):

        @dispatcher.register
        def before_generate_query(a, b, c):
            pass


def test_save_test_function(schema):
    assert schema.test_function is None

    @schema.parametrize()
    def test(case):
        pass

    assert getattr(test, PARAMETRIZE_MARKER).test_function is test


@pytest.mark.parametrize("apply_first", (True, False))
def test_local_dispatcher(schema, apply_first):
    assert schema.hooks.scope == HookScope.SCHEMA

    # When there are schema-level hooks
    @schema.hook("before_generate_query")
    def schema_hook(context, strategy):
        return strategy

    # And per-test hooks are applied
    def local_hook(context, strategy):
        return strategy

    # And order of decorators is any
    apply = schema.hooks.apply(local_hook, name="before_generate_cookies")
    parametrize = schema.parametrize()
    if apply_first:

        def wrap(x):
            return parametrize(apply(x))

    else:

        def wrap(x):
            return apply(parametrize(x))

    @wrap
    def test(case):
        pass

    # Then a hook dispatcher instance is attached to the test function
    assert isinstance(test._schemathesis_hooks, HookDispatcher)
    assert test._schemathesis_hooks.scope == HookScope.TEST
    # And this dispatcher contains only local hooks
    assert test._schemathesis_hooks.get_all_by_name("before_generate_cookies") == [local_hook]
    assert test._schemathesis_hooks.get_all_by_name("before_generate_query") == []
    # And the schema-level dispatcher still contains only schema-level hooks
    assert getattr(test, PARAMETRIZE_MARKER).hooks.get_all_by_name("before_generate_query") == [schema_hook]
    assert getattr(test, PARAMETRIZE_MARKER).hooks.get_all_by_name("before_generate_cookies") == []


@pytest.mark.hypothesis_nested
@pytest.mark.operations("custom_format")
def test_multiple_hooks_per_spec(schema):
    @schema.hook("before_generate_query")
    def first_hook(context, strategy):
        return strategy.filter(lambda x: x["id"].isdigit())

    @schema.hook("before_generate_query")
    def second_hook(context, strategy):
        return strategy.filter(lambda x: int(x["id"]) % 2 == 0)

    assert schema.hooks.get_all_by_name("before_generate_query") == [first_hook, second_hook]

    strategy = schema["/custom_format"]["GET"].as_strategy()

    @given(case=strategy)
    @settings(max_examples=3)
    def test(case):
        assert case.query["id"].isdigit()
        assert int(case.query["id"]) % 2 == 0

    test()


@pytest.mark.hypothesis_nested
@pytest.mark.operations("custom_format")
def test_before_process_path_hook(schema):
    @schema.hook
    def before_process_path(context, path, methods):
        methods["get"]["parameters"][0]["name"] = "foo"
        methods["get"]["parameters"][0]["enum"] = ["bar"]

    strategy = schema["/custom_format"]["GET"].as_strategy()

    @given(case=strategy)
    @settings(max_examples=3)
    def test(case):
        assert case.query == {"foo": "bar"}

    test()


def test_register_wrong_scope(schema):
    with pytest.raises(
        ValueError,
        match=r"Cannot register hook 'before_load_schema' on SCHEMA scope dispatcher. "
        r"Use a dispatcher with GLOBAL scope\(s\) instead",
    ):

        @schema.hook
        def before_load_schema(ctx, raw_schema):
            pass


def test_before_add_examples(testdir, simple_openapi):
    testdir.make_test(
        """
@schema.hook
def before_add_examples(context, examples):
    new = schemathesis.models.Case(
        operation=context.operation,
        query={"foo": "bar"}
    )
    examples.append(new)

@schema.parametrize()
@settings(phases=[Phase.explicit])
def test_a(case):
    assert case.query == {"foo": "bar"}


def another_hook(context, examples):
    new = schemathesis.models.Case(
        operation=context.operation,
        query={"spam": "baz"}
    )
    examples.append(new)

IDX = 0

@schema.parametrize()
@schema.hooks.apply(another_hook, name="before_add_examples")
@settings(phases=[Phase.explicit])
def test_b(case):
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


def test_deprecated_attribute():
    context = HookContext(1)
    with pytest.warns(Warning) as records:
        assert context.endpoint == context.operation == 1
    assert str(records[0].message) == (
        "Property `endpoint` is deprecated and will be removed in Schemathesis 4.0. Use `operation` instead."
    )


def test_before_init_operation(testdir, simple_openapi):
    testdir.make_test(
        """
@schema.hook
def before_init_operation(context, operation):
    operation.query[0].definition["schema"] = {"enum": [42]}

@schema.parametrize()
def test_a(case):
    assert case.query == {"id": 42}
    """,
        schema=simple_openapi,
    )
    result = testdir.runpytest()
    result.assert_outcomes(passed=1)


def test_after_load_schema(testdir, simple_openapi):
    testdir.make_test(
        """
LINK_STATUS = "200"
# Totally not working link, but it is for testing only
KEY = "userId"
EXPRESSION = "$response.body#/id"
PARAMETERS = {KEY: EXPRESSION}

@schemathesis.hook
def after_load_schema(
    context: schemathesis.hooks.HookContext,
    schema: schemathesis.schemas.BaseSchema,
) -> None:
    schema.add_link(
        source=schema["/query"]["get"],
        target=schema["/query"]["get"],
        status_code=LINK_STATUS,
        parameters=PARAMETERS,
    )

schema = schemathesis.from_dict(raw_schema)

@schema.parametrize()
def test_a(case):
    link = schema.get_links(case.operation)[LINK_STATUS][case.operation.verbose_name]
    assert link.operation == case.operation
    assert link.parameters == [(None, KEY, EXPRESSION)]
    """,
        schema=simple_openapi,
    )
    result = testdir.runpytest()
    result.assert_outcomes(passed=1)
