from __future__ import annotations

import pytest

import schemathesis
from schemathesis.engine import events
from schemathesis.engine.run import PhaseName
from schemathesis.generation import GenerationMode
from test.utils import EventStream

MAX_EXAMPLES = 12
NEGATIVE_MAX_EXAMPLES = 100


def _fuzzing_cases(stream):
    return [
        case.value
        for scenario in stream.find_all(events.ScenarioFinished)
        if scenario.phase == PhaseName.FUZZING
        for case in scenario.recorder.cases.values()
    ]


# Credentials do not vary the resource lookup, so a security header is not another varying input.
@pytest.mark.parametrize("secured", [False, True])
def test_single_captured_resource_does_not_dominate_fuzzing(ctx, secured):
    api = ctx.openapi.apps.resource_pool(secured=secured)
    schema = schemathesis.openapi.from_url(api.schema_url)
    schema.config.generation.update(modes=[GenerationMode.POSITIVE])

    stream = EventStream(
        schema,
        phases=[PhaseName.EXAMPLES, PhaseName.FUZZING],
        seed=1,
        max_examples=MAX_EXAMPLES,
        deterministic=True,
        checks=(),
    ).execute()

    stream.assert_no_errors()
    requests = api.calls_under("/api/items/", method="GET")
    fuzzing_ids = [case.path_parameters["itemId"] for case in _fuzzing_cases(stream)]
    assert len(requests) >= MAX_EXAMPLES + 1
    assert len(fuzzing_ids) == MAX_EXAMPLES
    assert fuzzing_ids.count(12) <= len(fuzzing_ids) / 3
    assert len(set(fuzzing_ids)) >= len(fuzzing_ids) / 2


def test_multiple_captured_resources_are_reused(ctx):
    existing_ids = frozenset({1, 2, 12, 2999, 3000})
    api = ctx.openapi.apps.resource_pool(existing_ids=existing_ids)
    schema = schemathesis.openapi.from_url(api.schema_url)
    schema.config.generation.update(modes=[GenerationMode.POSITIVE])

    stream = EventStream(
        schema,
        phases=[PhaseName.EXAMPLES, PhaseName.FUZZING],
        seed=1,
        max_examples=MAX_EXAMPLES,
        deterministic=True,
        checks=(),
    ).execute()

    stream.assert_no_errors()
    fuzzing_cases = _fuzzing_cases(stream)
    assert len(fuzzing_cases) == MAX_EXAMPLES
    assert sum(bool(case.meta.pool_draws) for case in fuzzing_cases) >= len(fuzzing_cases) / 2


def test_captured_resource_is_reused_when_other_inputs_vary(ctx):
    api = ctx.openapi.apps.resource_update()
    schema = schemathesis.openapi.from_url(api.schema_url)
    schema.config.generation.update(modes=[GenerationMode.NEGATIVE])

    stream = EventStream(
        schema,
        phases=[PhaseName.EXAMPLES, PhaseName.FUZZING],
        seed=1,
        max_examples=NEGATIVE_MAX_EXAMPLES,
        deterministic=True,
        checks=(),
    ).execute()

    stream.assert_no_errors()
    # A valid id with an invalid body reaches the update logic instead of stopping at 404.
    item_ids = [
        case.path_parameters["itemId"]
        for case in _fuzzing_cases(stream)
        if case.method.upper() == "PATCH"
        and case.meta.components["path"].mode == GenerationMode.POSITIVE
        and case.meta.components["body"].mode == GenerationMode.NEGATIVE
    ]
    assert item_ids
    assert item_ids.count(12) >= len(item_ids) / 2
