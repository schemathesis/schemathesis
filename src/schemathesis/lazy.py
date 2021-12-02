from inspect import signature
from typing import Any, Callable, Dict, Optional, Union

import attr
import pytest
from _pytest.fixtures import FixtureRequest
from pytest_subtests import SubTests, nullcontext

from .constants import CodeSampleStyle, DataGenerationMethod
from .exceptions import InvalidSchema
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

        def wrapper(func: Callable) -> Callable:
            if is_given_applied(func):
                # The user wrapped the test function with `@schema.given`
                # These args & kwargs go as extra to the underlying test generator
                given_args = get_given_args(func)
                given_kwargs = get_given_kwargs(func)
                test_function = validate_given_args(func, given_args, given_kwargs)
                if test_function is not None:
                    return test_function
                given_kwargs = merge_given_args(func, given_args, given_kwargs)
                del given_args
            else:
                given_kwargs = {}

            def test(request: FixtureRequest) -> None:
                """The actual test, which is executed by pytest."""
                __tracebackhide__ = True  # pylint: disable=unused-variable
                if hasattr(test, "_schemathesis_hooks"):
                    func._schemathesis_hooks = test._schemathesis_hooks  # type: ignore
                schema = get_schema(
                    request=request,
                    name=self.fixture_name,
                    base_url=self.base_url,
                    method=method,
                    endpoint=endpoint,
                    tag=tag,
                    operation_id=operation_id,
                    hooks=self.hooks,
                    test_function=func,
                    validate_schema=validate_schema,
                    skip_deprecated_operations=skip_deprecated_operations,
                    data_generation_methods=data_generation_methods,
                    code_sample_style=_code_sample_style,
                    app=self.app,
                )
                fixtures = get_fixtures(func, request, given_kwargs)
                # Changing the node id is required for better reporting - the method and path will appear there
                node_id = request.node._nodeid
                settings = getattr(test, "_hypothesis_internal_use_settings", None)
                tests = list(schema.get_all_tests(func, settings, _given_kwargs=given_kwargs))
                if not tests:
                    fail_on_no_matches(node_id)
                request.session.testscollected += len(tests)
                capmam = request.node.config.pluginmanager.get_plugin("capturemanager")
                if capmam is not None:
                    suspend_capture_ctx = capmam.global_and_fixture_disabled
                else:
                    suspend_capture_ctx = nullcontext
                subtests = SubTests(request.node.ihook, suspend_capture_ctx, request)
                for result, data_generation_method in tests:
                    if isinstance(result, Ok):
                        operation, sub_test = result.ok()
                        subtests.item._nodeid = _get_node_name(node_id, operation, data_generation_method)
                        run_subtest(operation, data_generation_method, fixtures, sub_test, subtests)
                    else:
                        _schema_error(subtests, result.err(), node_id, data_generation_method)
                subtests.item._nodeid = node_id

            test = pytest.mark.usefixtures(self.fixture_name)(test)

            # Needed to prevent a failure when settings are applied to the test function
            test.is_hypothesis_test = True  # type: ignore

            return test

        return wrapper

    def given(self, *args: GivenInput, **kwargs: GivenInput) -> Callable:
        return given_proxy(*args, **kwargs)


def _get_node_name(node_id: str, operation: APIOperation, data_generation_method: DataGenerationMethod) -> str:
    """Make a test node name. For example: test_api[GET /users]."""
    return f"{node_id}[{operation.method.upper()} {operation.full_path}][{data_generation_method.as_short_name()}]"


def _get_partial_node_name(node_id: str, data_generation_method: DataGenerationMethod, **kwargs: Any) -> str:
    """Make a test node name for failing tests caused by schema errors."""
    name = node_id
    if "method" in kwargs:
        name += f"[{kwargs['method']} {kwargs['path']}]"
    else:
        name += f"[{kwargs['path']}]"
    name += f"[{data_generation_method.as_short_name()}]"
    return name


def run_subtest(
    operation: APIOperation,
    data_generation_method: DataGenerationMethod,
    fixtures: Dict[str, Any],
    sub_test: Callable,
    subtests: SubTests,
) -> None:
    """Run the given subtest with pytest fixtures."""
    with subtests.test(
        verbose_name=operation.verbose_name, data_generation_method=data_generation_method.as_short_name()
    ):
        sub_test(**fixtures)


def _schema_error(
    subtests: SubTests, error: InvalidSchema, node_id: str, data_generation_method: DataGenerationMethod
) -> None:
    """Run a failing test, that will show the underlying problem."""
    sub_test = error.as_failing_test_function()
    # `full_path` is always available in this case
    kwargs = {"path": error.full_path}
    if error.method:
        kwargs["method"] = error.method.upper()
    subtests.item._nodeid = _get_partial_node_name(node_id, data_generation_method, **kwargs)
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
