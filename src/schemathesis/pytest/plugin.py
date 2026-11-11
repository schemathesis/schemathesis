from __future__ import annotations

import sys
import time
import unittest
from collections.abc import Callable, Generator, Sequence
from contextlib import contextmanager
from functools import partial
from typing import TYPE_CHECKING, Any, NoReturn, cast

import pytest
from _pytest.config import hookimpl
from _pytest.python import Class, Function, FunctionDefinition, Metafunc, Module, PyCollector
from hypothesis.errors import FailedHealthCheck, InvalidArgument, Unsatisfiable
from pluggy import Result as PluggyResult

from schemathesis.checks import collect_after_run_failures, run_checks_for
from schemathesis.core.control import SkipTest
from schemathesis.core.errors import (
    IncorrectUsage,
    InvalidHeadersExample,
    InvalidSchema,
    SchemathesisError,
    format_exception,
)
from schemathesis.core.failures import RUN_CHECKS_LABEL, FailureGroup, as_reported_failure, format_failures, get_origin
from schemathesis.core.marks import Mark
from schemathesis.core.result import Ok, Result
from schemathesis.engine import Status
from schemathesis.generation.feedback import FeedbackSources
from schemathesis.generation.hypothesis.given import GIVEN_AND_EXPLICIT_EXAMPLE_ERROR_MESSAGE, resolve_given
from schemathesis.generation.hypothesis.marks import (
    ApiOperationMark,
    ExamplesControllerMark,
    MissingPathParameters,
    UnserializablePayloadMark,
)
from schemathesis.generation.hypothesis.reporting import (
    build_health_check_error,
    build_unsatisfiable_error,
    ignore_hypothesis_output,
)
from schemathesis.generation.stateful import StatefulCallbackMark, StatefulSchemaMark
from schemathesis.pytest import _subtests
from schemathesis.pytest._keys import _PYTEST_SCHEMAS_KEY, _WORKER_AFTER_RUN_FAILURES_KEY, track_schema
from schemathesis.pytest._subtests import HAS_CORE_SUBTESTS, is_subtest_report
from schemathesis.pytest.control_flow import fail_on_no_matches
from schemathesis.pytest.reporting import PytestReportOutcome, open_writers, write_report_locations, write_to_writers
from schemathesis.pytest.warnings import (
    emit_schema_warnings,
    emit_unserializable_payload_warning,
)
from schemathesis.python._constants.orchestrator import make_constants_value_source
from schemathesis.schemas import APIOperation

if TYPE_CHECKING:
    from _pytest._code import TracebackEntry
    from _pytest.fixtures import FuncFixtureInfo
    from _pytest.terminal import TerminalReporter

    from schemathesis.config import ReportsConfig
    from schemathesis.core.spec import SchemaMetadata
    from schemathesis.engine.recorder import ScenarioRecorder
    from schemathesis.pytest.reporting import PytestReportDispatcher
    from schemathesis.reporting.allure import AllureCallBuffer, AllureHookForwarder, AllureWriter
    from schemathesis.reporting.har import HarWriter
    from schemathesis.reporting.html.writer import HtmlWriter
    from schemathesis.reporting.junitxml import JunitXmlWriter
    from schemathesis.reporting.vcr import VcrWriter
    from schemathesis.schemas import BaseSchema

_CASSETTE_KEY: pytest.StashKey[
    dict[int, tuple[PytestReportDispatcher, list[VcrWriter | HarWriter | JunitXmlWriter | AllureWriter | HtmlWriter]]]
] = pytest.StashKey()
_STATEFUL_WRITERS_KEY: pytest.StashKey[list[VcrWriter | HarWriter | JunitXmlWriter | AllureWriter | HtmlWriter]] = (
    pytest.StashKey()
)
_ALLURE_FORWARDER_KEY: pytest.StashKey[AllureHookForwarder] = pytest.StashKey()
_ALLURE_BUFFER_KEY: pytest.StashKey[AllureCallBuffer] = pytest.StashKey()
_REPORT_OUTCOME_KEY: pytest.StashKey[PytestReportOutcome] = pytest.StashKey()


