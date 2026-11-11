from __future__ import annotations

from collections.abc import Callable, Generator
from dataclasses import dataclass
from inspect import signature
from typing import TYPE_CHECKING, Any
from unittest import SkipTest

import pytest
from hypothesis.core import HypothesisHandle

from schemathesis.auths import AuthStorage
from schemathesis.core.errors import InvalidSchema
from schemathesis.core.failures import FailureGroup, as_reported_failure
from schemathesis.core.result import Err, Ok, Result
from schemathesis.filters import FilterSet, FilterValue, MatcherFunc, RegexValue
from schemathesis.generation.feedback import FeedbackSources
from schemathesis.generation.hypothesis.given import (
    GIVEN_REFRESH_ATTR,
    GIVEN_TARGET_ATTR,
    GivenInput,
    given_proxy,
    resolve_given,
)
from schemathesis.pytest._keys import track_schema
from schemathesis.pytest._subtests import make_subtests
from schemathesis.pytest._test_builder import build_operation_test, schema_error_suffix
from schemathesis.pytest.control_flow import fail_on_no_matches
from schemathesis.pytest.warnings import emit_schema_warnings
from schemathesis.python._constants.orchestrator import make_constants_value_source
from schemathesis.schemas import BaseSchema

if TYPE_CHECKING:
    import hypothesis
    from _pytest.fixtures import FixtureRequest

    from schemathesis.schemas import APIOperation


def get_all_tests(
    *,
    schema: BaseSchema,
    test_func: Callable,
    settings: hypothesis.settings | None,
    given_kwargs: dict[str, GivenInput] | None,
) -> Generator[Result[tuple[APIOperation, Callable], InvalidSchema], None, None]:
    """Generate all operations and Hypothesis tests for them."""
    feedback = FeedbackSources(constants_value_source=make_constants_value_source(schema))
    for result in schema.get_all_operations():
        if isinstance(result, Ok):
            operation = result.ok()
            test = build_operation_test(
                config=schema.config,
                operation=operation,
                test_func=test_func,
                explicit_settings=settings,
                given_kwargs=given_kwargs or {},
                feedback=feedback,
            )
            yield Ok((operation, test))
            for schema_error in operation.schema_errors:
                yield Err(schema_error)
        else:
            yield result


