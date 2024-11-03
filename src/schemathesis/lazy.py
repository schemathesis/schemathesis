from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass, field
from inspect import signature
from typing import TYPE_CHECKING, Any, Callable, Generator, Type

import pytest
from hypothesis.core import HypothesisHandle
from pytest_subtests import SubTests

from schemathesis.core import NOT_SET

from ._hypothesis._given import (
    GivenInput,
    get_given_args,
    get_given_kwargs,
    given_proxy,
    is_given_applied,
    merge_given_args,
    validate_given_args,
)
from ._override import CaseOverride, check_no_override_mark, get_override_from_mark, set_override_mark
from ._pytest.control_flow import fail_on_no_matches
from .auths import AuthStorage
from .exceptions import OperationSchemaError
from .filters import FilterSet, FilterValue, MatcherFunc, RegexValue, is_deprecated
from .hooks import HookDispatcher, HookScope
from .internal.result import Ok
from .schemas import BaseSchema

if TYPE_CHECKING:
    from _pytest.fixtures import FixtureRequest
    from pyrate_limiter import Limiter

    from schemathesis.core import NotSet

    from .generation import DataGenerationMethodInput, GenerationConfig
    from .internal.output import OutputConfig
    from .models import APIOperation


@dataclass
class LazySchema:
    fixture_name: str
    base_url: str | None | NotSet = NOT_SET
    app: Any = NOT_SET
    filter_set: FilterSet = field(default_factory=FilterSet)
    hooks: HookDispatcher = field(default_factory=lambda: HookDispatcher(scope=HookScope.SCHEMA))
    auth: AuthStorage = field(default_factory=AuthStorage)
    validate_schema: bool = True
    data_generation_methods: DataGenerationMethodInput | NotSet = NOT_SET
    generation_config: GenerationConfig | NotSet = NOT_SET
    output_config: OutputConfig | NotSet = NOT_SET
    rate_limiter: Limiter | None = None
    sanitize_output: bool = True

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
        return self.__class__(
            fixture_name=self.fixture_name,
            base_url=self.base_url,
            app=self.app,
            hooks=self.hooks,
            auth=self.auth,
            validate_schema=self.validate_schema,
            data_generation_methods=self.data_generation_methods,
            generation_config=self.generation_config,
            output_config=self.output_config,
            rate_limiter=self.rate_limiter,
            sanitize_output=self.sanitize_output,
            filter_set=filter_set,
        )

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
        return self.__class__(
            fixture_name=self.fixture_name,
            base_url=self.base_url,
            app=self.app,
            hooks=self.hooks,
            auth=self.auth,
            validate_schema=self.validate_schema,
            data_generation_methods=self.data_generation_methods,
            generation_config=self.generation_config,
            output_config=self.output_config,
            rate_limiter=self.rate_limiter,
            sanitize_output=self.sanitize_output,
            filter_set=filter_set,
        )

    def hook(self, hook: str | Callable) -> Callable:
        return self.hooks.register(hook)

    def parametrize(
        self,
        validate_schema: bool | NotSet = NOT_SET,
        data_generation_methods: DataGenerationMethodInput | NotSet = NOT_SET,
        generation_config: GenerationConfig | NotSet = NOT_SET,
        output_config: OutputConfig | NotSet = NOT_SET,
    ) -> Callable:
        if data_generation_methods is NOT_SET:
            data_generation_methods = self.data_generation_methods
        if generation_config is NOT_SET:
            generation_config = self.generation_config
        if output_config is NOT_SET:
            output_config = self.output_config

        def wrapper(test: Callable) -> Callable:
            if is_given_applied(test):
                # The user wrapped the test function with `@schema.given`
                # These args & kwargs go as extra to the underlying test generator
                given_args = get_given_args(test)
                given_kwargs = get_given_kwargs(test)
                test_function = validate_given_args(test, given_args, given_kwargs)
                if test_function is not None:
                    return test_function
                given_kwargs = merge_given_args(test, given_args, given_kwargs)
                del given_args
            else:
                given_kwargs = {}

            def wrapped_test(request: FixtureRequest) -> None:
                """The actual test, which is executed by pytest."""
                __tracebackhide__ = True
                if hasattr(wrapped_test, "_schemathesis_hooks"):
                    test._schemathesis_hooks = wrapped_test._schemathesis_hooks  # type: ignore
                schema = get_schema(
                    request=request,
                    name=self.fixture_name,
                    base_url=self.base_url,
                    hooks=self.hooks,
                    auth=self.auth if self.auth.providers is not None else NOT_SET,
                    test_function=test,
                    validate_schema=validate_schema,
                    data_generation_methods=data_generation_methods,
                    generation_config=generation_config,
                    output_config=output_config,
                    app=self.app,
                    rate_limiter=self.rate_limiter,
                    sanitize_output=self.sanitize_output,
                    filter_set=self.filter_set,
                )
                fixtures = get_fixtures(test, request, given_kwargs)
                # Changing the node id is required for better reporting - the method and path will appear there
                node_id = request.node._nodeid
                settings = getattr(wrapped_test, "_hypothesis_internal_use_settings", None)

                as_strategy_kwargs: Callable[[APIOperation], dict[str, Any]] | None = None

                override = get_override_from_mark(test)
                if override is not None:

                    def as_strategy_kwargs(_operation: APIOperation) -> dict[str, Any]:
                        nonlocal override

                        return {
                            location: entry for location, entry in override.for_operation(_operation).items() if entry
                        }

                tests = list(
                    schema.get_all_tests(
                        test, settings, as_strategy_kwargs=as_strategy_kwargs, _given_kwargs=given_kwargs
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
                        subtests.item._nodeid = f"{node_id}[{operation.method.upper()} {operation.full_path}]"
                        run_subtest(operation, fixtures, sub_test, subtests)
                    else:
                        _schema_error(subtests, result.err(), node_id)
                subtests.item._nodeid = node_id

            wrapped_test = pytest.mark.usefixtures(self.fixture_name)(wrapped_test)
            _copy_marks(test, wrapped_test)

            # Needed to prevent a failure when settings are applied to the test function
            wrapped_test.is_hypothesis_test = True  # type: ignore
            wrapped_test.hypothesis = HypothesisHandle(test, wrapped_test, given_kwargs)  # type: ignore

            return wrapped_test

        return wrapper

    def given(self, *args: GivenInput, **kwargs: GivenInput) -> Callable:
        return given_proxy(*args, **kwargs)

    def override(
        self,
        *,
        query: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        cookies: dict[str, str] | None = None,
        path_parameters: dict[str, str] | None = None,
    ) -> Callable[[Callable], Callable]:
        """Override Open API parameters with fixed values."""

        def _add_override(test: Callable) -> Callable:
            check_no_override_mark(test)
            override = CaseOverride(
                query=query or {}, headers=headers or {}, cookies=cookies or {}, path_parameters=path_parameters or {}
            )
            set_override_mark(test, override)
            return test

        return _add_override


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

    with subtests.test(verbose_name=operation.verbose_name):
        sub_test(**fixtures)


SEPARATOR = "\n===================="


def _schema_error(subtests: SubTests, error: OperationSchemaError, node_id: str) -> None:
    """Run a failing test, that will show the underlying problem."""
    sub_test = error.as_failing_test_function()
    # `full_path` is always available in this case
    kwargs = {"path": error.full_path}
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


def get_schema(
    *,
    request: FixtureRequest,
    name: str,
    base_url: str | None | NotSet = None,
    filter_set: FilterSet,
    app: Any = None,
    test_function: Callable,
    hooks: HookDispatcher,
    auth: AuthStorage | NotSet,
    validate_schema: bool | NotSet = NOT_SET,
    data_generation_methods: DataGenerationMethodInput | NotSet = NOT_SET,
    generation_config: GenerationConfig | NotSet = NOT_SET,
    output_config: OutputConfig | NotSet = NOT_SET,
    rate_limiter: Limiter | None,
    sanitize_output: bool,
) -> BaseSchema:
    """Loads a schema from the fixture."""
    schema = request.getfixturevalue(name)
    if not isinstance(schema, BaseSchema):
        raise ValueError(f"The given schema must be an instance of BaseSchema, got: {type(schema)}")

    return schema.clone(
        base_url=base_url,
        filter_set=filter_set,
        app=app,
        test_function=test_function,
        hooks=schema.hooks.merge(hooks),
        auth=auth,
        validate_schema=validate_schema,
        data_generation_methods=data_generation_methods,
        generation_config=generation_config,
        output_config=output_config,
        rate_limiter=rate_limiter,
        sanitize_output=sanitize_output,
    )


def get_fixtures(func: Callable, request: FixtureRequest, given_kwargs: dict[str, Any]) -> dict[str, Any]:
    """Load fixtures, needed for the test function."""
    sig = signature(func)
    return {
        name: request.getfixturevalue(name) for name in sig.parameters if name != "case" and name not in given_kwargs
    }