# Present only while a lazy-schema subtest runs; holds that subtest's outcome.
_SUBTEST_OUTCOME_KEY: pytest.StashKey[PytestReportOutcome] = pytest.StashKey()


def _is_trio(test_function: Callable) -> bool:
    return any(mark.name == "trio" for mark in getattr(test_function, "pytestmark", []))


def _attach_async_inner(hypothesis_test: Callable, test_function: Callable) -> None:
    import inspect

    from schemathesis.generation.hypothesis.builder import make_async_test

    if inspect.iscoroutinefunction(test_function):
        # `pytest-trio` expects a coroutine function.
        if _is_trio(test_function):
            hypothesis_test.hypothesis.inner_test = test_function  # type: ignore[attr-defined]
        else:
            hypothesis_test.hypothesis.inner_test = make_async_test(test_function)  # type: ignore[attr-defined]


def _is_schema(value: object) -> bool:
    from schemathesis.schemas import BaseSchema

    return isinstance(value, BaseSchema)


def _is_schema_dict(value: object) -> bool:
    from schemathesis.schemas import BaseSchema

    return isinstance(value, dict) and all(isinstance(v, BaseSchema) for v in value.values())


SchemaHandleMark = Mark["BaseSchema"](attr_name="schema", check=_is_schema)
MultiSchemaHandleMark = Mark["dict[str, BaseSchema]"](attr_name="multi_schema", check=_is_schema_dict)