@dataclass(slots=True)
class LazySchema:
    fixture_name: str
    filter_set: FilterSet
    auth: AuthStorage

    def __init__(
        self,
        fixture_name: str,
        filter_set: FilterSet | None = None,
        auth: AuthStorage | None = None,
    ) -> None:
        self.fixture_name = fixture_name
        self.filter_set = filter_set or FilterSet()
        self.auth = auth if auth is not None else AuthStorage()

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
        filter_set = self.filter_set.with_include(
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
        return self.__class__(fixture_name=self.fixture_name, filter_set=filter_set, auth=self.auth)

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
        filter_set = self.filter_set.with_exclude(
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
            deprecated=deprecated,
        )
        return self.__class__(fixture_name=self.fixture_name, filter_set=filter_set, auth=self.auth)

    def parametrize(self) -> Callable:
        def wrapper(test_func: Callable) -> Callable:
            given_kwargs: dict[str, GivenInput] = {}
            invalid_test: Callable | None = None

            def refresh_given_state() -> None:
                nonlocal invalid_test
                invalid_test, resolved = resolve_given(test_func)
                given_kwargs.clear()
                given_kwargs.update(resolved)

            refresh_given_state()

            def wrapped_test(*args: Any, request: FixtureRequest, **kwargs: Any) -> None:
                """The actual test, which is executed by pytest."""
                __tracebackhide__ = True

                # Load all checks eagerly, so they are accessible inside the test function
                from schemathesis.checks import load_all_checks

                load_all_checks()
                if invalid_test is not None:
                    invalid_test()
                    return  # pragma: no cover

                schema = get_schema(
                    request=request,
                    name=self.fixture_name,
                    test_function=test_func,
                    filter_set=self.filter_set,
                )
                if self.auth.is_defined:
                    schema.auth = AuthStorage(providers=list(self.auth.providers) + list(schema.auth.providers))
                emit_schema_warnings(schema)
                track_schema(request.session.config, schema)
                fixtures = _fixtures_for(test_func, request, given_kwargs)
                settings = getattr(wrapped_test, "_hypothesis_internal_use_settings", None)
                tests = list(
                    get_all_tests(
                        schema=schema,
                        test_func=test_func,
                        settings=settings,
                        given_kwargs=given_kwargs,
                    )
                )
                _run_operation_tests(request, schema, tests, fixtures)

            # The test body reads settings applied on top of the returned test, so the name must refer to that object
            wrapped_test = _as_pytest_test(
                test_func, wrapped_test, self.fixture_name, given_kwargs, refresh_given_state
            )
            return wrapped_test

        return wrapper

    def given(self, *args: GivenInput, **kwargs: GivenInput) -> Callable:
        return given_proxy(*args, **kwargs)


def _fixtures_for(test_func: Callable, request: FixtureRequest, given_kwargs: dict[str, GivenInput]) -> dict[str, Any]:
    __tracebackhide__ = True
    # Check if test function is a method and inject self from request.instance
    if "self" in signature(test_func).parameters and request.instance is not None:
        return {"self": request.instance, **get_fixtures(test_func, request, given_kwargs)}
    return get_fixtures(test_func, request, given_kwargs)


def _run_operation_tests(
    request: FixtureRequest,
    schema: BaseSchema,
    tests: list[Result[tuple[APIOperation, Callable], InvalidSchema]],
    fixtures: dict[str, Any],
) -> None:
    """Run each operation test as a subtest of the collected test."""
    __tracebackhide__ = True
    # Changing the node id is required for better reporting - the method and path will appear there
    node_id = request.node.nodeid
    if not tests:
        fail_on_no_matches(node_id)
    request.session.testscollected += len(tests)
    subtests = make_subtests(request)
    for result in tests:
        _run_operation_test(request, schema, result, fixtures, subtests, node_id)
    _set_node_id(request.node, node_id)


def _run_operation_test(
    request: FixtureRequest,
    schema: BaseSchema,
    result: Result[tuple[APIOperation, Callable], InvalidSchema],
    fixtures: dict[str, Any],
    subtests: pytest.Subtests,
    node_id: str,
) -> None:
    __tracebackhide__ = True
    from schemathesis.pytest.plugin import report_subtest

    if isinstance(result, Ok):
        operation, sub_test = result.ok()
        _set_node_id(request.node, f"{node_id}[{operation.label}]")
        with report_subtest(request.node, schema, operation.label, operation.tags):
            run_subtest(operation, fixtures, sub_test, subtests)
    else:
        error = result.err()
        with report_subtest(request.node, schema, error.label, None):
            _schema_error(subtests, error, node_id, request.node)


def _as_pytest_test(
    test_func: Callable,
    wrapped_test: Callable,
    fixture_name: str,
    given_kwargs: dict[str, GivenInput],
    refresh: Callable[[], None],
) -> Callable:
    """Make `wrapped_test` a pytest test that stands in for `test_func`."""
    wrapped_test = pytest.mark.usefixtures(fixture_name)(wrapped_test)
    _copy_marks(test_func, wrapped_test)

    # Ad-hoc attributes that prevent a failure when settings are applied to the test function
    wrapped_test.is_hypothesis_test = True  # type: ignore[attr-defined]
    wrapped_test.hypothesis = HypothesisHandle(test_func, wrapped_test, given_kwargs)  # type: ignore[attr-defined]

    # For methods, wrap with staticmethod to prevent pytest from passing self
    result = staticmethod(wrapped_test) if "self" in signature(test_func).parameters else wrapped_test

    setattr(result, GIVEN_TARGET_ATTR, test_func)
    setattr(result, GIVEN_REFRESH_ATTR, refresh)

    return result


def _copy_marks(source: Callable, target: Callable) -> None:
    marks = getattr(source, "pytestmark", [])
    # Pytest adds this attribute in `usefixtures`
    target.pytestmark.extend(marks)  # type: ignore[attr-defined]


def run_subtest(
    operation: APIOperation, fixtures: dict[str, Any], sub_test: Callable, subtests: pytest.Subtests
) -> None:
    """Run the given subtest with pytest fixtures."""
    __tracebackhide__ = True

    with subtests.test(label=operation.label):
        try:
            sub_test(**fixtures)
        except FailureGroup as exc:
            raise as_reported_failure(exc) from None
        except SkipTest as exc:
            raise pytest.skip.Exception(str(exc)).with_traceback(exc.__traceback__) from None


SEPARATOR = "\n===================="


def _set_node_id(node: pytest.Function, node_id: str) -> None:
    # pytest has no public API for renaming a node. Subtests are reported and counted under this id, so a failing
    # operation does not also fail the parent test.
    if hasattr(node, "_id"):
        # pytest >= 9.2 keeps a structured id
        node._id = node._id.parse(node_id)
    else:
        node._nodeid = node_id  # type: ignore[attr-defined]


def _schema_error(subtests: pytest.Subtests, error: InvalidSchema, node_id: str, node: pytest.Function) -> None:
    """Run a failing test, that will show the underlying problem."""
    sub_test = error.as_failing_test_function()
    _set_node_id(node, f"{node_id}{schema_error_suffix(error)}")
    __tracebackhide__ = True
    with subtests.test(label=error.label):
        sub_test()


def get_schema(*, request: FixtureRequest, name: str, filter_set: FilterSet, test_function: Callable) -> BaseSchema:
    """Loads a schema from the fixture."""
    schema = request.getfixturevalue(name)
    if not isinstance(schema, BaseSchema):
        raise ValueError(f"The given schema must be an instance of BaseSchema, got: {type(schema)}")

    # Merge config-based operation filters with user-provided filters
    # This ensures operations disabled in schemathesis.toml are respected
    merged_filter_set = schema.config.operations.filter_set_with(include=filter_set)

    return schema.clone(filter_set=merged_filter_set, test_function=test_function)


def get_fixtures(func: Callable, request: FixtureRequest, given_kwargs: dict[str, Any]) -> dict[str, Any]:
    """Load fixtures, needed for the test function."""
    sig = signature(func)
    return {
        name: request.getfixturevalue(name)
        for name in sig.parameters
        if name not in ("case", "self") and name not in given_kwargs
    }
