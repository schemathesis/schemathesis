import json

import pytest

from scripts.analyze.metrics import (
    Bucket,
    CallBucket,
    FailureRef,
    OperationMetrics,
    PhaseMetrics,
    RunMetrics,
    analyze,
    classify_call,
)


def test_bucket_total_sums_all_fields():
    bucket = Bucket(
        positive_accepted=3,
        negative_rejected=2,
        positive_drift=4,
        negative_drift=1,
        server_error=2,
        route_rejected=2,
        auth_rejected=1,
        other=3,
    )
    assert bucket.total == 18
    assert bucket.handler_reached == 12
    assert bucket.drift == 5


def test_bucket_handler_reached_ratio_zero_when_total_zero():
    assert Bucket().handler_reached_ratio == 0.0


def test_bucket_handler_reached_ratio_division():
    bucket = Bucket(positive_accepted=3, route_rejected=1)
    assert bucket.handler_reached_ratio == 0.75


def test_bucket_useful_ratio_excludes_drift():
    # Drift (P+4xx and N+2xx) is not "useful" — even though it reached the handler,
    # the call did not exercise the operation as intended.
    bucket = Bucket(positive_accepted=3, negative_rejected=2, server_error=1, positive_drift=4, negative_drift=2)
    assert bucket.useful == 6
    assert bucket.useful_ratio == 0.5


def test_run_metrics_default_empty():
    run = RunMetrics(
        schemathesis_version="4.16.1",
        seed=1,
        command="schemathesis run http://example.com/openapi.json",
        duration_seconds=0.0,
    )
    assert run.buckets.total == 0
    assert run.status_histogram == {}
    assert run.phases == []
    assert run.operations == {}
    assert run.failures == []


def test_failure_ref_is_hashable():
    failure = FailureRef(check_name="server_error", operation_label="GET /x", failure_type="boom", message="m")
    assert {failure}  # hashable


def test_failure_ref_fingerprint_excludes_message():
    a = FailureRef(check_name="c", operation_label="GET /x", failure_type="t", message="alpha")
    b = FailureRef(check_name="c", operation_label="GET /x", failure_type="t", message="beta")
    # Different messages must not produce different fingerprints — title-only dedup.
    assert a.fingerprint == b.fingerprint
    assert a.message != b.message


def test_operation_metrics_default_buckets_total_zero():
    op = OperationMetrics(label="GET /a")
    assert op.buckets.total == 0
    assert op.wasted_by_location == {}
    assert op.failures == []


def test_phase_metrics_truncated_default_false():
    phase = PhaseMetrics(name="fuzzing", duration_seconds=1.5, buckets=Bucket(positive_accepted=2))
    assert phase.truncated is False


def test_analyze_extracts_initialize_header(tmp_path):
    path = tmp_path / "run.ndjson"
    path.write_text(
        json.dumps(
            {
                "Initialize": {
                    "command": "schemathesis run http://x/openapi.json",
                    "schemathesis_version": "4.16.1",
                    "seed": 42,
                }
            }
        )
        + "\n"
    )
    run = analyze(path)
    assert run.schemathesis_version == "4.16.1"
    assert run.seed == 42
    assert run.command == "schemathesis run http://x/openapi.json"
    assert run.duration_seconds == 0.0


def _stub_call(*, status, overall_mode="positive", components=None, matches_route=True):
    return {
        "status": status,
        "overall_mode": overall_mode,
        "components": components or {},
        "matches_route": matches_route,
    }


def test_classify_positive_2xx_is_positive_accepted():
    assert classify_call(_stub_call(status=200)).bucket is CallBucket.POSITIVE_ACCEPTED


def test_classify_negative_2xx_is_negative_drift():
    assert classify_call(_stub_call(status=200, overall_mode="negative")).bucket is CallBucket.NEGATIVE_DRIFT


def test_classify_positive_4xx_is_positive_drift():
    result = classify_call(
        _stub_call(status=422, overall_mode="positive", components={"body": "positive", "headers": "negative"})
    )
    assert result.bucket is CallBucket.POSITIVE_DRIFT
    assert result.locations_present == ("body", "headers")


def test_classify_negative_4xx_is_negative_rejected():
    assert classify_call(_stub_call(status=400, overall_mode="negative")).bucket is CallBucket.NEGATIVE_REJECTED


def test_classify_5xx_is_server_error_regardless_of_mode():
    assert classify_call(_stub_call(status=503, overall_mode="positive")).bucket is CallBucket.SERVER_ERROR
    assert classify_call(_stub_call(status=502, overall_mode="negative")).bucket is CallBucket.SERVER_ERROR


def test_classify_401_is_auth_rejected():
    assert classify_call(_stub_call(status=401)).bucket is CallBucket.AUTH_REJECTED


def test_classify_404_no_route_match_is_route_rejected():
    assert classify_call(_stub_call(status=404, matches_route=False)).bucket is CallBucket.ROUTE_REJECTED


