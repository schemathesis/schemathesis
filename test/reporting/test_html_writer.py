import pytest
import requests

from schemathesis.config import OutputConfig
from schemathesis.core.failures import Failure
from schemathesis.core.transport import Response
from schemathesis.engine import Status
from schemathesis.engine.recorder import ScenarioRecorder
from schemathesis.engine.run import PhaseName
from schemathesis.generation.stateful import STATEFUL_TESTS_LABEL
from schemathesis.reporting.html import HtmlReportWriter
from schemathesis.reporting.recorders import grouped_failures_from_recorder


def make_recorder(case_factory, response_factory, *, label="GET /users", fail=False):
    recorder = ScenarioRecorder(label=label)
    case = case_factory()
    recorder.record_case(parent_id=None, case=case, transition=None, is_transition_applied=False)
    # `response_factory.requests` returns a raw `requests.Response`; the recorder (and our writer)
    # expect the wire-normalized `schemathesis.core.transport.Response`, same as every real caller.
    response = Response.from_requests(response_factory.requests(status_code=500 if fail else 200), verify=True)
    recorder.record_response(case_id=case.id, response=response)
    if fail:
        recorder.record_check_failure(
            name="server_error",
            case_id=case.id,
            code_sample=f"curl -X GET http://127.0.0.1/users?case={case.id}",
            failure=Failure(operation=label, title="Server error", message="boom"),
        )
    else:
        recorder.record_check_success(name="not_a_server_error", case_id=case.id)
    return recorder


def run_writer(tmp_path, recorders, *, phases=True):
    writer = HtmlReportWriter(tmp_path / "report", OutputConfig())
    writer.set_meta(location="openapi.yaml", base_url="http://127.0.0.1", command="st run", seed=1)
    if phases:
        writer.record_phase_started(PhaseName.FUZZING, at=100.0)
    for index, recorder in enumerate(recorders):
        failures = grouped_failures_from_recorder(recorder)
        writer.record_scenario(
            label=recorder.label,
            elapsed_sec=1.0,
            status=Status.FAILURE if failures else Status.SUCCESS,
            phase=PhaseName.FUZZING,
            recorder=recorder,
            failures=failures,
            skip_reason=None,
            at=101.0 + index,
        )
    if phases:
        writer.record_phase_finished(PhaseName.FUZZING, at=110.0)
        writer.set_run_summary(running_time=10.0, stop_reason=None)
    writer.close()
    return tmp_path / "report"


def test_writer_produces_directory_layout(tmp_path, case_factory, response_factory):
    output = run_writer(tmp_path, [make_recorder(case_factory, response_factory, fail=True)])
    assert (output / "index.html").is_file()
    assert (output / "assets" / "report.css").is_file()
    assert (output / "assets" / "app.js").is_file()
    operation_pages = list((output / "operations").glob("*.html"))
    assert len(operation_pages) == 1
    index = (output / "index.html").read_text()
    assert f'href="operations/{operation_pages[0].name}"' in index


def test_writer_operation_page_contains_failure(tmp_path, case_factory, response_factory):
    output = run_writer(tmp_path, [make_recorder(case_factory, response_factory, fail=True)])
    page = next((output / "operations").glob("*.html")).read_text()
    assert "server_error" in page
    assert "boom" in page
    assert 'aria-label="Copy request"' in page


def test_writer_merges_scenarios_for_same_label(tmp_path, case_factory, response_factory):
    recorders = [
        make_recorder(case_factory, response_factory, fail=True),
        make_recorder(case_factory, response_factory, fail=True),
    ]
    output = run_writer(tmp_path, recorders)
    assert len(list((output / "operations").glob("*.html"))) == 1
    index = (output / "index.html").read_text()
    # Two cases total across both scenarios.
    assert '<td class="numeric">2</td>' in index


def test_writer_passed_operation_gets_pass_banner(tmp_path, case_factory, response_factory):
    output = run_writer(tmp_path, [make_recorder(case_factory, response_factory, fail=False)])
    assert "pass-banner" in next((output / "operations").glob("*.html")).read_text()


