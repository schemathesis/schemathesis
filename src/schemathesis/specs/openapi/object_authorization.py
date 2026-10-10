from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING
from urllib.parse import unquote

from schemathesis.auths import AuthContext
from schemathesis.core.parameters import ParameterLocation
from schemathesis.specs.openapi._auth_retry import clone_case, remove_auth_from_cookie_header
from schemathesis.specs.openapi.semantic_pool import resolve_combinator, resolve_ref
from schemathesis.specs.openapi.stateful.dependencies import naming
from schemathesis.wfc.escalation import escalating_provider

if TYPE_CHECKING:
    from schemathesis.auths import AuthProvider
    from schemathesis.core.jsonschema.types import JsonSchemaObject
    from schemathesis.generation.case import Case
    from schemathesis.schemas import APIOperation


def _apply(case: Case, provider: AuthProvider) -> None:
    context = AuthContext(operation=case.operation, app=case.operation.app)
    provider.set(case, provider.get(case, context), context)


def owner_provider(case: Case) -> AuthProvider:
    provider = escalating_provider(case.operation.schema)
    assert provider is not None and case._auth_identity is not None
    return provider.provider_for(case._auth_identity)


def strip_credentials(case: Case, provider: AuthProvider) -> Case:
    """Copy `case` without anything `provider` puts on a request."""
    stripped = clone_case(case)
    # Applying the provider to an empty request reveals which keys it owns, whatever their names.
    probe = clone_case(case)
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


JsonPath = tuple[str | int, ...]


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
    schema = resolve_combinator(resolve_ref(schema, root), root)
    # Where the schema says nothing about a node's fields, all of them count.
    if isinstance(body, dict):
        properties = schema.get("properties")
        if isinstance(properties, dict):
            for name, subschema in properties.items():
                if name in body and isinstance(subschema, dict):
                    yield from _declared_scalars(subschema, body[name], root, (*path, name))
        else:
            for name, item in body.items():
                yield from _declared_scalars({}, item, root, (*path, name))
    elif isinstance(body, list):
        items = schema.get("items")
        for index, item in enumerate(body):
            yield from _declared_scalars(items if isinstance(items, dict) else {}, item, root, (*path, index))
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


def is_collection(operation: APIOperation) -> bool:
    return operation.method.upper() == "GET" and naming.trailing_path_parameter(operation.path) is None


def listing_case(case: Case, operation: APIOperation | None) -> Case | None:
    """A request to the collection `operation` with the path values `case` already has, sent as its owner."""
    if operation is None or not is_collection(operation):
        return None
    names = [parameter.name for parameter in operation.path_parameters]
    if any(name not in (case.path_parameters or {}) for name in names):
        return None
    listing = operation.Case(path_parameters={name: case.path_parameters[name] for name in names})
    listing._auth_identity = case._auth_identity
    return listing


def path_resource(case: Case, parameter: str) -> str:
    """The resource the path parameter `parameter` of `case` takes, or the parameter name when none is inferred."""
    node = case.operation.schema.analysis.dependency_graph.operations.get(case.operation.label)
    for slot in node.inputs if node is not None else ():
        if slot.parameter_location == ParameterLocation.PATH and slot.parameter_name == parameter:
            return slot.resource.name
    return parameter


def listings(case: Case) -> Iterator[Case]:
    """Requests to the collections that list what the path parameters of `case` point to."""
    graph = case.operation.schema.analysis.dependency_graph
    node = graph.operations.get(case.operation.label)
    if node is None:
        return
    resources = {slot.resource.name for slot in node.inputs if slot.parameter_location == ParameterLocation.PATH}
    for label, other in graph.operations.items():
        if any(output.resource.name in resources for output in other.outputs):
            listing = listing_case(case, case.operation.schema.find_operation_by_label(label))
            if listing is not None:
                yield listing


def lists_equivalent(listed: object, owner: object, value: object, schema: JsonSchemaObject) -> bool:
    """Whether `listed` holds an entry that is the same object as `owner`."""
    for path in value_paths(listed, value):
        entry = listed
        for key in path[:-1]:
            entry = entry[key]  # type: ignore[index]  # `path` was found by walking `listed`
        if is_equivalent(owner, entry, value, schema):
            return True
    return False