def test_classify_405_no_route_match_is_route_rejected():
    # Coverage method-mutation often elicits 405 from servers that strictly check the method
    # before the body; this must land in route_rejected, not in positive_drift / negative_rejected.
    assert classify_call(_stub_call(status=405, matches_route=False)).bucket is CallBucket.ROUTE_REJECTED


def test_classify_405_matching_route_falls_through_to_mode_logic():
    # 405 with matching route is unusual but shouldn't be route_rejected; positive 405 = drift.
    assert (
        classify_call(_stub_call(status=405, matches_route=True, overall_mode="positive")).bucket
        is CallBucket.POSITIVE_DRIFT
    )


def test_classify_404_matching_route_negative_is_negative_rejected():
    assert (
        classify_call(_stub_call(status=404, matches_route=True, overall_mode="negative")).bucket
        is CallBucket.NEGATIVE_REJECTED
    )


def test_classify_3xx_is_other():
    assert classify_call(_stub_call(status=302)).bucket is CallBucket.OTHER


def test_classify_transport_error_is_other():
    assert classify_call(_stub_call(status="transport-error")).bucket is CallBucket.OTHER


def test_classify_excludes_unknown_location():
    result = classify_call(
        _stub_call(status=400, overall_mode="positive", components={"body": "positive", "UNKNOWN": "positive"})
    )
    assert result.bucket is CallBucket.POSITIVE_DRIFT
    assert result.locations_present == ("body",)


def test_classify_locations_present_only_for_positive_drift():
    # Negative-rejected, server-error, etc must not surface participating locations.
    cases = [
        _stub_call(status=400, overall_mode="negative", components={"body": "negative"}),
        _stub_call(status=503, overall_mode="positive", components={"body": "positive"}),
        _stub_call(status=200, overall_mode="negative", components={"body": "negative"}),
    ]
    for call in cases:
        assert classify_call(call).locations_present == ()


def test_analyze_run_aggregates_buckets(analyzer_ndjson):
    run = analyze(analyzer_ndjson)
    assert run.buckets.total > 0
    assert run.buckets.handler_reached > 0
    assert sum(run.status_histogram.values()) == run.buckets.total


def test_analyze_run_separates_5xx(analyzer_ndjson):
    run = analyze(analyzer_ndjson)
    fivexx_total = sum(
        count for status, count in run.status_histogram.items() if isinstance(status, int) and 500 <= status < 600
    )
    assert fivexx_total >= 1


def test_analyze_per_operation_keys(analyzer_ndjson):
    run = analyze(analyzer_ndjson)
    labels = set(run.operations)
    assert any("/always-200" in label for label in labels)
    assert any("/echo-validate" in label for label in labels)


def test_analyze_per_operation_buckets_match_run(analyzer_ndjson):
    run = analyze(analyzer_ndjson)
    summed = sum(op.buckets.total for op in run.operations.values())
    assert summed == run.buckets.total


def test_analyze_wasted_by_location_tracks_positive_drift(analyzer_ndjson):
    run = analyze(analyzer_ndjson)
    echo_op = next((op for label, op in run.operations.items() if "/echo-validate" in label), None)
    assert echo_op is not None
    if echo_op.buckets.positive_drift > 0:
        assert echo_op.wasted_by_location.get("body", 0) >= 1


def test_analyze_negative_rejected_present(analyzer_ndjson):
    # The fixture runs --mode=all, so negative tests fire and must classify as negative_rejected
    # (4xx) rather than spilling into positive_drift.
    run = analyze(analyzer_ndjson)
    assert run.buckets.negative_rejected > 0


def test_analyze_server_error_recorded(analyzer_ndjson):
    # /always-500 always returns 5xx and must be counted as server_error, not lumped into useful.
    run = analyze(analyzer_ndjson)
    assert run.buckets.server_error >= 1


def test_analyze_run_duration_positive(analyzer_ndjson):
    assert analyze(analyzer_ndjson).duration_seconds > 0.0


def test_analyze_phases_recorded(analyzer_ndjson):
    run = analyze(analyzer_ndjson)
    names = {phase.name for phase in run.phases}
    assert names >= {"Coverage", "Fuzzing"}
    assert names.isdisjoint({"API probing", "Schema analysis"})
    for phase in run.phases:
        assert phase.duration_seconds >= 0.0
        assert phase.truncated is False


def test_analyze_phase_buckets_sum_to_run_buckets(analyzer_ndjson):
    run = analyze(analyzer_ndjson)
    assert sum(phase.buckets.total for phase in run.phases) == run.buckets.total


def test_analyze_truncated_phase(tmp_path):
    path = tmp_path / "run.ndjson"
    path.write_text(
        "\n".join(
            [
                json.dumps({"Initialize": {"command": "x", "schemathesis_version": "test", "seed": 0}}),
                json.dumps({"EngineStarted": {"timestamp": 0.0}}),
                json.dumps({"PhaseStarted": {"phase": {"name": "Fuzzing"}, "timestamp": 1.0}}),
            ]
        )
        + "\n"
    )
    run = analyze(path)
    assert [(p.name, p.truncated) for p in run.phases] == [("Fuzzing", True)]


