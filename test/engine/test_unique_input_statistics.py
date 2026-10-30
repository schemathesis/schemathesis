"""Logical phase outcomes must not manufacture physical HTTP interactions."""

from __future__ import annotations

import json
import threading
import uuid
from io import StringIO

import pytest
import requests
import yaml
from flask import Flask, jsonify, request

import schemathesis
from schemathesis.cli.commands.run.warnings import WarningCollector, positive_call_outcomes
from schemathesis.cli.context import BaseExecutionContext
from schemathesis.config import SchemathesisConfig
from schemathesis.core.failures import FailureGroup
from schemathesis.engine import Status
from schemathesis.engine.context import EngineContext
from schemathesis.engine.run import PhaseName
from schemathesis.engine.run.unit._case import _call_and_validate_once, run_one_case
from schemathesis.engine.run.unit._scenario import start_scenario
from schemathesis.engine.statistic import Statistic
from schemathesis.generation import GenerationMode
from schemathesis.generation.meta import CaseMetadata, CoverageScenario, GenerationInfo, PhaseInfo
from schemathesis.reporting.har import HarWriter
from schemathesis.reporting.vcr import VcrWriter


def _config(*, unique=True, warnings=True, checks=True):
    return SchemathesisConfig.from_dict(
        {
            "seed": 1,
            "cache": {"enabled": False},
            "generation": {"mode": "positive", "unique-inputs": unique},
            "phases": {"examples": {"enabled": False}, "stateful": {"enabled": False}},
            "checks": {"enabled": False, "not_a_server_error": {"enabled": checks}},
            "warnings": {"display": ["low_valid_rate"], "fail-on": ["low_valid_rate"]} if warnings else False,
        }
    )


def _engine(*, accepted=(1, 2, 3), rejection=404, unique=True, warnings=True, checks=True):
    calls = []
    app = Flask(__name__)

    @app.get("/items/<int:item_id>")
    def items(item_id):
        calls.append(item_id)
        return jsonify({"id": item_id}), 200 if item_id in accepted else rejection

    raw = {
        "openapi": "3.0.3",
        "info": {"title": "Phase statistics", "version": "1"},
        "paths": {
            "/items/{id}": {
                "get": {
                    "parameters": [
                        {"name": "id", "in": "path", "required": True, "schema": {"type": "integer", "minimum": 1}}
                    ],
                    "responses": {str(code): {"description": "Response"} for code in (200, 400, 404, 409, 422, 500)},
                }
            }
        },
    }
    schema = schemathesis.openapi.from_dict(raw, config=_config(unique=unique, warnings=warnings, checks=checks))
    schema.app = app
    operation = schema["/items/{id}"]["get"]
    return EngineContext(schema=schema, stop_event=threading.Event()), operation, calls


def _scenario(engine, operation, phase=PhaseName.FUZZING):
    return start_scenario(operation=operation, ctx=engine, phase=phase, suite_id=uuid.uuid4(), scenario_id=uuid.uuid4())


def _metadata(mode=GenerationMode.POSITIVE):
    return CaseMetadata(
        generation=GenerationInfo(time=0.0, mode=mode),
        components={},
        phase=PhaseInfo.coverage(CoverageScenario.VALID_NUMBER, "existing resource ID"),
    )


def _run(engine, scenario, values, *, mode=GenerationMode.POSITIVE):
    for value in values:
        case = scenario.operation.Case(path_parameters={"id": value}, _meta=_metadata(mode) if mode else None)
        errors, pending = [], []
        run_one_case(case=case, ctx=engine, scenario=scenario, errors=errors, pending_events=pending)
        assert not errors and not pending


def _collector(engine, operation, scenario):
    context = BaseExecutionContext(config=engine.config, find_operation_by_label=lambda label: operation)
    collector = WarningCollector(config=engine.config)
    collector.on_scenario_finished(context, scenario.finished(Status.SUCCESS, skip_reason=None))
    return collector


