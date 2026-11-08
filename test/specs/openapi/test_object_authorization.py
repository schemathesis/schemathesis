import json

import pytest
import requests

import schemathesis
from schemathesis.auths import AuthContext
from schemathesis.engine.recorder import ScenarioRecorder
from schemathesis.generation import GenerationMode
from schemathesis.generation.meta import CaseMetadata, FuzzingPhaseData, GenerationInfo, PhaseInfo, TestPhase
from schemathesis.openapi.checks import ObjectLevelAuthorizationViolation
from schemathesis.resources import PoolDraw
from schemathesis.specs.openapi.checks import object_level_authorization
from schemathesis.specs.openapi.extra_data_source import referenced_by
from schemathesis.specs.openapi.object_authorization import is_equivalent, listing_case, listings, path_resource
from test.utils import check_context, crash_cache_dir

ORDER_SCHEMA = {
    "type": "object",
    "properties": {"id": {"type": "integer"}, "item": {"type": "string"}, "owner": {"type": "string"}},
}
OWNED_ORDER = "`bob` received the `Order` that `alice` owns"
PEERS_AUTH = {
    "auth": [
        {"name": "alice", "fixedHeaders": [{"name": "Authorization", "value": "ApiKey alice"}]},
        {"name": "bob", "fixedHeaders": [{"name": "Authorization", "value": "ApiKey bob"}]},
    ]
}
READ = "GET /api/orders/{order_id}"


def _auth_file(tmp_path):
    path = tmp_path / "auth.json"
    path.write_text(json.dumps(PEERS_AUTH))
    return str(path)


def _run(cli, api, tmp_path, *, peers=("alice", "bob"), phases="fuzzing"):
    wfc = {"path": _auth_file(tmp_path)}
    if peers:
        wfc["peers"] = list(peers)
    return cli.run(
        api.schema_url,
        "--max-examples=10",
        f"--phases={phases}",
        "--checks=object_level_authorization",
        config={"auth": {"wfc": wfc}},
    )


def _peer_requests(requests):
    return [r for r in requests if r.headers.get("Authorization") == "ApiKey bob"]


def _app(ctx, tmp_path, *orders_by, **app):
    api = ctx.openapi.apps.wfc_owned_orders("vulnerable", **app)
    # One order per caller, numbered in creation order.
    for user in orders_by:
        requests.post(f"{api.base_url}/api/orders", json={"item": "book"}, headers={"Authorization": f"ApiKey {user}"})
    config = schemathesis.Config.from_dict({"auth": {"wfc": {"path": _auth_file(tmp_path), "peers": ["alice", "bob"]}}})
    return api, schemathesis.openapi.from_url(api.schema_url, config=config)


def _alice_case(schema, operation=READ, draws=(), **kwargs):
    method, path = operation.split(" ", 1)
    operation = schema[path][method]
    meta = CaseMetadata(
        generation=GenerationInfo(time=0.0, mode=GenerationMode.POSITIVE),
        components={},
        phase=PhaseInfo(name=TestPhase.FUZZING, data=FuzzingPhaseData("", None, None, None)),
        pool_draws=draws,
    )
    case = operation.Case(_meta=meta, **kwargs)
    schema.auth.set(case, AuthContext(operation=operation, app=None))
    return case


def _draw(source="POST /api/orders", identity="alice", location="path", parameter="order_id"):
    return PoolDraw(location, parameter, "Order", "id", source, 200, source_identity=identity)


def _after(parent, parent_response, case):
    recorder = ScenarioRecorder(label="test")
    recorder.record_case(parent_id=None, case=parent, transition=None, is_transition_applied=False)
    recorder.record_response(case_id=parent.id, response=parent_response)
    recorder.record_case(parent_id=parent.id, case=case, transition=None, is_transition_applied=False)
    return check_context(recorder=recorder)


