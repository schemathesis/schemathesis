import pytest

from scripts.analyze.metrics import analyze


def test_analyze_extracts_initialize_header(tmp_path, write_ndjson):
    path = tmp_path / "run.ndjson"
    write_ndjson(
        path,
        [
            {
                "Initialize": {
                    "command": "schemathesis run http://x/openapi.json",
                    "schemathesis_version": "4.16.1",
                    "seed": 42,
                }
            }
        ],
    )
    run = analyze(path)
    assert run.schemathesis_version == "4.16.1"
    assert run.seed == 42
    assert run.command == "schemathesis run http://x/openapi.json"
    assert run.duration_seconds == 0.0


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
    summed = sum(operation.buckets.total for operation in run.operations.values())
    stateful_total = run.stateful.buckets.total if run.stateful is not None else 0
    assert summed + stateful_total == run.buckets.total


def test_analyze_wasted_by_location_tracks_positive_drift(analyzer_ndjson):
    run = analyze(analyzer_ndjson)
    echo = next((operation for label, operation in run.operations.items() if "/echo-validate" in label), None)
    assert echo is not None
    if echo.buckets.positive_drift > 0:
        assert echo.wasted_by_location.get("body", 0) >= 1


def test_analyze_negative_rejected_present(analyzer_ndjson):
    # `--mode=all` fires negative tests, which must classify as negative_rejected (4xx)
    # rather than spilling into positive_drift.
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


def test_analyze_failures_collected(analyzer_ndjson):
    run = analyze(analyzer_ndjson)
    assert run.failures
    assert len({f.fingerprint for f in run.failures}) == len(run.failures)
    for failure in run.failures:
        assert failure.check_name and failure.fingerprint and failure.operation_label
        assert failure.operation_label in run.operations
        assert failure in run.operations[failure.operation_label].failures


def test_analyze_failure_occurrence_count_includes_duplicates(analyzer_ndjson):
    run = analyze(analyzer_ndjson)
    assert run.failure_counts
    unique_per_check: dict[str, int] = {}
    for failure in run.failures:
        unique_per_check[failure.check_name] = unique_per_check.get(failure.check_name, 0) + 1
    for check_name, unique in unique_per_check.items():
        assert run.failure_counts[check_name] >= unique


def test_analyze_truncated_phase(tmp_path, write_ndjson):
    path = tmp_path / "run.ndjson"
    write_ndjson(
        path,
        [
            {"Initialize": {"command": "x", "schemathesis_version": "test", "seed": 0}},
            {"EngineStarted": {"timestamp": 0.0}},
            {"PhaseStarted": {"phase": {"name": "Fuzzing"}, "timestamp": 1.0}},
        ],
    )
    run = analyze(path)
    assert [(phase.name, phase.truncated) for phase in run.phases] == [("Fuzzing", True)]


def test_analyze_duration_falls_back_to_last_event_when_truncated(tmp_path, write_ndjson):
    path = tmp_path / "run.ndjson"
    write_ndjson(
        path,
        [
            {"Initialize": {"command": "x", "schemathesis_version": "test", "seed": 0}},
            {"EngineStarted": {"timestamp": 100.0}},
            {"PhaseStarted": {"phase": {"name": "Fuzzing"}, "timestamp": 100.5}},
            {"PhaseFinished": {"phase": {"name": "Fuzzing"}, "timestamp": 142.5}},
        ],
    )
    assert analyze(path).duration_seconds == 42.5


def test_analyze_routes_stateful_label_into_separate_field(tmp_path, write_ndjson):
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
    write_ndjson(path, [{"Initialize": {"command": "x", "schemathesis_version": "t", "seed": 0}}, payload])
    run = analyze(path)
    assert "Stateful tests" not in run.operations
    assert run.stateful is not None
    assert run.stateful.label == "Stateful tests"
    assert run.stateful.buckets.positive_accepted == 1


def test_analyze_phase_timings_accumulate(tmp_path, write_ndjson):
    path = tmp_path / "run.ndjson"
    payload = {
        "ScenarioFinished": {
            "timestamp": 100.5,
            "recorder": {
                "label": "GET /a",
                "cases": {
                    "x": {"value": {"method": "GET", "meta": {"generation": {"mode": "positive", "time": 0.05}}}},
                    "y": {"value": {"method": "GET", "meta": {"generation": {"mode": "positive", "time": 0.07}}}},
                },
                "interactions": {
                    "x": {"response": {"status_code": 200, "elapsed": 0.20}},
                    "y": {"response": {"status_code": 200, "elapsed": 0.30}},
                },
            },
        }
    }
    write_ndjson(
        path,
        [
            {"Initialize": {"command": "x", "schemathesis_version": "t", "seed": 0}},
            {"EngineStarted": {"timestamp": 100.0}},
            {"PhaseStarted": {"phase": {"name": "Fuzzing"}, "timestamp": 100.0}},
            payload,
            {"PhaseFinished": {"phase": {"name": "Fuzzing"}, "timestamp": 101.0}},
        ],
    )
    run = analyze(path)
    fuzzing = next(phase for phase in run.phases if phase.name == "Fuzzing")
    assert fuzzing.generation_seconds == pytest.approx(0.12)
    assert fuzzing.response_seconds == pytest.approx(0.50)