def test_analyze_duration_falls_back_to_last_event_when_truncated(tmp_path):
    path = tmp_path / "run.ndjson"
    path.write_text(
        "\n".join(
            [
                json.dumps({"Initialize": {"command": "x", "schemathesis_version": "test", "seed": 0}}),
                json.dumps({"EngineStarted": {"timestamp": 100.0}}),
                json.dumps({"PhaseStarted": {"phase": {"name": "Fuzzing"}, "timestamp": 100.5}}),
                json.dumps({"PhaseFinished": {"phase": {"name": "Fuzzing"}, "timestamp": 142.5}}),
            ]
        )
        + "\n"
    )
    assert analyze(path).duration_seconds == 42.5


def test_analyze_routes_stateful_label_into_separate_field(tmp_path):
    path = tmp_path / "run.ndjson"
    payload = {
        "ScenarioFinished": {
            "recorder": {
                "label": "Stateful tests",
                "cases": {"a": {"value": {"method": "GET", "meta": {"generation": {"mode": "positive"}}}}},
                "interactions": {"a": {"response": {"status_code": 200}}},
            }
        }
    }
    path.write_text(
        "\n".join(
            [json.dumps({"Initialize": {"command": "x", "schemathesis_version": "t", "seed": 0}}), json.dumps(payload)]
        )
        + "\n"
    )
    run = analyze(path)
    assert "Stateful tests" not in run.operations
    assert run.stateful is not None
    assert run.stateful.label == "Stateful tests"
    assert run.stateful.buckets.positive_accepted == 1


def test_analyze_failures_collected(analyzer_ndjson):
    run = analyze(analyzer_ndjson)
    assert run.failures
    assert len({f.fingerprint for f in run.failures}) == len(run.failures)
    for failure in run.failures:
        assert failure.check_name and failure.fingerprint and failure.operation_label
        assert failure.operation_label in run.operations
        assert failure in run.operations[failure.operation_label].failures


def test_analyze_phase_timings_accumulate(tmp_path):
    path = tmp_path / "run.ndjson"
    payload = {
        "ScenarioFinished": {
            "timestamp": 100.5,
            "recorder": {
                "label": "GET /a",
                "cases": {
                    "x": {
                        "value": {
                            "method": "GET",
                            "meta": {"generation": {"mode": "positive", "time": 0.05}},
                        }
                    },
                    "y": {
                        "value": {
                            "method": "GET",
                            "meta": {"generation": {"mode": "positive", "time": 0.07}},
                        }
                    },
                },
                "interactions": {
                    "x": {"response": {"status_code": 200, "elapsed": 0.20}},
                    "y": {"response": {"status_code": 200, "elapsed": 0.30}},
                },
            },
        }
    }
    path.write_text(
        "\n".join(
            [
                json.dumps({"Initialize": {"command": "x", "schemathesis_version": "t", "seed": 0}}),
                json.dumps({"EngineStarted": {"timestamp": 100.0}}),
                json.dumps({"PhaseStarted": {"phase": {"name": "Fuzzing"}, "timestamp": 100.0}}),
                json.dumps(payload),
                json.dumps({"PhaseFinished": {"phase": {"name": "Fuzzing"}, "timestamp": 101.0}}),
            ]
        )
        + "\n"
    )
    run = analyze(path)
    fuzzing = next(p for p in run.phases if p.name == "Fuzzing")
    assert fuzzing.generation_seconds == pytest.approx(0.12)
    assert fuzzing.response_seconds == pytest.approx(0.50)


def test_analyze_per_operation_timings_accumulate(tmp_path):
    path = tmp_path / "run.ndjson"
    payload = {
        "ScenarioFinished": {
            "timestamp": 100.5,
            "recorder": {
                "label": "GET /a",
                "cases": {
                    "x": {"value": {"method": "GET", "meta": {"generation": {"mode": "positive", "time": 0.04}}}},
                    "y": {"value": {"method": "GET", "meta": {"generation": {"mode": "positive", "time": 0.06}}}},
                },
                "interactions": {
                    "x": {"response": {"status_code": 200, "elapsed": 0.10}},
                    "y": {"response": {"status_code": 200, "elapsed": 0.20}},
                },
            },
        }
    }
    path.write_text(
        "\n".join(
            [
                json.dumps({"Initialize": {"command": "x", "schemathesis_version": "t", "seed": 0}}),
                json.dumps({"PhaseStarted": {"phase": {"name": "Fuzzing"}, "timestamp": 100.0}}),
                json.dumps(payload),
            ]
        )
        + "\n"
    )
    run = analyze(path)
    op = run.operations["GET /a"]
    assert op.generation_seconds == pytest.approx(0.10)
    assert op.response_seconds == pytest.approx(0.30)


