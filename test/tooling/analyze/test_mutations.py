import json

import pytest

from scripts.analyze.metrics import analyze


def _negative_case(case_id, operator, response_status, *, location="body", extra_mutations=()):
    mutations = [
        {"operator": operator, "channel": "schema", "schema_pointer": "", "keywords": ["type"], "parameter": "x"}
    ]
    mutations.extend(extra_mutations)
    return {
        case_id: {
            "value": {
                "method": "POST",
                "meta": {
                    "generation": {"mode": "negative", "time": 0.01},
                    "components": {location: {"mode": "negative"}},
                    "phase": {"name": "fuzzing", "data": {"mutations": mutations}},
                },
            }
        }
    }, {case_id: {"response": {"status_code": response_status, "elapsed": 0.05}}}


def _scenario_payload(label, cases, interactions, *, timestamp=100.5):
    return {
        "ScenarioFinished": {
            "timestamp": timestamp,
            "recorder": {"label": label, "cases": cases, "interactions": interactions},
        }
    }


def _write_mutation_run(path, payloads):
    lines = [
        json.dumps({"Initialize": {"command": "x", "schemathesis_version": "t", "seed": 0}}),
        json.dumps({"PhaseStarted": {"phase": {"name": "Fuzzing"}, "timestamp": 100.0}}),
    ]
    lines.extend(json.dumps(p) for p in payloads)
    path.write_text("\n".join(lines) + "\n")


def test_mutations_by_operator_counts_each_mutation(tmp_path):
    cases1, intr1 = _negative_case("c1", "change_type", 422)
    cases2, intr2 = _negative_case("c2", "change_type", 422)
    cases3, intr3 = _negative_case("c3", "remove_required_property", 400)
    payload = _scenario_payload("POST /widgets", {**cases1, **cases2, **cases3}, {**intr1, **intr2, **intr3})
    path = tmp_path / "run.ndjson"
    _write_mutation_run(path, [payload])
    run = analyze(path)
    assert run.mutations.by_operator == {"change_type": 2, "remove_required_property": 1}


def test_mutations_by_location_uses_components(tmp_path):
    body_cases, body_intr = _negative_case("c1", "change_type", 422, location="body")
    query_cases, query_intr = _negative_case("c2", "change_type", 422, location="query")
    payload = _scenario_payload("POST /w", {**body_cases, **query_cases}, {**body_intr, **query_intr})
    path = tmp_path / "run.ndjson"
    _write_mutation_run(path, [payload])
    run = analyze(path)
    assert run.mutations.by_location == {"body": 1, "query": 1}


@pytest.mark.parametrize(
    ("status", "expected_count", "expected_rejected", "expected_accepted"),
    [
        (422, 1, 1, 0),  # 4xx — server caught the negative mutation (the desired outcome)
        (200, 1, 0, 1),  # 2xx — wire-side negation (the finding we want to flag)
        (500, 1, 0, 0),  # 5xx — server crashed; counts but is neither accept nor reject
    ],
    ids=["rejected", "accepted", "server-error"],
)
def test_mutation_grid_outcome_attribution(tmp_path, status, expected_count, expected_rejected, expected_accepted):
    cases, intr = _negative_case("c1", "change_type", status, location="body")
    path = tmp_path / "run.ndjson"
    _write_mutation_run(path, [_scenario_payload("POST /w", cases, intr)])
    cell = analyze(path).mutations.grid["body|change_type"]
    assert (cell.count, cell.rejected, cell.accepted) == (expected_count, expected_rejected, expected_accepted)


def test_mutation_grid_handles_multiple_mutations_per_case(tmp_path):
    extra = [{"operator": "value_violator", "channel": "value", "schema_pointer": "", "keywords": ["minimum"]}]
    cases, intr = _negative_case("c1", "change_type", 422, location="body", extra_mutations=extra)
    path = tmp_path / "run.ndjson"
    _write_mutation_run(path, [_scenario_payload("POST /w", cases, intr)])
    run = analyze(path)
    assert run.mutations.by_operator == {"change_type": 1, "value_violator": 1}
    assert run.mutations.grid["body|change_type"].count == 1
    assert run.mutations.grid["body|value_violator"].count == 1


