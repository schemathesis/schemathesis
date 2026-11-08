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
    assert _run_feedback(cli, ctx.openapi.apps.planted_bug(), **extra_kwargs) == snapshot_cli


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_feedback_unmasks_planted_bug_via_dotted_path(ctx, cli, snapshot_cli):
    assert _run_feedback(cli, ctx.openapi.serve(error_feedback_apps.nested_planted_bug())) == snapshot_cli


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_feedback_unmasks_planted_bug_via_size_bound(ctx, cli, snapshot_cli):
    assert _run_feedback(cli, ctx.openapi.serve(error_feedback_apps.size_bound_planted_bug())) == snapshot_cli


# A malformed `Content-Type` on a rejection is the server's fault, not the schema's.
@pytest.mark.snapshot(replace_reproduce_with=True)
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


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_feedback_unmasks_planted_bug_via_format(ctx, cli, snapshot_cli):
    assert _run_feedback(cli, ctx.openapi.serve(error_feedback_apps.format_planted_bug())) == snapshot_cli


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_feedback_unmasks_planted_bug_via_numeric_bound(ctx, cli, snapshot_cli):
    assert _run_feedback(cli, ctx.openapi.serve(error_feedback_apps.numeric_bound_planted_bug())) == snapshot_cli


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_feedback_unmasks_planted_bug_via_positive_numeric_gate(ctx, cli, snapshot_cli):
    assert (
        _run_feedback(cli, ctx.openapi.serve(error_feedback_apps.positive_numeric_gate_planted_bug())) == snapshot_cli
    )


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_feedback_unmasks_planted_bug_via_unrecognized_property(ctx, cli, snapshot_cli):
    assert (
        _run_feedback(cli, ctx.openapi.serve(error_feedback_apps.unrecognized_property_planted_bug())) == snapshot_cli
    )


@pytest.mark.snapshot(replace_reproduce_with=True)
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


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_feedback_unmasks_planted_bug_via_pattern(ctx, cli, snapshot_cli):
    assert _run_feedback(cli, ctx.openapi.serve(error_feedback_apps.pattern_planted_bug())) == snapshot_cli


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_feedback_unmasks_planted_bug_via_type_mismatch(ctx, cli, snapshot_cli):
    assert _run_feedback(cli, ctx.openapi.serve(error_feedback_apps.jackson_typed_planted_bug())) == snapshot_cli


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_feedback_unmasks_planted_bug_via_type_mismatch_ref_bundled(ctx, cli, snapshot_cli):
    # Adjustment must reach the body schema even when bundled behind $ref / x-bundled.
    assert (
        _run_feedback(
            cli, ctx.openapi.serve(error_feedback_apps.jackson_typed_planted_bug_ref_bundled()), max_examples=30
        )
        == snapshot_cli
    )


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_feedback_unmasks_planted_bug_via_jackson_numeric_overflow(ctx, cli, snapshot_cli):
    assert _run_feedback(cli, ctx.openapi.serve(error_feedback_apps.jackson_overflow_planted_bug())) == snapshot_cli


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_feedback_unmasks_planted_bug_via_enum(ctx, cli, snapshot_cli):
    assert _run_feedback(cli, ctx.openapi.serve(error_feedback_apps.jackson_enum_planted_bug())) == snapshot_cli


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_feedback_unmasks_planted_bug_via_missing_query_parameter(ctx, cli, snapshot_cli):
    assert _run_feedback(cli, ctx.openapi.serve(error_feedback_apps.missing_query_param_planted_bug())) == snapshot_cli


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_feedback_recovers_constraints_dropped_from_pydantic_schema(ctx, cli, snapshot_cli):
    assert _run_feedback(cli, ctx.openapi.serve(error_feedback_apps.pydantic_planted_bug())) == snapshot_cli


@pytest.mark.snapshot(replace_reproduce_with=True)
@pytest.mark.parametrize("envelope", ["legacy", "modern", "wrapped"])
def test_feedback_unmasks_planted_bug_via_rails_envelope(ctx, cli, envelope, snapshot_cli):
    assert _run_feedback(cli, ctx.openapi.apps.rails_planted_bug(envelope=envelope), max_examples=30) == snapshot_cli


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_feedback_unmasks_planted_bug_via_laravel_envelope(ctx, cli, snapshot_cli):
    assert (
        _run_feedback(cli, ctx.openapi.serve(envelopes.planted_bug(envelopes.LARAVEL)), max_examples=30) == snapshot_cli
    )


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_feedback_unmasks_planted_bug_via_aspnet_envelope(ctx, cli, snapshot_cli):
    assert (
        _run_feedback(cli, ctx.openapi.serve(envelopes.planted_bug(envelopes.ASPNET)), max_examples=30) == snapshot_cli
    )


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_feedback_unmasks_planted_bug_via_zod_envelope(ctx, cli, snapshot_cli):
    assert _run_feedback(cli, ctx.openapi.serve(envelopes.planted_bug(envelopes.ZOD)), max_examples=30) == snapshot_cli


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_feedback_unmasks_planted_bug_via_ajv_envelope(ctx, cli, snapshot_cli):
    assert _run_feedback(cli, ctx.openapi.serve(envelopes.planted_bug(envelopes.AJV))) == snapshot_cli


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_feedback_unmasks_planted_bug_via_go_validator_envelope(ctx, cli, snapshot_cli):
    assert _run_feedback(cli, ctx.openapi.serve(envelopes.planted_bug(envelopes.GO_VALIDATOR))) == snapshot_cli


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_feedback_unmasks_planted_bug_via_symfony_envelope(ctx, cli, snapshot_cli):
    assert _run_feedback(cli, ctx.openapi.serve(envelopes.planted_bug(envelopes.SYMFONY))) == snapshot_cli


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_feedback_unmasks_planted_bug_via_confluent_envelope(ctx, cli, snapshot_cli):
    assert _run_feedback(cli, ctx.openapi.serve(error_feedback_apps.confluent_planted_bug())) == snapshot_cli


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_feedback_unmasks_planted_bug_via_marshmallow_envelope(ctx, cli, snapshot_cli):
    assert _run_feedback(cli, ctx.openapi.serve(envelopes.planted_bug(envelopes.MARSHMALLOW))) == snapshot_cli


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_feedback_unmasks_planted_bug_via_flask_rest_envelope(ctx, cli, snapshot_cli):
    assert _run_feedback(cli, ctx.openapi.serve(envelopes.planted_bug(envelopes.FLASK_REST))) == snapshot_cli


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_feedback_unmasks_planted_bug_via_litestar_envelope(ctx, cli, snapshot_cli):
    assert _run_feedback(cli, ctx.openapi.serve(envelopes.planted_bug(envelopes.LITESTAR))) == snapshot_cli


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_feedback_unmasks_planted_bug_via_restler_envelope(ctx, cli, snapshot_cli):
    assert _run_feedback(cli, ctx.openapi.serve(error_feedback_apps.restler_planted_bug())) == snapshot_cli