def test_writer_dedups_identical_curl_samples(tmp_path, case_factory, response_factory):
    recorder = ScenarioRecorder(label="GET /users")
    for _ in range(2):
        case = case_factory()
        recorder.record_case(parent_id=None, case=case, transition=None, is_transition_applied=False)
        response = Response.from_requests(response_factory.requests(status_code=500), verify=True)
        recorder.record_response(case_id=case.id, response=response)
        recorder.record_check_failure(
            name="server_error",
            case_id=case.id,
            code_sample="curl -X GET http://127.0.0.1/users",
            failure=Failure(operation="GET /users", title="Server error", message="boom"),
        )
    output = run_writer(tmp_path, [recorder])
    page = next((output / "operations").glob("*.html")).read_text()
    assert page.count('class="case-card"') == 1


@pytest.mark.parametrize(
    "statuses, expected_status",
    [
        ([Status.SUCCESS, Status.FAILURE], "failed"),
        ([Status.FAILURE, Status.SUCCESS], "failed"),
        ([Status.SKIP, Status.SUCCESS], "passed"),
        ([Status.SUCCESS, Status.SKIP], "passed"),
    ],
    ids=["success-then-failure", "failure-then-success", "skip-then-success", "success-then-skip"],
)
def test_writer_status_merge_priority(tmp_path, case_factory, response_factory, statuses, expected_status):
    writer = HtmlReportWriter(tmp_path / "report", OutputConfig())
    for status in statuses:
        recorder = make_recorder(case_factory, response_factory, fail=status == Status.FAILURE)
        failures = grouped_failures_from_recorder(recorder)
        writer.record_scenario(
            label="GET /users",
            elapsed_sec=1.0,
            status=status,
            phase=PhaseName.FUZZING,
            recorder=recorder,
            failures=failures,
            skip_reason="unreachable" if status == Status.SKIP else None,
            at=1.0,
        )
    writer.close()
    index = (tmp_path / "report" / "index.html").read_text()
    assert f'data-status="{expected_status}"' in index


@pytest.mark.parametrize("status", [Status.ERROR, Status.INTERRUPTED], ids=["error", "interrupted"])
def test_writer_error_and_interrupted_status_surface_as_failed(tmp_path, case_factory, response_factory, status):
    writer = HtmlReportWriter(tmp_path / "report", OutputConfig())
    recorder = make_recorder(case_factory, response_factory, fail=True)
    writer.record_scenario(
        label="GET /users",
        elapsed_sec=1.0,
        status=status,
        phase=PhaseName.FUZZING,
        recorder=recorder,
        failures=grouped_failures_from_recorder(recorder),
        skip_reason=None,
        at=1.0,
    )
    writer.close()
    index = (tmp_path / "report" / "index.html").read_text()
    # Without normalizing ERROR/INTERRUPTED toward an index group, this operation (with real
    # failing checks) would vanish from every group in the table instead of surfacing as failed.
    assert 'data-status="failed"' in index
    assert len(list((tmp_path / "report" / "operations").glob("*.html"))) == 1


def test_writer_tracks_cases_per_phase(tmp_path, case_factory, response_factory):
    writer = HtmlReportWriter(tmp_path / "report", OutputConfig())
    passing = make_recorder(case_factory, response_factory, fail=False)
    failing = make_recorder(case_factory, response_factory, fail=True)
    writer.record_scenario(
        label="GET /users",
        elapsed_sec=1.0,
        status=Status.SUCCESS,
        phase=PhaseName.EXAMPLES,
        recorder=passing,
        failures=[],
        skip_reason=None,
        at=1.0,
    )
    writer.record_scenario(
        label="GET /users",
        elapsed_sec=1.0,
        status=Status.FAILURE,
        phase=PhaseName.FUZZING,
        recorder=failing,
        failures=grouped_failures_from_recorder(failing),
        skip_reason=None,
        at=2.0,
    )
    writer.close()
    page = next((tmp_path / "report" / "operations").glob("*.html")).read_text()
    assert "1/1 cases" in page
    assert "0/1 cases" in page