def _check(api, context, case):
    """Whether `case` is reported, and which requests the peer sent while checking it."""
    response = case.call()
    sent = len(api.requests)
    try:
        object_level_authorization(context, response, case)
        reported = False
    except ObjectLevelAuthorizationViolation as exc:
        assert OWNED_ORDER in exc.message
        reported = True
    return reported, [r.path for r in _peer_requests(api.requests[sent:])]


def _verdict(api, context, case):
    """Whether `case` is reported, and whether the peer was probed for it."""
    reported, peer_requests = _check(api, context, case)
    return reported, bool(peer_requests)


@pytest.mark.snapshot(replace_reproduce_with=True)
@pytest.mark.parametrize("phases", ["fuzzing", "stateful"])
def test_peer_read_is_reported(cli, ctx, tmp_path, snapshot_cli, phases):
    api = ctx.openapi.apps.wfc_owned_orders("vulnerable")
    assert _run(cli, api, tmp_path, phases=phases) == snapshot_cli


def test_reproduction_names_the_sender(cli, ctx, tmp_path, snapshot_cli):
    api = ctx.openapi.apps.wfc_owned_orders("vulnerable")
    assert _run(cli, api, tmp_path) == snapshot_cli


def test_stateful_reproduction_names_each_sender(cli, ctx, tmp_path):
    api = ctx.openapi.apps.wfc_owned_orders("vulnerable")
    _run(cli, api, tmp_path, phases="stateful")
    (crash_file,) = crash_cache_dir(tmp_path).glob("*_object_level_authorization_*.json")
    code_sample = json.loads(crash_file.read_text())["code_sample"]
    assert [line for line in code_sample.splitlines() if line.startswith("#")] == [
        "# as alice (owner)",
        "# as alice (owner)",
        "# as bob (peer)",
    ]


@pytest.mark.parametrize("phases", ["fuzzing", "stateful"])
@pytest.mark.parametrize(
    ("app", "reported", "probed"),
    [
        pytest.param({"policy": "owner_checked"}, False, True, id="owner-checked"),
        pytest.param({"policy": "public_view"}, False, True, id="redacted-for-peers"),
        pytest.param({"policy": "fully_public"}, False, True, id="public"),
        pytest.param({"policy": "peer_html"}, False, True, id="html-for-peers"),
        pytest.param({"listing": "all"}, False, True, id="listed-to-everyone"),
        pytest.param({"listing": "all_renamed"}, False, True, id="listed-to-everyone-as-another-schema"),
        pytest.param({"listing": "own"}, True, True, id="listed-to-owner"),
        pytest.param({"listing": "ids"}, True, True, id="only-ids-listed-to-everyone"),
        # Servers often require credentials the schema never declares; the anonymous replay tells public data apart.
        pytest.param({"security": "undeclared"}, True, True, id="undeclared-auth"),
        pytest.param({"security": "undeclared", "policy": "owner_checked"}, False, True, id="undeclared-owner-checked"),
        pytest.param({"security": "undeclared", "policy": "fully_public"}, False, True, id="undeclared-public"),
        pytest.param({"security": "public_read"}, False, False, id="read-declared-public"),
        pytest.param({"security": "public_root"}, False, False, id="schema-declared-public"),
    ],
)
def test_run(cli, ctx, tmp_path, phases, app, reported, probed):
    api = ctx.openapi.apps.wfc_owned_orders(**{"policy": "vulnerable", **app})
    result = _run(cli, api, tmp_path, phases=phases)
    assert (result.exit_code, bool(_peer_requests(api.requests))) == (int(reported), probed), result.stdout


def test_without_peers_no_probe_is_sent(cli, ctx, tmp_path):
    api = ctx.openapi.apps.wfc_owned_orders("vulnerable")
    _run(cli, api, tmp_path, peers=())
    assert _peer_requests(api.requests) == []


