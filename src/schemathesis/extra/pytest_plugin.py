from __future__ import annotations

import unittest
from contextlib import contextmanager
from functools import partial
from typing import Any, Callable, Generator, Type, cast

import pytest
from _pytest import fixtures, nodes
from _pytest.config import hookimpl
from _pytest.fixtures import FuncFixtureInfo
from _pytest.python import Class, Function, FunctionDefinition, Metafunc, Module, PyCollector
from hypothesis import reporting
from hypothesis.errors import InvalidArgument, Unsatisfiable
from jsonschema.exceptions import SchemaError

from .._dependency_versions import IS_PYTEST_ABOVE_7, IS_PYTEST_ABOVE_8
from .._override import get_override_from_mark
from ..constants import (
    GIVEN_AND_EXPLICIT_EXAMPLES_ERROR_MESSAGE,
    RECURSIVE_REFERENCE_ERROR_MESSAGE,
    SERIALIZERS_SUGGESTION_MESSAGE,
)
from ..exceptions import (
    InvalidHeadersExample,
    InvalidRegularExpression,
    OperationSchemaError,
    SerializationNotPossible,
    SkipTest,
    UsageError,
)
from ..internal.result import Ok, Result
from ..models import APIOperation
from ..utils import (
    PARAMETRIZE_MARKER,
    fail_on_no_matches,
    get_given_args,
    get_given_kwargs,
    is_given_applied,
    is_schemathesis_test,
    merge_given_args,
    validate_given_args,
)


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

    if not IS_PYTEST_ABOVE_7:
        # On pytest 7, `self.obj` is already `partial`
        def _getobj(self) -> partial:
            """Tests defined as methods require `self` as the first argument.

            This method is called only for this case.
            """
            return partial(self.obj, self.parent.obj)  # type: ignore


class SchemathesisCase(PyCollector):
    def __init__(self, test_function: Callable, *args: Any, **kwargs: Any) -> None:
        self.given_kwargs: dict[str, Any] | None
        given_args = get_given_args(test_function)
        given_kwargs = get_given_kwargs(test_function)

        def _init_with_valid_test(_test_function: Callable, _args: tuple, _kwargs: dict[str, Any]) -> None:
            self.test_function = _test_function
            self.is_invalid_test = False
            self.given_kwargs = merge_given_args(test_function, _args, _kwargs)

        if is_given_applied(test_function):
            failing_test = validate_given_args(test_function, given_args, given_kwargs)
            if failing_test is not None:
                self.test_function = failing_test
                self.is_invalid_test = True
                self.given_kwargs = None
            else:
                _init_with_valid_test(test_function, given_args, given_kwargs)
        else:
            _init_with_valid_test(test_function, given_args, given_kwargs)
        self.schemathesis_case = getattr(test_function, PARAMETRIZE_MARKER)
        super().__init__(*args, **kwargs)

    def _get_test_name(self, operation: APIOperation) -> str:
        return f"{self.name}[{operation.verbose_name}]"

    def _gen_items(
        self, result: Result[APIOperation, OperationSchemaError]
    ) -> Generator[SchemathesisFunction, None, None]:
        """Generate all tests for the given API operation.

        Could produce more than one test item if
        parametrization is applied via ``pytest.mark.parametrize`` or ``pytest_generate_tests``.

        This implementation is based on the original one in pytest, but with slight adjustments
        to produce tests out of hypothesis ones.
        """
        from .._hypothesis import create_test

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
                override = get_override_from_mark(self.test_function)
                as_strategy_kwargs: dict | None
                if override is not None:
                    as_strategy_kwargs = {}
                    for location, entry in override.for_operation(operation).items():
                        if entry:
                            as_strategy_kwargs[location] = entry
                else:
                    as_strategy_kwargs = None
                funcobj = create_test(
                    operation=operation,
                    test=self.test_function,
                    _given_kwargs=self.given_kwargs,
                    data_generation_methods=self.schemathesis_case.data_generation_methods,
                    generation_config=self.schemathesis_case.generation_config,
                    as_strategy_kwargs=as_strategy_kwargs,
                    keep_async_fn=is_trio_test,
                )
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
            if not IS_PYTEST_ABOVE_8:
                fixtures.add_funcarg_pseudo_fixture_def(self.parent, metafunc, fixturemanager)  # type: ignore[arg-type]
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
        kwargs = {"cls": cls, "module": module}
        if IS_PYTEST_ABOVE_7:
            # Avoiding `Metafunc` is quite problematic for now, as there are quite a lot of internals we rely on
            kwargs["_ispytest"] = True
        metafunc = Metafunc(definition, fixtureinfo, self.config, **kwargs)
        methods = []
        if hasattr(module, "pytest_generate_tests"):
            methods.append(module.pytest_generate_tests)
        if hasattr(cls, "pytest_generate_tests"):
            cls = cast(Type, cls)
            methods.append(cls().pytest_generate_tests)
        self.ihook.pytest_generate_tests.call_extra(methods, {"metafunc": metafunc})
        return metafunc

    def collect(self) -> list[Function]:  # type: ignore
        """Generate different test items for all API operations available in the given schema."""
        try:
            items = [
                item
                for operation in self.schemathesis_case.get_all_operations(
                    hooks=getattr(self.test_function, "_schemathesis_hooks", None)
                )
                for item in self._gen_items(operation)
            ]
            if not items:
                fail_on_no_matches(self.nodeid)
            return items
        except Exception:
            pytest.fail("Error during collection")


