from __future__ import annotations

import pytest

import schemathesis
from schemathesis.core.warnings import SchemathesisWarning
from schemathesis.engine import Status, events, from_schema
from schemathesis.engine.run import PhaseName
from schemathesis.engine.supervisor import METHOD_NOT_ALLOWED_THRESHOLD
from schemathesis.generation import GenerationMode


def test_persistent_405_skips_op_in_later_phases(ctx):
    api = ctx.openapi.apps.unimplemented_method()
    schema = schemathesis.openapi.from_url(api.schema_url)
    schema.config.checks.update(included_check_names=["not_a_server_error"])
    schema.config.phases.update(phases=["examples", "coverage", "fuzzing"])
    schema.config.generation.update(modes=[GenerationMode.POSITIVE], max_examples=10)

    missing = "POST /missing"
    items = "GET /items"
    fuzzing_missing_skipped = 0
    fuzzing_missing_ran = 0
    fuzzing_items_ran = 0
    method_not_allowed_skip_reasons: dict[str, str | None] = {}
    for event in from_schema(schema).execute():
        if not isinstance(event, events.ScenarioFinished):
            continue
        if event.skip_warning is SchemathesisWarning.METHOD_NOT_ALLOWED and event.label is not None:
            method_not_allowed_skip_reasons[event.label] = event.skip_reason
        if event.phase is not PhaseName.FUZZING:
            continue
        if event.label == missing:
            if event.status is Status.SKIP:
                fuzzing_missing_skipped += 1
            else:
                fuzzing_missing_ran += 1
        elif event.label == items and event.status is not Status.SKIP:
            fuzzing_items_ran += 1

    assert fuzzing_missing_ran == 0, f"Expected {missing} fuzzing to be entirely skipped, but {fuzzing_missing_ran} ran"
    assert fuzzing_missing_skipped >= 1, f"Expected at least one synthetic SKIP event for {missing} in fuzzing"
    assert fuzzing_items_ran > 0, f"Expected {items} fuzzing to run normally, got {fuzzing_items_ran}"
    assert missing in method_not_allowed_skip_reasons, (
        f"Expected `method_not_allowed` skip_warning surfaced for {missing}; got {method_not_allowed_skip_reasons}"
    )
    reason = method_not_allowed_skip_reasons[missing]
    assert reason and "405" in reason, f"Expected skip_reason to mention 405, got {reason!r}"


def test_baked_cases_short_circuit_after_supervisor_fires(ctx):
    # Eager-baked Coverage cases past the supervisor's fire point must skip
    # the server call instead of running the full mutation set.
    api = ctx.openapi.apps.unimplemented_method()
    store = api.wsgi_app.config["store"]
    schema = schemathesis.openapi.from_url(api.schema_url)
    schema.config.checks.update(included_check_names=["not_a_server_error"])
    schema.config.phases.update(phases=["coverage", "fuzzing"])
    schema.config.generation.update(modes=[GenerationMode.POSITIVE], max_examples=50)

    list(from_schema(schema).execute())

    assert store.hits["missing"] >= METHOD_NOT_ALLOWED_THRESHOLD, (
        f"streak threshold should have been reached; got {store.hits['missing']} hits"
    )
    # Without the short-circuit: 50 Fuzzing examples + Coverage mutations would all run.
    assert store.hits["missing"] <= METHOD_NOT_ALLOWED_THRESHOLD + 5, (
        f"Expected server calls bounded after supervisor fires; got {store.hits['missing']} hits"
    )
    assert store.hits["items"] > 0, "GET /items should still be exercised"


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_method_not_allowed_warning_in_cli_output(cli, ctx, snapshot_cli):
    api = ctx.openapi.apps.unimplemented_method()
    assert (
        cli.run(
            api.schema_url,
            "--max-examples=10",
            "--phases=examples,coverage,fuzzing",
            "--mode=positive",
            "--continue-on-failure",
        )
        == snapshot_cli
    )


def test_method_not_allowed_warning_can_fail_the_run(cli, ctx):
    api = ctx.openapi.apps.unimplemented_method()
    result = cli.run(
        api.schema_url,
        "--max-examples=10",
        "--phases=examples,coverage,fuzzing",
        "--mode=positive",
        "--continue-on-failure",
        config={"warnings": {"fail-on": ["method_not_allowed"]}},
    )
    assert result.exit_code == 1, result.stdout
    assert "Method Not Allowed" in result.stdout


def test_stateful_skips_supervisor_blocked_operations(ctx):
    # Stateful's `TransitionController` must reject rules whose target operation
    # has a SKIP verdict so the dead op is never selected as a transition.
    api = ctx.openapi.apps.linked_with_unimplemented_method()
    store = api.wsgi_app.config["store"]
    schema = schemathesis.openapi.from_url(api.schema_url)
    schema.config.checks.update(included_check_names=["not_a_server_error"])
    schema.config.phases.update(phases=["coverage", "stateful"])
    schema.config.generation.update(modes=[GenerationMode.POSITIVE], max_examples=20)

    missing_hits_pre_stateful = 0
    saw_stateful_finish = False
    for event in from_schema(schema).execute():
        if isinstance(event, events.PhaseStarted) and event.phase.name == PhaseName.STATEFUL_TESTING:
            missing_hits_pre_stateful = store.hits["missing"]
        if isinstance(event, events.PhaseFinished) and event.phase.name == PhaseName.STATEFUL_TESTING:
            saw_stateful_finish = True

    assert saw_stateful_finish, "Stateful phase did not run"
    assert missing_hits_pre_stateful >= METHOD_NOT_ALLOWED_THRESHOLD, (
        f"streak should have triggered before Stateful started; got {missing_hits_pre_stateful} pre-Stateful hits"
    )
    stateful_missing_hits = store.hits["missing"] - missing_hits_pre_stateful
    assert stateful_missing_hits == 0, (
        f"Stateful sent {stateful_missing_hits} requests to POST /missing despite the SKIP verdict"
    )
    assert store.hits["items_id"] > 0, "Stateful did not chain GET /items/{itemId} from the link"
