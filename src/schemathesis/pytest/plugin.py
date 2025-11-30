from __future__ import annotations

import inspect
import unittest
from collections.abc import Callable, Generator
from functools import partial
from typing import TYPE_CHECKING, Any, cast

import pytest
from _pytest import nodes
from _pytest.config import hookimpl
from _pytest.python import Class, Function, FunctionDefinition, Metafunc, Module, PyCollector
from hypothesis.errors import FailedHealthCheck, InvalidArgument, Unsatisfiable
from jsonschema.exceptions import SchemaError
from pluggy import Result as PluggyResult
from pytest_subtests.plugin import SubTestReport

from schemathesis.core.compat import BaseExceptionGroup
from schemathesis.core.control import SkipTest
from schemathesis.core.errors import (
    SERIALIZERS_SUGGESTION_MESSAGE,
    IncorrectUsage,
    InvalidHeadersExample,
    InvalidRegexPattern,
    InvalidSchema,
    SchemathesisError,
    SerializationNotPossible,
    format_exception,
)
from schemathesis.core.failures import FailureGroup, get_origin
from schemathesis.core.marks import Mark
from schemathesis.core.result import Ok, Result
from schemathesis.generation import overrides
from schemathesis.generation.hypothesis.given import (
    GivenArgsMark,
    GivenKwargsMark,
    is_given_applied,
    merge_given_args,
    validate_given_args,
)
from schemathesis.generation.hypothesis.reporting import (
    HealthCheckTipStyle,
    build_health_check_error,
    build_unsatisfiable_error,
    ignore_hypothesis_output,
)
from schemathesis.pytest.control_flow import fail_on_no_matches
from schemathesis.schemas import APIOperation

if TYPE_CHECKING:
    from _pytest.fixtures import FuncFixtureInfo
    from _pytest.terminal import TerminalReporter

    from schemathesis.schemas import BaseSchema


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
        from schemathesis.checks import load_all_checks
        from schemathesis.generation.hypothesis.builder import (
            HypothesisTestConfig,
            HypothesisTestMode,
            create_test,
            make_async_test,
        )

        load_all_checks()

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
                as_strategy_kwargs = {}

                auth = self.schema.config.auth_for(operation=operation)
                if auth is not None:
                    from requests.auth import _basic_auth_str

                    as_strategy_kwargs["headers"] = {"Authorization": _basic_auth_str(*auth)}
                headers = self.schema.config.headers_for(operation=operation)
                if headers:
                    as_strategy_kwargs["headers"] = headers

                override = overrides.for_operation(operation=operation, config=self.schema.config)
                if override is not None:
                    for location, entry in override.items():
                        if entry:
                            as_strategy_kwargs[location.container_name] = entry
                modes = []
                phases = self.schema.config.phases_for(operation=operation)
                if phases.examples.enabled:
                    modes.append(HypothesisTestMode.EXAMPLES)
                if phases.fuzzing.enabled:
                    modes.append(HypothesisTestMode.FUZZING)
                if phases.coverage.enabled:
                    modes.append(HypothesisTestMode.COVERAGE)

                # Use fuzzing phase settings if fuzzing is enabled, since only fuzzing uses max_examples
                phase = "fuzzing" if HypothesisTestMode.FUZZING in modes else None
                funcobj = create_test(
                    operation=operation,
                    test_func=self.test_function,
                    config=HypothesisTestConfig(
                        modes=modes,
                        settings=self.schema.config.get_hypothesis_settings(operation=operation, phase=phase),
                        given_kwargs=self.given_kwargs,
                        project=self.schema.config,
                        as_strategy_kwargs=as_strategy_kwargs,
                        seed=self.schema.config.seed,
                    ),
                )
                if inspect.iscoroutinefunction(self.test_function):
                    # `pytest-trio` expects a coroutine function
                    if is_trio_test:
                        funcobj.hypothesis.inner_test = self.test_function  # type: ignore[attr-defined]
                    else:
                        funcobj.hypothesis.inner_test = make_async_test(self.test_function)  # type: ignore[attr-defined]
            name = self._get_test_name(operation)
        else:
            error = result.err()
            funcobj = error.as_failing_test_function()
            name = self.name
            if error.method:
                name += f"[{error.method.upper()} {error.path}]"
            else:
                name += f"[{error.path}]"

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
            cls = cast(type, cls)
            methods.append(cls().pytest_generate_tests)
        self.ihook.pytest_generate_tests.call_extra(methods, {"metafunc": metafunc})
        return metafunc

    def collect(self) -> list[Function]:  # type: ignore[return]
        """Generate different test items for all API operations available in the given schema."""
        try:
            items = [item for operation in self.schema.get_all_operations() for item in self._gen_items(operation)]
            if not items:
                fail_on_no_matches(self.nodeid)
            return items
        except Exception:
            pytest.fail("Error during collection")


@hookimpl(hookwrapper=True)  # type: ignore[untyped-decorator]
def pytest_pycollect_makeitem(collector: nodes.Collector, name: str, obj: Any) -> Generator[None, Any, None]:
    """Switch to a different collector if the test is parametrized marked by schemathesis."""
    outcome = yield
    try:
        schema = SchemaHandleMark.get(obj)
        assert schema is not None
        outcome.force_result(SchemathesisCase.from_parent(collector, test_function=obj, name=name, schema=schema))
    except Exception:
        outcome.get_result()