def test_writer_stateful_aggregate_label_counts_all_cases(tmp_path, ctx, case_factory, response_factory):
    # Stateful cases carry per-operation labels, not the recorder's aggregate "Stateful tests"
    # label; counting must fall back to all recorder cases for that aggregate label.
    schema = ctx.openapi.load_schema(
        {
            "/users": {"get": {"responses": {"200": {"description": "OK"}}}},
            "/orders": {"get": {"responses": {"200": {"description": "OK"}}}},
        }
    )
    recorder = ScenarioRecorder(label=STATEFUL_TESTS_LABEL)
    for path in ("/users", "/orders"):
        case = case_factory(operation=schema[path]["GET"])
        recorder.record_case(parent_id=None, case=case, transition=None, is_transition_applied=False)
        response = Response.from_requests(response_factory.requests(status_code=200), verify=True)
        recorder.record_response(case_id=case.id, response=response)
        recorder.record_check_success(name="not_a_server_error", case_id=case.id)
    writer = HtmlReportWriter(tmp_path / "report", OutputConfig())
    writer.record_scenario(
        label=STATEFUL_TESTS_LABEL,
        elapsed_sec=1.0,
        status=Status.SUCCESS,
        phase=PhaseName.STATEFUL_TESTING,
        recorder=recorder,
        failures=[],
        skip_reason=None,
        at=1.0,
    )
    writer.close()
    index = (tmp_path / "report" / "index.html").read_text()
    assert '<td class="numeric">2</td>' in index


def test_writer_ticks_accumulate_only_for_new_check_pairs(tmp_path, case_factory, response_factory):
    recorders = [
        make_recorder(case_factory, response_factory, fail=True),
        make_recorder(case_factory, response_factory, fail=True),
    ]
    output = run_writer(tmp_path, recorders)
    index = (output / "index.html").read_text()
    # Both scenarios fail the same check on the same label -> only the first is a new pair.
    assert index.count('class="rt-tick-group') == 1


def test_writer_new_check_name_adds_new_tick(tmp_path, case_factory, response_factory):
    writer = HtmlReportWriter(tmp_path / "report", OutputConfig())
    writer.record_phase_started(PhaseName.FUZZING, at=100.0)
    for index, name in enumerate(["server_error", "not_a_server_error"]):
        recorder = ScenarioRecorder(label="GET /users")
        case = case_factory()
        recorder.record_case(parent_id=None, case=case, transition=None, is_transition_applied=False)
        response = Response.from_requests(response_factory.requests(status_code=500), verify=True)
        recorder.record_response(case_id=case.id, response=response)
        recorder.record_check_failure(
            name=name,
            case_id=case.id,
            code_sample=f"curl {index}",
            failure=Failure(operation="GET /users", title="t", message="m"),
        )
        writer.record_scenario(
            label="GET /users",
            elapsed_sec=1.0,
            status=Status.FAILURE,
            phase=PhaseName.FUZZING,
            recorder=recorder,
            failures=grouped_failures_from_recorder(recorder),
            skip_reason=None,
            at=101.0 + index,
        )
    writer.record_phase_finished(PhaseName.FUZZING, at=110.0)
    writer.set_run_summary(running_time=10.0, stop_reason=None)
    writer.close()
    index_html = (tmp_path / "report" / "index.html").read_text()
    assert index_html.count('class="rt-tick-group') == 2


def test_writer_tolerates_unknown_response_charset(tmp_path, case_factory, response_factory):
    recorder = ScenarioRecorder(label="GET /users")
    case = case_factory()
    recorder.record_case(parent_id=None, case=case, transition=None, is_transition_applied=False)
    # A bogus charset in Content-Type propagates into `Response.encoding`; decoding must not crash close().
    raw_response = response_factory.requests(
        status_code=500, content_type="text/plain; charset=bogus-xyz", content=b"oops"
    )
    # The transport adapter derives `encoding` from the Content-Type charset; the factory skips that step.
    raw_response.encoding = requests.utils.get_encoding_from_headers(raw_response.headers)
    response = Response.from_requests(raw_response, verify=True)
    recorder.record_response(case_id=case.id, response=response)
    recorder.record_check_failure(
        name="server_error",
        case_id=case.id,
        code_sample="curl -X GET http://127.0.0.1/users",
        failure=Failure(operation="GET /users", title="Server error", message="boom"),
    )
    output = run_writer(tmp_path, [recorder])
    page = next((output / "operations").glob("*.html")).read_text()
    assert "binary or empty body" in page


