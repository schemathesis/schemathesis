from __future__ import annotations

from dataclasses import dataclass, field
from inspect import signature
from typing import Any, Callable, Generator

import pytest
from _pytest.fixtures import FixtureRequest
from hypothesis.core import HypothesisHandle
from hypothesis.errors import Flaky
from hypothesis.internal.escalation import format_exception, get_trimmed_traceback
from hypothesis.internal.reflection import impersonate
from pyrate_limiter import Limiter
from pytest_subtests import SubTests, nullcontext

from ._compat import MultipleFailures, get_interesting_origin
from ._override import CaseOverride, check_no_override_mark, get_override_from_mark, set_override_mark
from .auths import AuthStorage
from .code_samples import CodeSampleStyle
from .constants import FLAKY_FAILURE_MESSAGE, NOT_SET
from .exceptions import CheckFailed, OperationSchemaError, SkipTest, get_grouped_exception
from .filters import FilterSet, FilterValue, MatcherFunc, RegexValue, filter_set_from_components, is_deprecated
from .generation import DataGenerationMethodInput, GenerationConfig
from .hooks import HookDispatcher, HookScope
from .internal.deprecation import warn_filtration_arguments
from .internal.output import OutputConfig
from .internal.result import Ok
from .models import APIOperation
from .schemas import BaseSchema
from .types import Filter, GenericTest, NotSet
from .utils import (
    GivenInput,
    fail_on_no_matches,
    get_given_args,
    get_given_kwargs,
    given_proxy,
    is_given_applied,
    merge_given_args,
    validate_given_args,
)


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
    code_sample_style: CodeSampleStyle = CodeSampleStyle.default()
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
            code_sample_style=self.code_sample_style,
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
            code_sample_style=self.code_sample_style,
            rate_limiter=self.rate_limiter,
            sanitize_output=self.sanitize_output,
            filter_set=filter_set,
        )

    def hook(self, hook: str | Callable) -> Callable:
        return self.hooks.register(hook)

    def parametrize(
        self,
        method: Filter | None = NOT_SET,
        endpoint: Filter | None = NOT_SET,
        tag: Filter | None = NOT_SET,
        operation_id: Filter | None = NOT_SET,
        validate_schema: bool | NotSet = NOT_SET,
        skip_deprecated_operations: bool | NotSet = NOT_SET,
        data_generation_methods: DataGenerationMethodInput | NotSet = NOT_SET,
        generation_config: GenerationConfig | NotSet = NOT_SET,
        output_config: OutputConfig | NotSet = NOT_SET,
        code_sample_style: str | NotSet = NOT_SET,
    ) -> Callable:
        for name in ("method", "endpoint", "tag", "operation_id", "skip_deprecated_operations"):
            value = locals()[name]
            if value is not NOT_SET:
                warn_filtration_arguments(name)
        if data_generation_methods is NOT_SET:
            data_generation_methods = self.data_generation_methods
        if generation_config is NOT_SET:
            generation_config = self.generation_config
        if output_config is NOT_SET:
            output_config = self.output_config
        if isinstance(code_sample_style, str):
            _code_sample_style = CodeSampleStyle.from_str(code_sample_style)
        else:
            _code_sample_style = self.code_sample_style

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
                    method=method,
                    endpoint=endpoint,
                    tag=tag,
                    operation_id=operation_id,
                    hooks=self.hooks,
                    auth=self.auth if self.auth.providers is not None else NOT_SET,
                    test_function=test,
                    validate_schema=validate_schema,
                    skip_deprecated_operations=skip_deprecated_operations,
                    data_generation_methods=data_generation_methods,
                    generation_config=generation_config,
                    output_config=output_config,
                    code_sample_style=_code_sample_style,
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
                        test,
                        settings,
                        hooks=self.hooks,
                        as_strategy_kwargs=as_strategy_kwargs,
                        _given_kwargs=given_kwargs,
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
                        subtests.item._nodeid = _get_node_name(node_id, operation)
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
    ) -> Callable[[GenericTest], GenericTest]:
        """Override Open API parameters with fixed values."""

        def _add_override(test: GenericTest) -> GenericTest:
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


def _get_capturemanager(request: FixtureRequest) -> Generator:
    capturemanager = request.node.config.pluginmanager.get_plugin("capturemanager")
    if capturemanager is not None:
        return capturemanager.global_and_fixture_disabled
    return nullcontext


def _get_node_name(node_id: str, operation: APIOperation) -> str:
    """Make a test node name. For example: test_api[GET /users]."""
    return f"{node_id}[{operation.method.upper()} {operation.full_path}]"


