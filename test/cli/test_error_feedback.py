from __future__ import annotations

import pytest

import schemathesis
from schemathesis.core.error_feedback.pipeline import _reset_pipeline_for_tests
from schemathesis.engine import events, from_schema
from schemathesis.engine.run import PhaseName
from schemathesis.generation import GenerationMode
from test.apps.catalog.openapi import error_feedback as error_feedback_apps


@pytest.fixture(autouse=True)
def _reset_feedback_pipeline():
    # MRU singleton leaks across tests otherwise.
    _reset_pipeline_for_tests()


@pytest.mark.snapshot(replace_reproduce_with=True)
@pytest.mark.parametrize(
    "extra_kwargs",
    [
        {},
        {"config": {"phases": {"fuzzing": {"error-feedback": {"enabled": False}}}}},
    ],
    ids=["enabled", "disabled"],
)
def test_feedback_toggles_planted_bug_visibility(ctx, cli, snapshot_cli, extra_kwargs):
    api = ctx.openapi.apps.planted_bug()
    assert (
        cli.run(
            api.schema_url,
            "--max-examples=10",
            "--phases=coverage,fuzzing",
            "--mode=positive",
            "--continue-on-failure",
            **extra_kwargs,
        )
        == snapshot_cli
    )


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_feedback_unmasks_planted_bug_via_dotted_path(ctx, cli, snapshot_cli):
    api = ctx.openapi.apps.nested_planted_bug()
    assert (
        cli.run(
            api.schema_url,
            "--max-examples=10",
            "--phases=coverage,fuzzing",
            "--mode=positive",
            "--continue-on-failure",
        )
        == snapshot_cli
    )


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_feedback_unmasks_planted_bug_via_size_bound(ctx, cli, snapshot_cli):
    api = ctx.openapi.apps.size_bound_planted_bug()
    assert (
        cli.run(
            api.schema_url,
            "--max-examples=10",
            "--phases=coverage,fuzzing",
            "--mode=positive",
            "--continue-on-failure",
        )
        == snapshot_cli
    )


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_feedback_unmasks_planted_bug_via_format(ctx, cli, snapshot_cli):
    api = ctx.openapi.apps.format_planted_bug()
    assert (
        cli.run(
            api.schema_url,
            "--max-examples=10",
            "--phases=coverage,fuzzing",
            "--mode=positive",
            "--continue-on-failure",
        )
        == snapshot_cli
    )


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_feedback_unmasks_planted_bug_via_numeric_bound(ctx, cli, snapshot_cli):
    api = ctx.openapi.apps.numeric_bound_planted_bug()
    assert (
        cli.run(
            api.schema_url,
            "--max-examples=10",
            "--phases=coverage,fuzzing",
            "--mode=positive",
            "--continue-on-failure",
        )
        == snapshot_cli
    )


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_feedback_unmasks_planted_bug_via_pattern(ctx, cli, snapshot_cli):
    api = ctx.openapi.apps.pattern_planted_bug()
    assert (
        cli.run(
            api.schema_url,
            "--max-examples=10",
            "--phases=coverage,fuzzing",
            "--mode=positive",
            "--continue-on-failure",
        )
        == snapshot_cli
    )


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_feedback_unmasks_planted_bug_via_type_mismatch(ctx, cli, snapshot_cli):
    api = ctx.openapi.apps.jackson_typed_planted_bug()
    assert (
        cli.run(
            api.schema_url,
            "--max-examples=10",
            "--phases=coverage,fuzzing",
            "--mode=positive",
            "--continue-on-failure",
        )
        == snapshot_cli
    )


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_feedback_unmasks_planted_bug_via_type_mismatch_ref_bundled(ctx, cli, snapshot_cli):
    # Adjustment must reach the body schema even when bundled behind $ref / x-bundled.
    api = ctx.openapi.apps.jackson_typed_planted_bug_ref_bundled()
    assert (
        cli.run(
            api.schema_url,
            "--max-examples=30",
            "--phases=coverage,fuzzing",
            "--mode=positive",
            "--continue-on-failure",
        )
        == snapshot_cli
    )


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_feedback_unmasks_planted_bug_via_jackson_numeric_overflow(ctx, cli, snapshot_cli):
    api = ctx.openapi.apps.jackson_overflow_planted_bug()
    assert (
        cli.run(
            api.schema_url,
            "--max-examples=10",
            "--phases=coverage,fuzzing",
            "--mode=positive",
            "--continue-on-failure",
            "--seed=42",
        )
        == snapshot_cli
    )


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_feedback_unmasks_planted_bug_via_enum(ctx, cli, snapshot_cli):
    api = ctx.openapi.apps.jackson_enum_planted_bug()
    assert (
        cli.run(
            api.schema_url,
            "--max-examples=10",
            "--phases=coverage,fuzzing",
            "--mode=positive",
            "--continue-on-failure",
        )
        == snapshot_cli
    )


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_feedback_unmasks_planted_bug_via_missing_query_parameter(ctx, cli, snapshot_cli):
    api = ctx.openapi.apps.missing_query_param_planted_bug()
    assert (
        cli.run(
            api.schema_url,
            "--max-examples=10",
            "--phases=coverage,fuzzing",
            "--mode=positive",
            "--continue-on-failure",
        )
        == snapshot_cli
    )


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_feedback_recovers_constraints_dropped_from_pydantic_schema(ctx, cli, snapshot_cli):
    api = ctx.openapi.apps.pydantic_planted_bug()
    assert (
        cli.run(
            api.schema_url,
            "--phases=coverage,fuzzing",
            "--mode=positive",
            "--continue-on-failure",
            "--seed=100",
        )
        == snapshot_cli
    )