def test_writer_tolerates_undefined_response_charset(tmp_path, case_factory, response_factory):
    recorder = ScenarioRecorder(label="GET /users")
    case = case_factory()
    recorder.record_case(parent_id=None, case=case, transition=None, is_transition_applied=False)
    # The "undefined" charset resolves to a real codec whose decode raises bare `UnicodeError`,
    # not `UnicodeDecodeError`/`LookupError`; decoding must not crash close().
    raw_response = response_factory.requests(
        status_code=500, content_type="text/plain; charset=undefined", content=b"oops"
    )
    raw_response.encoding = requests.utils.get_encoding_from_headers(raw_response.headers)
    response = Response.from_requests(raw_response, verify=True)
    recorder.record_response(case_id=case.id, response=response)
    recorder.record_check_failure(
        name="server_error",
        case_id=case.id,
        code_sample="curl -X GET http://127.0.0.1/users",
        failure=Failure(operation="GET /users", title="Server error", message="boom"),
    )
    output = run_writer(tmp_path, [recorder])
    page = next((output / "operations").glob("*.html")).read_text()
    assert "binary or empty body" in page


def test_writer_record_error_for_known_operation_appears_on_page_and_index_gutter(
    tmp_path, case_factory, response_factory
):
    writer = HtmlReportWriter(tmp_path / "report", OutputConfig())
    writer.record_scenario(
        label="GET /users",
        elapsed_sec=1.0,
        status=Status.SUCCESS,
        phase=PhaseName.FUZZING,
        recorder=make_recorder(case_factory, response_factory, fail=False),
        failures=[],
        skip_reason=None,
        at=1.0,
    )
    writer.record_error(
        label="GET /users",
        title="Flaky test",
        message="Hypothesis found inconsistent responses",
        traceback="Traceback (most recent call last):\n  ...\nValueError: boom",
        phase="fuzzing",
    )
    writer.close()
    output = tmp_path / "report"
    index = (output / "index.html").read_text()
    assert 'class="gutter-note err"' in index
    assert "1 non-fatal error" in index
    page = next((output / "operations").glob("*.html")).read_text()
    assert "Flaky test" in page
    assert "Hypothesis found inconsistent responses" in page
    assert "Show traceback" in page
    assert "boom" in page


def test_writer_record_error_with_no_operation_appears_in_orphan_errors_section(tmp_path):
    writer = HtmlReportWriter(tmp_path / "report", OutputConfig())
    writer.record_error(
        label="schema-analysis",
        title="Schema warning",
        message="Could not resolve $ref",
        traceback=None,
        phase=None,
    )
    writer.close()
    output = tmp_path / "report"
    index = (output / "index.html").read_text()
    assert 'class="section errors-section"' in index
    assert "Schema warning" in index
    assert "Could not resolve $ref" in index
    assert not list((output / "operations").glob("*.html"))


def test_writer_error_with_no_checks_has_no_pass_banner(tmp_path):
    writer = HtmlReportWriter(tmp_path / "report", OutputConfig())
    writer.record_scenario(
        label="GET /users",
        elapsed_sec=0.0,
        status=Status.ERROR,
        phase=PhaseName.FUZZING,
        recorder=ScenarioRecorder(label="GET /users"),
        failures=[],
        skip_reason=None,
        at=1.0,
    )
    writer.close()
    page = next((tmp_path / "report" / "operations").glob("*.html")).read_text()
    assert "pass-banner" not in page
    assert "No check failures recorded" in page


def test_writer_closes_open_phase_at_run_end_for_interrupted_run(tmp_path, case_factory, response_factory):
    # Ctrl-C mid-fuzzing leaves the current phase with a start but no finish; without a
    # synthetic end it vanishes from the timeline and the "across N phases" count.
    writer = HtmlReportWriter(tmp_path / "report", OutputConfig())
    writer.record_phase_started(PhaseName.EXAMPLES, at=100.0)
    writer.record_phase_finished(PhaseName.EXAMPLES, at=110.0)
    writer.record_phase_started(PhaseName.FUZZING, at=110.0)
    recorder = make_recorder(case_factory, response_factory, fail=True)
    writer.record_scenario(
        label=recorder.label,
        elapsed_sec=1.0,
        status=Status.FAILURE,
        phase=PhaseName.FUZZING,
        recorder=recorder,
        failures=grouped_failures_from_recorder(recorder),
        skip_reason=None,
        at=115.0,
    )
    writer.set_run_summary(running_time=15.0, stop_reason="interrupted")
    writer.close()
    index = (tmp_path / "report" / "index.html").read_text()
    assert index.count('class="rt-phase ') == 2