@hookimpl(hookwrapper=True)  # type:ignore # pragma: no mutate
def pytest_pycollect_makeitem(collector: nodes.Collector, name: str, obj: Any) -> Generator[None, Any, None]:
    """Switch to a different collector if the test is parametrized marked by schemathesis."""
    outcome = yield
    if is_schemathesis_test(obj):
        outcome.force_result(SchemathesisCase.from_parent(collector, test_function=obj, name=name))
    else:
        outcome.get_result()


IGNORED_HYPOTHESIS_OUTPUT = ("Falsifying example",)


def _should_ignore_entry(value: str) -> bool:
    return value.startswith(IGNORED_HYPOTHESIS_OUTPUT)


def hypothesis_reporter(value: str) -> None:
    if _should_ignore_entry(value):
        return
    reporting.default(value)


@contextmanager
def skip_unnecessary_hypothesis_output() -> Generator:
    """Avoid printing Hypothesis output that is not necessary in Schemathesis' pytest plugin."""
    with reporting.with_reporter(hypothesis_reporter):  # type: ignore
        yield


@hookimpl(wrapper=True)
def pytest_pyfunc_call(pyfuncitem):  # type:ignore
    """It is possible to have a Hypothesis exception in runtime.

    For example - kwargs validation is failed for some strategy.
    """
    from hypothesis_jsonschema._canonicalise import HypothesisRefResolutionError

    from .._hypothesis import (
        get_invalid_example_headers_mark,
        get_invalid_regex_mark,
        get_non_serializable_mark,
        has_unsatisfied_example_mark,
    )

    __tracebackhide__ = True
    if isinstance(pyfuncitem, SchemathesisFunction):
        try:
            with skip_unnecessary_hypothesis_output():
                yield
        except InvalidArgument as exc:
            if "Inconsistent args" in str(exc) and "@example()" in str(exc):
                raise UsageError(GIVEN_AND_EXPLICIT_EXAMPLES_ERROR_MESSAGE) from None
            raise OperationSchemaError(exc.args[0]) from None
        except HypothesisRefResolutionError:
            pytest.skip(RECURSIVE_REFERENCE_ERROR_MESSAGE)
        except (SkipTest, unittest.SkipTest) as exc:
            if has_unsatisfied_example_mark(pyfuncitem.obj):
                raise Unsatisfiable("Failed to generate test cases from examples for this API operation") from None
            non_serializable = get_non_serializable_mark(pyfuncitem.obj)
            if non_serializable is not None:
                media_types = ", ".join(non_serializable.media_types)
                raise SerializationNotPossible(
                    "Failed to generate test cases from examples for this API operation because of"
                    f" unsupported payload media types: {media_types}\n{SERIALIZERS_SUGGESTION_MESSAGE}",
                    media_types=non_serializable.media_types,
                ) from None
            invalid_regex = get_invalid_regex_mark(pyfuncitem.obj)
            if invalid_regex is not None:
                raise InvalidRegularExpression.from_schema_error(invalid_regex, from_examples=True) from None
            invalid_headers = get_invalid_example_headers_mark(pyfuncitem.obj)
            if invalid_headers is not None:
                raise InvalidHeadersExample.from_headers(invalid_headers) from None
            pytest.skip(exc.args[0])
        except SchemaError as exc:
            raise InvalidRegularExpression.from_schema_error(exc, from_examples=False) from exc
        except Exception as exc:
            if hasattr(exc, "__notes__"):
                exc.__notes__ = [note for note in exc.__notes__ if not _should_ignore_entry(note)]  # type: ignore
            raise
        invalid_headers = get_invalid_example_headers_mark(pyfuncitem.obj)
        if invalid_headers is not None:
            raise InvalidHeadersExample.from_headers(invalid_headers) from None
    else:
        yield