class SchemathesisFunction(Function):
    def __init__(
        self,
        *args: Any,
        test_func: Callable,
        schema: BaseSchema,
        test_name: str | None = None,
        operation_label: str | None = None,
        operation_tags: list[str] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.test_function = test_func
        self.schema = schema
        self.test_name = test_name
        self.operation_label = operation_label
        self.operation_tags = operation_tags

    def runtest(self) -> None:
        __tracebackhide__ = True
        # A schema gets `after_run` only if one of its tests runs here, not when deselected or run by another worker.
        track_schema(self.config, self.schema)
        super().runtest()


class SchemathesisCase(PyCollector):
    def __init__(self, test_function: Callable, schema: BaseSchema, *args: Any, **kwargs: Any) -> None:
        failing_test, self.given_kwargs = resolve_given(test_function)
        self.test_function = failing_test if failing_test is not None else test_function
        self.is_invalid_test = failing_test is not None
        self.schema = schema
        super().__init__(*args, **kwargs)

    def _get_test_name(self, operation: APIOperation) -> str:
        return f"{self.name}[{operation.label}]"

    def _gen_items(
        self, result: Result[APIOperation, InvalidSchema], feedback: FeedbackSources
    ) -> Generator[SchemathesisFunction, None, None]:
        """Generate all tests for the given API operation.

        Could produce more than one test item if
        parametrization is applied via ``pytest.mark.parametrize`` or ``pytest_generate_tests``.

        This implementation is based on the original one in pytest, but with slight adjustments
        to produce tests out of hypothesis ones.
        """
        from schemathesis.checks import load_all_checks
        from schemathesis.pytest._test_builder import build_operation_test, schema_error_suffix

        load_all_checks()

        if isinstance(result, Ok):
            operation = result.ok()
            if self.is_invalid_test:
                funcobj = self.test_function
            else:
                funcobj = build_operation_test(
                    config=self.schema.config,
                    operation=operation,
                    test_func=self.test_function,
                    given_kwargs=self.given_kwargs,
                    feedback=feedback,
                )
                _attach_async_inner(funcobj, self.test_function)

        if isinstance(result, Ok):
            operation = result.ok()
            name = self._get_test_name(operation)
            operation_label = operation.label
            operation_tags = operation.tags
        else:
            error = result.err()
            funcobj = error.as_failing_test_function()
            name = f"{self.name}{schema_error_suffix(error)}"
            operation_label = error.label
            operation_tags = None

        yield from self._make_items(funcobj, name, operation_label, operation_tags)
        if isinstance(result, Ok):
            for schema_error in result.ok().schema_errors:
                yield from self._make_items(
                    schema_error.as_failing_test_function(), f"{name}[schema-error]", operation_label, operation_tags
                )

    def _make_items(
        self,
        funcobj: Callable,
        name: str,
        operation_label: str,
        operation_tags: list[str] | None,
    ) -> Generator[SchemathesisFunction, None, None]:
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
                schema=self.schema,
                originalname=name,
                operation_label=operation_label,
                operation_tags=operation_tags,
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
                    schema=self.schema,
                    operation_label=operation_label,
                    operation_tags=operation_tags,
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

    def collect(self) -> list[Function]:
        """Generate different test items for all API operations available in the given schema."""
        try:
            emit_schema_warnings(self.schema)
            feedback = FeedbackSources(constants_value_source=make_constants_value_source(self.schema))
            items: list[Function] = [
                item for operation in self.schema.get_all_operations() for item in self._gen_items(operation, feedback)
            ]
            if not items:
                fail_on_no_matches(self.nodeid)
            return items
        except Exception:
            pytest.fail("Error during collection")


class SchemathesisPrefixedCase(SchemathesisCase):
    """A collector that prefixes each test name with a schema label."""

    def __init__(
        self, test_function: Callable, schema: BaseSchema, schema_name: str, *args: Any, **kwargs: Any
    ) -> None:
        self._schema_name = schema_name
        super().__init__(test_function, schema, *args, **kwargs)

    def _get_test_name(self, operation: APIOperation) -> str:
        return f"{self.name}[{self._schema_name}][{operation.label}]"


class SchemathesisMultiCase(PyCollector):
    """A collector that produces test items from multiple named schemas."""

    def __init__(self, test_function: Callable, schemas: dict[str, BaseSchema], *args: Any, **kwargs: Any) -> None:
        self.test_function = test_function
        self.schemas = schemas
        super().__init__(*args, **kwargs)

    def collect(self) -> list[Function]:
        try:
            items = []
            # A collector created by pytest always has a parent.
            assert self.parent is not None
            for schema_name, schema in self.schemas.items():
                case = SchemathesisPrefixedCase.from_parent(
                    self.parent,
                    test_function=self.test_function,
                    name=self.name,
                    schema=schema,
                    schema_name=schema_name,
                )
                items.extend(case.collect())
            if not items:
                fail_on_no_matches(self.nodeid)
            return items
        except Exception:
            pytest.fail("Error during collection")


@hookimpl(hookwrapper=True)
def pytest_pycollect_makeitem(collector: Module | Class, name: str, obj: Any) -> Generator[None, Any, None]:
    """Switch to a different collector if the test is parametrized marked by schemathesis."""
    outcome = yield
    # Respect pytest's naming rules, e.g. a marked test imported under a non-test name is not collected again
    if not collector.istestfunction(obj, name):
        return
    try:
        schemas = MultiSchemaHandleMark.get(obj)
        if schemas is not None:
            outcome.force_result(
                SchemathesisMultiCase.from_parent(collector, test_function=obj, name=name, schemas=schemas)
            )
            return
        schema = SchemaHandleMark.get(obj)
        assert schema is not None
        outcome.force_result(SchemathesisCase.from_parent(collector, test_function=obj, name=name, schema=schema))
    except Exception:
        outcome.get_result()


@hookimpl(hookwrapper=True, trylast=True)
def pytest_report_teststatus(
    report: pytest.TestReport,
    config: pytest.Config,
) -> Generator[None, None, None]:
    outcome = yield
    result = cast(PluggyResult[tuple[str, str, str] | None], outcome)
    if not is_subtest_report(report):
        return
    r = result.get_result()
    if r is not None:
        category, short, verbose = r
        if isinstance(verbose, str):
            for prefix in ("SUBPASSED", "SUBFAILED", "SUBSKIPPED", "SUBXFAIL"):
                if verbose.startswith(prefix):
                    result.force_result((category, short, prefix))
                    break


def _strip_hypothesis_notes(excinfo: pytest.ExceptionInfo[BaseException], report: pytest.TestReport) -> None:
    if issubclass(excinfo.type, SchemathesisError) and hasattr(excinfo.value, "__notes__"):
        # Hypothesis adds quite a lot of additional debug information which is not that helpful in Schemathesis
        excinfo.value.__notes__.clear()
        report.longrepr = "".join(format_exception(excinfo.value))


def _dedupe_exception_group(excinfo: pytest.ExceptionInfo[BaseException], report: pytest.TestReport) -> None:
    if not isinstance(excinfo.value, BaseExceptionGroup):
        return
    # Use exception origin (type + traceback + context) as deduplication key
    origins: dict[tuple, BaseException] = {}
    for exc in excinfo.value.exceptions:
        origin = get_origin(exc)
        if origin not in origins:
            origins[origin] = exc

    if len(origins) < len(excinfo.value.exceptions):
        deduplicated = list(origins.values())
        message = excinfo.value.message.replace(
            f"{len(excinfo.value.exceptions)} distinct failures",
            f"{len(deduplicated)} distinct failures",
        )
        group = BaseExceptionGroup(message, deduplicated)
        report.longrepr = "".join(format_exception(group, with_traceback=True))


def _caller_frame_index(entries: Sequence[TracebackEntry]) -> int:
    # Keep internal Schemathesis frames + one extra one from the caller
    for index in range(len(entries) - 1, -1, -1):
        if not str(entries[index].path).endswith("schemathesis/generation/case.py"):
            return index
    return 0


@pytest.hookimpl(tryfirst=True)
def pytest_exception_interact(node: Function, call: pytest.CallInfo, report: pytest.TestReport) -> None:
    if call.excinfo:
        _strip_hypothesis_notes(call.excinfo, report)
        _dedupe_exception_group(call.excinfo, report)
        if issubclass(call.excinfo.type, FailureGroup):
            skip_frames = _caller_frame_index(call.excinfo.traceback)
            report.longrepr = "".join(
                format_exception(call.excinfo.value, with_traceback=True, skip_frames=skip_frames)
            )


@hookimpl(wrapper=True)
def pytest_pyfunc_call(pyfuncitem: pytest.Function) -> Generator[None, object, None]:
    """It is possible to have a Hypothesis exception in runtime.

    For example - kwargs validation is failed for some strategy.
    """
    __tracebackhide__ = True
    if isinstance(pyfuncitem, SchemathesisFunction):
        try:
            with ignore_hypothesis_output():
                yield
        except FailureGroup as exc:
            raise as_reported_failure(exc) from None
        except InvalidArgument as exc:
            raise _translate_invalid_argument(exc) from None
        except (SkipTest, unittest.SkipTest) as exc:
            _translate_skip(pyfuncitem, exc)
        except FailedHealthCheck as exc:
            operation = ApiOperationMark.get(pyfuncitem.obj)
            assert operation is not None
            raise build_health_check_error(operation, exc, with_tip=True) from None
        except Unsatisfiable:
            operation = ApiOperationMark.get(pyfuncitem.obj)
            assert operation is not None
            raise build_unsatisfiable_error(
                operation, with_tip=True, filter_tracker=operation.filter_case_tracker
            ) from None
        _raise_post_run_errors(pyfuncitem)
    else:
        yield


def _translate_invalid_argument(exc: InvalidArgument) -> SchemathesisError:
    if "Inconsistent args" in str(exc) and "@example()" in str(exc):
        return IncorrectUsage(GIVEN_AND_EXPLICIT_EXAMPLE_ERROR_MESSAGE)
    return InvalidSchema(exc.args[0])


def _translate_skip(pyfuncitem: pytest.Function, exc: SkipTest | unittest.SkipTest) -> NoReturn:
    __tracebackhide__ = True
    examples = ExamplesControllerMark.get(pyfuncitem.obj)
    if examples is not None and examples.deferred_errors:
        raise examples.deferred_errors[0] from None
    pytest.skip(exc.args[0])


def _raise_post_run_errors(pyfuncitem: pytest.Function) -> None:
    __tracebackhide__ = True
    # The operation ran, but part of it was left untested - a warning keeps the surviving cases passing.
    unserializable_payload = UnserializablePayloadMark.get(pyfuncitem.obj)
    if unserializable_payload is not None:
        emit_unserializable_payload_warning(unserializable_payload)

    examples = ExamplesControllerMark.get(pyfuncitem.obj)
    if examples is not None:
        for error in examples.deferred_errors:
            if isinstance(error, InvalidHeadersExample):
                raise error from None

    missing_path_parameters = MissingPathParameters.get(pyfuncitem.obj)
    if missing_path_parameters:
        raise missing_path_parameters from None


def _is_xdist_worker(config: pytest.Config) -> bool:
    return hasattr(config, "workerinput")


def pytest_configure(config: pytest.Config) -> None:
    config.stash[_CASSETTE_KEY] = {}
    if not HAS_CORE_SUBTESTS:
        config.pluginmanager.register(_subtests, "schemathesis-subtests")
    if config.pluginmanager.hasplugin("xdist"):
        from schemathesis.pytest.xdist import XdistReportingPlugin

        config.pluginmanager.register(XdistReportingPlugin(), "schemathesis-xdist")


def _has_reports(reports: ReportsConfig) -> bool:
    return any(report.enabled for report in (reports.vcr, reports.har, reports.junit, reports.allure, reports.html))


def _open_writers(
    schema: SchemaMetadata, pytest_config: pytest.Config
) -> list[VcrWriter | HarWriter | JunitXmlWriter | AllureWriter | HtmlWriter]:
    from schemathesis.reporting._command import get_command_representation
    from schemathesis.reporting.html.build import enabled_checks

    reports = schema.config.reports
    return open_writers(
        pytest_config,
        reports,
        reports.get_path,
        output=schema.config.output,
        seed=schema.config.seed,
        command=get_command_representation(schema.config.output.sanitization),
        api_title=schema.title,
        location=schema.location,
        base_url=schema.get_base_url() or None,
        checks=enabled_checks(schema.config.checks_config_for()),
        started_at=time.time(),
    )


def _writers_key(schema: SchemaMetadata) -> int:
    # Schemas cloned for each test share their report config, so it identifies whose reports a result belongs to.
    # The registered entry keeps that config alive, so its id cannot be reused while the entry exists.
    return id(schema.config)


def _ensure_writer_entry(
    config: pytest.Config, schema: SchemaMetadata
) -> tuple[PytestReportDispatcher, list[VcrWriter | HarWriter | JunitXmlWriter | AllureWriter | HtmlWriter]] | None:
    """The dispatcher and report writers shared by this schema's tests, created by the first test that needs them."""
    from schemathesis.pytest.reporting import PytestReportDispatcher

    entries = config.stash[_CASSETTE_KEY]
    schema_id = _writers_key(schema)
    entry = entries.get(schema_id)
    if entry is not None:
        return entry
    # Workers write no reports themselves; they collect results for the controller.
    writers = [] if _is_xdist_worker(config) else _open_writers(schema, config)
    if not writers and not _is_xdist_worker(config):
        return None
    entry = entries[schema_id] = (PytestReportDispatcher(schema), writers)
    return entry


def _register_allure_forwarder(
    label: str,
    writers: Sequence[VcrWriter | HarWriter | JunitXmlWriter | AllureWriter | HtmlWriter | AllureCallBuffer],
) -> AllureHookForwarder | None:
    import allure_commons

    from schemathesis.reporting.allure import AllureCallBuffer, AllureHookForwarder, AllureWriter

    allure_writers = [writer for writer in writers if isinstance(writer, (AllureWriter, AllureCallBuffer))]
    if not allure_writers:
        return None
    forwarder = AllureHookForwarder(label=label, writers=allure_writers)
    allure_commons.plugin_manager.register(forwarder)
    return forwarder


def _unregister_allure_forwarder(forwarder: AllureHookForwarder) -> None:
    import allure_commons

    allure_commons.plugin_manager.unregister(forwarder)


def _send_to_xdist_controller(
    config: pytest.Config,
    schema: SchemaMetadata,
    recorder: ScenarioRecorder,
    elapsed_sec: float,
    tags: list[str] | None = None,
    allure_calls: list[dict] | None = None,
    outcome: PytestReportOutcome | None = None,
    has_recorder: bool = True,
) -> None:
    from schemathesis.pytest.xdist import WORKER_OUTBOX_KEY, serialize_recorder

    config.stash[WORKER_OUTBOX_KEY].add(
        schema,
        serialize_recorder(
            recorder,
            elapsed_sec,
            tags=tags,
            allure_calls=allure_calls,
            status=outcome.status if outcome is not None else None,
            message=outcome.message if outcome is not None else None,
            has_recorder=has_recorder,
        ),
    )


def _get_pytest_report_outcome(report: pytest.TestReport, call: pytest.CallInfo) -> PytestReportOutcome:
    if report.passed:
        return PytestReportOutcome(Status.SUCCESS)
    if report.skipped:
        message = str(call.excinfo.value) if call.excinfo is not None else None
        return PytestReportOutcome(Status.SKIP, message)
    assert call.excinfo is not None
    exception = call.excinfo.value
    if isinstance(exception, (FailureGroup, AssertionError, pytest.fail.Exception)):
        return PytestReportOutcome(Status.FAILURE, str(exception))
    return PytestReportOutcome(Status.ERROR, f"{type(exception).__name__}: {exception}")


@hookimpl(wrapper=True)
def pytest_runtest_makereport(
    item: pytest.Item, call: pytest.CallInfo
) -> Generator[None, pytest.TestReport, pytest.TestReport]:
    report = yield
    if _SUBTEST_OUTCOME_KEY in item.stash:
        if call.when == "call":
            item.stash[_SUBTEST_OUTCOME_KEY] = _get_pytest_report_outcome(report, call)
        return report
    if not isinstance(item, SchemathesisFunction) or item.operation_label is None:
        return report
    if call.when != "call" and (call.when != "setup" or report.passed):
        return report

    item.stash[_REPORT_OUTCOME_KEY] = _get_pytest_report_outcome(report, call)
    return report


def _write_pytest_result(
    item: pytest.Item,
    schema: SchemaMetadata,
    label: str,
    tags: list[str] | None,
    dispatcher: PytestReportDispatcher,
    writers: list[VcrWriter | HarWriter | JunitXmlWriter | AllureWriter | HtmlWriter],
    outcome: PytestReportOutcome,
    allure_buffer: AllureCallBuffer | None,
) -> None:
    result = dispatcher.pop_recorder(label)
    has_recorder = result is not None
    if result is None:
        if outcome.status == Status.SUCCESS:
            return
        from schemathesis.engine.recorder import ScenarioRecorder

        recorder = ScenarioRecorder(label=label, config=schema.config.output)
        elapsed_sec = 0.0
    else:
        recorder, elapsed_sec = result
        if recorder.failing_case_ids():
            outcome = PytestReportOutcome(Status.FAILURE)
    if _is_xdist_worker(item.config):
        _send_to_xdist_controller(
            item.config,
            schema,
            recorder,
            elapsed_sec,
            tags=tags,
            allure_calls=allure_buffer.to_list() if allure_buffer is not None else None,
            outcome=outcome,
            has_recorder=has_recorder,
        )
    else:
        write_to_writers(
            writers,
            recorder,
            elapsed_sec,
            tags=tags,
            outcome=outcome,
            has_recorder=has_recorder,
        )


@contextmanager
def report_subtest(
    item: pytest.Item, schema: SchemaMetadata, label: str, tags: list[str] | None
) -> Generator[None, None, None]:
    reports = schema.config.reports
    entry = _ensure_writer_entry(item.config, schema) if _has_reports(reports) else None
    if entry is None:
        yield
        return
    dispatcher, writers = entry

    allure_buffer = None
    if _is_xdist_worker(item.config) and reports.allure.enabled:
        from schemathesis.reporting.allure import AllureCallBuffer

        allure_buffer = AllureCallBuffer()
        forwarder = _register_allure_forwarder(label, [allure_buffer])
    else:
        forwarder = _register_allure_forwarder(label, writers)
    item.stash[_SUBTEST_OUTCOME_KEY] = PytestReportOutcome(Status.SUCCESS)
    try:
        yield
    finally:
        outcome = item.stash[_SUBTEST_OUTCOME_KEY]
        del item.stash[_SUBTEST_OUTCOME_KEY]
        if forwarder is not None:
            _unregister_allure_forwarder(forwarder)
        _write_pytest_result(
            item,
            schema,
            label,
            tags,
            dispatcher,
            writers,
            outcome,
            allure_buffer,
        )


# Report writers must be attached before a `skip` marker raises during setup, or the skip is never reported.
@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item: pytest.Item) -> None:
    item_cls = getattr(item, "cls", None)
    if item_cls is not None and (schema := StatefulSchemaMark.get(item_cls)) is not None:
        _setup_stateful_test(item, item_cls, schema)
        return

    if not isinstance(item, SchemathesisFunction) or item.operation_label is None:
        return
    schema = SchemaHandleMark.get(item.test_function)
    if schema is not None:
        _setup_operation_reporting(item, schema, item.operation_label)


