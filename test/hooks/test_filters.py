import pytest
from hypothesis import strategies as st

import schemathesis
from schemathesis.generation import GenerationMode
from schemathesis.generation.value import GeneratedValue
from schemathesis.hooks import GLOBAL_HOOK_DISPATCHER, HookContext
from test.utils import run_given


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


@pytest.mark.parametrize(
    "dispatcher_factory",
    [lambda r: r.getfixturevalue("openapi_30"), lambda _: schemathesis],
    ids=["schema", "global"],
)
@pytest.mark.parametrize("register", [register_default, register_named])
def test_invalid_hook(request, dispatcher_factory, register):
    dispatcher = dispatcher_factory(request)

    with pytest.raises(ValueError) as exc_info:
        register(dispatcher)

    assert str(exc_info.value) == "Filters are not applicable to this hook: `before_process_path`"

    # Invalid hooks should not mutate global state
    with pytest.raises(ValueError) as exc_info:
        register(dispatcher)


@pytest.mark.parametrize("is_include", [True, False])
def test_simple_filter(ctx, is_include):
    api = ctx.openapi.apps.payload()
    schema = schemathesis.openapi.from_url(api.schema_url)

    if is_include:

        @schema.hook.apply_to(name="POST /api/payload")
        def map_body(context, body):
            return 42

        @schema.hook.apply_to(name="POST /api/payload")
        def filter_body(context, body):
            return True
    else:

        @schema.hook.skip_for(name="POST /api/payload")
        def map_body(context, body):
            return 42

        @schema.hook.skip_for(name="POST /api/payload")
        def filter_body(context, body):
            return True

        @schema.hook.skip_for(name="POST /api/payload")
        def flatmap_body(context, body):
            return True

        @schema.hook.skip_for(name="POST /api/payload")
        def before_generate_body(context, body):
            return True

    @run_given(case=schema["/api/payload"]["POST"].as_strategy(), max_examples=10)
    def test(case):
        if is_include:
            assert case.body == 42
        else:
            assert case.body != 42


def test_map_case_filter(ctx, cli, snapshot_cli):
    api = ctx.openapi.apps.success()
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
        assert cli.main("run", api.schema_url, "--phases=fuzzing", "--max-examples=1", hooks=module) == snapshot_cli


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


@pytest.mark.parametrize("hook", [multiple_skip_for, multiple_apply_to])
def test_filter_combo(ctx, hook):
    api = ctx.openapi.apps.payload()
    schema = schemathesis.openapi.from_url(api.schema_url)
    hook(schema)

    @run_given(case=schema["/api/payload"]["POST"].as_strategy(), max_examples=10)
    def test(case):
        assert case.body == 42


def test_filter_body_works_in_negative_mode(ctx):
    schema = ctx.openapi.load_schema(
        {
            "/test": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": {"type": "object"}}},
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )

    @schema.hook("before_generate_body")
    def inject(context, strategy):
        # Mix GeneratedValue-wrapped bytes (as syntax-level fuzzing does) with normal values
        return st.one_of(
            st.just(GeneratedValue(value=b"\xff", meta=None)),
            st.just(GeneratedValue(value={"key": "value"}, meta=None)),
        )

    @schema.hook("filter_body")
    def reject_bytes(context, body):
        return not isinstance(body, bytes)

    @run_given(case=schema["/test"]["POST"].as_strategy(generation_mode=GenerationMode.NEGATIVE), max_examples=5)
    def inner(case):
        assert not isinstance(case.body, bytes)


def _build_operation(ctx, operation_id, path):
    op = {"operationId": operation_id, "responses": {"200": {"description": "OK"}}}
    schema = ctx.openapi.load_schema({path: {"post": op}})
    return schema[path]["POST"]


# Each hook registered with a different apply_to filter must receive its own filter_set
def test_multiple_apply_to_have_distinct_filters(ctx):
    with ctx.restore_hooks():

        @schemathesis.hook.apply_to(operation_id="operationA")
        def before_call(context, case, **kwargs):
            pass

        hook_a = before_call

        @schemathesis.hook.apply_to(operation_id="operationB")
        def before_call(context, case, **kwargs):  # noqa: F811
            pass

        hook_b = before_call

        assert hook_a.filter_set is not hook_b.filter_set

        ctx_a = HookContext(operation=_build_operation(ctx, "operationA", "/a"))
        ctx_b = HookContext(operation=_build_operation(ctx, "operationB", "/b"))
        ctx_other = HookContext(operation=_build_operation(ctx, "operationC", "/c"))

        assert list(GLOBAL_HOOK_DISPATCHER.applicable("before_call", ctx_a)) == [hook_a]
        assert list(GLOBAL_HOOK_DISPATCHER.applicable("before_call", ctx_b)) == [hook_b]
        assert list(GLOBAL_HOOK_DISPATCHER.applicable("before_call", ctx_other)) == []


# Mixing apply_to and skip_for across multiple hooks must keep filters independent
def test_multiple_apply_to_with_skip_for(ctx):
    with ctx.restore_hooks():

        @schemathesis.hook.apply_to(operation_id="opInclude")
        def before_call(context, case, **kwargs):
            pass

        hook_include = before_call

        @schemathesis.hook.skip_for(operation_id="opExclude")
        def before_call(context, case, **kwargs):  # noqa: F811
            pass

        hook_exclude = before_call

        ctx_include = HookContext(operation=_build_operation(ctx, "opInclude", "/include"))
        ctx_exclude = HookContext(operation=_build_operation(ctx, "opExclude", "/exclude"))
        ctx_other = HookContext(operation=_build_operation(ctx, "opOther", "/other"))

        assert list(GLOBAL_HOOK_DISPATCHER.applicable("before_call", ctx_include)) == [hook_include, hook_exclude]
        assert list(GLOBAL_HOOK_DISPATCHER.applicable("before_call", ctx_exclude)) == []
        assert list(GLOBAL_HOOK_DISPATCHER.applicable("before_call", ctx_other)) == [hook_exclude]
