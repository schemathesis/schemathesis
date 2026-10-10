import json
from contextlib import nullcontext

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
from schemathesis.specs.openapi.object_authorization import is_equivalent, listing_case, listings, path_resource
from test.utils import check_context

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


def _auth_file(tmp_path):
    path = tmp_path / "auth.json"
    path.write_text(json.dumps(PEERS_AUTH))
    return str(path)


def _run(cli, api, tmp_path, *args, peers=("alice", "bob"), phases="fuzzing"):
    wfc = {"path": _auth_file(tmp_path)}
    if peers:
        wfc["peers"] = list(peers)
    return cli.run(
        api.schema_url,
        "--max-examples=10",
        f"--phases={phases}",
        "--checks=object_level_authorization",
        *args,
        config={"auth": {"wfc": wfc}},
    )


def _peer_reads(api):
    return [r for r in api.requests if r.method == "GET" and r.headers.get("Authorization") == "ApiKey bob"]


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_peer_reading_another_peers_object_is_reported(cli, ctx, tmp_path, snapshot_cli):
    api = ctx.openapi.apps.wfc_owned_orders("vulnerable")
    assert _run(cli, api, tmp_path) == snapshot_cli


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_stateful_peer_read_is_reported(cli, ctx, tmp_path, snapshot_cli):
    api = ctx.openapi.apps.wfc_owned_orders("vulnerable")
    assert _run(cli, api, tmp_path, phases="stateful") == snapshot_cli


@pytest.mark.parametrize("phases", ["fuzzing", "stateful"])
@pytest.mark.parametrize("policy", ["owner_checked", "public_view", "fully_public", "peer_html"])
def test_correct_or_public_access_is_not_reported(cli, ctx, tmp_path, policy, phases):
    api = ctx.openapi.apps.wfc_owned_orders(policy)
    result = _run(cli, api, tmp_path, phases=phases)
    assert (result.exit_code, bool(_peer_reads(api))) == (0, True), result.stdout


@pytest.mark.parametrize("phases", ["fuzzing", "stateful"])
@pytest.mark.parametrize(
    ("listing", "exit_code"),
    [("all", 0), ("own", 1), ("ids", 1)],
    ids=["listed-to-everyone", "listed-to-owner", "only-ids-listed-to-everyone"],
)
def test_object_every_user_can_list_is_not_reported(cli, ctx, tmp_path, phases, listing, exit_code):
    api = ctx.openapi.apps.wfc_owned_orders("vulnerable", listing)
    result = _run(cli, api, tmp_path, phases=phases)
    assert (result.exit_code, bool(_peer_reads(api))) == (exit_code, True), result.stdout


def _owner_case(
    ctx,
    tmp_path,
    method,
    path,
    location,
    parameter,
    source,
    source_operation="POST /api/orders",
    listing=None,
    **kwargs,
):
    api = ctx.openapi.apps.wfc_owned_orders("vulnerable", listing)
    requests.post(f"{api.base_url}/api/orders", json={"item": "book"}, headers={"Authorization": "ApiKey alice"})
    config = schemathesis.Config.from_dict({"auth": {"wfc": {"path": _auth_file(tmp_path), "peers": ["alice", "bob"]}}})
    schema = schemathesis.openapi.from_url(api.schema_url, config=config)
    operation = schema[path][method]
    draw = PoolDraw(location, parameter, "Order", "id", source_operation, 200, source_identity=source)
    meta = CaseMetadata(
        generation=GenerationInfo(time=0.0, mode=GenerationMode.POSITIVE),
        components={},
        phase=PhaseInfo(name=TestPhase.FUZZING, data=FuzzingPhaseData("", None, None, None)),
        pool_draws=(draw,) if source is not None else (),
    )
    case = operation.Case(_meta=meta, **kwargs)
    schema.auth.set(case, AuthContext(operation=operation, app=None))
    return api, case


def test_owner_read_of_own_object_is_probed(ctx, tmp_path):
    _, case = _owner_case(
        ctx, tmp_path, "GET", "/api/orders/{order_id}", "path", "order_id", "alice", path_parameters={"order_id": 1}
    )
    with pytest.raises(ObjectLevelAuthorizationViolation):
        object_level_authorization(check_context(), case.call(), case)


