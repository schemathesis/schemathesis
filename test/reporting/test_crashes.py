from __future__ import annotations

import json

import requests

from schemathesis.config import SanitizationConfig
from schemathesis.core.failures import Failure, ResponseTimeExceeded
from schemathesis.core.result import Ok
from schemathesis.core.transport import Response
from schemathesis.engine.recorder import ScenarioRecorder
from schemathesis.generation.stateful.state_machine import ExtractedParam, Transition
from schemathesis.openapi.checks import UseAfterFree
from schemathesis.reporting.crashes import (
    MANIFEST_FILENAME,
    CrashCheck,
    CrashFile,
    CrashStep,
    CrashWriter,
    build_crashes_from_recorder,
    load_manifest,
)


def _failure() -> Failure:
    return Failure(operation="GET /users", title="Server error", message="boom")


def _recorder_with_failure(
    case_factory,
    *,
    label: str = "GET /users",
    encoding: str | None = None,
    failure: Failure | None = None,
    check_name: str = "not_a_server_error",
    status: int = 500,
) -> ScenarioRecorder:
    recorder = ScenarioRecorder(label=label)
    case = case_factory()
    recorder.record_case(parent_id=None, case=case, transition=None, is_transition_applied=False)
    prepared = requests.Request(method="GET", url="http://127.0.0.1/users").prepare()
    response = Response(
        status_code=status,
        headers={"content-type": ["application/json"]},
        content=b'{"error": "boom"}',
        request=prepared,
        elapsed=0.1,
        verify=False,
        encoding=encoding,
    )
    recorder.record_response(case_id=case.id, response=response)
    recorder.record_check_failure(name=check_name, case_id=case.id, code_sample="curl x", failure=failure or _failure())
    return recorder


def _step(**overrides) -> CrashStep:
    defaults = {
        "method": "GET",
        "url": "http://x/users",
        "url_template": "/users",
        "request_headers": {},
        "response_status": 500,
        "response_headers": {},
        "response_body": "{}",
        "link": None,
        "checks": [CrashCheck(name="not_a_server_error", status="failure", message="boom")],
        "meta": None,
    }
    defaults.update(overrides)
    return CrashStep(**defaults)


def _crash(*, operation: str, fingerprint: str, case_id: str = "c1") -> CrashFile:
    return CrashFile(
        operation=operation,
        method="GET",
        path_template="/x",
        fingerprint=fingerprint,
        case_id=case_id,
        code_sample="curl x",
        sequence=[_step()],
    )


def test_build_crashes_skips_scenario_with_missing_interaction(case_factory):
    # A case with no recorded response (e.g. a network error mid-scenario) is skipped, not fatal.
    recorder = ScenarioRecorder(label="GET /users")
    case = case_factory()
    recorder.record_case(parent_id=None, case=case, transition=None, is_transition_applied=False)
    recorder.record_check_failure(name="not_a_server_error", case_id=case.id, code_sample="curl x", failure=_failure())

    crashes = build_crashes_from_recorder(recorder=recorder, failing_case_id=case.id, sanitization=SanitizationConfig())

    assert crashes == []


def test_build_crashes_captures_request_body_link(case_factory):
    # When a link feeds a later request body, the crash records its definition so it can be re-extracted on replay.
    recorder = ScenarioRecorder(label="GET /a -> PATCH /b")
    parent = case_factory()
    child = case_factory()
    recorder.record_case(parent_id=None, case=parent, transition=None, is_transition_applied=False)
    transition = Transition(
        id="GET /a -> [200] Update -> PATCH /b",
        parent_id=parent.id,
        is_inferred=False,
        parameters={},
        request_body=ExtractedParam(
            definition={"token": "$response.body#/token"}, value=Ok({"token": "live"}), is_required=True
        ),
    )
    recorder.record_case(parent_id=parent.id, case=child, transition=transition, is_transition_applied=True)
    for case in (parent, child):
        prepared = requests.Request(method="GET", url="http://127.0.0.1/a").prepare()
        recorder.record_response(
            case_id=case.id,
            response=Response(
                status_code=200 if case is parent else 500,
                headers={"content-type": ["application/json"]},
                content=b'{"token": "live"}',
                request=prepared,
                elapsed=0.1,
                verify=False,
            ),
        )
    recorder.record_check_failure(name="not_a_server_error", case_id=child.id, code_sample="curl", failure=_failure())

    crashes = build_crashes_from_recorder(
        recorder=recorder, failing_case_id=child.id, sanitization=SanitizationConfig()
    )

    assert len(crashes) == 1
    assert crashes[0].sequence[-1].link.request_body == {"token": "$response.body#/token"}