def test_writer_removes_stale_operation_pages_from_previous_run(tmp_path, case_factory, response_factory):
    output_dir = tmp_path / "report"
    first = HtmlReportWriter(output_dir, OutputConfig())
    first.record_scenario(
        label="GET /users",
        elapsed_sec=1.0,
        status=Status.SUCCESS,
        phase=PhaseName.FUZZING,
        recorder=make_recorder(case_factory, response_factory, fail=False),
        failures=[],
        skip_reason=None,
        at=1.0,
    )
    first.close()
    assert {page.name for page in (output_dir / "operations").glob("*.html")} == {"GET__users.html"}

    second = HtmlReportWriter(output_dir, OutputConfig())
    second.record_scenario(
        label="GET /orders",
        elapsed_sec=1.0,
        status=Status.SUCCESS,
        phase=PhaseName.FUZZING,
        recorder=make_recorder(case_factory, response_factory, label="GET /orders", fail=False),
        failures=[],
        skip_reason=None,
        at=1.0,
    )
    second.close()
    assert {page.name for page in (output_dir / "operations").glob("*.html")} == {"GET__orders.html"}
    assert (output_dir / "index.html").is_file()
    assert (output_dir / "assets" / "report.css").is_file()


def test_writer_handles_mixed_statuses_without_crashing(tmp_path, case_factory, response_factory):
    writer = HtmlReportWriter(tmp_path / "report", OutputConfig())
    writer.record_scenario(
        label="GET /users",
        elapsed_sec=1.0,
        status=Status.SUCCESS,
        phase=PhaseName.FUZZING,
        recorder=make_recorder(case_factory, response_factory, fail=False),
        failures=[],
        skip_reason=None,
        at=1.0,
    )
    writer.record_scenario(
        label="DELETE /users/{id}",
        elapsed_sec=0.0,
        status=Status.SKIP,
        phase=PhaseName.FUZZING,
        recorder=ScenarioRecorder(label="DELETE /users/{id}"),
        failures=[],
        skip_reason="unsupported media type",
        at=2.0,
    )
    writer.close()
    output = tmp_path / "report"
    index = (output / "index.html").read_text()
    assert "unsupported media type" in index
    assert len(list((output / "operations").glob("*.html"))) == 1


def test_writer_preserves_foreign_files_on_first_write(tmp_path, case_factory, response_factory):
    # Pointing --report-html-path at a directory that already holds an operations/ subtree must not
    # delete pre-existing files on the first write into it.
    output_dir = tmp_path / "report"
    (output_dir / "operations").mkdir(parents=True)
    (output_dir / "index.html").write_text("foreign site")
    foreign = output_dir / "operations" / "keep-me.html"
    foreign.write_text("user content")
    writer = HtmlReportWriter(output_dir, OutputConfig())
    writer.record_scenario(
        label="GET /users",
        elapsed_sec=1.0,
        status=Status.SUCCESS,
        phase=PhaseName.FUZZING,
        recorder=make_recorder(case_factory, response_factory, fail=False),
        failures=[],
        skip_reason=None,
        at=1.0,
    )
    writer.close()
    assert foreign.read_text() == "user content"


def test_writer_tolerates_dangling_parent_id(tmp_path, case_factory, response_factory):
    # A failing case whose parent chain references a case absent from the recorder must not crash
    # report generation; recorder state is normally consistent, but a partial one must degrade.
    recorder = ScenarioRecorder(label="POST /orders")
    case = case_factory()
    recorder.record_case(parent_id="missing-parent", case=case, transition=None, is_transition_applied=False)
    response = Response.from_requests(response_factory.requests(status_code=500), verify=True)
    recorder.record_response(case_id=case.id, response=response)
    recorder.record_check_failure(
        name="server_error",
        case_id=case.id,
        code_sample="curl -X POST http://127.0.0.1/orders",
        failure=Failure(operation="POST /orders", title="Server error", message="boom"),
    )
    output = run_writer(tmp_path, [recorder])
    assert "server_error" in next((output / "operations").glob("*.html")).read_text()