@pytest.mark.parametrize(
    ("method", "path", "location", "parameter", "source", "kwargs"),
    [
        ("GET", "/api/orders/{order_id}", "path", "order_id", "bob", {"path_parameters": {"order_id": 1}}),
        ("GET", "/api/orders/{order_id}", "query", "order_id", "alice", {"path_parameters": {"order_id": 1}}),
        (
            "GET",
            "/api/orders/{order_id}",
            "query",
            "order_id",
            "alice",
            {"path_parameters": {"order_id": 1}, "query": {"order_id": 999}},
        ),
        ("POST", "/api/orders", "body", "item", "alice", {"body": {"item": "book"}}),
    ],
    ids=["read-of-another-identity-object", "drawn-value-not-sent", "drawn-value-not-in-response", "write"],
)
def test_no_probe_without_owner_read(ctx, tmp_path, method, path, location, parameter, source, kwargs):
    api, case = _owner_case(ctx, tmp_path, method, path, location, parameter, source, **kwargs)
    assert object_level_authorization(check_context(), case.call(), case) is None
    assert [r for r in api.requests if r.headers.get("Authorization") == "ApiKey bob"] == []


@pytest.mark.parametrize(
    ("creator", "order_id", "expectation"),
    [
        ("alice", 1, pytest.raises(ObjectLevelAuthorizationViolation)),
        ("bob", 2, nullcontext()),
        ("alice", 2, nullcontext()),
    ],
    ids=["same-identity", "other-identity", "id-not-from-linked-step"],
)
def test_stateful_owner_is_the_identity_of_the_linked_step(ctx, tmp_path, creator, order_id, expectation):
    api, case = _owner_case(
        ctx, tmp_path, "GET", "/api/orders/{order_id}", "path", "order_id", None, path_parameters={"order_id": order_id}
    )
    requests.post(f"{api.base_url}/api/orders", json={"item": "book"}, headers={"Authorization": "ApiKey bob"})
    parent = case.operation.schema["/api/orders"]["POST"].Case(body={"item": "book"})
    parent._auth_identity = creator
    recorder = ScenarioRecorder(label="test")
    recorder.record_case(parent_id=None, case=parent, transition=None, is_transition_applied=False)
    recorder.record_response(case_id=parent.id, response=parent.call(headers={"Authorization": f"ApiKey {creator}"}))
    recorder.record_case(parent_id=parent.id, case=case, transition=None, is_transition_applied=False)
    with expectation:
        object_level_authorization(check_context(recorder=recorder), case.call(), case)


# Servers that let the client pick the id often answer a create with a bare acknowledgement.
# A list shows other users' objects too, so seeing an id there does not make it yours.
def test_listed_object_is_not_owned(ctx, tmp_path):
    api, case = _owner_case(
        ctx,
        tmp_path,
        "GET",
        "/api/orders/{order_id}",
        "path",
        "order_id",
        "alice",
        source_operation="GET /api/orders/{order_id}",
        path_parameters={"order_id": 1},
    )
    assert object_level_authorization(check_context(), case.call(), case) is None
    assert [r for r in api.requests if r.headers.get("Authorization") == "ApiKey bob"] == []


# A list scoped to its caller shows only their objects, so an id the peer does not see there is the owner's.
@pytest.mark.parametrize(
    ("listing", "expectation"),
    [("own", pytest.raises(ObjectLevelAuthorizationViolation, match=OWNED_ORDER)), ("ids", nullcontext())],
    ids=["listed-to-owner-only", "listed-to-peer"],
)
def test_object_listed_only_to_the_owner_is_owned(ctx, tmp_path, listing, expectation):
    _, case = _owner_case(
        ctx,
        tmp_path,
        "GET",
        "/api/orders/{order_id}",
        "path",
        "order_id",
        "alice",
        source_operation="GET /api/orders",
        listing=listing,
        path_parameters={"order_id": 1},
    )
    with expectation:
        object_level_authorization(check_context(), case.call(), case)


@pytest.mark.parametrize(
    ("listing", "expectation"),
    [("own", pytest.raises(ObjectLevelAuthorizationViolation, match=OWNED_ORDER)), ("ids", nullcontext())],
    ids=["listed-to-owner-only", "listed-to-peer"],
)
def test_stateful_object_listed_only_to_the_owner_is_owned(ctx, tmp_path, listing, expectation):
    _, case = _owner_case(
        ctx,
        tmp_path,
        "GET",
        "/api/orders/{order_id}",
        "path",
        "order_id",
        None,
        listing=listing,
        path_parameters={"order_id": 1},
    )
    parent = case.operation.schema["/api/orders"]["GET"].Case()
    parent._auth_identity = "alice"
    recorder = ScenarioRecorder(label="test")
    recorder.record_case(parent_id=None, case=parent, transition=None, is_transition_applied=False)
    recorder.record_response(case_id=parent.id, response=parent.call(headers={"Authorization": "ApiKey alice"}))
    recorder.record_case(parent_id=parent.id, case=case, transition=None, is_transition_applied=False)
    with expectation:
        object_level_authorization(check_context(recorder=recorder), case.call(), case)


