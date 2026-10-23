import pytest

from scripts.analyze.metrics import analyze
from test.tooling.analyze.helpers import scenario, write_run


def _failure_block(title, message):
    return {
        "name": "neg_data_rejection",
        "status": "failure",
        "failure_info": {"failure": {"type": title, "message": message}},
    }


def test_failures_per_minute_uses_unique_count(tmp_path):
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
                    "c2": [_failure_block("Schema mismatch", "y")],  # same fingerprint -> dedupes
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
    write_run(
        path,
        [
            {"EngineStarted": {"timestamp": 100.0}},
            {"PhaseStarted": {"phase": {"name": "fuzzing"}, "timestamp": 100.0}},
            payload,
            {"EngineFinished": {"timestamp": 160.0}},
        ],
    )
    run = analyze(path)
    assert run.duration_seconds == pytest.approx(60.0)
    assert run.rates.failures_per_minute == pytest.approx(2.0)


def test_twoxx_per_minute(tmp_path):
    cases = {
        f"c{i}": {"value": {"method": "GET", "meta": {"generation": {"mode": "positive", "time": 0.01}}}}
        for i in range(6)
    }
    interactions = {f"c{i}": {"response": {"status_code": 200}} for i in range(6)}
    payload = scenario("GET /a", cases, interactions, timestamp=160.0)
    path = tmp_path / "run.ndjson"
    write_run(
        path,
        [
            {"EngineStarted": {"timestamp": 100.0}},
            {"PhaseStarted": {"phase": {"name": "fuzzing"}, "timestamp": 100.0}},
            payload,
            {"EngineFinished": {"timestamp": 160.0}},
        ],
    )
    assert analyze(path).rates.twoxx_per_minute == pytest.approx(6.0)


def _scenario_at(label, timestamp, status):
    return scenario(
        label,
        {"c1": {"value": {"method": "GET", "meta": {"generation": {"mode": "positive"}}}}},
        {"c1": {"response": {"status_code": status}}},
        timestamp=timestamp,
    )


def test_new_operation_per_minute_timeline(tmp_path):
    path = tmp_path / "run.ndjson"
    write_run(
        path,
        [
            {"EngineStarted": {"timestamp": 100.0}},
            {"PhaseStarted": {"phase": {"name": "fuzzing"}, "timestamp": 100.0}},
            _scenario_at("GET /a", 100.5, 200),  # minute 0
            _scenario_at("GET /b", 165.0, 200),  # minute 1
            _scenario_at("GET /c", 230.0, 200),  # minute 2
            {"EngineFinished": {"timestamp": 240.0}},
        ],
    )
    assert analyze(path).rates.new_operation_per_minute_timeline == [
        {"minute": 0, "covered": 1},
        {"minute": 1, "covered": 2},
        {"minute": 2, "covered": 3},
    ]


def test_timeline_does_not_count_4xx_only_operations(tmp_path):
    path = tmp_path / "run.ndjson"
    write_run(
        path,
        [
            {"EngineStarted": {"timestamp": 100.0}},
            {"PhaseStarted": {"phase": {"name": "fuzzing"}, "timestamp": 100.0}},
            _scenario_at("GET /a", 100.5, 200),
            _scenario_at("GET /b", 165.0, 422),  # 4xx-only — excluded
            {"EngineFinished": {"timestamp": 180.0}},
        ],
    )
    assert analyze(path).rates.new_operation_per_minute_timeline == [{"minute": 0, "covered": 1}]


def test_timeline_uses_per_call_interaction_timestamp_when_present(tmp_path):
    # The scenario `timestamp` is shared by every case in the payload; per-interaction
    # `timestamp` is finer and must be preferred when available. Discovery for /a lands at
    # minute 0 (interaction timestamp 100.5), /b at minute 2 (interaction timestamp 220.0)
    # — even though both share the same scenario timestamp.
    payload = scenario(
        "Fuzz tests",
        {
            "c1": {"value": {"method": "GET", "path": "/a", "meta": {"generation": {"mode": "positive"}}}},
            "c2": {"value": {"method": "GET", "path": "/b", "meta": {"generation": {"mode": "positive"}}}},
        },
        {
            "c1": {"timestamp": 100.5, "response": {"status_code": 200}},
            "c2": {"timestamp": 220.0, "response": {"status_code": 200}},
        },
        timestamp=230.0,
    )
    path = tmp_path / "run.ndjson"
    write_run(
        path,
        [
            {"EngineStarted": {"timestamp": 100.0}},
            payload,
            {"EngineFinished": {"timestamp": 240.0}},
        ],
    )
    assert analyze(path).rates.new_operation_per_minute_timeline == [
        {"minute": 0, "covered": 1},
        {"minute": 1, "covered": 1},
        {"minute": 2, "covered": 2},
    ]


def test_reachability_lists_distinct_operations_with_2xx(tmp_path):
    path = tmp_path / "run.ndjson"
    write_run(
        path,
        [
            {"EngineStarted": {"timestamp": 100.0}},
            {"PhaseStarted": {"phase": {"name": "fuzzing"}, "timestamp": 100.0}},
            _scenario_at("GET /b", 100.5, 200),
            _scenario_at("GET /a", 101.0, 200),
            _scenario_at("GET /c", 102.0, 422),  # never gets a 2xx — excluded
            {"EngineFinished": {"timestamp": 130.0}},
        ],
    )
    assert analyze(path).reachability.covered_operations == ["GET /a", "GET /b"]


def test_reachability_counts_negative_2xx(tmp_path):
    # Negative-mode 2xx (NEGATIVE_DRIFT) is still a 2xx and marks the operation covered.
    payload = scenario(
        "POST /widgets",
        {
            "c1": {
                "value": {
                    "method": "POST",
                    "path": "/widgets",
                    "meta": {
                        "generation": {"mode": "negative"},
                        "components": {"body": {"mode": "negative"}},
                    },
                }
            }
        },
        {"c1": {"response": {"status_code": 200}}},
        timestamp=101.0,
    )
    path = tmp_path / "run.ndjson"
    write_run(
        path,
        [
            {"EngineStarted": {"timestamp": 100.0}},
            payload,
            {"EngineFinished": {"timestamp": 130.0}},
        ],
    )
    assert analyze(path).reachability.covered_operations == ["POST /widgets"]