def test_analyze_phase_timings_skip_missing_fields(tmp_path):
    # Older NDJSON may omit generation.time and response.elapsed; the per-phase totals
    # must stay at 0.0 for missing values rather than counting them as zero contributions.
    path = tmp_path / "run.ndjson"
    payload = {
        "ScenarioFinished": {
            "timestamp": 100.5,
            "recorder": {
                "label": "GET /a",
                "cases": {"x": {"value": {"method": "GET", "meta": {"generation": {"mode": "positive"}}}}},
                "interactions": {"x": {"response": {"status_code": 200}}},
            },
        }
    }
    path.write_text(
        "\n".join(
            [
                json.dumps({"Initialize": {"command": "x", "schemathesis_version": "t", "seed": 0}}),
                json.dumps({"PhaseStarted": {"phase": {"name": "Fuzzing"}, "timestamp": 100.0}}),
                json.dumps(payload),
                json.dumps({"PhaseFinished": {"phase": {"name": "Fuzzing"}, "timestamp": 101.0}}),
            ]
        )
        + "\n"
    )
    run = analyze(path)
    fuzzing = next(p for p in run.phases if p.name == "Fuzzing")
    assert fuzzing.generation_seconds == 0.0
    assert fuzzing.response_seconds == 0.0
    assert fuzzing.buckets.positive_accepted == 1


def test_analyze_failure_occurrence_count_includes_duplicates(analyzer_ndjson):
    # failure_counts is the raw occurrence tally, so it must be at least as large as
    # the deduped run.failures count for every check name observed.
    run = analyze(analyzer_ndjson)
    assert run.failure_counts
    unique_per_check: dict[str, int] = {}
    for f in run.failures:
        unique_per_check[f.check_name] = unique_per_check.get(f.check_name, 0) + 1
    for check_name, unique in unique_per_check.items():
        assert run.failure_counts[check_name] >= unique


# ---- Mutation context extraction (negative-mode fuzzing) ----


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


def test_analyze_mutations_by_operator_counts_each_mutation(tmp_path):
    cases1, intr1 = _negative_case("c1", "change_type", 422)
    cases2, intr2 = _negative_case("c2", "change_type", 422)
    cases3, intr3 = _negative_case("c3", "remove_required_property", 400)
    payload = _scenario_payload(
        "POST /widgets",
        {**cases1, **cases2, **cases3},
        {**intr1, **intr2, **intr3},
    )
    path = tmp_path / "run.ndjson"
    _write_mutation_run(path, [payload])
    run = analyze(path)
    assert run.mutations.by_operator == {"change_type": 2, "remove_required_property": 1}


def test_analyze_mutations_by_location_uses_components(tmp_path):
    body_cases, body_intr = _negative_case("c1", "change_type", 422, location="body")
    query_cases, query_intr = _negative_case("c2", "change_type", 422, location="query")
    payload = _scenario_payload("POST /w", {**body_cases, **query_cases}, {**body_intr, **query_intr})
    path = tmp_path / "run.ndjson"
    _write_mutation_run(path, [payload])
    run = analyze(path)
    assert run.mutations.by_location == {"body": 1, "query": 1}


def test_analyze_mutation_grid_records_rejected_cells(tmp_path):
    # 422 -> rejected (server caught the negative mutation -- good)
    cases, intr = _negative_case("c1", "change_type", 422, location="body")
    payload = _scenario_payload("POST /w", cases, intr)
    path = tmp_path / "run.ndjson"
    _write_mutation_run(path, [payload])
    run = analyze(path)
    cell = run.mutations.grid["body|change_type"]
    assert cell.count == 1
    assert cell.rejected == 1
    assert cell.accepted == 0


def test_analyze_mutation_grid_records_accepted_cells(tmp_path):
    # 200 on a negative-mode case -> accepted (wire-side negation -- the finding we want to flag)
    cases, intr = _negative_case("c1", "change_type", 200, location="body")
    payload = _scenario_payload("POST /w", cases, intr)
    path = tmp_path / "run.ndjson"
    _write_mutation_run(path, [payload])
    run = analyze(path)
    cell = run.mutations.grid["body|change_type"]
    assert cell.count == 1
    assert cell.accepted == 1
    assert cell.rejected == 0


def test_analyze_mutation_grid_5xx_neither_accepted_nor_rejected(tmp_path):
    # 5xx means the server crashed -- not a clean accept/reject; count bumps but neither sub-counter does
    cases, intr = _negative_case("c1", "change_type", 500, location="body")
    payload = _scenario_payload("POST /w", cases, intr)
    path = tmp_path / "run.ndjson"
    _write_mutation_run(path, [payload])
    run = analyze(path)
    cell = run.mutations.grid["body|change_type"]
    assert cell.count == 1
    assert cell.rejected == 0
    assert cell.accepted == 0


