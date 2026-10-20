from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote

from schemathesis.auths import AuthContext
from schemathesis.core.jsonschema import resolve_with_root_bundle
from schemathesis.core.parameters import ParameterLocation
from schemathesis.core.transport import SAFE_METHODS, response_content_type
from schemathesis.openapi.checks import ObjectLevelAuthorizationViolation
from schemathesis.specs.openapi._auth_retry import remove_auth_from_cookie_header, send_probe
from schemathesis.specs.openapi.adapter.security import declares_security
from schemathesis.specs.openapi.extra_data_source import declared_response_schema, referenced_by
from schemathesis.specs.openapi.semantic_pool import resolve_combinator
from schemathesis.specs.openapi.stateful.dependencies import naming
from schemathesis.specs.openapi.stateful.dependencies.models import Cardinality
from schemathesis.wfc.escalation import EscalatingAuthProvider, escalating_provider

if TYPE_CHECKING:
    from schemathesis.auths import AuthProvider
    from schemathesis.checks import CheckContext
    from schemathesis.core.jsonschema.types import JsonSchemaObject
    from schemathesis.core.transport import Response
    from schemathesis.generation.case import Case
    from schemathesis.schemas import APIOperation
    from schemathesis.specs.openapi.stateful.dependencies.models import InputSlot

JsonPath = tuple[str | int, ...]


@dataclass(slots=True)
class _OwnerRead:
    """A successful `GET` by a peer, which other peers' requests are compared against."""

    ctx: CheckContext
    case: Case
    response: Response
    body: object
    # Without a declared schema, every field of the body counts.
    schema: JsonSchemaObject
    provider: EscalatingAuthProvider
    owner: str

    def send_as(self, case: Case, peer: str) -> Response:
        return send_probe(self.ctx, self.case, apply_as(case, self.provider.provider_for(peer), peer))


def check_object_level_authorization(ctx: CheckContext, response: Response, case: Case) -> None:
    """Replay a peer's read of its own object as every other peer; fail when one gets the object nobody else may."""
    read = _owner_read(ctx, response, case)
    if read is None:
        return
    for resource, value, source_listing in [*_drawn_values(read), *_linked_values(read)]:
        if value_paths(read.body, value):
            for peer in read.provider.peers:
                if peer != read.owner:
                    _probe(read, peer, resource, value, source_listing)


def _owner_read(ctx: CheckContext, response: Response, case: Case) -> _OwnerRead | None:
    # Replaying a write as another identity would change the owner's data.
    if case.method != "GET" or not 200 <= response.status_code < 300 or case.meta is None:
        return None
    provider = escalating_provider(case.operation.schema)
    owner = case._auth_identity
    if provider is None or owner is None or owner not in provider.peers or _declared_public(case.operation):
        return None
    schema = declared_response_schema(case.operation, response.status_code, response_content_type(response))
    return _OwnerRead(ctx, case, response, _json_body(response), schema or {}, provider, owner)


def _probe(read: _OwnerRead, peer: str, resource: str, value: object, source_listing: Case | None) -> None:
    case = read.case
    if source_listing is not None and not _unlisted_for(read, source_listing, peer, value):
        return
    if not read.provider.claim_probe(("read", case.operation.label, resource, peer)):
        return
    peer_case = apply_as(case, read.provider.provider_for(peer), peer)
    peer_response = send_probe(read.ctx, case, peer_case)
    if _leaks(read, peer, peer_response, value):
        raise ObjectLevelAuthorizationViolation(
            operation=case.operation.label,
            message=(
                f"`{peer}` received the `{resource}` that `{read.owner}` owns\n\n"
                f"Owner: {case.method} {case.formatted_path} as {read.owner} -> {read.response.status_code}\n"
                f"Peer:  {peer_case.method} {peer_case.formatted_path} as {peer} -> "
                f"{peer_response.status_code} (equivalent body)"
            ),
            owner=read.owner,
            peer=peer,
            owner_case_id=case.id,
            case_id=peer_case.id,
        )


def _unlisted_for(read: _OwnerRead, listing: Case, peer: str, value: object) -> bool:
    """Whether `peer`'s copy of the list the owner took `value` from leaves it out, making it the owner's."""
    # Each value is listed once per peer, so a value the peer sees does not use up the operation's probe.
    if not read.provider.claim_probe(("listed", read.case.operation.label, str(value), peer)):
        return False
    return not value_paths(_json_body(read.send_as(listing, peer)), value)