def _collect_body_dates(schema, *, phase) -> list[str]:
    values: list[str] = []
    for event in from_schema(schema).execute():
        if isinstance(event, events.ScenarioFinished) and event.phase == phase:
            for case_node in event.recorder.cases.values():
                body = case_node.value.body
                if isinstance(body, dict) and isinstance(body.get("commitDate"), str):
                    values.append(body["commitDate"])
    return values


def _collect_query_tokens(schema, *, phase) -> list[str]:
    values: list[str] = []
    for event in from_schema(schema).execute():
        if isinstance(event, events.ScenarioFinished) and event.phase == phase:
            for case_node in event.recorder.cases.values():
                query = case_node.value.query
                if isinstance(query, dict) and isinstance(query.get("token"), str):
                    values.append(query["token"])
    return values


def test_stale_example_evicted_after_format_inference(ctx):
    api = ctx.openapi.apps.commit_date_with_example()
    schema = schemathesis.openapi.from_url(api.schema_url)
    schema.config.checks.update(included_check_names=["not_a_server_error"])
    schema.config.phases.update(phases=["coverage", "fuzzing"])
    schema.config.generation.update(modes=[GenerationMode.POSITIVE], max_examples=100)

    fuzzing_commit_dates = _collect_body_dates(schema, phase=PhaseName.FUZZING)
    assert fuzzing_commit_dates, "No fuzzing body draws collected"
    stale = [v for v in fuzzing_commit_dates if v == "dd-MM-yyyy"]
    assert not stale, f"Stale `commitDate`: {len(stale)}/{len(fuzzing_commit_dates)} fuzzing draws"


def test_stale_example_evicted_after_format_inference_on_query_param(ctx):
    api = ctx.openapi.apps.token_with_example()
    schema = schemathesis.openapi.from_url(api.schema_url)
    schema.config.checks.update(included_check_names=["not_a_server_error"])
    schema.config.phases.update(phases=["examples", "coverage", "fuzzing"])
    schema.config.generation.update(modes=[GenerationMode.POSITIVE], max_examples=100)

    fuzzing_token_values = _collect_query_tokens(schema, phase=PhaseName.FUZZING)
    assert fuzzing_token_values, "No fuzzing query draws collected"
    stale = [v for v in fuzzing_token_values if v == "NOT_A_UUID"]
    assert not stale, f"Stale `token`: {len(stale)}/{len(fuzzing_token_values)} fuzzing draws"