def test_analyze_mutation_grid_handles_multiple_mutations_per_case(tmp_path):
    # When a case has N mutations, each contributes to the count under the case's location
    extra = [{"operator": "value_violator", "channel": "value", "schema_pointer": "", "keywords": ["minimum"]}]
    cases, intr = _negative_case("c1", "change_type", 422, location="body", extra_mutations=extra)
    payload = _scenario_payload("POST /w", cases, intr)
    path = tmp_path / "run.ndjson"
    _write_mutation_run(path, [payload])
    run = analyze(path)
    assert run.mutations.by_operator == {"change_type": 1, "value_violator": 1}
    assert run.mutations.grid["body|change_type"].count == 1
    assert run.mutations.grid["body|value_violator"].count == 1


def test_analyze_mutation_location_mixed_when_multiple_components_negative(tmp_path):
    # When more than one component is in negative mode, attribute to "mixed" -- per-mutation
    # location can't be reliably disambiguated from the NDJSON.
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
    payload = _scenario_payload("POST /w", case, {"c1": {"response": {"status_code": 422}}})
    path = tmp_path / "run.ndjson"
    _write_mutation_run(path, [payload])
    run = analyze(path)
    assert run.mutations.by_location == {"mixed": 1}
    assert "mixed|change_type" in run.mutations.grid


def test_analyze_mutation_location_unknown_when_no_component_negative(tmp_path):
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
    payload = _scenario_payload("POST /w", case, {"c1": {"response": {"status_code": 422}}})
    path = tmp_path / "run.ndjson"
    _write_mutation_run(path, [payload])
    run = analyze(path)
    assert run.mutations.by_location == {"unknown": 1}


def test_analyze_coverage_scenarios_count_per_kind(tmp_path):
    cases = {
        "c1": {
            "value": {
                "method": "POST",
                "meta": {
                    "generation": {"mode": "negative", "time": 0.01},
                    "components": {"body": {"mode": "negative"}},
                    "phase": {"name": "coverage", "data": {"scenario": "value_above_maximum"}},
                },
            }
        },
        "c2": {
            "value": {
                "method": "POST",
                "meta": {
                    "generation": {"mode": "negative", "time": 0.01},
                    "components": {"body": {"mode": "negative"}},
                    "phase": {"name": "coverage", "data": {"scenario": "value_above_maximum"}},
                },
            }
        },
        "c3": {
            "value": {
                "method": "POST",
                "meta": {
                    "generation": {"mode": "negative", "time": 0.01},
                    "components": {"body": {"mode": "negative"}},
                    "phase": {"name": "coverage", "data": {"scenario": "missing_parameter"}},
                },
            }
        },
    }
    interactions = {
        "c1": {"response": {"status_code": 422}},
        "c2": {"response": {"status_code": 200}},  # accepted -- wire-side coercion
        "c3": {"response": {"status_code": 400}},
    }
    payload = _scenario_payload("POST /w", cases, interactions)
    path = tmp_path / "run.ndjson"
    _write_mutation_run(path, [payload])
    run = analyze(path)
    by_kind = run.coverage_scenarios.by_kind
    assert by_kind["value_above_maximum"].count == 2
    assert by_kind["value_above_maximum"].rejected == 1
    assert by_kind["value_above_maximum"].accepted == 1
    assert by_kind["missing_parameter"].count == 1
    assert by_kind["missing_parameter"].rejected == 1


def test_analyze_coverage_scenarios_only_collected_in_coverage_phase(tmp_path):
    # A scenario field outside the coverage phase is ignored.
    cases = {
        "c1": {
            "value": {
                "method": "POST",
                "meta": {
                    "generation": {"mode": "negative", "time": 0.01},
                    "components": {"body": {"mode": "negative"}},
                    "phase": {"name": "fuzzing", "data": {"scenario": "value_above_maximum"}},
                },
            }
        },
    }
    interactions = {"c1": {"response": {"status_code": 422}}}
    payload = _scenario_payload("POST /w", cases, interactions)
    path = tmp_path / "run.ndjson"
    _write_mutation_run(path, [payload])
    run = analyze(path)
    assert run.coverage_scenarios.by_kind == {}


# ---- Rates + new-op timeline ----


def _failure_block(title, msg):
    return {
        "name": "neg_data_rejection",
        "status": "failure",
        "failure_info": {"failure": {"type": title, "message": msg}},
    }


