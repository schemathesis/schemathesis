from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING

from schemathesis.auths import AuthContext
from schemathesis.specs.openapi._auth_retry import clone_case, remove_auth_from_cookie_header
from schemathesis.specs.openapi.semantic_pool import resolve_combinator, resolve_ref
from schemathesis.wfc.escalation import escalating_provider

if TYPE_CHECKING:
    from schemathesis.auths import AuthProvider
    from schemathesis.core.jsonschema.types import JsonSchemaObject
    from schemathesis.generation.case import Case


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
    return {path} if text is not None and text == _text(value) else set()


def _declared_scalars(
    schema: JsonSchemaObject, body: object, root: JsonSchemaObject, path: JsonPath = ()
) -> Iterator[tuple[JsonPath, object]]:
    schema = resolve_combinator(resolve_ref(schema, root), root)
    if isinstance(body, dict):
        properties = schema.get("properties")
        if isinstance(properties, dict):
            for name, subschema in properties.items():
                if name in body and isinstance(subschema, dict):
                    yield from _declared_scalars(subschema, body[name], root, (*path, name))
    elif isinstance(body, list):
        items = schema.get("items")
        if isinstance(items, dict):
            for index, item in enumerate(body):
                yield from _declared_scalars(items, item, root, (*path, index))
    elif body is not None:
        yield path, body


def is_equivalent(owner: object, peer: object, value: object, schema: JsonSchemaObject) -> bool:
    """Whether `peer` is the same object as `owner`, judged on schema-declared fields."""
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