def _leaks(read: _OwnerRead, peer: str, peer_response: Response, value: object) -> bool:
    """Whether `peer` got the owner's object, and only because object-level checks are missing."""
    if not _equivalent_response(peer_response, read.body, value, read.schema):
        return False
    # A body anyone gets is public; an unenforced declared scheme is `ignored_auth`'s to report.
    anonymous = send_probe(read.ctx, read.case, strip_credentials(read.case, owner_provider(read.case)))
    if _equivalent_response(anonymous, read.body, value, read.schema):
        return False
    # An object the peer can list in full is shared by design.
    return not any(
        _lists_equivalent(_json_body(read.send_as(listing, peer)), read.body, value, read.schema)
        for listing in listings(read.case)
    )


def _drawn_values(read: _OwnerRead) -> Iterator[tuple[str, object, Case | None]]:
    """Values the pool supplied from the owner's earlier requests, with the list they came from, if any."""
    case = read.case
    assert case.meta is not None
    for draw in case.meta.pool_draws:
        container = case.get_container(ParameterLocation(draw.location))
        if draw.source_identity != read.owner or draw.source_input or not isinstance(container, Mapping):
            continue
        value = container.get(draw.parameter_name)
        if value is None:
            continue
        if draw.source_operation.split(" ", 1)[0] not in SAFE_METHODS:
            yield draw.resource_name, value, None
            continue
        # A read shows other users' objects too, so it proves ownership only through a list the peer is then shown.
        operation = case.operation.schema.find_operation_by_label(draw.source_operation)
        source = listing_case(case, operation, draw.source_query)
        if source is not None:
            yield draw.resource_name, value, source


def _linked_values(read: _OwnerRead) -> Iterator[tuple[str, object, Case | None]]:
    """Path values the owner's previous stateful step created or listed, with that step when it was a list."""
    case = read.case
    parent = read.ctx.internals.find_parent(case_id=case.id)
    if parent is None or parent._auth_identity != read.owner:
        return
    is_read = parent.method.upper() in SAFE_METHODS
    if is_read and not is_collection(parent.operation):
        return
    parent_response = read.ctx.internals.find_response(case_id=parent.id)
    parent_body = _json_body(parent_response) if parent_response is not None else None
    referenced = referenced_by(parent)
    for name, value in (case.path_parameters or {}).items():
        # The step either returned the id or sent it as the key of what it created.
        if str(value) not in referenced and (value_paths(parent_body, value) or value_paths(parent.body, value)):
            yield path_resource(case, name), value, parent if is_read else None


def _declared_public(operation: APIOperation) -> bool:
    """Whether the schema says anyone may call `operation`."""
    if declares_security(operation):
        return False
    # Without schema-wide `security` the server may still require credentials; the anonymous replay decides.
    return "security" in operation.schema.raw_schema


def _apply(case: Case, provider: AuthProvider) -> None:
    context = AuthContext(operation=case.operation, app=case.operation.app)
    provider.set(case, provider.get(case, context), context)


def owner_provider(case: Case) -> AuthProvider:
    provider = escalating_provider(case.operation.schema)
    assert provider is not None and case._auth_identity is not None
    return provider.provider_for(case._auth_identity)


def strip_credentials(case: Case, provider: AuthProvider) -> Case:
    """Copy `case` without anything `provider` puts on a request."""
    stripped = case.derive()
    # Applying the provider to an empty request reveals which keys it owns, whatever their names.
    probe = case.derive()
    probe.headers.clear()
    probe.query.clear()
    probe.cookies.clear()
    _apply(probe, provider)
    for name in probe.headers:
        stripped.headers.pop(name, None)
    for name in probe.query:
        stripped.query.pop(name, None)
    for name in probe.cookies:
        stripped.cookies.pop(name, None)
        remove_auth_from_cookie_header(stripped.headers, name)
    return stripped


def apply_as(case: Case, peer: AuthProvider, name: str) -> Case:
    """Copy `case` as if `name` had sent it."""
    probe = strip_credentials(case, owner_provider(case))
    _apply(probe, peer)
    probe._auth_identity = name
    return probe


def _json_body(response: Response) -> object:
    try:
        return response.json()
    except ValueError:
        return None


def _text(value: object) -> str | None:
    # Path and query values arrive as strings, so ids compare by their string form.
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    return str(value)


def value_paths(body: object, value: object, path: JsonPath = ()) -> set[JsonPath]:
    """Locations in `body` holding `value`."""
    if isinstance(body, dict):
        return {found for key, item in body.items() for found in value_paths(item, value, (*path, key))}
    if isinstance(body, list):
        return {found for index, item in enumerate(body) for found in value_paths(item, value, (*path, index))}
    text = _text(body)
    expected = _text(value)
    # Path values can reach checks percent-encoded while bodies hold the decoded form.
    return {path} if text is not None and expected is not None and text in (expected, unquote(expected)) else set()