def test_analyze_failures_per_minute_uses_unique_count(tmp_path):
    # 2 unique failures over 60s = 2 per minute (not the raw occurrences).
    payload = {
        "ScenarioFinished": {
            "timestamp": 160.0,
            "recorder": {
                "label": "GET /a",
                "cases": {
                    "c1": {"value": {"method": "GET", "meta": {"generation": {"mode": "negative", "time": 0.01}}}},
                    "c2": {"value": {"method": "GET", "meta": {"generation": {"mode": "negative", "time": 0.01}}}},
                    "c3": {"value": {"method": "GET", "meta": {"generation": {"mode": "negative", "time": 0.01}}}},
                },
                "checks": {
                    "c1": [_failure_block("Schema mismatch", "x")],
                    "c2": [_failure_block("Schema mismatch", "y")],  # same title -> dedupes
                    "c3": [_failure_block("Other failure", "z")],
                },
                "interactions": {
                    "c1": {"response": {"status_code": 400}},
                    "c2": {"response": {"status_code": 400}},
                    "c3": {"response": {"status_code": 400}},
                },
            },
        }
    }
    path = tmp_path / "run.ndjson"
    path.write_text(
        "\n".join(
            [
                json.dumps({"Initialize": {"command": "x", "schemathesis_version": "t", "seed": 0}}),
                json.dumps({"EngineStarted": {"timestamp": 100.0}}),
                json.dumps({"PhaseStarted": {"phase": {"name": "Fuzzing"}, "timestamp": 100.0}}),
                json.dumps(payload),
                json.dumps({"EngineFinished": {"timestamp": 160.0}}),
            ]
        )
        + "\n"
    )
    run = analyze(path)
    # 60s = 1 minute, 2 unique failures
    assert run.duration_seconds == pytest.approx(60.0)
    assert run.rates.failures_per_minute == pytest.approx(2.0)


def test_analyze_twoxx_per_minute(tmp_path):
    cases = {
        f"c{i}": {"value": {"method": "GET", "meta": {"generation": {"mode": "positive", "time": 0.01}}}}
        for i in range(6)
    }
    intr = {f"c{i}": {"response": {"status_code": 200}} for i in range(6)}
    payload = {
        "ScenarioFinished": {"timestamp": 160.0, "recorder": {"label": "GET /a", "cases": cases, "interactions": intr}}
    }
    path = tmp_path / "run.ndjson"
    path.write_text(
        "\n".join(
            [
                json.dumps({"Initialize": {"command": "x", "schemathesis_version": "t", "seed": 0}}),
                json.dumps({"EngineStarted": {"timestamp": 100.0}}),
                json.dumps({"PhaseStarted": {"phase": {"name": "Fuzzing"}, "timestamp": 100.0}}),
                json.dumps(payload),
                json.dumps({"EngineFinished": {"timestamp": 160.0}}),
            ]
        )
        + "\n"
    )
    run = analyze(path)
    # 6 2xx in 60s = 6 per minute
    assert run.rates.twoxx_per_minute == pytest.approx(6.0)


def test_analyze_new_op_per_minute_timeline(tmp_path):
    # Three operations discovered at minutes 0, 1, 2 respectively.
    def case(label, ts):
        return {
            "ScenarioFinished": {
                "timestamp": ts,
                "recorder": {
                    "label": label,
                    "cases": {
                        "c1": {"value": {"method": "GET", "meta": {"generation": {"mode": "positive", "time": 0.01}}}}
                    },
                    "interactions": {"c1": {"response": {"status_code": 200}}},
                },
            }
        }

    path = tmp_path / "run.ndjson"
    path.write_text(
        "\n".join(
            [
                json.dumps({"Initialize": {"command": "x", "schemathesis_version": "t", "seed": 0}}),
                json.dumps({"EngineStarted": {"timestamp": 100.0}}),
                json.dumps({"PhaseStarted": {"phase": {"name": "Fuzzing"}, "timestamp": 100.0}}),
                json.dumps(case("GET /a", 100.5)),  # minute 0
                json.dumps(case("GET /b", 165.0)),  # minute 1
                json.dumps(case("GET /c", 230.0)),  # minute 2
                json.dumps({"EngineFinished": {"timestamp": 240.0}}),
            ]
        )
        + "\n"
    )
    run = analyze(path)
    timeline = run.rates.new_op_per_minute_timeline
    assert timeline == [
        {"minute": 0, "covered": 1},
        {"minute": 1, "covered": 2},
        {"minute": 2, "covered": 3},
    ]


def test_analyze_timeline_does_not_count_4xx_only_ops(tmp_path):
    # An operation that only ever gets 4xx is not in covered_operations and not in timeline.
    def case(label, ts, status):
        return {
            "ScenarioFinished": {
                "timestamp": ts,
                "recorder": {
                    "label": label,
                    "cases": {
                        "c1": {"value": {"method": "GET", "meta": {"generation": {"mode": "positive", "time": 0.01}}}}
                    },
                    "interactions": {"c1": {"response": {"status_code": status}}},
                },
            }
        }

    path = tmp_path / "run.ndjson"
    path.write_text(
        "\n".join(
            [
                json.dumps({"Initialize": {"command": "x", "schemathesis_version": "t", "seed": 0}}),
                json.dumps({"EngineStarted": {"timestamp": 100.0}}),
                json.dumps({"PhaseStarted": {"phase": {"name": "Fuzzing"}, "timestamp": 100.0}}),
                json.dumps(case("GET /a", 100.5, 200)),
                json.dumps(case("GET /b", 165.0, 422)),  # always 4xx -- should not appear in timeline
                json.dumps({"EngineFinished": {"timestamp": 180.0}}),
            ]
        )
        + "\n"
    )
    run = analyze(path)
    timeline = run.rates.new_op_per_minute_timeline
    assert timeline == [{"minute": 0, "covered": 1}]