def _setup_operation_reporting(item: SchemathesisFunction, schema: BaseSchema, label: str) -> None:
    reports = schema.config.reports
    if not _has_reports(reports):
        return
    entry = _ensure_writer_entry(item.config, schema)
    if entry is None:
        return
    if _is_xdist_worker(item.config):
        if reports.allure.enabled:
            from schemathesis.reporting.allure import AllureCallBuffer

            buffer = AllureCallBuffer()
            forwarder = _register_allure_forwarder(label, [buffer])
            assert forwarder is not None
            item.stash[_ALLURE_BUFFER_KEY] = buffer
            item.stash[_ALLURE_FORWARDER_KEY] = forwarder
        return

    _, writers = entry
    forwarder = _register_allure_forwarder(label, writers)
    if forwarder is not None:
        item.stash[_ALLURE_FORWARDER_KEY] = forwarder


def _setup_stateful_test(item: pytest.Item, item_cls: type, schema: BaseSchema) -> None:
    track_schema(item.config, schema)
    # Attach a callback to the TestCase class so the state machine can hand off
    # its recorder to the report writers once the scenario finishes.
    if _is_xdist_worker(item.config):
        reports = schema.config.reports
        if _has_reports(reports):

            def _xdist_stateful_callback(recorder: ScenarioRecorder, elapsed_sec: float) -> None:
                _send_to_xdist_controller(item.config, schema, recorder, elapsed_sec)

            item.stash[_STATEFUL_WRITERS_KEY] = []
            StatefulCallbackMark.set(item_cls, _xdist_stateful_callback)
    else:
        writers = _open_writers(schema, item.config)
        item.stash[_STATEFUL_WRITERS_KEY] = writers
        if writers:

            def _stateful_callback(recorder: ScenarioRecorder, elapsed_sec: float) -> None:
                # Tags are not available for stateful tests — operations are discovered dynamically
                write_to_writers(writers, recorder, elapsed_sec)

            StatefulCallbackMark.set(item_cls, _stateful_callback)


