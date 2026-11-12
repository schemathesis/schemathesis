from __future__ import annotations

import pytest

from test.apps.catalog.openapi import envelopes
from test.apps.catalog.openapi import error_feedback as error_feedback_apps


def _run_feedback(cli, api, max_examples=10, **kwargs):
    return cli.run(
        api.schema_url,
        "--no-shrink",
        f"--max-examples={max_examples}",
        "--phases=coverage,fuzzing",
        "--mode=positive",
        "--continue-on-failure",
        **kwargs,
    )


@pytest.mark.parametrize(
    "extra_kwargs",
    [
        {},
        {"config": {"phases": {"fuzzing": {"error-feedback": {"enabled": False}}}}},
    ],
    ids=["enabled", "disabled"],
)
def test_feedback_toggles_planted_bug_visibility(ctx, cli, snapshot_cli, extra_kwargs):
    assert _run_feedback(cli, ctx.openapi.apps.planted_bug(), **extra_kwargs) == snapshot_cli


# A malformed `Content-Type` on a rejection is the server's fault, not the schema's.
def test_feedback_ignores_rejection_with_malformed_content_type(ctx, cli, snapshot_cli):
    api = ctx.openapi.serve(error_feedback_apps.malformed_content_type_rejection())
    assert (
        cli.run(
            api.schema_url,
            "--seed=1",
            "--no-shrink",
            "--max-examples=5",
            "--phases=fuzzing",
            "--mode=positive",
        )
        == snapshot_cli
    )


def test_feedback_unmasks_planted_bug_via_missing_request_body(ctx, cli, snapshot_cli):
    api = ctx.openapi.serve(error_feedback_apps.missing_request_body_planted_bug())
    assert (
        cli.run(
            api.schema_url,
            "--no-shrink",
            # Feedback must adapt generation before the planted bug surfaces, which takes many cases.
            "--max-examples=80",
            "--phases=coverage,fuzzing",
            "--mode=positive",
            "-c",
            "not_a_server_error",
            "--continue-on-failure",
        )
        == snapshot_cli
    )


def test_feedback_keeps_closed_body_closed_behind_error_envelope(ctx, cli, snapshot_cli):
    api = ctx.openapi.serve(error_feedback_apps.envelope_wrapped_closed_body())
    assert (
        cli.run(
            api.schema_url,
            "--max-examples=10",
            "--phases=coverage,fuzzing",
            "--mode=positive",
            "-c",
            "positive_data_acceptance",
            config={"checks": {"positive_data_acceptance": {"expected-statuses": ["2xx", "422"]}}},
        )
        == snapshot_cli
    )


@pytest.mark.parametrize(
    ("factory", "max_examples"),
    [
        (error_feedback_apps.nested_planted_bug, 10),
        (error_feedback_apps.size_bound_planted_bug, 10),
        (error_feedback_apps.format_planted_bug, 10),
        (error_feedback_apps.numeric_bound_planted_bug, 10),
        (error_feedback_apps.positive_numeric_gate_planted_bug, 10),
        (error_feedback_apps.unrecognized_property_planted_bug, 10),
        (error_feedback_apps.pattern_planted_bug, 10),
        (error_feedback_apps.jackson_typed_planted_bug, 10),
        (error_feedback_apps.jackson_typed_planted_bug_ref_bundled, 30),
        (error_feedback_apps.jackson_overflow_planted_bug, 10),
        (error_feedback_apps.jackson_enum_planted_bug, 10),
        (error_feedback_apps.missing_query_param_planted_bug, 10),
        (error_feedback_apps.pydantic_planted_bug, 10),
        (error_feedback_apps.confluent_planted_bug, 10),
        (error_feedback_apps.restler_planted_bug, 10),
    ],
    ids=[
        "via_dotted_path",
        "via_size_bound",
        "via_format",
        "via_numeric_bound",
        "via_positive_numeric_gate",
        "via_unrecognized_property",
        "via_pattern",
        "via_type_mismatch",
        "via_type_mismatch_ref_bundled",
        "via_jackson_numeric_overflow",
        "via_enum",
        "via_missing_query_parameter",
        "via_pydantic_dropped_constraints",
        "via_confluent_envelope",
        "via_restler_envelope",
    ],
)
def test_feedback_unmasks_planted_bug(ctx, cli, snapshot_cli, factory, max_examples):
    assert _run_feedback(cli, ctx.openapi.serve(factory()), max_examples) == snapshot_cli


@pytest.mark.parametrize(
    ("envelope", "max_examples"),
    [(envelopes.LARAVEL, 30), (envelopes.AJV, 10)],
    ids=["laravel", "ajv"],
)
def test_feedback_unmasks_planted_bug_via_envelope(ctx, cli, snapshot_cli, envelope, max_examples):
    assert _run_feedback(cli, ctx.openapi.serve(envelopes.planted_bug(envelope)), max_examples) == snapshot_cli


@pytest.mark.parametrize("envelope", ["legacy", "modern", "wrapped"])
def test_feedback_unmasks_planted_bug_via_rails_envelope(ctx, cli, envelope, snapshot_cli):
    assert _run_feedback(cli, ctx.openapi.apps.rails_planted_bug(envelope=envelope), max_examples=30) == snapshot_cli