def _declared_scalars(
    schema: JsonSchemaObject, body: object, root: JsonSchemaObject, path: JsonPath = ()
) -> Iterator[tuple[JsonPath, object]]:
    schema = resolve_combinator(resolve_with_root_bundle(schema, root), root)
    properties = schema.get("properties")
    items = schema.get("items")
    if isinstance(body, dict) and isinstance(properties, dict):
        for name, subschema in properties.items():
            if name in body and isinstance(subschema, dict):
                yield from _declared_scalars(subschema, body[name], root, (*path, name))
    elif isinstance(body, list) and isinstance(items, dict):
        for index, item in enumerate(body):
            yield from _declared_scalars(items, item, root, (*path, index))
    else:
        # Where the schema says nothing about a node's fields, all of them count.
        yield from _scalars(body, path)


def _scalars(body: object, path: JsonPath = ()) -> Iterator[tuple[JsonPath, object]]:
    if isinstance(body, dict):
        for name, item in body.items():
            yield from _scalars(item, (*path, name))
    elif isinstance(body, list):
        for index, item in enumerate(body):
            yield from _scalars(item, (*path, index))
    elif body is not None:
        yield path, body


def is_equivalent(owner: object, peer: object, value: object, schema: JsonSchemaObject) -> bool:
    """Whether `peer` is the same object as `owner`, judged on the fields the schema describes."""
    shared_id = value_paths(owner, value) & value_paths(peer, value)
    if not shared_id:
        return False
    owner_fields = dict(_declared_scalars(schema, owner, schema))
    peer_fields = dict(_declared_scalars(schema, peer, schema))
    shared = owner_fields.keys() & peer_fields.keys()
    if any(owner_fields[path] != peer_fields[path] for path in shared):
        return False
    # An id alone is what a stub or an empty shell returns too.
    return bool(shared - shared_id)


def _equivalent_response(response: Response, owner_body: object, value: object, schema: JsonSchemaObject) -> bool:
    return 200 <= response.status_code < 300 and is_equivalent(owner_body, _json_body(response), value, schema)


def _lists_equivalent(listed: object, owner: object, value: object, schema: JsonSchemaObject) -> bool:
    """Whether `listed` holds an entry that is the same object as `owner`."""
    for path in value_paths(listed, value):
        entry = listed
        for key in path[:-1]:
            entry = entry[key]  # type: ignore[index]  # `path` was found by walking `listed`
        if is_equivalent(owner, entry, value, schema):
            return True
    return False


def is_collection(operation: APIOperation) -> bool:
    if operation.method.upper() != "GET" or naming.trailing_path_parameter(operation.path) is not None:
        return False
    # A read that returns one object may create it, so only one that returns many is a list to compare against.
    node = operation.schema.analysis.dependency_graph.operations.get(operation.label)
    return node is not None and any(output.cardinality == Cardinality.MANY for output in node.outputs)


def listing_case(case: Case, operation: APIOperation | None, query: dict[str, Any] | None = None) -> Case | None:
    """A request to the collection `operation` with the path values `case` already has, sent as its owner."""
    if operation is None or not is_collection(operation):
        return None
    names = [parameter.name for parameter in operation.path_parameters]
    if any(name not in (case.path_parameters or {}) for name in names):
        return None
    path_parameters = {name: case.path_parameters[name] for name in names}
    # The list's own query picks the page the value was on.
    listing = operation.Case(path_parameters=path_parameters, query=dict(query) if query else None)
    listing._auth_identity = case._auth_identity
    return listing


def _path_slots(case: Case) -> list[InputSlot]:
    node = case.operation.schema.analysis.dependency_graph.operations.get(case.operation.label)
    return [slot for slot in node.inputs if slot.parameter_location == ParameterLocation.PATH] if node else []


def path_resource(case: Case, parameter: str) -> str:
    """The resource the path parameter `parameter` of `case` takes, or the parameter name when none is inferred."""
    return next((slot.resource.name for slot in _path_slots(case) if slot.parameter_name == parameter), parameter)


def listings(case: Case) -> Iterator[Case]:
    """Requests to the collections that list what the path parameters of `case` point to."""
    resources = {slot.resource.name for slot in _path_slots(case)}
    # `/things` lists what `/things/{id}` reads even when its items are named differently.
    parent = naming.collection_path(case.operation.path)
    for label, other in case.operation.schema.analysis.dependency_graph.operations.items():
        if other.path == parent or any(output.resource.name in resources for output in other.outputs):
            listing = listing_case(case, case.operation.schema.find_operation_by_label(label))
            if listing is not None:
                yield listing