def pytest_runtest_teardown(item: pytest.Item, nextitem: pytest.Item | None) -> None:
    # Applications reached over ASGI keep their lifespan running between requests; end it with the test that
    # started it, while its fixtures are still alive.
    try:
        # Only the ASGI client starts lifespans, so while it is not loaded there is nothing to end.
        if "schemathesis.python.asgi" in sys.modules:
            from schemathesis.python.asgi import shutdown_lifespans

            shutdown_lifespans()
    finally:
        _teardown_reporting(item)


def _teardown_reporting(item: pytest.Item) -> None:
    item_cls = getattr(item, "cls", None)
    if item_cls is not None and StatefulSchemaMark.is_set(item_cls):
        _teardown_stateful_test(item, item_cls)
        return
    if not isinstance(item, SchemathesisFunction) or item.operation_label is None:
        return
    _teardown_operation_reporting(item, item.operation_label)


def _teardown_stateful_test(item: pytest.Item, item_cls: type) -> None:
    StatefulCallbackMark.set(item_cls, None)
    for writer in item.stash.get(_STATEFUL_WRITERS_KEY, []):
        writer.close()


def _teardown_operation_reporting(item: SchemathesisFunction, label: str) -> None:
    forwarder = item.stash.get(_ALLURE_FORWARDER_KEY, None)
    if forwarder is not None:
        del item.stash[_ALLURE_FORWARDER_KEY]
        _unregister_allure_forwarder(forwarder)
    allure_buffer = item.stash.get(_ALLURE_BUFFER_KEY, None)
    if allure_buffer is not None:
        del item.stash[_ALLURE_BUFFER_KEY]
    schema = SchemaHandleMark.get(item.test_function)
    if schema is None:
        return
    cassettes = item.config.stash[_CASSETTE_KEY]
    if not cassettes:
        return
    entry = cassettes.get(_writers_key(schema))
    if entry is None:
        return
    dispatcher, writers = entry
    _write_pytest_result(
        item,
        schema,
        label,
        item.operation_tags,
        dispatcher,
        writers,
        item.stash.get(_REPORT_OUTCOME_KEY, PytestReportOutcome(Status.SUCCESS)),
        allure_buffer,
    )


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    for dispatcher, writers in session.config.stash.get(_CASSETTE_KEY, {}).values():
        dispatcher.unregister()
        for writer in writers:
            writer.close()
    _run_after_run_checks(session)


