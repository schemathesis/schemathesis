from __future__ import annotations

import asyncio
import unittest
from functools import partial
from typing import TYPE_CHECKING, Any, Callable, Generator, Type, cast

import pytest
from _pytest import nodes
from _pytest.config import hookimpl
from _pytest.python import Class, Function, FunctionDefinition, Metafunc, Module, PyCollector
from hypothesis.errors import InvalidArgument, Unsatisfiable
from jsonschema.exceptions import SchemaError

from schemathesis.core.control import SkipTest
from schemathesis.core.errors import (
    RECURSIVE_REFERENCE_ERROR_MESSAGE,
    SERIALIZERS_SUGGESTION_MESSAGE,
    IncorrectUsage,
    InvalidHeadersExample,
    InvalidRegexPattern,
    InvalidSchema,
    SerializationNotPossible,
)
from schemathesis.core.marks import Mark
from schemathesis.core.result import Ok, Result
from schemathesis.generation.hypothesis.given import (
    GivenArgsMark,
    GivenKwargsMark,
    is_given_applied,
    merge_given_args,
    validate_given_args,
)
from schemathesis.generation.hypothesis.reporting import ignore_hypothesis_output
from schemathesis.generation.overrides import OverrideMark
from schemathesis.pytest.control_flow import fail_on_no_matches
from schemathesis.schemas import APIOperation

if TYPE_CHECKING:
    from _pytest.fixtures import FuncFixtureInfo

    from schemathesis.schemas import BaseSchema

GIVEN_AND_EXPLICIT_EXAMPLES_ERROR_MESSAGE = (
    "Unsupported test setup. Tests using `@schema.given` cannot be combined with explicit schema examples in the same "
    "function. Separate these tests into distinct functions to avoid conflicts."
)


def _is_schema(value: object) -> bool:
    from schemathesis.schemas import BaseSchema

    return isinstance(value, BaseSchema)


SchemaHandleMark = Mark["BaseSchema"](attr_name="schema", check=_is_schema)