def test_build_crashes_includes_related_sibling_case(case_factory):
    # Use-after-free needs the sibling DELETE that freed the resource, which the failing read's parent chain omits.
    recorder = ScenarioRecorder(label="use-after-free")
    create, delete, read = case_factory(), case_factory(), case_factory()
    recorder.record_case(parent_id=None, case=create, transition=None, is_transition_applied=False)
    recorder.record_case(parent_id=create.id, case=delete, transition=None, is_transition_applied=False)
    recorder.record_case(parent_id=create.id, case=read, transition=None, is_transition_applied=False)
    for case in (create, delete, read):
        prepared = requests.Request(method="GET", url="http://127.0.0.1/r").prepare()
        recorder.record_response(
            case_id=case.id,
            response=Response(
                status_code=200,
                headers={"content-type": ["application/json"]},
                content=b"{}",
                request=prepared,
                elapsed=0.1,
                verify=False,
            ),
        )
    failure = UseAfterFree(
        operation="GET /r", message="boom", free="DELETE /r", usage="GET /r", deleted_case_id=delete.id
    )
    recorder.record_check_failure(name="use_after_free", case_id=read.id, code_sample="curl", failure=failure)

    crashes = build_crashes_from_recorder(recorder=recorder, failing_case_id=read.id, sanitization=SanitizationConfig())

    assert len(crashes) == 1
    # Three steps (create, delete, read), not two — the freeing DELETE is preserved before the failing read.
    assert len(crashes[0].sequence) == 3


def test_remove_by_operation_matches_exact_operation(tmp_path, case_factory):
    # A shared operation-name prefix must not cause the wrong crash file to be deleted.
    writer = CrashWriter(directory=tmp_path)
    writer.open(schema_location="x", base_url="x")
    writer.write(_crash(operation="GET /users", fingerprint="aaaaaaaa"))
    writer.write(_crash(operation="GET /users/{id}", fingerprint="bbbbbbbb"))

    writer.remove_by_operation("GET /users")

    remaining = sorted(
        json.loads(f.read_text())["operation"] for f in tmp_path.glob("*.json") if f.name != MANIFEST_FILENAME
    )
    assert remaining == ["GET /users/{id}"]


def test_remove_by_operation_skips_malformed_file(tmp_path):
    # A malformed crash file in the directory must not block healing of a valid one.
    writer = CrashWriter(directory=tmp_path)
    writer.open(schema_location="x", base_url="x")
    writer.write(_crash(operation="GET /users", fingerprint="aaaaaaaa"))
    (tmp_path / "broken.json").write_text("{not valid json")

    writer.remove_by_operation("GET /users")

    remaining = {f.name for f in tmp_path.glob("*.json") if f.name != MANIFEST_FILENAME}
    assert remaining == {"broken.json"}


def test_remove_by_operation_skips_non_object_file(tmp_path):
    # A valid-JSON-but-non-object file must be skipped, not crash cleanup on `data.get(...)`.
    writer = CrashWriter(directory=tmp_path)
    writer.open(schema_location="x", base_url="x")
    writer.write(_crash(operation="GET /users", fingerprint="aaaaaaaa"))
    (tmp_path / "list.json").write_text("[]")

    writer.remove_by_operation("GET /users")

    remaining = {f.name for f in tmp_path.glob("*.json") if f.name != MANIFEST_FILENAME}
    assert remaining == {"list.json"}


def test_remove_files_ignores_already_dropped_file(tmp_path):
    # A filename already gone (cleaned or removed concurrently) must be skipped, not raise.
    writer = CrashWriter(directory=tmp_path)
    writer.open(schema_location="x", base_url="x")
    crash = _crash(operation="GET /users", fingerprint="aaaaaaaa")
    writer.write(crash)

    writer.remove_files({crash.filename(), "already_gone.json"})

    assert not [f for f in tmp_path.glob("*.json") if f.name != MANIFEST_FILENAME]


def test_rerun_refreshes_stored_case_id(tmp_path):
    # case_id is regenerated each run, so a re-run refreshes the stored id and replaying the latest run resolves.
    run1 = CrashWriter(directory=tmp_path)
    run1.open(schema_location="x", base_url="x")
    run1.write(_crash(operation="GET /users", fingerprint="aaaaaaaa", case_id="OLD123"))

    run2 = CrashWriter(directory=tmp_path)
    run2.open(schema_location="x", base_url="x")
    run2.write(_crash(operation="GET /users", fingerprint="aaaaaaaa", case_id="NEW456"))

    stored = [json.loads(f.read_text()) for f in tmp_path.glob("*.json") if f.name != MANIFEST_FILENAME]
    assert [s["case_id"] for s in stored] == ["NEW456"]


def test_same_run_keeps_first_case_id(tmp_path):
    # When the same failure recurs in one run, the stored id stays the first occurrence shown in that run's output.
    writer = CrashWriter(directory=tmp_path)
    writer.open(schema_location="x", base_url="x")
    writer.write(_crash(operation="GET /users", fingerprint="aaaaaaaa", case_id="FIRST1"))
    writer.write(_crash(operation="GET /users", fingerprint="aaaaaaaa", case_id="SECOND"))

    stored = [json.loads(f.read_text()) for f in tmp_path.glob("*.json") if f.name != MANIFEST_FILENAME]
    assert [s["case_id"] for s in stored] == ["FIRST1"]


