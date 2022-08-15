from inspect import signature
from typing import Any, Callable, Dict, Generator, Optional, Type, Union

import attr
import pytest
from _pytest.fixtures import FixtureRequest
from hypothesis.core import HypothesisHandle
from hypothesis.errors import Flaky
from hypothesis.internal.escalation import format_exception, get_interesting_origin, get_trimmed_traceback
from hypothesis.internal.reflection import impersonate
from pytest_subtests import SubTests, nullcontext

from ._compat import MultipleFailures
from .auth import AuthStorage
from .constants import FLAKY_FAILURE_MESSAGE, CodeSampleStyle
from .exceptions import CheckFailed, InvalidSchema, SkipTest, get_grouped_exception
from .hooks import HookDispatcher, HookScope
from .models import APIOperation
from .schemas import BaseSchema
from .types import DataGenerationMethodInput, Filter, GenericTest, NotSet
from .utils import (
    NOT_SET,
    GivenInput,
    Ok,
    fail_on_no_matches,
    get_given_args,
    get_given_kwargs,
    given_proxy,
    is_given_applied,
    merge_given_args,
    validate_given_args,
)


@attr.s(slots=True)  # pragma: no mutate
class LazySchema:
    fixture_name: str = attr.ib()  # pragma: no mutate
    base_url: Union[Optional[str], NotSet] = attr.ib(default=NOT_SET)  # pragma: no mutate
    method: Optional[Filter] = attr.ib(default=NOT_SET)  # pragma: no mutate
    endpoint: Optional[Filter] = attr.ib(default=NOT_SET)  # pragma: no mutate
    tag: Optional[Filter] = attr.ib(default=NOT_SET)  # pragma: no mutate
    operation_id: Optional[Filter] = attr.ib(default=NOT_SET)  # pragma: no mutate
    app: Any = attr.ib(default=NOT_SET)  # pragma: no mutate
    hooks: HookDispatcher = attr.ib(factory=lambda: HookDispatcher(scope=HookScope.SCHEMA))  # pragma: no mutate
    auth: AuthStorage = attr.ib(factory=AuthStorage)  # pragma: no mutate
    validate_schema: bool = attr.ib(default=True)  # pragma: no mutate
    skip_deprecated_operations: bool = attr.ib(default=False)  # pragma: no mutate
    data_generation_methods: Union[DataGenerationMethodInput, NotSet] = attr.ib(default=NOT_SET)
    code_sample_style: CodeSampleStyle = attr.ib(default=CodeSampleStyle.default())  # pragma: no mutate

    def parametrize(
        self,
        method: Optional[Filter] = NOT_SET,
        endpoint: Optional[Filter] = NOT_SET,
        tag: Optional[Filter] = NOT_SET,
        operation_id: Optional[Filter] = NOT_SET,
        validate_schema: Union[bool, NotSet] = NOT_SET,
        skip_deprecated_operations: Union[bool, NotSet] = NOT_SET,
        data_generation_methods: Union[DataGenerationMethodInput, NotSet] = NOT_SET,
        code_sample_style: Union[str, NotSet] = NOT_SET,
    ) -> Callable:
        if method is NOT_SET:
            method = self.method
        if endpoint is NOT_SET:
            endpoint = self.endpoint
        if tag is NOT_SET:
            tag = self.tag
        if operation_id is NOT_SET:
            operation_id = self.operation_id
        if data_generation_methods is NOT_SET:
            data_generation_methods = self.data_generation_methods
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
                __tracebackhide__ = True  # pylint: disable=unused-variable
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
                    auth=self.auth if self.auth.provider is not None else NOT_SET,
                    test_function=test,
                    validate_schema=validate_schema,
                    skip_deprecated_operations=skip_deprecated_operations,
                    data_generation_methods=data_generation_methods,
                    code_sample_style=_code_sample_style,
                    app=self.app,
                )
                fixtures = get_fixtures(test, request, given_kwargs)
                # Changing the node id is required for better reporting - the method and path will appear there
                node_id = request.node._nodeid
                settings = getattr(wrapped_test, "_hypothesis_internal_use_settings", None)
                tests = list(schema.get_all_tests(test, settings, _given_kwargs=given_kwargs))
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
    fixtures: Dict[str, Any],
    sub_test: Callable,
    subtests: SubTests,
) -> None:
    """Run the given subtest with pytest fixtures."""
    __tracebackhide__ = True  # pylint: disable=unused-variable

    # Deduplicate found checks in case of Hypothesis finding multiple of them
    failed_checks = {}
    exceptions = []
    inner_test = sub_test.hypothesis.inner_test  # type: ignore

    @impersonate(inner_test)  # type: ignore
    def collecting_wrapper(*args: Any, **kwargs: Any) -> None:
        __tracebackhide__ = True  # pylint: disable=unused-variable
        try:
            inner_test(*args, **kwargs)
        except CheckFailed as failed:
            failed_checks[failed.__class__] = failed
            raise failed
        except Exception as exception:
            # Deduplicate it later, as it is more costly than for `CheckFailed`
            exceptions.append(exception)
            raise

    def get_exception_class() -> Type[CheckFailed]:
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


def _schema_error(subtests: SubTests, error: InvalidSchema, node_id: str) -> None:
    """Run a failing test, that will show the underlying problem."""
    sub_test = error.as_failing_test_function()
    # `full_path` is always available in this case
    kwargs = {"path": error.full_path}
    if error.method:
        kwargs["method"] = error.method.upper()
    subtests.item._nodeid = _get_partial_node_name(node_id, **kwargs)
    with subtests.test(**kwargs):
        sub_test()


def get_schema(
    *,
    request: FixtureRequest,
    name: str,
    base_url: Union[Optional[str], NotSet] = None,
    method: Optional[Filter] = None,
    endpoint: Optional[Filter] = None,
    tag: Optional[Filter] = None,
    operation_id: Optional[Filter] = None,
    app: Any = None,
    test_function: GenericTest,
    hooks: HookDispatcher,
    auth: Union[AuthStorage, NotSet],
    validate_schema: Union[bool, NotSet] = NOT_SET,
    skip_deprecated_operations: Union[bool, NotSet] = NOT_SET,
    data_generation_methods: Union[DataGenerationMethodInput, NotSet] = NOT_SET,
    code_sample_style: CodeSampleStyle,
) -> BaseSchema:
    """Loads a schema from the fixture."""
    schema = request.getfixturevalue(name)
    if not isinstance(schema, BaseSchema):
        raise ValueError(f"The given schema must be an instance of BaseSchema, got: {type(schema)}")
    return schema.clone(
        base_url=base_url,
        method=method,
        endpoint=endpoint,
        tag=tag,
        operation_id=operation_id,
        app=app,
        test_function=test_function,
        hooks=schema.hooks.merge(hooks),
        auth=auth,
        validate_schema=validate_schema,
        skip_deprecated_operations=skip_deprecated_operations,
        data_generation_methods=data_generation_methods,
        code_sample_style=code_sample_style,
    )


def get_fixtures(func: Callable, request: FixtureRequest, given_kwargs: Dict[str, Any]) -> Dict[str, Any]:
    """Load fixtures, needed for the test function."""
    sig = signature(func)
    return {
        name: request.getfixturevalue(name) for name in sig.parameters if name != "case" and name not in given_kwargs
    }