@pytest.mark.snapshot(replace_reproduce_with=True)
@pytest.mark.parametrize(
    "checks", ["--checks=object_level_authorization", "--exclude-checks=ignored_auth"], ids=["selected", "default"]
)
def test_without_peers_warns_only_when_selected(cli, ctx, snapshot_cli, checks):
    api = ctx.openapi.apps.wfc_owned_orders("vulnerable")
    assert (
        cli.run(
            api.schema_url,
            "--max-examples=10",
            "--phases=fuzzing",
            # Negative bodies fail `POST /api/orders` with a platform-dependent minimal example
            "--mode=positive",
            checks,
            "-H",
            "Authorization: ApiKey alice",
        )
        == snapshot_cli
    )


def test_cli_wfc_path_preserves_configured_peers(cli, ctx, tmp_path):
    api = ctx.openapi.apps.wfc_owned_orders("vulnerable")
    auth = _auth_file(tmp_path)
    path = tmp_path / "report.json"

    result = cli.run(
        api.schema_url,
        "--max-examples=10",
        "--phases=fuzzing",
        "--checks=object_level_authorization",
        f"--auth-wfc={auth}",
        f"--report-wfc-path={path}",
        config={"auth": {"wfc": {"path": auth, "peers": ["alice", "bob"]}}},
    )

    assert (result.exit_code, bool(_peer_requests(api.requests))) == (1, True), result.stdout
    report = json.loads(path.read_text(encoding="utf-8"))
    (fault,) = report["faults"]["foundFaults"]
    assert (fault["operationId"], [category["code"] for category in fault["faultCategories"]]) == (
        "GET:/api/orders/{order_id}",
        [906],
    )
    assert fault["testCaseId"] in {test_case["id"] for test_case in report["testCases"]}


def _replay(cli, tmp_path, *, users=PEERS_AUTH["auth"], peers=("alice", "bob")):
    path = tmp_path / "replay-auth.json"
    path.write_text(json.dumps({"auth": users}))
    wfc = {"path": str(path)}
    if peers:
        wfc["peers"] = list(peers)
    return cli.main("replay", config={"auth": {"wfc": wfc}})


def _senders(api):
    return [(request.method, request.headers.get("Authorization")) for request in api.requests]


def _strip_identities(tmp_path):
    for path in crash_cache_dir(tmp_path).glob("*.json"):
        data = json.loads(path.read_text())
        for step in data.get("sequence", ()):
            step.pop("auth_identity", None)
        path.write_text(json.dumps(data))


ALICE = "ApiKey alice"
BOB = "ApiKey bob"


@pytest.mark.snapshot(replace_reproduce_with=True)
@pytest.mark.parametrize(
    ("phases", "replayed"),
    [
        # The owner read, the recorded peer read, then the re-run check's own peer and anonymous probes.
        ("fuzzing", [("GET", ALICE), ("GET", BOB), ("GET", BOB), ("GET", None)]),
        ("stateful", [("POST", ALICE), ("GET", ALICE), ("GET", BOB), ("GET", BOB), ("GET", None)]),
    ],
)
def test_replay_sends_each_step_as_its_recorded_identity(cli, ctx, tmp_path, snapshot_cli, phases, replayed):
    api = ctx.openapi.apps.wfc_owned_orders("vulnerable")
    _run(cli, api, tmp_path, phases=phases)
    api.requests.clear()

    assert _replay(cli, tmp_path) == snapshot_cli
    assert _senders(api) == replayed


@pytest.mark.parametrize(
    ("phases", "replayed"),
    [
        ("fuzzing", [("GET", ALICE), ("GET", ALICE), ("GET", BOB), ("GET", None)]),
        ("stateful", [("POST", ALICE), ("GET", ALICE), ("GET", ALICE)] * 3),
    ],
)
def test_replay_without_recorded_identities_uses_the_current_one(cli, ctx, tmp_path, phases, replayed):
    api = ctx.openapi.apps.wfc_owned_orders("vulnerable")
    _run(cli, api, tmp_path, phases=phases)
    _strip_identities(tmp_path)
    api.requests.clear()

    _replay(cli, tmp_path)

    assert _senders(api) == replayed


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_replay_with_recorded_identity_not_configured(cli, ctx, tmp_path, snapshot_cli):
    api = ctx.openapi.apps.wfc_owned_orders("vulnerable")
    _run(cli, api, tmp_path, phases="stateful")
    api.requests.clear()

    carol = {"name": "carol", "fixedHeaders": [{"name": "Authorization", "value": "ApiKey carol"}]}

    assert _replay(cli, tmp_path, users=[PEERS_AUTH["auth"][0], carol], peers=()) == snapshot_cli
    assert api.requests == []