@pytest.mark.parametrize("repeated", [False, True], ids=["cross-phase", "same-phase-repeat"])
def test_cached_positive_outcomes_prevent_false_low_rate(repeated, tmp_path):
    engine, operation, calls = _engine()
    coverage = _scenario(engine, operation, PhaseName.COVERAGE)
    _run(engine, coverage, [1, 2])
    fuzzing = _scenario(engine, operation)
    _run(engine, fuzzing, [1, 2, 3, *range(10, 21)] + ([1] * 30 if repeated else []))

    outcomes = positive_call_outcomes(fuzzing.recorder)
    assert (outcomes.accepted, outcomes.unreachable, outcomes.total) == (3, 11, 14)
    collector = _collector(engine, operation, fuzzing)
    assert collector.data.valid_rates[operation.label][PhaseName.FUZZING.value] == outcomes
    assert not collector.data.low_valid_rate
    assert not collector.fails_the_run()

    # Logical cache observations are not physical requests or fresh validation results.
    assert len(calls) == 14
    assert calls.count(1) == calls.count(2) == 1
    assert len(fuzzing.recorder.cases) == len(fuzzing.recorder.interactions) == 12
    statistic = Statistic()
    for scenario in (coverage, fuzzing):
        statistic.on_scenario_finished(scenario.recorder)
    assert statistic.total_cases == 14
    assert statistic.errored_cases == 0

    har_path = tmp_path / "calls.har"
    har = HarWriter(har_path, engine.config.output)
    har.open()
    vcr_output = StringIO()
    vcr = VcrWriter(vcr_output, engine.config.output)
    vcr.open(command="schemathesis run")
    for scenario in (coverage, fuzzing):
        har.write(scenario.recorder)
        vcr.write(scenario.recorder)
    har.close()
    vcr.close()
    assert len(json.loads(har_path.read_text(encoding="utf-8"))["log"]["entries"]) == 14
    assert len(yaml.safe_load(vcr_output.getvalue())["http_interactions"]) == 14


@pytest.mark.parametrize("all_cached", [False, True], ids=["physical", "all-cached"])
def test_true_low_rate_still_fails_without_inflation(all_cached):
    engine, operation, calls = _engine(accepted=(1,))
    coverage = _scenario(engine, operation, PhaseName.COVERAGE)
    values = [1, *range(10, 21)]
    if all_cached:
        _run(engine, coverage, values)
    scenario = _scenario(engine, operation)
    _run(engine, scenario, values + [1] * 30)
    outcomes = positive_call_outcomes(scenario.recorder)
    assert (outcomes.accepted, outcomes.total) == (1, 12)
    collector = _collector(engine, operation, scenario)
    assert collector.data.valid_rates[operation.label][PhaseName.FUZZING.value] == outcomes
    assert operation.label in collector.data.low_valid_rate
    assert collector.fails_the_run()
    assert len(calls) == 12
    assert len(scenario.recorder.interactions) == (0 if all_cached else 12)


def test_cached_outcomes_do_not_enable_disabled_warning():
    engine, operation, _ = _engine(accepted=(1,), warnings=False)
    earlier = _scenario(engine, operation, PhaseName.COVERAGE)
    values = [1, *range(10, 21)]
    _run(engine, earlier, values)
    later = _scenario(engine, operation)
    _run(engine, later, values)
    collector = _collector(engine, operation, later)
    assert not collector.data.low_valid_rate
    assert not collector.fails_the_run()
    assert positive_call_outcomes(later.recorder).total == 12


@pytest.mark.parametrize("mode", [GenerationMode.NEGATIVE, None], ids=["negative", "no-metadata"])
def test_cached_outcomes_respect_current_generation_mode(mode):
    engine, operation, calls = _engine()
    earlier = _scenario(engine, operation, PhaseName.COVERAGE)
    _run(engine, earlier, [1])
    later = _scenario(engine, operation)
    _run(engine, later, [1], mode=mode)
    assert positive_call_outcomes(later.recorder).total == 0
    assert calls == [1]


def test_positive_duplicate_can_reuse_response_to_negative_case():
    engine, operation, calls = _engine()
    earlier = _scenario(engine, operation, PhaseName.COVERAGE)
    _run(engine, earlier, [1], mode=GenerationMode.NEGATIVE)
    later = _scenario(engine, operation)
    _run(engine, later, [1])
    assert positive_call_outcomes(later.recorder).accepted == 1
    assert not later.recorder.interactions
    assert calls == [1]


@pytest.mark.parametrize("status", [401, 403, 429, 500])
def test_uninformative_cached_outcome_is_not_valid_or_rejected(status):
    # Disable checks to isolate classification rather than failure replay on 500.
    engine, operation, calls = _engine(accepted=(), rejection=status, checks=False)
    earlier = _scenario(engine, operation, PhaseName.COVERAGE)
    _run(engine, earlier, [1])
    later = _scenario(engine, operation)
    _run(engine, later, [1])
    assert positive_call_outcomes(later.recorder).total == 0
    assert calls == [1]