def _get_partial_node_name(node_id: str, **kwargs: Any) -> str:
    """Make a test node name for failing tests caused by schema errors."""
    name = node_id
    if "method" in kwargs:
        name += f"[{kwargs['method']} {kwargs['path']}]"
    else:
        name += f"[{kwargs['path']}]"
    return name


def run_subtest(
    operation: APIOperation,
    fixtures: dict[str, Any],
    sub_test: Callable,
    subtests: SubTests,
) -> None:
    """Run the given subtest with pytest fixtures."""
    __tracebackhide__ = True

    # Deduplicate found checks in case of Hypothesis finding multiple of them
    failed_checks = {}
    exceptions = []
    inner_test = sub_test.hypothesis.inner_test  # type: ignore

    @impersonate(inner_test)  # type: ignore
    def collecting_wrapper(*args: Any, **kwargs: Any) -> None:
        __tracebackhide__ = True
        try:
            inner_test(*args, **kwargs)
        except CheckFailed as failed:
            failed_checks[failed.__class__] = failed
            raise failed
        except Exception as exception:
            # Deduplicate it later, as it is more costly than for `CheckFailed`
            exceptions.append(exception)
            raise

    def get_exception_class() -> type[CheckFailed]:
        return get_grouped_exception("Lazy", *failed_checks.values())

    sub_test.hypothesis.inner_test = collecting_wrapper  # type: ignore

    with subtests.test(verbose_name=operation.verbose_name):
        try:
            sub_test(**fixtures)
        except SkipTest as exc:
            pytest.skip(exc.args[0])
        except (MultipleFailures, CheckFailed) as exc:
            # Hypothesis doesn't report the underlying failures in these circumstances, hence we display them manually
            exc_class = get_exception_class()
            failures = "".join(f"{SEPARATOR} {failure.args[0]}" for failure in failed_checks.values())
            unique_exceptions = {get_interesting_origin(exception): exception for exception in exceptions}
            total_problems = len(failed_checks) + len(unique_exceptions)
            if total_problems == 1:
                raise
            message = f"Schemathesis found {total_problems} distinct sets of failures.{failures}"
            for exception in unique_exceptions.values():
                # Non-check exceptions
                message += f"{SEPARATOR}\n\n"
                tb = get_trimmed_traceback(exception)
                message += format_exception(exception, tb)
            raise exc_class(message, causes=tuple(failed_checks.values())).with_traceback(exc.__traceback__) from None
        except Flaky as exc:
            exc_class = get_exception_class()
            failure = next(iter(failed_checks.values()))
            message = f"{FLAKY_FAILURE_MESSAGE}{failure}"
            # The outer frame is the one for user's test function, take it as the root one
            traceback = exc.__traceback__.tb_next
            # The next one comes from Hypothesis internals - remove it
            traceback.tb_next = None
            raise exc_class(message, causes=tuple(failed_checks.values())).with_traceback(traceback) from None


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


def get_schema(
    *,
    request: FixtureRequest,
    name: str,
    base_url: str | None | NotSet = None,
    method: Filter | None = None,
    endpoint: Filter | None = None,
    tag: Filter | None = None,
    operation_id: Filter | None = None,
    filter_set: FilterSet,
    app: Any = None,
    test_function: GenericTest,
    hooks: HookDispatcher,
    auth: AuthStorage | NotSet,
    validate_schema: bool | NotSet = NOT_SET,
    skip_deprecated_operations: bool | NotSet = NOT_SET,
    data_generation_methods: DataGenerationMethodInput | NotSet = NOT_SET,
    generation_config: GenerationConfig | NotSet = NOT_SET,
    output_config: OutputConfig | NotSet = NOT_SET,
    code_sample_style: CodeSampleStyle,
    rate_limiter: Limiter | None,
    sanitize_output: bool,
) -> BaseSchema:
    """Loads a schema from the fixture."""
    schema = request.getfixturevalue(name)
    if not isinstance(schema, BaseSchema):
        raise ValueError(f"The given schema must be an instance of BaseSchema, got: {type(schema)}")

    filter_set = filter_set_from_components(
        include=True,
        method=method,
        endpoint=endpoint,
        tag=tag,
        operation_id=operation_id,
        skip_deprecated_operations=skip_deprecated_operations,
        parent=schema.filter_set.merge(filter_set),
    )
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
        code_sample_style=code_sample_style,
        rate_limiter=rate_limiter,
        sanitize_output=sanitize_output,
    )


def get_fixtures(func: Callable, request: FixtureRequest, given_kwargs: dict[str, Any]) -> dict[str, Any]:
    """Load fixtures, needed for the test function."""
    sig = signature(func)
    return {
        name: request.getfixturevalue(name) for name in sig.parameters if name != "case" and name not in given_kwargs
    }