@pytest.mark.parametrize(
    ("listing", "operation", "draw", "kwargs", "reported", "probed"),
    [
        pytest.param(None, READ, _draw(), {"path_parameters": {"order_id": 1}}, True, True, id="created"),
        pytest.param(
            None, READ, _draw(identity="bob"), {"path_parameters": {"order_id": 1}}, False, False, id="created-by-peer"
        ),
        pytest.param(
            None, READ, _draw(location="query"), {"path_parameters": {"order_id": 1}}, False, False, id="not-sent"
        ),
        pytest.param(
            None,
            READ,
            _draw(location="query"),
            {"path_parameters": {"order_id": 1}, "query": {"order_id": 999}},
            False,
            False,
            id="not-in-response",
        ),
        pytest.param(
            None,
            "POST /api/orders",
            _draw(location="body", parameter="item"),
            {"body": {"item": "book"}},
            False,
            False,
            id="write",
        ),
        # A single-object read shows other users' objects too.
        pytest.param(None, READ, _draw(READ), {"path_parameters": {"order_id": 1}}, False, False, id="read"),
        pytest.param(
            "own",
            READ,
            _draw("GET /api/orders"),
            {"path_parameters": {"order_id": 1}},
            True,
            True,
            id="listed-to-owner",
        ),
        pytest.param(
            "ids",
            READ,
            _draw("GET /api/orders"),
            {"path_parameters": {"order_id": 1}},
            False,
            True,
            id="listed-to-peer",
        ),
        # A read that returns one object may create it, so replaying it as the peer would write and prove nothing.
        pytest.param(
            "issuing",
            READ,
            _draw("GET /api/orders"),
            {"path_parameters": {"order_id": 1}},
            False,
            False,
            id="single-object-read",
        ),
    ],
)
def test_drawn_value(ctx, tmp_path, listing, operation, draw, kwargs, reported, probed):
    api, schema = _app(ctx, tmp_path, "alice", listing=listing)
    case = _alice_case(schema, operation, (draw,), **kwargs)
    assert _verdict(api, check_context(), case) == (reported, probed)


def test_each_operation_is_probed_once_per_peer(ctx, tmp_path):
    api, schema = _app(ctx, tmp_path, "alice")
    case = _alice_case(schema, draws=(_draw(),), path_parameters={"order_id": 1})
    assert [_verdict(api, check_context(), case), _verdict(api, check_context(), case)] == [
        (True, True),
        (False, False),
    ]


# Re-running the owner's list without its query shows another page, where the id seems to be the owner's alone.
def test_listed_value_is_checked_on_the_same_page(ctx, tmp_path):
    api, schema = _app(ctx, tmp_path, "alice", "bob", listing="all_paged")
    listing = schema["/api/orders"]["GET"].Case(query={"offset": 1})
    listing._auth_identity = "alice"
    data_source = schema.create_extra_data_source()
    response = listing.call(headers={"Authorization": "ApiKey alice"})
    data_source.record_response(operation=listing.operation, response=response, case=listing)
    draws = data_source.pick_correlated_values(operation=schema["/api/orders/{order_id}"]["GET"]).draws
    case = _alice_case(schema, draws=draws, path_parameters={"order_id": 2})
    assert _check(api, check_context(), case) == (False, ["/api/orders"])


