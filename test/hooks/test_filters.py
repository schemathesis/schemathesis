import pytest
from hypothesis import given, settings

import schemathesis


def register_default(dispatcher):
    @dispatcher.hook.apply_to(method="GET")
    def before_process_path(context, path, methods):
        pass


def register_named(dispatcher):
    exec("""
@dispatcher.hook("before_process_path").apply_to(method="GET")
def custom_name(context, path, methods):
    pass
    """)


@pytest.mark.parametrize("dispatcher_factory", [lambda r: r.getfixturevalue("openapi_30"), lambda _: schemathesis])
@pytest.mark.parametrize("register", [register_default, register_named])
def test_invalid_hook(request, dispatcher_factory, register):
    dispatcher = dispatcher_factory(request)

    with pytest.raises(ValueError) as exc_info:
        register(dispatcher)

    assert str(exc_info.value) == "Filters are not applicable to this hook: `before_process_path`"

    # Invalid hooks should not mutate global state
    with pytest.raises(ValueError) as exc_info:
        register(dispatcher)


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("payload")
@pytest.mark.parametrize("is_include", [True, False])
def test_simple_filter(schema_url, is_include):
    schema = schemathesis.openapi.from_url(schema_url)

    if is_include:

        @schema.hook.apply_to(name="POST /payload")
        def map_body(context, body):
            return 42

        @schema.hook.apply_to(name="POST /payload")
        def filter_body(context, body):
            return True
    else:

        @schema.hook.skip_for(name="POST /payload")
        def map_body(context, body):
            return 42

        @schema.hook.skip_for(name="POST /payload")
        def filter_body(context, body):
            return True

        @schema.hook.skip_for(name="POST /payload")
        def flatmap_body(context, body):
            return True

        @schema.hook.skip_for(name="POST /payload")
        def before_generate_body(context, body):
            return True

    @given(case=schema["/payload"]["POST"].as_strategy())
    @settings(max_examples=10)
    def test(case):
        if is_include:
            assert case.body == 42
        else:
            assert case.body != 42

    test()


@pytest.mark.operations("success")
def test_map_case_filter(ctx, cli, openapi3_schema_url, snapshot_cli):
    # All these hooks should not be called because of the applied filter
    with ctx.hook(
        r"""
@schemathesis.hook.apply_to(path_regex=r"/fake/path")
def map_case(ctx, case):
    1 / 0

@schemathesis.hook.apply_to(path_regex=r"/fake/path")
def filter_case(ctx, case):
    1 / 0

@schemathesis.hook.apply_to(path_regex=r"/fake/path")
def flatmap_case(ctx, case):
    1 / 0

@schemathesis.hook.apply_to(path_regex=r"/fake/path")
def before_generate_case(ctx, case):
    1 / 0


try:
    @schemathesis.hook("before_process_path").apply_to(method="GET")
    def custom_name(context, path, methods):
        pass
except:
    pass
"""
    ) as module:
        assert (
            cli.main("run", openapi3_schema_url, "--phases=fuzzing", "--max-examples=1", hooks=module) == snapshot_cli
        )


def multiple_skip_for(schema):
    exec("""
@schema.hook.skip_for(name="first").skip_for(name="second")
def map_body(ctx, body):
    return 42
    """)


def multiple_apply_to(schema):
    exec("""
@schema.hook.apply_to(method="POST").apply_to(path="/api/payload")
def map_body(ctx, body):
    return 42
    """)


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("payload")
@pytest.mark.parametrize("hook", [multiple_skip_for, multiple_apply_to])
def test_filter_combo(schema_url, hook):
    schema = schemathesis.openapi.from_url(schema_url)
    hook(schema)

    @given(case=schema["/payload"]["POST"].as_strategy())
    @settings(max_examples=10)
    def test(case):
        assert case.body == 42

    test()
