from types import SimpleNamespace

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

import schemathesis
from schemathesis.generation import GenerationMode
from schemathesis.hooks import HookContext, _should_skip_hook
from schemathesis.specs.openapi.negative import GeneratedValue


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


def test_filter_body_works_in_negative_mode(ctx):
    schema = schemathesis.openapi.from_dict(
        ctx.openapi.build_schema(
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

    @given(case=schema["/test"]["POST"].as_strategy(generation_mode=GenerationMode.NEGATIVE))
    @settings(max_examples=5)
    def inner(case):
        assert not isinstance(case.body, bytes)

    inner()


def _make_hook_context(operation_id):
    """Build a minimal HookContext whose operation carries the given operationId."""
    operation = SimpleNamespace(
        definition=SimpleNamespace(raw={"operationId": operation_id}),
        label=operation_id,
        method="POST",
        path=f"/fake/{operation_id}",
        tags=[],
    )
    return HookContext(operation=operation)


def test_multiple_apply_to_have_distinct_filters(ctx):
    """Hooks registered with different apply_to filters must each receive their own filter_set.

    Regression: a bug in to_filterable_hook caused the outer ``filter_set``
    variable to go stale after the first registration, so every subsequent hook
    was silently assigned the *first* hook's filter_set.
    """
    with ctx.restore_hooks():

        @schemathesis.hook.apply_to(operation_id="operationA")
        def before_call(context, case, **kwargs):
            """Hook for operation A."""

        hook_a = before_call

        @schemathesis.hook.apply_to(operation_id="operationB")
        def before_call(context, case, **kwargs):  # noqa: F811
            """Hook for operation B."""

        hook_b = before_call

        # Each hook must carry its own, distinct filter_set
        assert hook_a.filter_set is not hook_b.filter_set

        ctx_a = _make_hook_context("operationA")
        ctx_b = _make_hook_context("operationB")
        ctx_other = _make_hook_context("operationC")

        # hook_a should run for operationA only
        assert not _should_skip_hook(hook_a, ctx_a)
        assert _should_skip_hook(hook_a, ctx_b)
        assert _should_skip_hook(hook_a, ctx_other)

        # hook_b should run for operationB only
        assert _should_skip_hook(hook_b, ctx_a)
        assert not _should_skip_hook(hook_b, ctx_b)
        assert _should_skip_hook(hook_b, ctx_other)


def test_multiple_apply_to_with_skip_for(ctx):
    """Mixing apply_to and skip_for across multiple hooks must keep filters independent."""
    with ctx.restore_hooks():

        @schemathesis.hook.apply_to(operation_id="opInclude")
        def before_call(context, case, **kwargs):
            """Include-filtered hook."""

        hook_include = before_call

        @schemathesis.hook.skip_for(operation_id="opExclude")
        def before_call(context, case, **kwargs):  # noqa: F811
            """Exclude-filtered hook."""

        hook_exclude = before_call

        ctx_include = _make_hook_context("opInclude")
        ctx_exclude = _make_hook_context("opExclude")
        ctx_other = _make_hook_context("opOther")

        # hook_include: runs only for opInclude
        assert not _should_skip_hook(hook_include, ctx_include)
        assert _should_skip_hook(hook_include, ctx_exclude)
        assert _should_skip_hook(hook_include, ctx_other)

        # hook_exclude: runs for everything EXCEPT opExclude
        assert not _should_skip_hook(hook_exclude, ctx_include)
        assert _should_skip_hook(hook_exclude, ctx_exclude)
        assert not _should_skip_hook(hook_exclude, ctx_other)