# A listed id the peer also sees proves nothing, so it must not use up the one probe per operation and peer.
def test_value_listed_to_the_peer_does_not_block_a_later_owned_one(ctx, tmp_path):
    api, schema = _app(ctx, tmp_path, "alice", "bob", listing="own")
    peer_order = _alice_case(schema, draws=(_draw("GET /api/orders"),), path_parameters={"order_id": 2})
    assert [_check(api, check_context(), peer_order), _check(api, check_context(), peer_order)] == [
        (False, ["/api/orders"]),
        (False, []),
    ]
    owned_order = _alice_case(schema, draws=(_draw("GET /api/orders"),), path_parameters={"order_id": 1})
    assert [_check(api, check_context(), owned_order)[0], _check(api, check_context(), owned_order)] == [
        True,
        (False, []),
    ]


@pytest.mark.parametrize(
    ("orders_by", "listing", "parent", "parent_identity", "read_id", "reported", "probed"),
    [
        pytest.param(("alice", "bob"), None, ("POST /api/orders", {}), "alice", 1, True, True, id="created"),
        pytest.param(("alice", "bob"), None, ("POST /api/orders", {}), "bob", 2, False, False, id="created-by-peer"),
        pytest.param(("alice", "bob"), None, ("POST /api/orders", {}), "alice", 2, False, False, id="not-from-step"),
        pytest.param(
            ("alice",), None, (READ, {"path_parameters": {"order_id": 1}}), "alice", 1, False, False, id="read"
        ),
        pytest.param(("alice",), "own", ("GET /api/orders", {}), "alice", 1, True, True, id="listed-to-owner"),
        pytest.param(("alice",), "ids", ("GET /api/orders", {}), "alice", 1, False, True, id="listed-to-peer"),
        pytest.param(("alice",), "issuing", ("GET /api/orders", {}), "alice", 2, False, False, id="single-object-read"),
    ],
)
def test_linked_step(ctx, tmp_path, orders_by, listing, parent, parent_identity, read_id, reported, probed):
    api, schema = _app(ctx, tmp_path, *orders_by, listing=listing)
    method, path = parent[0].split(" ", 1)
    body = {"body": {"item": "book"}} if method == "POST" else {}
    step = schema[path][method].Case(**body, **parent[1])
    step._auth_identity = parent_identity
    case = _alice_case(schema, path_parameters={"order_id": read_id})
    context = _after(step, step.call(headers={"Authorization": f"ApiKey {parent_identity}"}), case)
    assert _verdict(api, context, case) == (reported, probed)


# Servers that let the client pick the id often answer a create with a bare acknowledgement.
def test_linked_step_that_sent_the_id(ctx, tmp_path, response_factory):
    api, schema = _app(ctx, tmp_path, "alice")
    step = schema["/api/orders"]["POST"].Case(body={"id": 1, "item": "book"})
    step._auth_identity = "alice"
    case = _alice_case(schema, path_parameters={"order_id": 1})
    assert _check(api, _after(step, response_factory.requests(content=b'{"message": "Added"}'), case), case)[0]


# Bob's order 1 is only referenced by alice's notes, while the order alice puts under id 5 is hers.
WRITES = [
    pytest.param("POST /api/orders/{order_id}/notes", {"path_parameters": {"order_id": 1}}, 1, False, id="referenced"),
    pytest.param("PUT /api/orders/{order_id}", {"path_parameters": {"order_id": 5}}, 5, True, id="created"),
    pytest.param("POST /api/order-notes", {"body": {"order_id": 1, "text": "hi"}}, 1, False, id="referenced-in-body"),
]


def _alice_write(ctx, tmp_path, operation, kwargs):
    api, schema = _app(ctx, tmp_path, "bob", writes=True)
    method, path = operation.split(" ", 1)
    write = schema[path][method].Case(**{"body": {"text": "hi", "item": "book"}, **kwargs})
    write._auth_identity = "alice"
    return api, schema, write, write.call(headers={"Authorization": "ApiKey alice"})