def test_analyze_reachability_lists_distinct_ops_with_2xx(tmp_path):
    def case(label, status, ts):
        return {
            "ScenarioFinished": {
                "timestamp": ts,
                "recorder": {
                    "label": label,
                    "cases": {
                        "c1": {"value": {"method": "GET", "meta": {"generation": {"mode": "positive", "time": 0.01}}}}
                    },
                    "interactions": {"c1": {"response": {"status_code": status}}},
                },
            }
        }

    path = tmp_path / "run.ndjson"
    path.write_text(
        "\n".join(
            [
                json.dumps({"Initialize": {"command": "x", "schemathesis_version": "t", "seed": 0}}),
                json.dumps({"EngineStarted": {"timestamp": 100.0}}),
                json.dumps({"PhaseStarted": {"phase": {"name": "Fuzzing"}, "timestamp": 100.0}}),
                json.dumps(case("GET /b", 200, 100.5)),
                json.dumps(case("GET /a", 200, 101.0)),
                json.dumps(case("GET /c", 422, 102.0)),  # 4xx-only -- not covered
                json.dumps({"EngineFinished": {"timestamp": 130.0}}),
            ]
        )
        + "\n"
    )
    run = analyze(path)
    # Sorted alphabetically; only ops with >= 1 2xx.
    assert run.reachability.covered_operations == ["GET /a", "GET /b"]


def test_analyze_reachability_counts_negative_2xx(tmp_path):
    # Negative-mode 2xx (NEGATIVE_DRIFT) is still a 2xx and should mark the op covered.
    payload = {
        "ScenarioFinished": {
            "timestamp": 101.0,
            "recorder": {
                "label": "POST /widgets",
                "cases": {
                    "c1": {
                        "value": {
                            "method": "POST",
                            "path": "/widgets",
                            "meta": {
                                "generation": {"mode": "negative", "time": 0.01},
                                "components": {"body": {"mode": "negative"}},
                            },
                        }
                    }
                },
                "interactions": {"c1": {"response": {"status_code": 200}}},
            },
        }
    }
    path = tmp_path / "run.ndjson"
    path.write_text(
        "\n".join(
            [
                json.dumps({"Initialize": {"command": "x", "schemathesis_version": "t", "seed": 0}}),
                json.dumps({"EngineStarted": {"timestamp": 100.0}}),
                json.dumps(payload),
                json.dumps({"EngineFinished": {"timestamp": 130.0}}),
            ]
        )
        + "\n"
    )
    run = analyze(path)
    assert run.reachability.covered_operations == ["POST /widgets"]


def test_analyze_fuzz_scenario_uses_per_case_label(tmp_path):
    # FuzzScenarioFinished carries the synthetic "Fuzz tests" recorder label; per-case
    # method/path must drive operation attribution.
    payload = {
        "FuzzScenarioFinished": {
            "timestamp": 101.0,
            "recorder": {
                "label": "Fuzz tests",
                "cases": {
                    "c1": {
                        "value": {
                            "method": "GET",
                            "path": "/owners",
                            "meta": {"generation": {"mode": "positive", "time": 0.01}},
                        }
                    },
                    "c2": {
                        "value": {
                            "method": "POST",
                            "path": "/pets",
                            "meta": {"generation": {"mode": "positive", "time": 0.01}},
                        }
                    },
                },
                "interactions": {
                    "c1": {"response": {"status_code": 200}},
                    "c2": {"response": {"status_code": 200}},
                },
            },
        }
    }
    path = tmp_path / "run.ndjson"
    path.write_text(
        "\n".join(
            [
                json.dumps({"Initialize": {"command": "x", "schemathesis_version": "t", "seed": 0}}),
                json.dumps({"EngineStarted": {"timestamp": 100.0}}),
                json.dumps(payload),
                json.dumps({"EngineFinished": {"timestamp": 130.0}}),
            ]
        )
        + "\n"
    )
    run = analyze(path)
    assert sorted(run.operations) == ["GET /owners", "POST /pets"]
    # And the synthetic "Fuzz tests" label must NOT appear among real operations.
    assert "Fuzz tests" not in run.operations


