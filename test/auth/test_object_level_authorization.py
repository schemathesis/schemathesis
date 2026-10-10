import json

import pytest
import requests

import schemathesis
from schemathesis.auths import AuthContext
from schemathesis.generation import GenerationMode
from schemathesis.generation.meta import CaseMetadata, FuzzingPhaseData, GenerationInfo, PhaseInfo, TestPhase
from schemathesis.openapi.checks import ObjectLevelAuthorizationViolation
from schemathesis.resources import PoolDraw
from schemathesis.specs.openapi.checks import object_level_authorization
from schemathesis.specs.openapi.object_authorization import is_equivalent
from test.utils import check_context

ORDER_SCHEMA = {
    "type": "object",
    "properties": {"id": {"type": "integer"}, "item": {"type": "string"}, "owner": {"type": "string"}},
}
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


def _run(cli, api, tmp_path, *args, peers=("alice", "bob")):
    wfc = {"path": _auth_file(tmp_path)}
    if peers:
        wfc["peers"] = list(peers)
    return cli.run(
        api.schema_url,
        "--max-examples=10",
        "--phases=fuzzing",
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


@pytest.mark.parametrize("policy", ["owner_checked", "public_view", "fully_public", "peer_html"])
def test_correct_or_public_access_is_not_reported(cli, ctx, tmp_path, policy):
    api = ctx.openapi.apps.wfc_owned_orders(policy)
    result = _run(cli, api, tmp_path)
    assert (result.exit_code, bool(_peer_reads(api))) == (0, True), result.stdout


def _owner_case(ctx, tmp_path, method, path, location, parameter, source, **kwargs):
    api = ctx.openapi.apps.wfc_owned_orders("vulnerable")
    requests.post(f"{api.base_url}/api/orders", json={"item": "book"}, headers={"Authorization": "ApiKey alice"})
    config = schemathesis.Config.from_dict({"auth": {"wfc": {"path": _auth_file(tmp_path), "peers": ["alice", "bob"]}}})
    schema = schemathesis.openapi.from_url(api.schema_url, config=config)
    operation = schema[path][method]
    draw = PoolDraw(location, parameter, "Order", "id", "POST /api/orders", 201, source_identity=source)
    meta = CaseMetadata(
        generation=GenerationInfo(time=0.0, mode=GenerationMode.POSITIVE),
        components={},
        phase=PhaseInfo(name=TestPhase.FUZZING, data=FuzzingPhaseData("", None, None, None)),
        pool_draws=(draw,),
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
        ("POST", "/api/orders", "body", "item", "alice", {"body": {"item": "book"}}),
    ],
    ids=["read-of-another-identity-object", "drawn-value-not-sent", "write"],
)
def test_no_probe_without_owner_read(ctx, tmp_path, method, path, location, parameter, source, kwargs):
    api, case = _owner_case(ctx, tmp_path, method, path, location, parameter, source, **kwargs)
    assert object_level_authorization(check_context(), case.call(), case) is None
    assert [r for r in api.requests if r.headers.get("Authorization") == "ApiKey bob"] == []


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
    ],
    ids=["same", "string-id", "redacted", "other-object", "id-only-stub", "undeclared-differs", "empty-list", "list"],
)
def test_equivalence(owner, peer, value, expected):
    schema = ORDER_SCHEMA if isinstance(owner, dict) else {"type": "array", "items": ORDER_SCHEMA}
    assert is_equivalent(owner, peer, value, schema) is expected


@pytest.mark.parametrize(
    ("body", "schema", "expected"),
    [
        ({"id": 1, "item": "a"}, {}, False),
        ([{"id": 1, "item": "a"}], {"type": "array"}, False),
        ({"id": 1, "item": None, "owner": "alice"}, ORDER_SCHEMA, True),
    ],
    ids=["undeclared-response", "undeclared-items", "null-field"],
)
def test_equivalence_compares_declared_values_only(body, schema, expected):
    assert is_equivalent(body, body, 1, schema) is expected