def test_stateful_owner_read_in_the_linked_step_is_not_owned(ctx, tmp_path):
    api, case = _owner_case(
        ctx, tmp_path, "GET", "/api/orders/{order_id}", "path", "order_id", None, path_parameters={"order_id": 1}
    )
    parent = case.operation.Case(path_parameters={"order_id": 1})
    parent._auth_identity = "alice"
    recorder = ScenarioRecorder(label="test")
    recorder.record_case(parent_id=None, case=parent, transition=None, is_transition_applied=False)
    recorder.record_response(case_id=parent.id, response=parent.call(headers={"Authorization": "ApiKey alice"}))
    recorder.record_case(parent_id=parent.id, case=case, transition=None, is_transition_applied=False)
    assert object_level_authorization(check_context(recorder=recorder), case.call(), case) is None
    assert [r for r in api.requests if r.headers.get("Authorization") == "ApiKey bob"] == []


def test_stateful_owner_sent_the_id_in_the_linked_request(ctx, tmp_path, response_factory):
    _, case = _owner_case(
        ctx, tmp_path, "GET", "/api/orders/{order_id}", "path", "order_id", None, path_parameters={"order_id": 1}
    )
    parent = case.operation.schema["/api/orders"]["POST"].Case(body={"id": 1, "item": "book"})
    parent._auth_identity = "alice"
    recorder = ScenarioRecorder(label="test")
    recorder.record_case(parent_id=None, case=parent, transition=None, is_transition_applied=False)
    recorder.record_response(case_id=parent.id, response=response_factory.requests(content=b'{"message": "Added"}'))
    recorder.record_case(parent_id=parent.id, case=case, transition=None, is_transition_applied=False)
    with pytest.raises(ObjectLevelAuthorizationViolation):
        object_level_authorization(check_context(recorder=recorder), case.call(), case)


def test_without_peers_no_probe_is_sent(cli, ctx, tmp_path):
    api = ctx.openapi.apps.wfc_owned_orders("vulnerable")
    _run(cli, api, tmp_path, peers=())
    assert _peer_reads(api) == []


@pytest.mark.parametrize(
    ("owner", "peer", "value", "expected"),
    [
        ({"id": 1, "item": "a", "owner": "alice"}, {"id": 1, "item": "a", "owner": "alice"}, 1, True),
        ({"id": 1, "item": "a", "owner": "alice"}, {"id": 1, "item": "a", "owner": "alice"}, "1", True),
        ({"id": 1, "item": "a", "owner": "alice"}, {"id": 1, "item": "<hidden>", "owner": "alice"}, 1, False),
        ({"id": 1, "item": "a", "owner": "alice"}, {"id": 2, "item": "a", "owner": "alice"}, 1, False),
        ({"id": 1}, {"id": 1}, 1, False),
        ({"id": 1, "item": "a", "served": "t1"}, {"id": 1, "item": "a", "served": "t2"}, 1, True),
        ({"id": 1, "item": "a"}, [], 1, False),
        ([{"id": 1, "item": "a"}], [{"id": 1, "item": "a"}], 1, True),
        ({"id": "a b", "item": "a"}, {"id": "a b", "item": "a"}, "a%20b", True),
    ],
    ids=[
        "same",
        "string-id",
        "redacted",
        "other-object",
        "id-only-stub",
        "undeclared-differs",
        "empty-list",
        "list",
        "percent-encoded-path-value",
    ],
)
def test_equivalence(owner, peer, value, expected):
    schema = ORDER_SCHEMA if isinstance(owner, dict) else {"type": "array", "items": ORDER_SCHEMA}
    assert is_equivalent(owner, peer, value, schema) is expected


@pytest.mark.parametrize(
    ("body", "schema", "expected"),
    [
        ({"id": 1, "item": "a"}, {}, True),
        ([{"id": 1, "item": "a"}], {"type": "array"}, True),
        ({"id": 1, "item": "a"}, {"type": "array", "items": ORDER_SCHEMA}, True),
        ({"id": 1}, {}, False),
        ({"id": 1, "item": None, "owner": "alice"}, ORDER_SCHEMA, True),
    ],
    ids=["undeclared-response", "undeclared-items", "shape-mismatch", "undeclared-id-only", "null-field"],
)
def test_equivalence_of_identical_bodies(body, schema, expected):
    assert is_equivalent(body, body, 1, schema) is expected


# Fields the schema does not describe still have to match, so volatile ones make the result inconclusive.
@pytest.mark.parametrize("schema", [{}, {"type": "array", "items": ORDER_SCHEMA}], ids=["undeclared", "shape-mismatch"])
def test_undescribed_fields_must_all_match(schema):
    assert is_equivalent({"id": 1, "item": "a", "at": "t1"}, {"id": 1, "item": "a", "at": "t2"}, 1, schema) is False


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