def test_analyze_coverage_method_mutation_404_classified_as_route_rejected(tmp_path):
    # Coverage scenario `unspecified_http_method` mutates case.method to one not declared by
    # the operation. A 404 in this case is route_rejected, not positive_drift — the route-match
    # check must compare against the *declared* method (POST), not the mutated one (TRACE).
    payload = {
        "ScenarioFinished": {
            "timestamp": 101.0,
            "recorder": {
                "label": "POST /widgets",
                "cases": {
                    "c1": {
                        "value": {
                            "method": "TRACE",
                            "path": "/widgets",
                            "meta": {
                                "generation": {"mode": "positive", "time": 0.01},
                                "phase": {
                                    "name": "coverage",
                                    "data": {"scenario": "unspecified_http_method"},
                                },
                            },
                        }
                    }
                },
                "interactions": {"c1": {"response": {"status_code": 404}}},
            },
        }
    }
    path = tmp_path / "run.ndjson"
    path.write_text(
        "\n".join(
            [
                json.dumps({"Initialize": {"command": "x", "schemathesis_version": "t", "seed": 0}}),
                json.dumps({"EngineStarted": {"timestamp": 100.0}}),
                json.dumps(payload),
                json.dumps({"EngineFinished": {"timestamp": 130.0}}),
            ]
        )
        + "\n"
    )
    run = analyze(path)
    assert run.buckets.route_rejected == 1
    assert run.buckets.positive_drift == 0


def test_analyze_stateful_404_with_matching_method_not_route_rejected(tmp_path):
    # Stateful 404 where the case calls the declared method should land in negative_rejected /
    # positive_drift, NOT route_rejected — the synthetic "Stateful tests" recorder label
    # must not drive route matching.
    payload = {
        "ScenarioFinished": {
            "timestamp": 101.0,
            "recorder": {
                "label": "Stateful tests",
                "cases": {
                    "c1": {
                        "value": {
                            "method": "GET",
                            "path": "/widgets/{id}",
                            "meta": {"generation": {"mode": "positive", "time": 0.01}},
                        }
                    }
                },
                "interactions": {"c1": {"response": {"status_code": 404}}},
            },
        }
    }
    path = tmp_path / "run.ndjson"
    path.write_text(
        "\n".join(
            [
                json.dumps({"Initialize": {"command": "x", "schemathesis_version": "t", "seed": 0}}),
                json.dumps({"EngineStarted": {"timestamp": 100.0}}),
                json.dumps(payload),
                json.dumps({"EngineFinished": {"timestamp": 130.0}}),
            ]
        )
        + "\n"
    )
    run = analyze(path)
    assert run.buckets.route_rejected == 0
    # Stateful aggregation captures the call.
    assert run.stateful is not None
    assert run.stateful.buckets.total == 1


def test_analyze_stateful_scenario_keeps_synthetic_label(tmp_path):
    # Stateful keeps grouping under the synthetic label so per-op rankings stay focused on real ops.
    payload = {
        "ScenarioFinished": {
            "timestamp": 101.0,
            "recorder": {
                "label": "Stateful tests",
                "cases": {
                    "c1": {
                        "value": {
                            "method": "GET",
                            "path": "/owners",
                            "meta": {"generation": {"mode": "positive", "time": 0.01}},
                        }
                    },
                },
                "interactions": {"c1": {"response": {"status_code": 200}}},
            },
        }
    }
    path = tmp_path / "run.ndjson"
    path.write_text(
        "\n".join(
            [
                json.dumps({"Initialize": {"command": "x", "schemathesis_version": "t", "seed": 0}}),
                json.dumps({"EngineStarted": {"timestamp": 100.0}}),
                json.dumps(payload),
                json.dumps({"EngineFinished": {"timestamp": 130.0}}),
            ]
        )
        + "\n"
    )
    run = analyze(path)
    assert run.stateful is not None
    assert run.stateful.label == "Stateful tests"
    assert "GET /owners" not in run.operations


def test_analyze_reachability_empty_when_no_2xx(tmp_path):
    payload = {
        "ScenarioFinished": {
            "timestamp": 101.0,
            "recorder": {
                "label": "GET /a",
                "cases": {
                    "c1": {"value": {"method": "GET", "meta": {"generation": {"mode": "positive", "time": 0.01}}}}
                },
                "interactions": {"c1": {"response": {"status_code": 422}}},
            },
        }
    }
    path = tmp_path / "run.ndjson"
    path.write_text(
        "\n".join(
            [
                json.dumps({"Initialize": {"command": "x", "schemathesis_version": "t", "seed": 0}}),
                json.dumps({"EngineStarted": {"timestamp": 100.0}}),
                json.dumps(payload),
                json.dumps({"EngineFinished": {"timestamp": 130.0}}),
            ]
        )
        + "\n"
    )
    run = analyze(path)
    assert run.reachability.covered_operations == []


def test_analyze_mutations_empty_when_no_mutation_data(tmp_path):
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
    payload = _scenario_payload("GET /w", case, {"c1": {"response": {"status_code": 200}}})
    path = tmp_path / "run.ndjson"
    _write_mutation_run(path, [payload])
    run = analyze(path)
    assert run.mutations.by_operator == {}
    assert run.mutations.by_location == {}
    assert run.mutations.grid == {}