def test_writer_tolerates_embedded_null_charset(tmp_path, case_factory, response_factory):
    # A charset with an embedded NUL makes bytes.decode raise a bare ValueError, not UnicodeError/LookupError.
    recorder = ScenarioRecorder(label="GET /users")
    case = case_factory()
    recorder.record_case(parent_id=None, case=case, transition=None, is_transition_applied=False)
    raw_response = response_factory.requests(status_code=500, content=b"oops")
    raw_response.encoding = "utf-8\x00"
    response = Response.from_requests(raw_response, verify=True)
    recorder.record_response(case_id=case.id, response=response)
    recorder.record_check_failure(
        name="server_error",
        case_id=case.id,
        code_sample="curl -X GET http://127.0.0.1/users",
        failure=Failure(operation="GET /users", title="Server error", message="boom"),
    )
    output = run_writer(tmp_path, [recorder])
    page = next((output / "operations").glob("*.html")).read_text()
    assert "binary or empty body" in page


def test_writer_error_on_skipped_operation_still_surfaces(tmp_path):
    writer = HtmlReportWriter(tmp_path / "report", OutputConfig())
    writer.record_scenario(
        label="GET /users",
        elapsed_sec=0.0,
        status=Status.SKIP,
        phase=PhaseName.FUZZING,
        recorder=ScenarioRecorder(label="GET /users"),
        failures=[],
        skip_reason="unsatisfiable",
        at=1.0,
    )
    writer.record_error(
        label="GET /users",
        title="Generation error",
        message="cannot satisfy schema",
        traceback=None,
        phase="fuzzing",
    )
    writer.close()
    index = (tmp_path / "report" / "index.html").read_text()
    # A skipped operation gets no page, so its error must still appear in the run-level errors section.
    assert "Generation error" in index
    assert "cannot satisfy schema" in index


def test_writer_keeps_distinct_checks_sharing_a_curl(tmp_path, case_factory, response_factory):
    recorder = ScenarioRecorder(label="GET /users")
    for name in ("server_error", "status_code_conformance"):
        case = case_factory()
        recorder.record_case(parent_id=None, case=case, transition=None, is_transition_applied=False)
        response = Response.from_requests(response_factory.requests(status_code=500), verify=True)
        recorder.record_response(case_id=case.id, response=response)
        recorder.record_check_failure(
            name=name,
            case_id=case.id,
            code_sample="curl -X GET http://127.0.0.1/users",
            failure=Failure(operation="GET /users", title=name, message=name),
        )
    output = run_writer(tmp_path, [recorder])
    page = next((output / "operations").glob("*.html")).read_text()
    # Same request, different failing checks: neither may be dropped by curl-based dedup.
    assert "server_error" in page
    assert "status_code_conformance" in page


def test_writer_omits_missing_response_reason(tmp_path, case_factory, response_factory):
    recorder = ScenarioRecorder(label="GET /users")
    case = case_factory()
    recorder.record_case(parent_id=None, case=case, transition=None, is_transition_applied=False)
    raw_response = response_factory.requests(status_code=500)
    raw_response.reason = None
    response = Response.from_requests(raw_response, verify=True)
    recorder.record_response(case_id=case.id, response=response)
    recorder.record_check_failure(
        name="server_error",
        case_id=case.id,
        code_sample="curl -X GET http://127.0.0.1/users",
        failure=Failure(operation="GET /users", title="Server error", message="boom"),
    )
    output = run_writer(tmp_path, [recorder])
    page = next((output / "operations").glob("*.html")).read_text()
    assert "500 None" not in page


def test_writer_tolerates_cyclic_parent_id(tmp_path, case_factory, response_factory):
    # A parent_id cycle must not spin report generation forever.
    recorder = ScenarioRecorder(label="POST /orders")
    case = case_factory()
    recorder.record_case(parent_id=case.id, case=case, transition=None, is_transition_applied=False)
    response = Response.from_requests(response_factory.requests(status_code=500), verify=True)
    recorder.record_response(case_id=case.id, response=response)
    recorder.record_check_failure(
        name="server_error",
        case_id=case.id,
        code_sample="curl -X POST http://127.0.0.1/orders",
        failure=Failure(operation="POST /orders", title="Server error", message="boom"),
    )
    output = run_writer(tmp_path, [recorder])
    assert "server_error" in next((output / "operations").glob("*.html")).read_text()
