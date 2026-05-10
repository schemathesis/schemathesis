import json

from scripts.analyze.metrics import analyze


def _case(
    case_id,
    *,
    method="GET",
    path="/api/sessions",
    pool_draws=(),
    status=200,
):
    case = {
        "value": {
            "method": method,
            "path": path,
            "meta": {
                "generation": {"mode": "positive"},
                "phase": {"name": "fuzzing", "data": {"description": "Positive test case"}},
                "pool_draws": list(pool_draws),
            },
        },
    }
    interaction = {"response": {"status_code": status}} if status is not None else {}
    return {case_id: case}, {case_id: interaction}


def _draw(parameter_name="user_id", source_operation="POST /api/users", source_status=201, resource_name="User"):
    return {
        "location": "body",
        "parameter_name": parameter_name,
        "resource_name": resource_name,
        "resource_field": "id",
        "source_operation": source_operation,
        "source_status": source_status,
    }


def _scenario(label, cases, interactions, *, timestamp=100.5):
    return {
        "ScenarioFinished": {
            "timestamp": timestamp,
            "recorder": {"label": label, "cases": cases, "interactions": interactions},
        }
    }


def _write(path, payloads):
    lines = [json.dumps({"Initialize": {"command": "x", "schemathesis_version": "t", "seed": 0}})]
    lines.extend(json.dumps(payload) for payload in payloads)
    path.write_text("\n".join(lines) + "\n")


def test_no_pool_draws_yields_empty_stats(tmp_path):
    cases, interactions = _case("c1", status=200)
    path = tmp_path / "run.ndjson"
    _write(path, [_scenario("Fuzz tests", cases, interactions)])
    run = analyze(path)
    assert run.pool_draws.total_draws == 0
    assert run.pool_draws.cases_with_draws == 0
    assert run.pool_draws.by_edge == {}


def test_per_edge_counts_with_status_split(tmp_path):
    cases = {}
    interactions = {}
    # 3x 2xx, 1x 4xx, 1x 5xx for the User edge; 2x 2xx for a different Org edge.
    for idx, status in enumerate((200, 200, 200, 422, 503)):
        c, i = _case(f"u{idx}", pool_draws=[_draw()], status=status)
        cases.update(c)
        interactions.update(i)
    org_draw = _draw(parameter_name="org_id", source_operation="POST /api/orgs", resource_name="Org")
    for idx in range(2):
        c, i = _case(f"o{idx}", pool_draws=[org_draw], status=200)
        cases.update(c)
        interactions.update(i)

    path = tmp_path / "run.ndjson"
    _write(path, [_scenario("Fuzz tests", cases, interactions)])
    run = analyze(path)

    assert run.pool_draws.cases_with_draws == 7
    assert run.pool_draws.total_draws == 7
    user_edge = run.pool_draws.by_edge["GET /api/sessions||POST /api/users||User"]
    assert user_edge.count == 5
    assert user_edge.twoxx == 3
    assert user_edge.fourxx == 1
    assert user_edge.fivexx == 1
    org_edge = run.pool_draws.by_edge["GET /api/sessions||POST /api/orgs||Org"]
    assert org_edge.count == 2
    assert org_edge.twoxx == 2

    assert run.pool_draws.by_consumer == {"GET /api/sessions": 7}
    assert run.pool_draws.by_source == {"POST /api/users": 5, "POST /api/orgs": 2}
    assert run.pool_draws.by_resource == {"User": 5, "Org": 2}


def test_multi_draw_case_counted_once_for_cases_but_per_draw_for_edges(tmp_path):
    # One case with two draws (path + body slot from different producers).
    draws = [
        _draw(parameter_name="user_id", source_operation="POST /api/users", resource_name="User"),
        _draw(parameter_name="post_id", source_operation="POST /api/posts", resource_name="Post"),
    ]
    cases, interactions = _case("c1", method="PATCH", path="/api/posts/{id}", pool_draws=draws, status=200)
    path = tmp_path / "run.ndjson"
    _write(path, [_scenario("Fuzz tests", cases, interactions)])
    run = analyze(path)

    assert run.pool_draws.cases_with_draws == 1
    assert run.pool_draws.total_draws == 2
    assert set(run.pool_draws.by_edge) == {
        "PATCH /api/posts/{id}||POST /api/users||User",
        "PATCH /api/posts/{id}||POST /api/posts||Post",
    }
    assert run.pool_draws.by_consumer == {"PATCH /api/posts/{id}": 2}


def test_aggregates_across_scenarios(tmp_path):
    s1_cases, s1_intr = _case("a", pool_draws=[_draw()], status=200)
    s2_cases, s2_intr = _case("b", pool_draws=[_draw()], status=200)
    path = tmp_path / "run.ndjson"
    _write(
        path,
        [
            _scenario("Coverage", s1_cases, s1_intr),
            _scenario("Fuzz tests", s2_cases, s2_intr, timestamp=200.0),
        ],
    )
    run = analyze(path)
    edge = run.pool_draws.by_edge["GET /api/sessions||POST /api/users||User"]
    assert edge.count == 2
    assert run.pool_draws.total_draws == 2


def test_missing_response_buckets_into_other_status(tmp_path):
    # Interaction with no response (engine errored or timed out before getting one).
    cases, interactions = _case("c1", pool_draws=[_draw()], status=None)
    path = tmp_path / "run.ndjson"
    _write(path, [_scenario("Fuzz tests", cases, interactions)])
    run = analyze(path)
    edge = run.pool_draws.by_edge["GET /api/sessions||POST /api/users||User"]
    assert edge.count == 1
    assert edge.twoxx == 0
    assert edge.fourxx == 0
    assert edge.fivexx == 0
    assert edge.other_status == 1