@pytest.mark.parametrize(("operation", "kwargs", "read_id", "reported"), WRITES)
def test_pooled_write_owns_only_what_it_created(ctx, tmp_path, operation, kwargs, read_id, reported):
    api, schema, write, response = _alice_write(ctx, tmp_path, operation, kwargs)
    data_source = schema.create_extra_data_source()
    data_source.record_request(operation=write.operation, case=write, status_code=response.status_code)
    data_source.record_response(operation=write.operation, response=response, case=write)
    draws = data_source.pick_correlated_values(operation=schema["/api/orders/{order_id}"]["GET"]).draws
    case = _alice_case(schema, draws=draws, path_parameters={"order_id": read_id})
    assert _verdict(api, check_context(), case) == (reported, reported)


@pytest.mark.parametrize(
    ("operation", "kwargs", "read_id", "reported"),
    [*WRITES, pytest.param("POST /api/notes", {"query": {"order_id": 1}}, 1, False, id="referenced-in-query")],
)
def test_linked_write_owns_only_what_it_created(ctx, tmp_path, operation, kwargs, read_id, reported):
    api, schema, write, response = _alice_write(ctx, tmp_path, operation, kwargs)
    case = _alice_case(schema, path_parameters={"order_id": read_id})
    assert _verdict(api, _after(write, response, case), case) == (reported, reported)


@pytest.mark.parametrize(
    ("owner", "peer", "value", "schema", "expected"),
    [
        pytest.param({"id": 1, "item": "a"}, {"id": 1, "item": "a"}, 1, ORDER_SCHEMA, True, id="same"),
        pytest.param({"id": 1, "item": "a"}, {"id": 1, "item": "a"}, "1", ORDER_SCHEMA, True, id="string-id"),
        pytest.param({"id": "a b", "item": "a"}, {"id": "a b", "item": "a"}, "a%20b", ORDER_SCHEMA, True, id="encoded"),
        pytest.param({"id": 1, "item": "a"}, {"id": 1, "item": "<hidden>"}, 1, ORDER_SCHEMA, False, id="redacted"),
        pytest.param({"id": 1, "item": "a"}, {"id": 2, "item": "a"}, 1, ORDER_SCHEMA, False, id="other-object"),
        pytest.param({"id": 1}, {"id": 1}, 1, ORDER_SCHEMA, False, id="id-only-stub"),
        pytest.param({"id": 1, "item": "a"}, {"item": "a"}, 1, ORDER_SCHEMA, False, id="id-missing"),
        pytest.param({"id": 1, "item": "a"}, [], 1, ORDER_SCHEMA, False, id="empty-list"),
        pytest.param({"id": 1, "item": None, "owner": "a"}, {"id": 1, "owner": "a"}, 1, ORDER_SCHEMA, True, id="null"),
        pytest.param(
            [{"id": 1, "item": "a"}],
            [{"id": 1, "item": "a"}],
            1,
            {"type": "array", "items": ORDER_SCHEMA},
            True,
            id="list",
        ),
        # Fields the schema does not describe are ignored, unless it describes none at that level.
        pytest.param(
            {"id": 1, "item": "a", "at": 1}, {"id": 1, "item": "a", "at": 2}, 1, ORDER_SCHEMA, True, id="extra"
        ),
        pytest.param({"id": 1, "item": "a"}, {"id": 1, "item": "a"}, 1, {}, True, id="undeclared"),
        pytest.param(
            {"id": 1, "item": "a", "at": 1}, {"id": 1, "item": "a", "at": 2}, 1, {}, False, id="undeclared-differs"
        ),
        pytest.param({"id": 1}, {"id": 1}, 1, {}, False, id="undeclared-id-only"),
        pytest.param(
            [{"id": 1, "item": "a"}], [{"id": 1, "item": "a"}], 1, {"type": "array"}, True, id="undeclared-items"
        ),
        pytest.param(
            {"id": 1, "item": "a"},
            {"id": 1, "item": "a"},
            1,
            {"type": "array", "items": ORDER_SCHEMA},
            True,
            id="shape-mismatch-same",
        ),
        pytest.param(
            {"id": 1, "item": "a", "at": 1},
            {"id": 1, "item": "a", "at": 2},
            1,
            {"type": "array", "items": ORDER_SCHEMA},
            False,
            id="shape-mismatch",
        ),
    ],
)
def test_equivalence(owner, peer, value, schema, expected):
    assert is_equivalent(owner, peer, value, schema) is expected


