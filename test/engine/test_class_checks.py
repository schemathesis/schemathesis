import schemathesis
from schemathesis.engine import events
from schemathesis.engine.run import PhaseName
from test.utils import EventStream


def test_disabled_after_run_check_does_not_execute(ctx, restore_checks):
    called = []

    @schemathesis.check
    class TrackAfterRun:
        def after_run(self, ctx):
            called.append(True)

    api = ctx.openapi.apps.success()
    schema = schemathesis.openapi.from_url(api.schema_url)
    schema.config.checks.update(excluded_check_names=["TrackAfterRun"])
    EventStream(schema, phases=[PhaseName.FUZZING], max_examples=3).execute()

    assert not called


def test_disabled_after_response_check_does_not_execute(ctx, restore_checks):
    called = []

    @schemathesis.check
    class TrackAfterResponse:
        def after_response(self, ctx, response, case):
            called.append(True)

    api = ctx.openapi.apps.success()
    schema = schemathesis.openapi.from_url(api.schema_url)
    schema.config.checks.update(excluded_check_names=["TrackAfterResponse"])
    EventStream(schema, phases=[PhaseName.FUZZING], max_examples=3).execute()

    assert not called


def test_after_run_flags_operations_without_success(ctx, restore_checks):
    @schemathesis.check
    class RequireSuccess:
        def __init__(self):
            self.codes = {}

        def after_response(self, ctx, response, case):
            self.codes.setdefault(case.operation.label, set()).add(response.status_code)

        def after_run(self, ctx):
            bad = sorted(label for label, codes in self.codes.items() if not any(200 <= c < 300 for c in codes))
            if bad:
                raise AssertionError("never returned success: " + ", ".join(bad))

    api = ctx.openapi.apps.success_and_failure()
    schema = schemathesis.openapi.from_url(api.schema_url)
    stream = EventStream(schema, checks=[RequireSuccess], phases=[PhaseName.FUZZING], max_examples=5).execute()

    finished = stream.finished
    assert finished is not None
    messages = [failure.message for failure in finished.failures]
    assert any("/api/failure" in message for message in messages), messages
    assert not any("/api/success" in message for message in messages), messages


def test_after_run_error_is_reported_not_crash(ctx, restore_checks):
    @schemathesis.check
    class BoomAfterRun:
        def after_run(self, ctx):
            raise KeyError("boom")

    api = ctx.openapi.apps.success()
    schema = schemathesis.openapi.from_url(api.schema_url)
    stream = EventStream(schema, checks=[BoomAfterRun], phases=[PhaseName.FUZZING], max_examples=3).execute()

    finished = stream.finished
    assert finished is not None
    assert any("boom" in (failure.message or "") for failure in finished.failures), finished.failures


def test_after_run_skipped_when_run_aborts(ctx, restore_checks):
    @schemathesis.check
    class FailFast:
        def after_response(self, ctx, response, case):
            raise AssertionError("response boom")

        def after_run(self, ctx):
            raise AssertionError("after_run should not run")

    api = ctx.openapi.apps.success()
    schema = schemathesis.openapi.from_url(api.schema_url)
    stream = EventStream(
        schema, checks=[FailFast], phases=[PhaseName.FUZZING], max_examples=5, max_failures=1
    ).execute()

    finished = stream.finished
    assert finished is not None
    assert not any("after_run should not run" in (failure.message or "") for failure in finished.failures), (
        finished.failures
    )


def test_check_build_error_surfaces_as_event_not_crash(ctx, restore_checks):
    @schemathesis.check
    class BadInit:
        def __init__(self):
            raise ValueError("bad init")

        def after_response(self, ctx, response, case):
            return None

    api = ctx.openapi.apps.success()
    schema = schemathesis.openapi.from_url(api.schema_url)
    stream = EventStream(schema, checks=[BadInit], phases=[PhaseName.FUZZING], max_examples=3).execute()

    errors = stream.find_all(events.NonFatalError)
    assert any("BadInit" in str(error.value) for error in errors), errors


def test_after_run_receives_global_config(ctx, restore_checks):
    captured = {}

    @schemathesis.check
    class Capture:
        def after_run(self, ctx):
            captured["auth"] = ctx._auth
            captured["headers"] = dict(ctx._headers) if ctx._headers is not None else None
            captured["transport"] = ctx._transport_kwargs

    api = ctx.openapi.apps.success()
    schema = schemathesis.openapi.from_url(api.schema_url)
    EventStream(
        schema,
        checks=[Capture],
        phases=[PhaseName.FUZZING],
        max_examples=3,
        headers={"X-Token": "secret"},
        auth=("user", "pass"),
    ).execute()

    assert captured["auth"] == ("user", "pass")
    assert captured["headers"]["X-Token"] == "secret"
    assert captured["transport"] is not None


def test_single_instance_across_phases_and_operations(ctx, restore_checks):
    instances = []

    @schemathesis.check
    class Tracker:
        def __init__(self):
            instances.append(self)

        def after_response(self, ctx, response, case):
            return None

        def after_run(self, ctx):
            return None

    api = ctx.openapi.apps.success()
    schema = schemathesis.openapi.from_url(api.schema_url)
    EventStream(
        schema,
        checks=[Tracker],
        phases=[PhaseName.COVERAGE, PhaseName.FUZZING],
        max_examples=3,
    ).execute()

    assert len(instances) == 1


def test_after_response_observes_phase(ctx, restore_checks):
    seen_phases = set()

    @schemathesis.check
    class RecordPhase:
        def after_response(self, ctx, response, case):
            seen_phases.add(ctx.phase)

    api = ctx.openapi.apps.success()
    schema = schemathesis.openapi.from_url(api.schema_url)
    EventStream(
        schema,
        checks=[RecordPhase],
        phases=[PhaseName.COVERAGE, PhaseName.FUZZING],
        max_examples=3,
    ).execute()

    assert PhaseName.COVERAGE in seen_phases, seen_phases
    assert PhaseName.FUZZING in seen_phases, seen_phases


def test_after_response_observes_stateful_phase(ctx, restore_checks):
    seen_phases = set()

    @schemathesis.check
    class RecordPhase:
        def after_response(self, ctx, response, case):
            seen_phases.add(ctx.phase)

    api = ctx.openapi.apps.users_crud()
    schema = schemathesis.openapi.from_url(api.schema_url)
    EventStream(
        schema,
        checks=[RecordPhase],
        phases=[PhaseName.STATEFUL_TESTING],
        max_examples=5,
    ).execute()

    assert PhaseName.STATEFUL_TESTING in seen_phases, seen_phases