def test_mutation_location_mixed_when_multiple_components_negative(tmp_path):
    case = {
        "c1": {
            "value": {
                "method": "POST",
                "meta": {
                    "generation": {"mode": "negative", "time": 0.01},
                    "components": {"body": {"mode": "negative"}, "query": {"mode": "negative"}},
                    "phase": {
                        "name": "fuzzing",
                        "data": {"mutations": [{"operator": "change_type", "channel": "schema"}]},
                    },
                },
            }
        }
    }
    path = tmp_path / "run.ndjson"
    _write_mutation_run(path, [_scenario_payload("POST /w", case, {"c1": {"response": {"status_code": 422}}})])
    run = analyze(path)
    assert run.mutations.by_location == {"mixed": 1}
    assert "mixed|change_type" in run.mutations.grid


def test_mutation_location_unknown_when_no_component_negative(tmp_path):
    case = {
        "c1": {
            "value": {
                "method": "POST",
                "meta": {
                    "generation": {"mode": "negative", "time": 0.01},
                    "components": {"body": {"mode": "positive"}},
                    "phase": {
                        "name": "fuzzing",
                        "data": {"mutations": [{"operator": "change_type", "channel": "schema"}]},
                    },
                },
            }
        }
    }
    path = tmp_path / "run.ndjson"
    _write_mutation_run(path, [_scenario_payload("POST /w", case, {"c1": {"response": {"status_code": 422}}})])
    assert analyze(path).mutations.by_location == {"unknown": 1}


def test_mutations_empty_when_no_mutation_data(tmp_path):
    case = {
        "c1": {
            "value": {
                "method": "GET",
                "meta": {
                    "generation": {"mode": "positive", "time": 0.01},
                    "components": {"path": {"mode": "positive"}},
                    "phase": {"name": "coverage", "data": {}},
                },
            }
        }
    }
    path = tmp_path / "run.ndjson"
    _write_mutation_run(path, [_scenario_payload("GET /w", case, {"c1": {"response": {"status_code": 200}}})])
    run = analyze(path)
    assert run.mutations.by_operator == {}
    assert run.mutations.by_location == {}
    assert run.mutations.grid == {}


def test_coverage_scenarios_count_per_kind(tmp_path):
    def _case(scenario):
        return {
            "value": {
                "method": "POST",
                "meta": {
                    "generation": {"mode": "negative", "time": 0.01},
                    "components": {"body": {"mode": "negative"}},
                    "phase": {"name": "coverage", "data": {"scenario": scenario}},
                },
            }
        }

    cases = {"c1": _case("value_above_maximum"), "c2": _case("value_above_maximum"), "c3": _case("missing_parameter")}
    interactions = {
        "c1": {"response": {"status_code": 422}},
        "c2": {"response": {"status_code": 200}},  # accepted -- wire-side coercion
        "c3": {"response": {"status_code": 400}},
    }
    path = tmp_path / "run.ndjson"
    _write_mutation_run(path, [_scenario_payload("POST /w", cases, interactions)])
    by_kind = analyze(path).coverage_scenarios.by_kind
    assert by_kind["value_above_maximum"].count == 2
    assert by_kind["value_above_maximum"].rejected == 1
    assert by_kind["value_above_maximum"].accepted == 1
    assert by_kind["missing_parameter"].count == 1
    assert by_kind["missing_parameter"].rejected == 1


def test_coverage_scenarios_only_collected_in_coverage_phase(tmp_path):
    case = {
        "c1": {
            "value": {
                "method": "POST",
                "meta": {
                    "generation": {"mode": "negative", "time": 0.01},
                    "components": {"body": {"mode": "negative"}},
                    "phase": {"name": "fuzzing", "data": {"scenario": "value_above_maximum"}},
                },
            }
        }
    }
    path = tmp_path / "run.ndjson"
    _write_mutation_run(path, [_scenario_payload("POST /w", case, {"c1": {"response": {"status_code": 422}}})])
    assert analyze(path).coverage_scenarios.by_kind == {}