class SchemathesisFunction(Function):
    def __init__(
        self,
        *args: Any,
        test_func: Callable,
        test_name: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.test_function = test_func
        self.test_name = test_name


class SchemathesisCase(PyCollector):
    def __init__(self, test_function: Callable, schema: BaseSchema, *args: Any, **kwargs: Any) -> None:
        self.given_kwargs: dict[str, Any]
        given_args = GivenArgsMark.get(test_function)
        given_kwargs = GivenKwargsMark.get(test_function)

        assert given_args is not None
        assert given_kwargs is not None

        def _init_with_valid_test(_test_function: Callable, _args: tuple, _kwargs: dict[str, Any]) -> None:
            self.test_function = _test_function
            self.is_invalid_test = False
            self.given_kwargs = merge_given_args(test_function, _args, _kwargs)

        if is_given_applied(test_function):
            failing_test = validate_given_args(test_function, given_args, given_kwargs)
            if failing_test is not None:
                self.test_function = failing_test
                self.is_invalid_test = True
                self.given_kwargs = {}
            else:
                _init_with_valid_test(test_function, given_args, given_kwargs)
        else:
            _init_with_valid_test(test_function, given_args, given_kwargs)
        self.schema = schema
        super().__init__(*args, **kwargs)

    def _get_test_name(self, operation: APIOperation) -> str:
        return f"{self.name}[{operation.label}]"

    def _gen_items(self, result: Result[APIOperation, InvalidSchema]) -> Generator[SchemathesisFunction, None, None]:
        """Generate all tests for the given API operation.

        Could produce more than one test item if
        parametrization is applied via ``pytest.mark.parametrize`` or ``pytest_generate_tests``.

        This implementation is based on the original one in pytest, but with slight adjustments
        to produce tests out of hypothesis ones.
        """
        from schemathesis.generation.hypothesis.builder import HypothesisTestConfig, create_test

        is_trio_test = False
        for mark in getattr(self.test_function, "pytestmark", []):
            if mark.name == "trio":
                is_trio_test = True
                break

        if isinstance(result, Ok):
            operation = result.ok()
            if self.is_invalid_test:
                funcobj = self.test_function
            else:
                override = OverrideMark.get(self.test_function)
                if override is not None:
                    as_strategy_kwargs = {}
                    for location, entry in override.for_operation(operation).items():
                        if entry:
                            as_strategy_kwargs[location] = entry
                else:
                    as_strategy_kwargs = {}
                funcobj = create_test(
                    operation=operation,
                    test_func=self.test_function,
                    config=HypothesisTestConfig(
                        given_kwargs=self.given_kwargs,
                        generation=self.schema.generation_config,
                        as_strategy_kwargs=as_strategy_kwargs,
                    ),
                )
                if asyncio.iscoroutinefunction(self.test_function):
                    # `pytest-trio` expects a coroutine function
                    if is_trio_test:
                        funcobj.hypothesis.inner_test = self.test_function  # type: ignore
                    else:
                        funcobj.hypothesis.inner_test = make_async_test(self.test_function)  # type: ignore
            name = self._get_test_name(operation)
        else:
            error = result.err()
            funcobj = error.as_failing_test_function()
            name = self.name
            # `full_path` is always available in this case
            if error.method:
                name += f"[{error.method.upper()} {error.full_path}]"
            else:
                name += f"[{error.full_path}]"

        cls = self._get_class_parent()
        definition: FunctionDefinition = FunctionDefinition.from_parent(
            name=self.name, parent=self.parent, callobj=funcobj
        )
        fixturemanager = self.session._fixturemanager
        fixtureinfo = fixturemanager.getfixtureinfo(definition, funcobj, cls)

        metafunc = self._parametrize(cls, definition, fixtureinfo)

        if isinstance(self.parent, Class):
            # On pytest 7, Class collects the test methods directly, therefore
            funcobj = partial(funcobj, self.parent.obj)

        if not metafunc._calls:
            yield SchemathesisFunction.from_parent(
                name=name,
                parent=self.parent,
                callobj=funcobj,
                fixtureinfo=fixtureinfo,
                test_func=self.test_function,
                originalname=self.name,
            )
        else:
            fixtureinfo.prune_dependency_tree()
            for callspec in metafunc._calls:
                subname = f"{name}[{callspec.id}]"
                yield SchemathesisFunction.from_parent(
                    self.parent,
                    name=subname,
                    callspec=callspec,
                    callobj=funcobj,
                    fixtureinfo=fixtureinfo,
                    keywords={callspec.id: True},
                    originalname=name,
                    test_func=self.test_function,
                )

    def _get_class_parent(self) -> type | None:
        clscol = self.getparent(Class)
        return clscol.obj if clscol else None

    def _parametrize(self, cls: type | None, definition: FunctionDefinition, fixtureinfo: FuncFixtureInfo) -> Metafunc:
        parent = self.getparent(Module)
        module = parent.obj if parent is not None else parent
        # Avoiding `Metafunc` is quite problematic for now, as there are quite a lot of internals we rely on
        metafunc = Metafunc(definition, fixtureinfo, self.config, cls=cls, module=module, _ispytest=True)
        methods = []
        if module is not None and hasattr(module, "pytest_generate_tests"):
            methods.append(module.pytest_generate_tests)
        if hasattr(cls, "pytest_generate_tests"):
            cls = cast(Type, cls)
            methods.append(cls().pytest_generate_tests)
        self.ihook.pytest_generate_tests.call_extra(methods, {"metafunc": metafunc})
        return metafunc

    def collect(self) -> list[Function]:  # type: ignore
        """Generate different test items for all API operations available in the given schema."""
        try:
            items = [item for operation in self.schema.get_all_operations() for item in self._gen_items(operation)]
            if not items:
                fail_on_no_matches(self.nodeid)
            return items
        except Exception:
            pytest.fail("Error during collection")


def make_async_test(test: Callable) -> Callable:
    def async_run(*args: Any, **kwargs: Any) -> None:
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
        coro = test(*args, **kwargs)
        future = asyncio.ensure_future(coro, loop=loop)
        loop.run_until_complete(future)

    return async_run


@hookimpl(hookwrapper=True)  # type:ignore
def pytest_pycollect_makeitem(collector: nodes.Collector, name: str, obj: Any) -> Generator[None, Any, None]:
    """Switch to a different collector if the test is parametrized marked by schemathesis."""
    outcome = yield
    try:
        schema = SchemaHandleMark.get(obj)
        assert schema is not None
        outcome.force_result(SchemathesisCase.from_parent(collector, test_function=obj, name=name, schema=schema))
    except Exception:
        outcome.get_result()


@hookimpl(wrapper=True)
def pytest_pyfunc_call(pyfuncitem):  # type:ignore
    """It is possible to have a Hypothesis exception in runtime.

    For example - kwargs validation is failed for some strategy.
    """
    from hypothesis_jsonschema._canonicalise import HypothesisRefResolutionError

    from schemathesis.generation.hypothesis.builder import (
        InvalidHeadersExampleMark,
        InvalidRegexMark,
        NonSerializableMark,
        UnsatisfiableExampleMark,
    )

    __tracebackhide__ = True
    if isinstance(pyfuncitem, SchemathesisFunction):
        try:
            with ignore_hypothesis_output():
                yield
        except InvalidArgument as exc:
            if "Inconsistent args" in str(exc) and "@example()" in str(exc):
                raise IncorrectUsage(GIVEN_AND_EXPLICIT_EXAMPLES_ERROR_MESSAGE) from None
            raise InvalidSchema(exc.args[0]) from None
        except HypothesisRefResolutionError:
            pytest.skip(RECURSIVE_REFERENCE_ERROR_MESSAGE)
        except (SkipTest, unittest.SkipTest) as exc:
            if UnsatisfiableExampleMark.is_set(pyfuncitem.obj):
                raise Unsatisfiable("Failed to generate test cases from examples for this API operation") from None
            non_serializable = NonSerializableMark.get(pyfuncitem.obj)
            if non_serializable is not None:
                media_types = ", ".join(non_serializable.media_types)
                raise SerializationNotPossible(
                    "Failed to generate test cases from examples for this API operation because of"
                    f" unsupported payload media types: {media_types}\n{SERIALIZERS_SUGGESTION_MESSAGE}",
                    media_types=non_serializable.media_types,
                ) from None
            invalid_regex = InvalidRegexMark.get(pyfuncitem.obj)
            if invalid_regex is not None:
                raise InvalidRegexPattern.from_schema_error(invalid_regex, from_examples=True) from None
            invalid_headers = InvalidHeadersExampleMark.get(pyfuncitem.obj)
            if invalid_headers is not None:
                raise InvalidHeadersExample.from_headers(invalid_headers) from None
            pytest.skip(exc.args[0])
        except SchemaError as exc:
            raise InvalidRegexPattern.from_schema_error(exc, from_examples=False) from exc
        invalid_headers = InvalidHeadersExampleMark.get(pyfuncitem.obj)
        if invalid_headers is not None:
            raise InvalidHeadersExample.from_headers(invalid_headers) from None
    else:
        yield