def test_equivalence_with_recursive_all_of(ctx):
    operation = ctx.openapi.load_operation(
        "GET /nodes",
        {
            "responses": {
                "200": {
                    "description": "OK",
                    "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Node"}}},
                }
            }
        },
        components={
            "schemas": {
                "Node": {
                    "allOf": [
                        {"$ref": "#/components/schemas/Node"},
                        {
                            "type": "object",
                            "properties": {"id": {"type": "string"}, "item": {"type": "string"}},
                        },
                    ]
                }
            }
        },
    )
    response_schema = operation.responses.get("200").get_schema("application/json").schema
    assert is_equivalent({"id": "node-1", "item": "book"}, {"id": "node-1", "item": "book"}, "node-1", response_schema)


# A key the client picks for what it creates is not a reference, unlike an id that names another object.
def test_body_values_a_create_references(ctx):
    book = {"type": "object", "properties": {"book_title": {"type": "string"}, "secret": {"type": "string"}}}
    title = {"name": "book_title", "in": "path", "required": True, "schema": {"type": "string"}}
    ok = {"description": "OK", "content": {"application/json": {"schema": book}}}
    listed = {"type": "object", "properties": {"Books": {"type": "array", "items": book}}}
    schema = ctx.openapi.load_schema(
        {
            "/books": {
                "get": {
                    "responses": {"200": {"description": "OK", "content": {"application/json": {"schema": listed}}}}
                },
                "post": {
                    "requestBody": {"content": {"application/json": {"schema": book}}},
                    "responses": {"200": {"description": "OK"}},
                },
            },
            "/books/{book_title}": {"get": {"parameters": [title], "responses": {"200": ok}}},
        }
    )
    case = schema["/books"]["POST"].Case(body={"book_title": "mine", "secret": "s", "id": "7", "shelf_id": "3"})
    assert referenced_by(case) == {"s", "3"}


def test_nested_collection_without_the_parent_value_is_not_listed(ctx):
    schema = ctx.openapi.load_schema(
        {
            "/users/{user}/orders": {
                "get": {
                    "parameters": [{"name": "user", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "OK"}},
                }
            },
            "/orders/{order_id}": {
                "get": {
                    "parameters": [{"name": "order_id", "in": "path", "required": True, "schema": {"type": "integer"}}],
                    "responses": {"200": {"description": "OK"}},
                }
            },
        }
    )
    case = schema["/orders/{order_id}"]["GET"].Case(path_parameters={"order_id": 1})
    assert listing_case(case, schema["/users/{user}/orders"]["GET"]) is None


def test_dependency_candidates_skip_unrelated_slots_and_operations(ctx):
    parameter = {"in": "path", "required": True, "schema": {"type": "integer"}}
    schema = ctx.openapi.load_schema(
        {
            "/parents/{parent_id}/orders/{order_id}/{opaque}": {
                "get": {
                    "parameters": [
                        {**parameter, "name": "parent_id"},
                        {**parameter, "name": "order_id"},
                        {**parameter, "name": "opaque"},
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            },
            "/orders": {
                "get": {
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {"application/json": {"schema": {"type": "array", "items": ORDER_SCHEMA}}},
                        }
                    }
                }
            },
        }
    )
    case = schema["/parents/{parent_id}/orders/{order_id}/{opaque}"]["GET"].Case(
        path_parameters={"parent_id": 1, "order_id": 2, "opaque": 3}
    )
    assert (path_resource(case, "order_id"), path_resource(case, "opaque")) == ("Order", "opaque")
    assert [listing.operation.label for listing in listings(case)] == ["GET /orders"]
