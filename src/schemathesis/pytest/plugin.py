from __future__ import annotations

import inspect
import sys
import unittest
from collections.abc import Callable, Generator
from functools import partial
from typing import TYPE_CHECKING, Any, cast

import pytest
from _pytest import nodes
from _pytest.config import hookimpl
from _pytest.python import Class, Function, FunctionDefinition, Metafunc, Module, PyCollector
from _pytest.subtests import SubtestReport
from hypothesis.errors import FailedHealthCheck, InvalidArgument, Unsatisfiable
from jsonschema.exceptions import SchemaError
from pluggy import Result as PluggyResult

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
from schemathesis.generation.stateful.state_machine import StatefulCallbackMark, StatefulSchemaMark
from schemathesis.pytest.control_flow import fail_on_no_matches
from schemathesis.pytest.warnings import emit_openapi_auth_warnings
from schemathesis.schemas import APIOperation

if TYPE_CHECKING:
    from _pytest.fixtures import FuncFixtureInfo
    from _pytest.terminal import TerminalReporter

    from schemathesis.engine.recorder import ScenarioRecorder
    from schemathesis.pytest.reporting import PytestReportDispatcher
    from schemathesis.reporting.har import HarWriter
    from schemathesis.reporting.junitxml import JunitXmlWriter
    from schemathesis.reporting.vcr import VcrWriter
    from schemathesis.schemas import BaseSchema