def test_build_crashes_skips_timing_only_failure(case_factory):
    # Response-time failures are timing-dependent; re-sending the request can't reproduce them, so they're not recorded.
    failure = ResponseTimeExceeded(operation="GET /users", elapsed=5.0, deadline=0.1, message="too slow")
    recorder = _recorder_with_failure(case_factory, failure=failure, check_name="max_response_time", status=200)

    crashes = build_crashes_from_recorder(
        recorder=recorder, failing_case_id=next(iter(recorder.checks)), sanitization=SanitizationConfig()
    )

    assert crashes == []


def test_build_crashes_tolerates_unknown_response_charset(case_factory):
    # A server-supplied charset that Python can't resolve must not blow up crash building.
    recorder = _recorder_with_failure(case_factory, encoding="not-a-real-codec")

    crashes = build_crashes_from_recorder(
        recorder=recorder, failing_case_id=next(iter(recorder.checks)), sanitization=SanitizationConfig()
    )

    assert len(crashes) == 1


def test_crash_step_roundtrips_dict_that_looks_like_bytes_sentinel():
    # A generated dict that happens to mimic the bytes-encoding tag must survive as a dict, not decode to bytes.
    payload = {"__schemathesis_bytes__": "aGVsbG8="}
    step = _step(query=payload)

    restored = CrashStep.from_dict(step.to_dict())

    assert restored.query == payload


def test_crash_step_roundtrips_real_bytes_body():
    step = _step(case_body=b"\x00\x01raw", media_type="application/octet-stream")

    restored = CrashStep.from_dict(step.to_dict())

    assert restored.case_body == b"\x00\x01raw"


def test_crash_step_roundtrips_bytes_in_param_containers():
    raw = b"\xff\x00bin"
    step = _step(path_parameters={"id": raw}, case_headers={"X-Token": raw}, cookies={"sid": raw})

    restored = CrashStep.from_dict(step.to_dict())

    assert restored.path_parameters == {"id": raw}
    assert restored.case_headers == {"X-Token": raw}
    assert restored.cookies == {"sid": raw}


def test_request_headers_are_sanitized(case_factory):
    # Sensitive request headers (e.g. Authorization) must be filtered out of the stored crash step.
    recorder = ScenarioRecorder(label="GET /users")
    case = case_factory()
    recorder.record_case(parent_id=None, case=case, transition=None, is_transition_applied=False)
    prepared = requests.Request(
        method="GET", url="http://127.0.0.1/users", headers={"Authorization": "super-secret-token"}
    ).prepare()
    response = Response(
        status_code=500,
        headers={"content-type": ["application/json"]},
        content=b'{"error": "boom"}',
        request=prepared,
        elapsed=0.1,
        verify=False,
        encoding=None,
    )
    recorder.record_response(case_id=case.id, response=response)
    recorder.record_check_failure(name="not_a_server_error", case_id=case.id, code_sample="curl x", failure=_failure())

    crashes = build_crashes_from_recorder(recorder=recorder, failing_case_id=case.id, sanitization=SanitizationConfig())

    assert len(crashes) == 1
    headers = crashes[0].sequence[0].request_headers
    assert "super-secret-token" not in json.dumps(headers)
    authorization = next(value for key, value in headers.items() if key.lower() == "authorization")
    assert authorization == "[Filtered]"


def test_list_root_request_body_is_sanitized(case_factory):
    # A JSON body whose root is a list (not an object) must still be sanitized before it reaches the cache.
    recorder = ScenarioRecorder(label="POST /items")
    case = case_factory(body=[{"token": "secret"}])
    recorder.record_case(parent_id=None, case=case, transition=None, is_transition_applied=False)
    prepared = requests.Request(method="POST", url="http://127.0.0.1/items").prepare()
    response = Response(
        status_code=500,
        headers={"content-type": ["application/json"]},
        content=b"{}",
        request=prepared,
        elapsed=0.1,
        verify=False,
    )
    recorder.record_response(case_id=case.id, response=response)
    recorder.record_check_failure(name="not_a_server_error", case_id=case.id, code_sample="curl", failure=_failure())

    crashes = build_crashes_from_recorder(recorder=recorder, failing_case_id=case.id, sanitization=SanitizationConfig())

    assert crashes[0].sequence[-1].case_body == [{"token": "[Filtered]"}]


def test_load_manifest_rejects_unknown_format_version(tmp_path):
    # A crash directory written by a newer Schemathesis is treated as incompatible, not crashed on or silently loaded.
    (tmp_path / MANIFEST_FILENAME).write_text(
        json.dumps(
            {
                "format_version": 2,
                "schemathesis_version": "9.9.9",
                "schema_location": "http://x/schema.json",
                "base_url": "http://x",
                "created_at": "2026-01-01T00:00:00+00:00",
            }
        )
    )

    assert load_manifest(tmp_path) is None
