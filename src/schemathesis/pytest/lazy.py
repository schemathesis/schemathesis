from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass, field
from inspect import signature
from typing import TYPE_CHECKING, Any, Callable, Generator, Type

import pytest
from hypothesis.core import HypothesisHandle
from pytest_subtests import SubTests

from schemathesis.core.errors import InvalidSchema
from schemathesis.core.result import Ok, Result
from schemathesis.filters import FilterSet, FilterValue, MatcherFunc, RegexValue, is_deprecated
from schemathesis.generation import overrides
from schemathesis.generation.hypothesis.builder import HypothesisTestConfig, HypothesisTestMode, create_test
from schemathesis.generation.hypothesis.given import (
    GivenArgsMark,
    GivenInput,
    GivenKwargsMark,
    given_proxy,
    is_given_applied,
    merge_given_args,
    validate_given_args,
)
from schemathesis.pytest.control_flow import fail_on_no_matches
from schemathesis.schemas import BaseSchema

if TYPE_CHECKING:
    import hypothesis
    from _pytest.fixtures import FixtureRequest

    from schemathesis.schemas import APIOperation


def get_all_tests(
    *,
    schema: BaseSchema,
    test_func: Callable,
    modes: list[HypothesisTestMode],
    settings: hypothesis.settings | None = None,
    seed: int | None = None,
    as_strategy_kwargs: Callable[[APIOperation], dict[str, Any]] | None = None,
    given_kwargs: dict[str, GivenInput] | None = None,
) -> Generator[Result[tuple[APIOperation, Callable], InvalidSchema], None, None]:
    """Generate all operations and Hypothesis tests for them."""
    for result in schema.get_all_operations():
        if isinstance(result, Ok):
            operation = result.ok()
            if callable(as_strategy_kwargs):
                _as_strategy_kwargs = as_strategy_kwargs(operation)
            else:
                _as_strategy_kwargs = {}
            test = create_test(
                operation=operation,
                test_func=test_func,
                config=HypothesisTestConfig(
                    settings=settings,
                    modes=modes,
                    seed=seed,
                    project=schema.config,
                    as_strategy_kwargs=_as_strategy_kwargs,
                    given_kwargs=given_kwargs or {},
                ),
            )
            yield Ok((operation, test))
        else:
            yield result


@dataclass
class LazySchema:
    fixture_name: str
    filter_set: FilterSet = field(default_factory=FilterSet)

    def include(
        self,
        func: MatcherFunc | None = None,
        *,
        name: FilterValue | None = None,
        name_regex: str | None = None,
        method: FilterValue | None = None,
        method_regex: str | None = None,
        path: FilterValue | None = None,
        path_regex: str | None = None,
        tag: FilterValue | None = None,
        tag_regex: RegexValue | None = None,
        operation_id: FilterValue | None = None,
        operation_id_regex: RegexValue | None = None,
    ) -> LazySchema:
        """Include only operations that match the given filters."""
        filter_set = self.filter_set.clone()
        filter_set.include(
            func,
            name=name,
            name_regex=name_regex,
            method=method,
            method_regex=method_regex,
            path=path,
            path_regex=path_regex,
            tag=tag,
            tag_regex=tag_regex,
            operation_id=operation_id,
            operation_id_regex=operation_id_regex,
        )
        return self.__class__(fixture_name=self.fixture_name, filter_set=filter_set)

    def exclude(
        self,
        func: MatcherFunc | None = None,
        *,
        name: FilterValue | None = None,
        name_regex: str | None = None,
        method: FilterValue | None = None,
        method_regex: str | None = None,
        path: FilterValue | None = None,
        path_regex: str | None = None,
        tag: FilterValue | None = None,
        tag_regex: RegexValue | None = None,
        operation_id: FilterValue | None = None,
        operation_id_regex: RegexValue | None = None,
        deprecated: bool = False,
    ) -> LazySchema:
        """Exclude operations that match the given filters."""
        filter_set = self.filter_set.clone()
        if deprecated:
            if func is None:
                func = is_deprecated
            else:
                filter_set.exclude(is_deprecated)
        filter_set.exclude(
            func,
            name=name,
            name_regex=name_regex,
            method=method,
            method_regex=method_regex,
            path=path,
            path_regex=path_regex,
            tag=tag,
            tag_regex=tag_regex,
            operation_id=operation_id,
            operation_id_regex=operation_id_regex,
        )
        return self.__class__(fixture_name=self.fixture_name, filter_set=filter_set)

    def parametrize(self) -> Callable:
        def wrapper(test_func: Callable) -> Callable:
            if is_given_applied(test_func):
                # The user wrapped the test function with `@schema.given`
                # These args & kwargs go as extra to the underlying test generator
                given_args = GivenArgsMark.get(test_func)
                given_kwargs = GivenKwargsMark.get(test_func)
                assert given_args is not None
                assert given_kwargs is not None
                test_function = validate_given_args(test_func, given_args, given_kwargs)
                if test_function is not None:
                    return test_function
                given_kwargs = merge_given_args(test_func, given_args, given_kwargs)
                del given_args
            else:
                given_kwargs = {}

            def wrapped_test(request: FixtureRequest) -> None:
                """The actual test, which is executed by pytest."""
                __tracebackhide__ = True
                schema = get_schema(
                    request=request,
                    name=self.fixture_name,
                    test_function=test_func,
                    filter_set=self.filter_set,
                )
                fixtures = get_fixtures(test_func, request, given_kwargs)
                # Changing the node id is required for better reporting - the method and path will appear there
                node_id = request.node._nodeid
                settings = getattr(wrapped_test, "_hypothesis_internal_use_settings", None)

                def as_strategy_kwargs(_operation: APIOperation) -> dict[str, Any]:
                    override = overrides.for_operation(config=schema.config, operation=_operation)

                    return {location: entry for location, entry in override.items() if entry}

                tests = list(
                    get_all_tests(
                        schema=schema,
                        test_func=test_func,
                        settings=settings,
                        modes=list(HypothesisTestMode),
                        as_strategy_kwargs=as_strategy_kwargs,
                        given_kwargs=given_kwargs,
                    )
                )
                if not tests:
                    fail_on_no_matches(node_id)
                request.session.testscollected += len(tests)
                suspend_capture_ctx = _get_capturemanager(request)
                subtests = SubTests(request.node.ihook, suspend_capture_ctx, request)
                for result in tests:
                    if isinstance(result, Ok):
                        operation, sub_test = result.ok()
                        subtests.item._nodeid = f"{node_id}[{operation.method.upper()} {operation.path}]"
                        run_subtest(operation, fixtures, sub_test, subtests)
                    else:
                        _schema_error(subtests, result.err(), node_id)
                subtests.item._nodeid = node_id

            wrapped_test = pytest.mark.usefixtures(self.fixture_name)(wrapped_test)
            _copy_marks(test_func, wrapped_test)

            # Needed to prevent a failure when settings are applied to the test function
            wrapped_test.is_hypothesis_test = True  # type: ignore
            wrapped_test.hypothesis = HypothesisHandle(test_func, wrapped_test, given_kwargs)  # type: ignore

            return wrapped_test

        return wrapper

    def given(self, *args: GivenInput, **kwargs: GivenInput) -> Callable:
        return given_proxy(*args, **kwargs)