def test_analyze_per_operation_timings_accumulate(tmp_path, write_ndjson):
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
    write_ndjson(
        path,
        [
            {"Initialize": {"command": "x", "schemathesis_version": "t", "seed": 0}},
            {"PhaseStarted": {"phase": {"name": "Fuzzing"}, "timestamp": 100.0}},
            payload,
        ],
    )
    run = analyze(path)
    operation = run.operations["GET /a"]
    assert operation.generation_seconds == pytest.approx(0.10)
    assert operation.response_seconds == pytest.approx(0.30)


def test_analyze_phase_timings_skip_missing_fields(tmp_path, write_ndjson):
    # Older NDJSON may omit generation.time and response.elapsed; per-phase totals
    # must stay at 0.0 rather than counting absent values as zero contributions.
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
    write_ndjson(
        path,
        [
            {"Initialize": {"command": "x", "schemathesis_version": "t", "seed": 0}},
            {"PhaseStarted": {"phase": {"name": "Fuzzing"}, "timestamp": 100.0}},
            payload,
            {"PhaseFinished": {"phase": {"name": "Fuzzing"}, "timestamp": 101.0}},
        ],
    )
    run = analyze(path)
    fuzzing = next(phase for phase in run.phases if phase.name == "Fuzzing")
    assert fuzzing.generation_seconds == 0.0
    assert fuzzing.response_seconds == 0.0
    assert fuzzing.buckets.positive_accepted == 1


def test_analyze_fuzz_scenario_uses_per_case_label(tmp_path, write_ndjson):
    # FuzzScenarioFinished carries the synthetic "Fuzz tests" recorder label; per-case
    # method/path must drive operation attribution.
    payload = {
        "FuzzScenarioFinished": {
            "timestamp": 101.0,
            "recorder": {
                "label": "Fuzz tests",
                "cases": {
                    "c1": {"value": {"method": "GET", "path": "/owners", "meta": {"generation": {"mode": "positive"}}}},
                    "c2": {"value": {"method": "POST", "path": "/pets", "meta": {"generation": {"mode": "positive"}}}},
                },
                "interactions": {
                    "c1": {"response": {"status_code": 200}},
                    "c2": {"response": {"status_code": 200}},
                },
            },
        }
    }
    path = tmp_path / "run.ndjson"
    write_ndjson(
        path,
        [
            {"Initialize": {"command": "x", "schemathesis_version": "t", "seed": 0}},
            {"EngineStarted": {"timestamp": 100.0}},
            payload,
            {"EngineFinished": {"timestamp": 130.0}},
        ],
    )
    run = analyze(path)
    assert sorted(run.operations) == ["GET /owners", "POST /pets"]
    assert "Fuzz tests" not in run.operations


def test_analyze_coverage_method_mutation_404_classified_as_route_rejected(tmp_path, write_ndjson):
    # Coverage `unspecified_http_method` mutates case.method (e.g. TRACE on a POST-only
    # operation). Route-match must use the *declared* method, so a 404 lands in
    # route_rejected — not positive_drift.
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
                                "generation": {"mode": "positive"},
                                "phase": {"name": "coverage", "data": {"scenario": "unspecified_http_method"}},
                            },
                        }
                    }
                },
                "interactions": {"c1": {"response": {"status_code": 404}}},
            },
        }
    }
    path = tmp_path / "run.ndjson"
    write_ndjson(
        path,
        [
            {"Initialize": {"command": "x", "schemathesis_version": "t", "seed": 0}},
            {"EngineStarted": {"timestamp": 100.0}},
            payload,
            {"EngineFinished": {"timestamp": 130.0}},
        ],
    )
    run = analyze(path)
    assert run.buckets.route_rejected == 1
    assert run.buckets.positive_drift == 0


def test_analyze_stateful_404_with_matching_method_not_route_rejected(tmp_path, write_ndjson):
    # The synthetic "Stateful tests" recorder label must not drive route matching, otherwise
    # every case would compare its method against itself.
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
                            "meta": {"generation": {"mode": "positive"}},
                        }
                    }
                },
                "interactions": {"c1": {"response": {"status_code": 404}}},
            },
        }
    }
    path = tmp_path / "run.ndjson"
    write_ndjson(
        path,
        [
            {"Initialize": {"command": "x", "schemathesis_version": "t", "seed": 0}},
            {"EngineStarted": {"timestamp": 100.0}},
            payload,
            {"EngineFinished": {"timestamp": 130.0}},
        ],
    )
    run = analyze(path)
    assert run.buckets.route_rejected == 0
    assert run.stateful is not None
    assert run.stateful.buckets.total == 1


def test_analyze_stateful_scenario_keeps_synthetic_label(tmp_path, write_ndjson):
    payload = {
        "ScenarioFinished": {
            "timestamp": 101.0,
            "recorder": {
                "label": "Stateful tests",
                "cases": {
                    "c1": {"value": {"method": "GET", "path": "/owners", "meta": {"generation": {"mode": "positive"}}}}
                },
                "interactions": {"c1": {"response": {"status_code": 200}}},
            },
        }
    }
    path = tmp_path / "run.ndjson"
    write_ndjson(
        path,
        [
            {"Initialize": {"command": "x", "schemathesis_version": "t", "seed": 0}},
            {"EngineStarted": {"timestamp": 100.0}},
            payload,
            {"EngineFinished": {"timestamp": 130.0}},
        ],
    )
    run = analyze(path)
    assert run.stateful is not None
    assert run.stateful.label == "Stateful tests"
    assert "GET /owners" not in run.operations