@hookimpl(tryfirst=True)  # type: ignore[untyped-decorator]
def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    if isinstance(report, SubTestReport) and report.passed:
        report._schemathesis_ignore_in_summary = True


@hookimpl(tryfirst=True, hookwrapper=True)  # type: ignore[untyped-decorator]
def pytest_terminal_summary(terminalreporter: "TerminalReporter") -> Generator[None, None, None]:
    passed = terminalreporter.stats.get("passed", [])
    if passed:
        terminalreporter.stats["passed"] = [
            report for report in passed if not getattr(report, "_schemathesis_ignore_in_summary", False)
        ]
    yield


@hookimpl(hookwrapper=True, trylast=True)  # type: ignore[untyped-decorator]
def pytest_report_teststatus(
    report: pytest.TestReport,
    config: pytest.Config,
) -> Generator[None, None, None]:
    outcome = yield
    result = cast(PluggyResult[tuple[str, str, str] | None], outcome)
    if not isinstance(report, SubTestReport):
        return

    description = report.sub_test_description()
    shortletter = getattr(config.option, "no_subtests_shortletter", False)

    if report.passed:
        short = "" if shortletter else ","
        result.force_result(("passed", short, f"{description} SUBPASS"))
    elif report.skipped:
        short = "" if shortletter else "-"
        result.force_result(("skipped", short, f"{description} SUBSKIP"))
    elif report.outcome == "failed":
        short = "" if shortletter else "u"
        result.force_result(("failed", short, f"{description} SUBFAIL"))


@pytest.hookimpl(tryfirst=True)  # type: ignore[untyped-decorator]
def pytest_exception_interact(node: Function, call: pytest.CallInfo, report: pytest.TestReport) -> None:
    if call.excinfo:
        if issubclass(call.excinfo.type, SchemathesisError) and hasattr(call.excinfo.value, "__notes__"):
            # Hypothesis adds quite a lot of additional debug information which is not that helpful in Schemathesis
            call.excinfo.value.__notes__.clear()
            report.longrepr = "".join(format_exception(call.excinfo.value))
        # Deduplicate identical exceptions in exception groups
        if isinstance(call.excinfo.value, BaseExceptionGroup):
            # Use exception origin (type + traceback + context) as deduplication key
            origins: dict[tuple, BaseException] = {}
            for exc in call.excinfo.value.exceptions:
                origin = get_origin(exc)
                if origin not in origins:
                    origins[origin] = exc

            if len(origins) < len(call.excinfo.value.exceptions):
                deduplicated = list(origins.values())
                message = call.excinfo.value.message.replace(
                    f"{len(call.excinfo.value.exceptions)} distinct failures",
                    f"{len(deduplicated)} distinct failures",
                )
                group = BaseExceptionGroup(message, deduplicated)
                report.longrepr = "".join(format_exception(group, with_traceback=True))

        if call.excinfo.type is FailureGroup:
            tb_entries = list(call.excinfo.traceback)
            total_frames = len(tb_entries)

            # Keep internal Schemathesis frames + one extra one from the caller
            skip_frames = 0
            for i in range(total_frames - 1, -1, -1):
                entry = tb_entries[i]

                if not str(entry.path).endswith("schemathesis/generation/case.py"):
                    skip_frames = i
                    break

            report.longrepr = "".join(
                format_exception(call.excinfo.value, with_traceback=True, skip_frames=skip_frames)
            )


@hookimpl(wrapper=True)
def pytest_pyfunc_call(pyfuncitem):  # type: ignore[no-untyped-def]
    """It is possible to have a Hypothesis exception in runtime.

    For example - kwargs validation is failed for some strategy.
    """
    from schemathesis.generation.hypothesis.builder import (
        ApiOperationMark,
        InvalidHeadersExampleMark,
        InvalidRegexMark,
        MissingPathParameters,
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
                from schemathesis.generation.hypothesis.given import GIVEN_AND_EXPLICIT_EXAMPLE_ERROR_MESSAGE

                raise IncorrectUsage(GIVEN_AND_EXPLICIT_EXAMPLE_ERROR_MESSAGE) from None
            raise InvalidSchema(exc.args[0]) from None
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
        except FailedHealthCheck as exc:
            operation = ApiOperationMark.get(pyfuncitem.obj)
            assert operation is not None
            raise build_health_check_error(
                operation, exc, with_tip=True, tip_style=HealthCheckTipStyle.PYTEST
            ) from None
        except Unsatisfiable:
            operation = ApiOperationMark.get(pyfuncitem.obj)
            assert operation is not None
            raise build_unsatisfiable_error(operation, with_tip=True) from None
        except SchemaError as exc:
            raise InvalidRegexPattern.from_schema_error(exc, from_examples=False) from exc

        invalid_headers = InvalidHeadersExampleMark.get(pyfuncitem.obj)
        if invalid_headers is not None:
            raise InvalidHeadersExample.from_headers(invalid_headers) from None

        missing_path_parameters = MissingPathParameters.get(pyfuncitem.obj)
        if missing_path_parameters:
            raise missing_path_parameters from None
    else:
        yield