def _copy_marks(source: Callable, target: Callable) -> None:
    marks = getattr(source, "pytestmark", [])
    # Pytest adds this attribute in `usefixtures`
    target.pytestmark.extend(marks)  # type: ignore


def _get_capturemanager(request: FixtureRequest) -> Generator | Type[nullcontext]:
    capturemanager = request.node.config.pluginmanager.get_plugin("capturemanager")
    if capturemanager is not None:
        return capturemanager.global_and_fixture_disabled
    return nullcontext


def run_subtest(operation: APIOperation, fixtures: dict[str, Any], sub_test: Callable, subtests: SubTests) -> None:
    """Run the given subtest with pytest fixtures."""
    __tracebackhide__ = True

    with subtests.test(label=operation.label):
        sub_test(**fixtures)


SEPARATOR = "\n===================="


def _schema_error(subtests: SubTests, error: InvalidSchema, node_id: str) -> None:
    """Run a failing test, that will show the underlying problem."""
    sub_test = error.as_failing_test_function()
    kwargs = {"path": error.path}
    if error.method:
        kwargs["method"] = error.method.upper()
    subtests.item._nodeid = _get_partial_node_name(node_id, **kwargs)
    __tracebackhide__ = True
    with subtests.test(**kwargs):
        sub_test()


def _get_partial_node_name(node_id: str, **kwargs: Any) -> str:
    """Make a test node name for failing tests caused by schema errors."""
    name = node_id
    if "method" in kwargs:
        name += f"[{kwargs['method']} {kwargs['path']}]"
    else:
        name += f"[{kwargs['path']}]"
    return name


def get_schema(*, request: FixtureRequest, name: str, filter_set: FilterSet, test_function: Callable) -> BaseSchema:
    """Loads a schema from the fixture."""
    schema = request.getfixturevalue(name)
    if not isinstance(schema, BaseSchema):
        raise ValueError(f"The given schema must be an instance of BaseSchema, got: {type(schema)}")

    return schema.clone(filter_set=filter_set, test_function=test_function)


def get_fixtures(func: Callable, request: FixtureRequest, given_kwargs: dict[str, Any]) -> dict[str, Any]:
    """Load fixtures, needed for the test function."""
    sig = signature(func)
    return {
        name: request.getfixturevalue(name) for name in sig.parameters if name != "case" and name not in given_kwargs
    }