def test_stale_body_example_evicted_in_coverage_after_examples_observations(ctx):
    api = ctx.openapi.apps.commit_date_with_examples()
    schema = schemathesis.openapi.from_url(api.schema_url)
    schema.config.checks.update(included_check_names=["not_a_server_error"])
    schema.config.phases.update(phases=["examples", "coverage"])
    schema.config.generation.update(modes=[GenerationMode.POSITIVE], max_examples=20)

    coverage_commit_dates = _collect_body_dates(schema, phase=PhaseName.COVERAGE)
    assert coverage_commit_dates, "No coverage body draws collected"
    stale = [v for v in coverage_commit_dates if v in error_feedback_apps.STALE_DATES]
    assert not stale, f"Stale `commitDate`: {len(stale)}/{len(coverage_commit_dates)} coverage draws"


def test_stale_query_example_evicted_in_coverage_after_examples_observations(ctx):
    api = ctx.openapi.apps.token_with_examples()
    schema = schemathesis.openapi.from_url(api.schema_url)
    schema.config.checks.update(included_check_names=["not_a_server_error"])
    schema.config.phases.update(phases=["examples", "coverage"])
    schema.config.generation.update(modes=[GenerationMode.POSITIVE], max_examples=20)

    coverage_token_values = _collect_query_tokens(schema, phase=PhaseName.COVERAGE)
    assert coverage_token_values, "No coverage query draws collected"
    stale = [v for v in coverage_token_values if v in error_feedback_apps.STALE_TOKENS]
    assert not stale, f"Stale `token`: {len(stale)}/{len(coverage_token_values)} coverage draws"


def test_stateful_body_generation_consumes_format_inferred_during_fuzzing(ctx):
    api = ctx.openapi.apps.commit_date_with_link()
    schema = schemathesis.openapi.from_url(api.schema_url)
    schema.config.checks.update(included_check_names=["not_a_server_error"])
    schema.config.phases.update(phases=["fuzzing", "stateful"])
    schema.config.generation.update(modes=[GenerationMode.POSITIVE], max_examples=50)

    stateful_commit_dates = _collect_body_dates(schema, phase=PhaseName.STATEFUL_TESTING)
    assert stateful_commit_dates, "No stateful POST body draws collected"
    iso_matches = [v for v in stateful_commit_dates if error_feedback_apps.ISO_DATETIME.match(v)]
    assert iso_matches, (
        f"None of {len(stateful_commit_dates)} stateful `commitDate` draws match the inferred date-time format; "
        f"first few: {stateful_commit_dates[:5]}"
    )


@pytest.mark.snapshot(replace_reproduce_with=True)
@pytest.mark.parametrize("envelope", ["legacy", "modern", "wrapped"])
def test_feedback_unmasks_planted_bug_via_rails_envelope(ctx, cli, envelope, snapshot_cli):
    api = ctx.openapi.apps.rails_planted_bug(envelope=envelope)
    assert (
        cli.run(
            api.schema_url,
            "--max-examples=30",
            "--phases=coverage,fuzzing",
            "--mode=positive",
            "--continue-on-failure",
        )
        == snapshot_cli
    )


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_feedback_unmasks_planted_bug_via_laravel_envelope(ctx, cli, snapshot_cli):
    api = ctx.openapi.apps.laravel_planted_bug()
    assert (
        cli.run(
            api.schema_url,
            "--max-examples=30",
            "--phases=coverage,fuzzing",
            "--mode=positive",
            "--continue-on-failure",
        )
        == snapshot_cli
    )


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_feedback_unmasks_planted_bug_via_aspnet_envelope(ctx, cli, snapshot_cli):
    api = ctx.openapi.apps.aspnet_planted_bug()
    assert (
        cli.run(
            api.schema_url,
            "--max-examples=30",
            "--phases=coverage,fuzzing",
            "--mode=positive",
            "--continue-on-failure",
        )
        == snapshot_cli
    )


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_feedback_unmasks_planted_bug_via_zod_envelope(ctx, cli, snapshot_cli):
    api = ctx.openapi.apps.zod_planted_bug()
    assert (
        cli.run(
            api.schema_url,
            "--max-examples=30",
            "--phases=coverage,fuzzing",
            "--mode=positive",
            "--continue-on-failure",
        )
        == snapshot_cli
    )
