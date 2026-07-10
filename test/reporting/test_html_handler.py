import datetime
import uuid

from schemathesis.cli.commands.fuzz.context import FuzzExecutionContext
from schemathesis.cli.commands.run.handlers.html import HtmlReportHandler
from schemathesis.cli.context import BaseExecutionContext
from schemathesis.config import SchemathesisConfig
from schemathesis.core.failures import ServerError
from schemathesis.core.transport import Response
from schemathesis.engine import Status, events
from schemathesis.engine.fuzz._executor import FUZZ_TESTS_LABEL
from schemathesis.engine.recorder import ScenarioRecorder
from schemathesis.engine.run import PhaseName


def read_report(directory):
    index = (directory / "index.html").read_text()
    pages = {page.name: page.read_text() for page in (directory / "operations").glob("*.html")}
    return index, pages


def run_fuzz_scenario(tmp_path, recorder, *, status=Status.FAILURE, elapsed_time=1.0):
    event = events.FuzzScenarioFinished(
        id=uuid.uuid4(), suite_id=uuid.uuid4(), worker_id=0, recorder=recorder, status=status, elapsed_time=elapsed_time
    )
    config = SchemathesisConfig().projects.get_default()
    execution_context = FuzzExecutionContext(config=config)
    execution_context.on_event(event)
    handler = HtmlReportHandler(output_dir=tmp_path / "report", config=config)
    handler.handle_event(execution_context, event)
    handler.shutdown(execution_context)
    return read_report(tmp_path / "report")


def test_html_report_fuzz_per_operation_status_is_isolated(ctx, tmp_path, response_factory):
    # A single fuzz scenario spans multiple operations; a failure in one must not mark every
    # other operation in the same scenario as failed too.
    schema = ctx.openapi.load_schema(
        {
            "/users": {"get": {"responses": {"200": {"description": "OK"}}}},
            "/orders": {"get": {"responses": {"200": {"description": "OK"}}}},
        }
    )
    recorder = ScenarioRecorder(label=FUZZ_TESTS_LABEL)
    for path, fail in (("/users", True), ("/orders", False)):
        operation = schema[path]["GET"]
        case = operation.Case()
        recorder.record_case(parent_id=None, case=case, transition=None, is_transition_applied=False)
        raw_response = response_factory.requests(status_code=500 if fail else 200, content_type="application/json")
        response = Response.from_requests(raw_response, verify=True)
        recorder.record_response(case_id=case.id, response=response)
        if fail:
            recorder.record_check_failure(
                name="not_a_server_error",
                case_id=case.id,
                code_sample=f"curl http://127.0.0.1{path}",
                failure=ServerError(operation=operation.label, status_code=500, case_id=case.id),
            )
        else:
            recorder.record_check_success(name="not_a_server_error", case_id=case.id)

    _, pages = run_fuzz_scenario(tmp_path, recorder)
    assert "pass-banner" in pages["GET__orders.html"]
    assert "case-card" in pages["GET__users.html"]


def test_html_report_fuzz_per_operation_elapsed_excludes_other_operations(ctx, tmp_path, response_factory):
    # `event.elapsed_time` covers the whole multi-operation scenario; each operation's page must
    # show only the time spent on its own interactions, not the scenario total.
    schema = ctx.openapi.load_schema(
        {
            "/users": {"get": {"responses": {"200": {"description": "OK"}}}},
            "/orders": {"get": {"responses": {"200": {"description": "OK"}}}},
        }
    )
    recorder = ScenarioRecorder(label=FUZZ_TESTS_LABEL)
    for path, elapsed_seconds in (("/users", 0.1), ("/orders", 5.0)):
        operation = schema[path]["GET"]
        case = operation.Case()
        recorder.record_case(parent_id=None, case=case, transition=None, is_transition_applied=False)
        raw_response = response_factory.requests(status_code=200, content_type="application/json")
        raw_response.elapsed = datetime.timedelta(seconds=elapsed_seconds)
        response = Response.from_requests(raw_response, verify=True)
        recorder.record_response(case_id=case.id, response=response)
        recorder.record_check_success(name="not_a_server_error", case_id=case.id)

    _, pages = run_fuzz_scenario(tmp_path, recorder, status=Status.SUCCESS, elapsed_time=100.0)
    assert "0.1s" in pages["GET__users.html"]
    assert "5.0s" in pages["GET__orders.html"]
    assert "1m 40s" not in pages["GET__users.html"]
    assert "1m 40s" not in pages["GET__orders.html"]


def test_html_report_handler_definition_lands_on_page_across_repeated_events(
    ctx, tmp_path, case_factory, response_factory
):
    # Each scenario event for a label re-fetches the operation and re-serializes its raw
    # definition; caching that per label must not change what ends up on the page.
    schema = ctx.openapi.load_schema(
        {"/users": {"get": {"summary": "Fetch users", "responses": {"200": {"description": "OK"}}}}}
    )
    operation = schema["/users"]["GET"]
    config = SchemathesisConfig().projects.get_default()
    execution_context = BaseExecutionContext(config=config, find_operation_by_label=schema.find_operation_by_label)
    handler = HtmlReportHandler(output_dir=tmp_path / "report", config=config)
    for phase in (PhaseName.EXAMPLES, PhaseName.FUZZING):
        case = case_factory(operation=operation)
        recorder = ScenarioRecorder(label=operation.label)
        recorder.record_case(parent_id=None, case=case, transition=None, is_transition_applied=False)
        response = Response.from_requests(response_factory.requests(status_code=200), verify=True)
        recorder.record_response(case_id=case.id, response=response)
        recorder.record_check_success(name="not_a_server_error", case_id=case.id)
        event = events.ScenarioFinished(
            id=uuid.uuid4(),
            suite_id=uuid.uuid4(),
            phase=phase,
            label=operation.label,
            status=Status.SUCCESS,
            recorder=recorder,
            elapsed_time=0.1,
            skip_reason=None,
            is_final=False,
        )
        handler.handle_event(execution_context, event)
    handler.shutdown(execution_context)
    page = next((tmp_path / "report" / "operations").glob("*.html")).read_text()
    assert "Fetch users" in page
    assert "schema-details" in page