_CASSETTE_KEY: pytest.StashKey[
    dict[int, tuple[PytestReportDispatcher, list[VcrWriter | HarWriter | JunitXmlWriter]]]
] = pytest.StashKey()
_STATEFUL_WRITERS_KEY: pytest.StashKey[list[VcrWriter | HarWriter | JunitXmlWriter]] = pytest.StashKey()


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
        operation_label: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.test_function = test_func
        self.test_name = test_name
        self.operation_label = operation_label


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

        operation_label = operation.label if isinstance(result, Ok) else None

        if not metafunc._calls:
            yield SchemathesisFunction.from_parent(
                name=name,
                parent=self.parent,
                callobj=funcobj,
                fixtureinfo=fixtureinfo,
                test_func=self.test_function,
                originalname=self.name,
                operation_label=operation_label,
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
                    operation_label=operation_label,
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
            emit_openapi_auth_warnings(self.schema)
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
    if isinstance(report, SubtestReport) and report.passed:
        report._schemathesis_ignore_in_summary = True


@hookimpl(tryfirst=True, hookwrapper=True)  # type: ignore[untyped-decorator]
def pytest_terminal_summary(terminalreporter: TerminalReporter) -> Generator[None, None, None]:
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
    if not isinstance(report, SubtestReport):
        return

    description = report._sub_test_description()

    if report.passed:
        result.force_result(("passed", ",", f"{description} SUBPASS"))
    elif report.skipped:
        result.force_result(("skipped", "-", f"{description} SUBSKIP"))
    elif report.outcome == "failed":
        result.force_result(("failed", "u", f"{description} SUBFAIL"))


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
            raise build_unsatisfiable_error(
                operation, with_tip=True, filter_tracker=operation.filter_case_tracker
            ) from None
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


def _is_xdist_worker(config: pytest.Config) -> bool:
    return hasattr(config, "workerinput")


def pytest_configure(config: pytest.Config) -> None:
    config.stash[_CASSETTE_KEY] = {}
    if config.pluginmanager.hasplugin("xdist"):
        from schemathesis.pytest.xdist import XdistReportingPlugin

        config.pluginmanager.register(XdistReportingPlugin(), "schemathesis-xdist")


def _open_writers(schema: BaseSchema) -> list[VcrWriter | HarWriter | JunitXmlWriter]:
    from schemathesis.config._report import ReportFormat
    from schemathesis.reporting.har import HarWriter
    from schemathesis.reporting.junitxml import JunitXmlWriter
    from schemathesis.reporting.vcr import VcrWriter

    writers: list[VcrWriter | HarWriter | JunitXmlWriter] = []
    reports = schema.config.reports
    seed = schema.config.seed
    command = " ".join(sys.argv)
    if reports.vcr.enabled:
        path = reports.get_path(ReportFormat.VCR)
        vcr_writer = VcrWriter(output=path, config=schema.config.output, preserve_bytes=reports.preserve_bytes)
        vcr_writer.open(seed=seed, command=command)
        writers.append(vcr_writer)
    if reports.har.enabled:
        path = reports.get_path(ReportFormat.HAR)
        har_writer = HarWriter(output=path, config=schema.config.output, preserve_bytes=reports.preserve_bytes)
        har_writer.open(seed=seed)
        writers.append(har_writer)
    if reports.junit.enabled:
        path = reports.get_path(ReportFormat.JUNIT)
        writers.append(JunitXmlWriter(output=path, config=schema.config.output))
    return writers


def _write_to_writers(
    writers: list[VcrWriter | HarWriter | JunitXmlWriter],
    recorder: ScenarioRecorder,
    elapsed_sec: float,
) -> None:
    from schemathesis.reporting.junitxml import JunitXmlWriter

    for writer in writers:
        if isinstance(writer, JunitXmlWriter):
            writer.write(recorder, elapsed_sec)
        else:
            writer.write(recorder)


def _push_to_xdist_workeroutput(
    workeroutput: dict,
    schema: BaseSchema,
    recorder: ScenarioRecorder,
    elapsed_sec: float,
) -> None:
    from schemathesis.pytest.xdist import (
        SCHEMATHESIS_RECORDERS_KEY,
        _schema_id,
        _serialize_writer_config,
        serialize_recorder,
    )

    sid = _schema_id(schema)
    recorders = workeroutput.setdefault(SCHEMATHESIS_RECORDERS_KEY, {})
    if sid not in recorders:
        recorders[sid] = {"writer_config": _serialize_writer_config(schema), "records": []}
    recorders[sid]["records"].append(serialize_recorder(recorder, elapsed_sec))


def pytest_runtest_setup(item: pytest.Item) -> None:
    schema = StatefulSchemaMark.get(item.cls) if item.cls is not None else None
    if schema is not None:
        # Attach a callback to the TestCase class so the state machine can hand off
        # its recorder to the report writers once the scenario finishes.
        _schema = schema
        if _is_xdist_worker(item.config):
            reports = _schema.config.reports
            if reports.vcr.enabled or reports.har.enabled or reports.junit.enabled:

                def _xdist_stateful_callback(recorder: Any, elapsed_sec: float) -> None:
                    _push_to_xdist_workeroutput(item.config.workeroutput, _schema, recorder, elapsed_sec)

                item.stash[_STATEFUL_WRITERS_KEY] = []
                StatefulCallbackMark.set(item.cls, _xdist_stateful_callback)
        else:
            writers = _open_writers(_schema)
            item.stash[_STATEFUL_WRITERS_KEY] = writers
            if writers:

                def _stateful_callback(recorder: Any, elapsed_sec: float) -> None:
                    _write_to_writers(writers, recorder, elapsed_sec)

                StatefulCallbackMark.set(item.cls, _stateful_callback)
        return

    if not isinstance(item, SchemathesisFunction):
        return
    if item.operation_label is None:
        return
    schema = SchemaHandleMark.get(item.test_function)
    if schema is None or id(schema) in item.config.stash[_CASSETTE_KEY]:
        return
    if _is_xdist_worker(item.config):
        reports = schema.config.reports
        if not (reports.vcr.enabled or reports.har.enabled or reports.junit.enabled):
            return
        from schemathesis.pytest.reporting import PytestReportDispatcher

        dispatcher = PytestReportDispatcher(schema)
        item.config.stash[_CASSETTE_KEY][id(schema)] = (dispatcher, [])
        return
    writers = _open_writers(schema)
    if not writers:
        return
    from schemathesis.pytest.reporting import PytestReportDispatcher

    dispatcher = PytestReportDispatcher(schema)
    item.config.stash[_CASSETTE_KEY][id(schema)] = (dispatcher, writers)


def pytest_runtest_teardown(item: pytest.Item, nextitem: pytest.Item | None) -> None:
    if item.cls is not None and StatefulSchemaMark.is_set(item.cls):
        StatefulCallbackMark.set(item.cls, None)
        for writer in item.stash.get(_STATEFUL_WRITERS_KEY, []):
            writer.close()
        return

    if not isinstance(item, SchemathesisFunction):
        return
    if item.operation_label is None:
        return
    schema = SchemaHandleMark.get(item.test_function)
    if schema is None:
        return
    entry = item.config.stash[_CASSETTE_KEY].get(id(schema))
    if entry is None:
        return
    dispatcher, writers = entry
    result = dispatcher.pop_recorder(item.operation_label)
    if result is not None:
        recorder, elapsed_sec = result
        if _is_xdist_worker(item.config):
            _push_to_xdist_workeroutput(item.config.workeroutput, schema, recorder, elapsed_sec)
        else:
            _write_to_writers(writers, recorder, elapsed_sec)


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    for dispatcher, writers in session.config.stash.get(_CASSETTE_KEY, {}).values():
        dispatcher.unregister()
        for writer in writers:
            writer.close()
