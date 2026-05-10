import json

import pytest

from scripts.analyze.metrics import _parse_transition_id, _walk_depth, analyze


def _stateful_case(case_id, *, parent_id=None, transition=None, applied=False, status=200):
    case = {
        "value": {
            "method": "GET",
            "path": "/x",
            "meta": {
                "generation": {"mode": "positive"},
                "phase": {"name": "stateful", "data": {"description": "Positive test case"}},
            },
        },
        "parent_id": parent_id,
        "is_transition_applied": applied,
    }
    if transition is not None:
        case["transition"] = transition
    interaction = {"response": {"status_code": status}} if status is not None else {}
    return {case_id: case}, {case_id: interaction}


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


@pytest.mark.parametrize(
    ("transition_id", "expected"),
    [
        (
            "GET /api/albums -> [200] DeleteAlbum -> DELETE /api/albums/{id}",
            ("GET /api/albums", "DELETE /api/albums/{id}", "DeleteAlbum", "200"),
        ),
        (
            "POST /pets -> [201] GetPet -> GET /pets/{id}",
            ("POST /pets", "GET /pets/{id}", "GetPet", "201"),
        ),
        # Unparsable shape (e.g. GraphQL, malformed) returns empty pieces rather than raising.
        ("opaque-id-without-arrows", ("", "", "", "")),
    ],
    ids=["openapi-200", "openapi-201", "unparsable"],
)
def test_parse_transition_id(transition_id, expected):
    assert _parse_transition_id(transition_id) == expected


def test_walk_depth_chain():
    parents = {"a": None, "b": "a", "c": "b", "d": "c"}
    cache = {}
    assert _walk_depth("a", parents, cache) == 0
    assert _walk_depth("d", parents, cache) == 3
    # Cache hit on the second call.
    assert _walk_depth("c", parents, cache) == 2


def test_walk_depth_handles_orphan_parent():
    # parent_id points outside the scenario — treat as root.
    parents = {"a": "missing"}
    assert _walk_depth("a", parents, {}) == 0


def test_accumulate_transitions_counts_per_id_with_status_split(tmp_path):
    transition_a = {
        "id": "GET /a -> [200] LinkA -> GET /b",
        "parent_id": "p1",
        "is_inferred": True,
    }
    transition_b = {
        "id": "GET /a -> [200] LinkB -> POST /c",
        "parent_id": "p2",
        "is_inferred": False,
    }
    cases = {}
    interactions = {}
    # 3 root cases (no transition), then 5 hits on LinkA (3x 2xx, 1x 4xx, 1x 5xx) and 2 hits
    # on LinkB (both 2xx). Mixed `applied` flag on LinkA.
    for cid in ("p1", "p2", "p3"):
        c, i = _stateful_case(cid, status=200)
        cases.update(c)
        interactions.update(i)
    for idx, status in enumerate((200, 200, 200, 422, 503)):
        c, i = _stateful_case(f"a{idx}", parent_id="p1", transition=transition_a, applied=idx < 4, status=status)
        cases.update(c)
        interactions.update(i)
    for idx in range(2):
        c, i = _stateful_case(f"b{idx}", parent_id="p2", transition=transition_b, applied=True, status=200)
        cases.update(c)
        interactions.update(i)
    path = tmp_path / "run.ndjson"
    _write(path, [_scenario("Stateful tests", cases, interactions)])

    run = analyze(path)
    by_id = run.transitions.by_id
    assert set(by_id) == {transition_a["id"], transition_b["id"]}

    a = by_id[transition_a["id"]]
    assert a.count == 5
    assert a.applied_count == 4
    assert a.twoxx == 3
    assert a.fourxx == 1
    assert a.fivexx == 1
    assert a.is_inferred is True
    assert a.source_operation == "GET /a"
    assert a.target_operation == "GET /b"
    assert a.link_name == "LinkA"
    assert a.status_code == "200"

    b = by_id[transition_b["id"]]
    assert b.count == 2
    assert b.is_inferred is False


def test_depth_aggregation_only_includes_stateful_scenarios(tmp_path):
    coverage_cases, coverage_intr = _stateful_case("cov1", status=200)
    coverage_cases["cov1"]["value"]["meta"]["phase"]["name"] = "coverage"
    fuzzing_cases, fuzzing_intr = _stateful_case("fz1", status=200)
    fuzzing_cases["fz1"]["value"]["meta"]["phase"]["name"] = "fuzzing"

    transition = {"id": "GET /a -> [200] L -> GET /b", "parent_id": "root", "is_inferred": True}
    stateful_cases = {}
    stateful_intr = {}
    root, root_intr = _stateful_case("root", status=200)
    stateful_cases.update(root)
    stateful_intr.update(root_intr)
    chained, chained_intr = _stateful_case("ch", parent_id="root", transition=transition, applied=True, status=200)
    stateful_cases.update(chained)
    stateful_intr.update(chained_intr)

    path = tmp_path / "run.ndjson"
    _write(
        path,
        [
            _scenario("Coverage", coverage_cases, coverage_intr),
            _scenario("Fuzz tests", fuzzing_cases, fuzzing_intr),
            _scenario("Stateful tests", stateful_cases, stateful_intr),
        ],
    )

    run = analyze(path)
    # Only the 2 stateful cases participate in depth.
    assert run.transitions.depth.cases == 2
    assert run.transitions.depth.max == 1
    assert run.transitions.depth.by_depth == {0: 1, 1: 1}


def test_distinct_targets_collected_across_scenarios(tmp_path):
    t1 = {"id": "GET /a -> [200] L -> GET /b", "parent_id": "r1", "is_inferred": True}
    t2 = {"id": "GET /a -> [200] L2 -> POST /c", "parent_id": "r2", "is_inferred": True}
    s1_cases, s1_intr = _stateful_case("r1", status=200)
    c, i = _stateful_case("c1", parent_id="r1", transition=t1, applied=True, status=200)
    s1_cases.update(c)
    s1_intr.update(i)
    s2_cases, s2_intr = _stateful_case("r2", status=200)
    c, i = _stateful_case("c2", parent_id="r2", transition=t2, applied=True, status=200)
    s2_cases.update(c)
    s2_intr.update(i)

    path = tmp_path / "run.ndjson"
    _write(
        path,
        [
            _scenario("Stateful tests", s1_cases, s1_intr),
            _scenario("Stateful tests", s2_cases, s2_intr, timestamp=200.0),
        ],
    )

    run = analyze(path)
    assert run.transitions.distinct_targets == ["GET /b", "POST /c"]


def test_no_transitions_for_stateful_runs_with_only_initial_steps(tmp_path):
    cases, intr = _stateful_case("init", status=200)
    path = tmp_path / "run.ndjson"
    _write(path, [_scenario("Stateful tests", cases, intr)])
    run = analyze(path)
    assert run.transitions.by_id == {}
    assert run.transitions.depth.cases == 1
    assert run.transitions.depth.max == 0