def test_unique_inputs_disabled_keeps_physical_response_statistics():
    engine, operation, calls = _engine(unique=False)
    earlier = _scenario(engine, operation, PhaseName.COVERAGE)
    _run(engine, earlier, [1, 2])
    later = _scenario(engine, operation)
    _run(engine, later, [1, 2, 3, *range(10, 21), 1])
    assert len(calls) == 17
    outcomes = positive_call_outcomes(later.recorder)
    assert (outcomes.accepted, outcomes.total) == (4, 15)


@pytest.mark.parametrize("accepted", [True, False], ids=["data", "errors"])
def test_graphql_cached_classification_uses_original_body(accepted):
    schema = schemathesis.graphql.from_file("type Query { book(id: Int!): String }", config=_config(checks=False))
    schema.config.update(base_url="http://localhost/graphql")
    # A deliberately small recording limit cannot erase semantic outcome evidence.
    schema.config.output.truncation.max_recorded_payload_size = 8
    app = Flask(__name__)
    calls = []

    @app.post("/graphql")
    def graphql():
        calls.append(request.json)
        return jsonify({"data": {"book": "Hitchhiker"}} if accepted else {"errors": [{"message": "Not found"}]})

    schema.app = app
    operation = schema["Query"]["book"]
    engine = EngineContext(schema=schema, stop_event=threading.Event())
    scenarios = [_scenario(engine, operation, phase) for phase in (PhaseName.COVERAGE, PhaseName.FUZZING)]
    for scenario in scenarios:
        case = operation.Case(body="{book(id:1)}", _meta=_metadata())
        # GraphQL errors may intentionally fail API checks; this control is about cached call classification.
        _call_and_validate_once(case=case, ctx=engine, scenario=scenario, pending_events=[])
    outcomes = positive_call_outcomes(scenarios[1].recorder)
    assert (outcomes.accepted, outcomes.rejected) == ((1, 0) if accepted else (0, 1))
    assert len(calls) == 1
    assert not scenarios[1].recorder.interactions


def test_cached_check_failure_is_rethrown_without_rechecking():
    engine, operation, calls = _engine(accepted=(), rejection=500)
    earlier = _scenario(engine, operation, PhaseName.COVERAGE)
    first = operation.Case(path_parameters={"id": 1}, _meta=_metadata())
    with pytest.raises(FailureGroup) as original:
        _call_and_validate_once(case=first, ctx=engine, scenario=earlier, pending_events=[])
    assert earlier.recorder.checks[first.id]
    later = _scenario(engine, operation)
    duplicate = operation.Case(path_parameters={"id": 1}, _meta=_metadata())
    with pytest.raises(FailureGroup) as replayed:
        _call_and_validate_once(case=duplicate, ctx=engine, scenario=later, pending_events=[])
    assert replayed.value is original.value
    assert not later.recorder.cases and not later.recorder.interactions and not later.recorder.checks
    assert positive_call_outcomes(later.recorder).total == 0
    assert calls == [1]


def test_cached_transport_error_has_no_positive_outcome():
    engine, operation, calls = _engine()
    attempts = []
    transport_error = requests.Timeout("synthetic WSGI no-response")

    @operation.schema.app.before_request
    def timeout():
        attempts.append(request.path)
        raise transport_error

    earlier = _scenario(engine, operation, PhaseName.COVERAGE)
    first = operation.Case(path_parameters={"id": 1}, _meta=_metadata())
    with pytest.raises(requests.Timeout) as original:
        _call_and_validate_once(case=first, ctx=engine, scenario=earlier, pending_events=[])
    later = _scenario(engine, operation)
    duplicate = operation.Case(path_parameters={"id": 1}, _meta=_metadata())
    with pytest.raises(requests.Timeout) as replayed:
        _call_and_validate_once(case=duplicate, ctx=engine, scenario=later, pending_events=[])
    assert replayed.value is original.value is transport_error
    assert positive_call_outcomes(earlier.recorder).total == positive_call_outcomes(later.recorder).total == 0
    assert attempts == ["/items/1"] and calls == []
    assert not later.recorder.interactions and not later.recorder.checks