def pytest_terminal_summary(terminalreporter: TerminalReporter, config: pytest.Config) -> None:
    write_report_locations(terminalreporter, config)


def _run_after_run_checks(session: pytest.Session) -> None:
    outputs = _format_after_run_failures(session.config)
    if _is_xdist_worker(session.config):
        from schemathesis.pytest.xdist import SCHEMATHESIS_AFTER_RUN_FAILURES_KEY

        # Worker output never reaches the terminal; pytest-xdist hands `workeroutput` to the controller to report.
        session.config.workeroutput[SCHEMATHESIS_AFTER_RUN_FAILURES_KEY] = outputs  # type: ignore[attr-defined]
        return
    _report_after_run_failures(session, outputs + session.config.stash.get(_WORKER_AFTER_RUN_FAILURES_KEY, []))


def _format_after_run_failures(config: pytest.Config) -> list[str]:
    return [
        format_failures(case_id=None, response=None, failures=failures, curl=None, config=schema.config.output)
        for schema in config.stash.get(_PYTEST_SCHEMAS_KEY, {}).values()
        if (failures := collect_after_run_failures(schema.config, run_checks_for(schema)))
    ]


def _report_after_run_failures(session: pytest.Session, outputs: list[str]) -> None:
    # xdist workers that ran tests of the same schema often fail its checks identically.
    for output in dict.fromkeys(outputs):
        _write_after_run_failures(session, output)
    if outputs and session.exitstatus == pytest.ExitCode.OK:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED


def _write_after_run_failures(session: pytest.Session, output: str) -> None:
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    if reporter is not None:
        reporter.write_sep("=", RUN_CHECKS_LABEL, red=True)
        reporter.write_line(output, red=True)
    else:
        # No terminal reporter (`-p no:terminal`): surface on stderr so the exit code is explainable.
        print(RUN_CHECKS_LABEL, file=sys.stderr)  # noqa: T201
        print(output, file=sys.stderr)  # noqa: T201
