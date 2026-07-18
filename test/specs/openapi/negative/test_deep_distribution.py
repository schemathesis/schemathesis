from __future__ import annotations

import json

import jsonschema_rs
import pytest
from hypothesis import HealthCheck, Phase, given, settings

import schemathesis
from schemathesis.core.jsonschema import _is_valid_uuid
from schemathesis.engine.events import ScenarioFinished
from schemathesis.generation.meta import FuzzingPhaseData


@pytest.mark.snapshot(replace_reproduce_with=True, replace_invalid_component=True)
def test_deep_leaf_bug_detected_via_negative_fuzzing(ctx, cli, snapshot_cli):
    # Depth-3 `required` violation must reach the bug; the server accepts any payload.
    api = ctx.openapi.apps.deep_leaf_bug()
    assert (
        cli.run(
            api.schema_url,
            "--mode=negative",
            "--phases=fuzzing",
            "--max-examples=50",
            "--continue-on-failure",
            "--no-shrink",
            "--seed=0",
        )
        == snapshot_cli
    )


def test_negative_fuzzing_distributes_across_depths(ctx):
    # 500 examples: ~14 targets sampled uniformly; depth-3 leaves need the budget to reliably appear.
    api = ctx.openapi.apps.deep_leaf_bug()
    schema = schemathesis.openapi.from_url(api.schema_url)
    schema.config.seed = 42
    schema.config.generation.update(modes=[schemathesis.GenerationMode.NEGATIVE])
    schema.config.phases.examples.enabled = False
    schema.config.phases.coverage.enabled = False
    schema.config.phases.stateful.enabled = False
    schema.config.phases.fuzzing.generation.update(max_examples=500)

    seen_depths: set[int] = set()
    for event in schemathesis.engine.from_schema(schema).execute():
        if not isinstance(event, ScenarioFinished):
            continue
        for case_node in event.recorder.cases.values():
            case = case_node.value
            if case.meta is None:
                continue
            data = case.meta.phase.data
            if not isinstance(data, FuzzingPhaseData):
                continue
            for mutation in data.mutations:
                seen_depths.add(len(mutation.path))

    assert seen_depths >= {0, 1, 2, 3}, f"only saw depths {sorted(seen_depths)}"


def test_operator_swarm_yields_homogeneous_and_heterogeneous_cases(ctx):
    # 500 examples with continue_on_failure: heterogeneous cases (~9% chance each secondary fires)
    # only appear when Hypothesis isn't stopped by the first single-mutation failure.
    api = ctx.openapi.apps.deep_leaf_bug()
    schema = schemathesis.openapi.from_url(api.schema_url)
    schema.config.seed = 42
    schema.config.generation.update(modes=[schemathesis.GenerationMode.NEGATIVE])
    schema.config.phases.examples.enabled = False
    schema.config.phases.coverage.enabled = False
    schema.config.phases.stateful.enabled = False
    schema.config.update(continue_on_failure=True)
    schema.config.phases.fuzzing.generation.update(max_examples=500)

    operator_sets: list[set[str]] = []
    for event in schemathesis.engine.from_schema(schema).execute():
        if not isinstance(event, ScenarioFinished):
            continue
        for case_node in event.recorder.cases.values():
            case = case_node.value
            if case.meta is None:
                continue
            data = case.meta.phase.data
            if not isinstance(data, FuzzingPhaseData):
                continue
            operators = {mutation.operator for mutation in data.mutations}
            if operators:
                operator_sets.append(operators)

    assert any(len(s) == 1 for s in operator_sets), "expected at least one homogeneous case"
    assert any(len(s) > 1 for s in operator_sets), "expected at least one heterogeneous case"


def test_value_channel_produces_format_uuid_near_miss(ctx):
    # 300 examples with continue_on_failure: value channel fires ~15%, picking format:uuid ~1/14 times.
    api = ctx.openapi.apps.additional_properties_bug()
    schema = schemathesis.openapi.from_url(api.schema_url)
    schema.config.seed = 42
    schema.config.generation.update(modes=[schemathesis.GenerationMode.NEGATIVE])
    schema.config.phases.examples.enabled = False
    schema.config.phases.coverage.enabled = False
    schema.config.phases.stateful.enabled = False
    schema.config.update(continue_on_failure=True)
    schema.config.phases.fuzzing.generation.update(max_examples=300)
    for _event in schemathesis.engine.from_schema(schema).execute():
        pass

    for request in api.requests:
        if request.method != "POST":
            continue
        if not request.body:
            continue
        try:
            body = json.loads(request.body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        if not isinstance(body, dict):
            continue
        for value in body.values():
            if isinstance(value, str) and len(value) == 36 and value.count("-") == 4 and not _is_valid_uuid(value):
                return
    pytest.fail("no uuid-shaped near-miss found across runner output")


BODY_WITH_NO_ADDITIONAL_PROPERTIES = {
    "type": "object",
    "additionalProperties": False,
    "required": ["email"],
    "properties": {"email": {"type": "string", "format": "email"}},
}


@pytest.mark.hypothesis_nested
def test_additional_properties_negation_stays_invalid(ctx):
    # See GH-4332. Negating `additionalProperties` drops the `properties` context it is defined
    # against, so the mutated schema still admits bodies valid against the original.
    # 500 examples because only a small fraction of draws pick this keyword combination.
    schema = ctx.openapi.load_schema(
        {
            "/reset": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": BODY_WITH_NO_ADDITIONAL_PROPERTIES}},
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    validator = jsonschema_rs.Draft4Validator(BODY_WITH_NO_ADDITIONAL_PROPERTIES, validate_formats=True)

    @given(case=schema["/reset"]["POST"].as_strategy(generation_mode=schemathesis.GenerationMode.NEGATIVE))
    @settings(
        max_examples=500,
        deadline=None,
        derandomize=True,
        suppress_health_check=list(HealthCheck),
        phases=[Phase.generate],
    )
    def test(case):
        body = case.body
        if not isinstance(body, (dict, list, str, int, float, bool)) and body is not None:
            return
        assert not validator.is_valid(body), f"False positive: body {body!r} is valid against the schema"

    test()


@pytest.mark.parametrize(
    "factory_name",
    [
        "header_constraint_bug",
        "one_of_branch_bug",
        "additional_properties_bug",
    ],
)
@pytest.mark.snapshot(replace_reproduce_with=True, replace_invalid_component=True)
def test_secondary_planted_bug_detected(cli, ctx, snapshot_cli, factory_name):
    api = getattr(ctx.openapi.apps, factory_name)()
    result = cli.run(
        api.schema_url,
        "--mode=negative",
        "--phases=fuzzing",
        "--max-examples=30",
        "--continue-on-failure",
        "--no-shrink",
        "--seed=42",
    )
    assert result == snapshot_cli
