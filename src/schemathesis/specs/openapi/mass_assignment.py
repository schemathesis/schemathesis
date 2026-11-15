"""Mass assignment checks (OWASP API3).

Resends a successful JSON write with fields clients should not set and fails if the API stores them.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote, urlparse

from schemathesis.config import MassAssignmentConfig
from schemathesis.core import media_types
from schemathesis.core.jsonschema import resolve_with_root_bundle
from schemathesis.core.transport import response_content_type
from schemathesis.hooks import HookContext, dispatch_before_mass_assignment_probe, schema_hook_dispatchers
from schemathesis.openapi.checks import MassAssignment
from schemathesis.specs.openapi.extra_data_source import declared_response_schema
from schemathesis.specs.openapi.semantic_pool import resolve_combinator
from schemathesis.specs.openapi.stateful.dependencies import naming

if TYPE_CHECKING:
    from schemathesis.checks import CheckContext
    from schemathesis.core.jsonschema.types import JsonSchemaObject
    from schemathesis.core.transport import Response
    from schemathesis.generation.case import Case
    from schemathesis.schemas import APIOperation

WRITE_METHODS = frozenset({"POST", "PUT", "PATCH"})

# Common privilege fields and the values an attacker would send.
PRIVILEGE_FIELDS: dict[str, object] = {
    "is_admin": True,
    "isAdmin": True,
    "admin": True,
    "is_superuser": True,
    "isSuperuser": True,
    "superuser": True,
    "is_staff": True,
    "isStaff": True,
    "role": "admin",
    "roles": ["admin"],
    "is_verified": True,
    "isVerified": True,
    "verified": True,
    "email_verified": True,
    "emailVerified": True,
    "balance": 987654321,
    "credits": 987654321,
}
# Preferred enum values, most privileged first.
PRIVILEGED_ENUM_VALUES = ("admin", "administrator", "superuser", "root", "owner", "staff", "moderator", "premium")
# Values a server would not pick by itself.
MARKER_NUMBER = 987654321
MARKER_DATE = "2001-02-03"
MARKER_DATE_TIME = "2001-02-03T04:05:06Z"
# Fields that are often unique, changed in POST probes to avoid 409s.
UNIQUE_FIELD_HINTS = frozenset({"email", "username", "user_name", "login", "handle", "slug", "name", "title", "code"})

_MISSING = object()
JsonPath = tuple[str, ...]


class FieldOrigin:
    READ_ONLY = "read-only"
    RESPONSE_ONLY = "response-only"
    UNDOCUMENTED = "undocumented"
    CONFIGURED = "configured"
    HOOK = "hook"


@dataclass(slots=True)
class MassAssignmentProbe:
    """Probe passed to `before_mass_assignment_probe` hooks."""

    case: Case
    original: Case
    fields: dict[str, Any] = field(default_factory=dict)
    skip: bool = False


@dataclass(slots=True)
class Target:
    """A field to inject."""

    name: str
    origin: str
    schema: JsonSchemaObject | None
    baseline: object = _MISSING
    value: object = _MISSING
    # True if no server would pick this value, so no baseline is needed.
    unique: bool = False


@dataclass(slots=True)
class Finding:
    target: Target
    observed_with: str


def split_path(path: str | None) -> JsonPath:
    """Split a dotted path like `data.attributes`."""
    if not path:
        return ()
    return tuple(part for part in path.split(".") if part)


def dig(body: object, path: JsonPath) -> Any:
    for key in path:
        if not isinstance(body, dict) or key not in body:
            return None
        body = body[key]
    return body


def settings_for(ctx: CheckContext) -> MassAssignmentConfig:
    config = getattr(ctx.config, "mass_assignment", None)
    return config if isinstance(config, MassAssignmentConfig) else MassAssignmentConfig()


def is_unique_value(value: object) -> bool:
    return isinstance(value, (str, int, float)) and not isinstance(value, bool)


def check_mass_assignment(ctx: CheckContext, response: Response, case: Case) -> None:
    settings = settings_for(ctx)
    request_path = split_path(settings.request_envelope)
    if not _is_eligible(response, case, request_path):
        return
    attempts = ctx.mass_assignment_attempts
    label = case.operation.label
    if attempts is not None and attempts.get(label, 0) >= settings.max_attempts:
        return
    envelope, response_properties = resource_view(case, response, settings)
    baseline_body = unwrap(_json_object(response), envelope)
    targets = list(iter_targets(case, response_properties, settings))
    if not targets:
        return

    # Read the baseline first: PUT and PATCH probes overwrite the same resource.
    baseline_read: dict[str, Any] | None = None
    if any(target.name not in (baseline_body or {}) for target in targets):
        read = resource_read(case, response, settings)
        if read is not None:
            baseline_read = unwrap(_json_object(_send(ctx, case, read)), envelope)

    token = uuid.uuid4().hex[:8]
    for target in targets:
        target.baseline = _baseline_value(target.name, baseline_body, baseline_read)
        if target.value is _MISSING:
            target.value, target.unique = choose_value(target, token)
        elif target.baseline is not _MISSING and matches(target.baseline, target.value):
            target.value = _MISSING
    targets = [target for target in targets if target.value is not _MISSING]

    probe_case = case.derive(keep_auth=True)
    assert isinstance(case.body, dict)
    body = _copy_along(case.body, request_path)
    if case.operation.method.upper() == "POST":
        inner = dig(body, request_path)
        assert isinstance(inner, dict)
        inner.update(uniquify(inner, request_properties(case, request_path), token))
    probe_case.body = body
    probe = MassAssignmentProbe(
        case=probe_case, original=case, fields={target.name: target.value for target in targets}
    )
    dispatch_before_mass_assignment_probe(
        *schema_hook_dispatchers(case.operation.schema), context=HookContext(operation=case.operation), probe=probe
    )
    if probe.skip:
        return
    targets = _apply_hook_changes(targets, probe.fields)
    if not targets:
        return
    inner = dig(probe.case.body, request_path)
    if not isinstance(inner, dict):
        return
    inner.update({target.name: target.value for target in targets})
    probe_case = probe.case
    probe_response = _send(ctx, case, probe_case)

    accepted = 200 <= probe_response.status_code < 300
    if attempts is not None:
        attempts[label] = settings.max_attempts if accepted else attempts.get(label, 0) + 1
    if not accepted:
        return

    probe_body = unwrap(_json_object(probe_response), envelope)
    read = resource_read(probe_case, probe_response, settings)
    read_body = None
    read_label = None
    if read is not None:
        read_response = _send(ctx, case, read)
        if 200 <= read_response.status_code < 300:
            read_body = unwrap(_json_object(read_response), envelope)
            read_label = f"GET {read.formatted_path}"

    findings = list(evaluate(targets, probe_body=probe_body, read_body=read_body, read_label=read_label))

    if settings.cleanup and case.operation.method.upper() == "POST" and read is not None and _created_new(case, read):
        _cleanup(ctx, case, read)

    if findings:
        raise_failure(case, probe_case, read if read_body is not None else None, findings)


def _apply_hook_changes(targets: list[Target], fields: Mapping[str, Any]) -> list[Target]:
    """Apply the hook's changes to `probe.fields`."""
    by_name = {target.name: target for target in targets}
    result = []
    for name, value in fields.items():
        target = by_name.get(name)
        if target is None:
            result.append(
                Target(name=name, origin=FieldOrigin.HOOK, schema=None, value=value, unique=is_unique_value(value))
            )
            continue
        if not matches(target.value, value) or type(target.value) is not type(value):
            target.value = value
            target.unique = is_unique_value(value) and target.origin in (FieldOrigin.CONFIGURED, FieldOrigin.HOOK)
        result.append(target)
    return result


def _copy_along(body: dict[str, Any], path: JsonPath) -> dict[str, Any]:
    """Copy `body` and the objects along `path`."""
    result = dict(body)
    node = result
    for key in path:
        child = node.get(key)
        if not isinstance(child, dict):
            break
        node[key] = dict(child)
        node = node[key]
    return result


def _is_eligible(response: Response, case: Case, request_path: JsonPath = ()) -> bool:
    if case.operation.method.upper() not in WRITE_METHODS:
        return False
    if not (200 <= response.status_code < 300):
        return False
    if not isinstance(case.body, dict) or case.media_type is None or not media_types.is_json(case.media_type):
        return False
    if not isinstance(dig(case.body, request_path), dict):
        return False
    return case.meta is None or not case.meta.generation.mode.is_negative


def _json_object(response: Response) -> dict[str, Any] | None:
    try:
        body = response.json()
    except ValueError:
        return None
    return body if isinstance(body, dict) else None


def _send(ctx: CheckContext, parent: Case, probe: Case) -> Response:
    # Keeps the run's credentials, unlike auth probes.
    kwargs = dict(ctx.internals.transport_kwargs or {})
    return ctx.internals.send_probe(parent, probe, kwargs)


def _resolve(schema: object, root: JsonSchemaObject) -> JsonSchemaObject | None:
    if not isinstance(schema, dict):
        return None
    return resolve_combinator(resolve_with_root_bundle(schema, root), root)


def _properties(schema: object, path: JsonPath = ()) -> dict[str, Any]:
    if not isinstance(schema, dict):
        return {}
    root = schema
    node: object = schema
    for key in path:
        node = _properties_of(node, root).get(key)
    return _properties_of(node, root)


def _properties_of(schema: object, root: JsonSchemaObject) -> dict[str, Any]:
    resolved = _resolve(schema, root)
    properties = resolved.get("properties") if resolved is not None else None
    return properties if isinstance(properties, dict) else {}


def request_properties(case: Case, path: JsonPath = ()) -> dict[str, Any]:
    for body in case.operation.body.items:
        if body.media_type == case.media_type:
            return _properties(body.raw_schema, path)
    return {}


def _request_writable(case: Case, path: JsonPath = ()) -> tuple[set[str], set[str]]:
    """Split request properties into writable and read-only names."""
    writable: set[str] = set()
    forbidden: set[str] = set()
    for body in case.operation.body.items:
        if body.media_type != case.media_type:
            continue
        # `readOnly` properties become `{"not": {}}` in the validation schema.
        for name, subschema in _properties(body.validation_schema, path).items():
            if subschema == {"not": {}} or subschema is False:
                forbidden.add(name)
            else:
                writable.add(name)
    return writable, forbidden


def _resolved_properties(schema: object, root: JsonSchemaObject) -> dict[str, JsonSchemaObject]:
    resolved = _resolve(schema, root)
    properties = resolved.get("properties") if resolved is not None else None
    if not isinstance(properties, dict):
        return {}
    return {name: sub for name, subschema in properties.items() if (sub := _resolve(subschema, root)) is not None}


def resource_view(
    case: Case, response: Response, settings: MassAssignmentConfig | None = None
) -> tuple[JsonPath, dict[str, JsonSchemaObject]]:
    """Find the resource in the response schema, e.g. inside `{"article": {...}}`."""
    settings = settings or MassAssignmentConfig()
    schema = declared_response_schema(case.operation, response.status_code, response_content_type(response))
    if settings.response_envelope is not None:
        path = split_path(settings.response_envelope)
        if schema is None:
            return path, {}
        node: object = schema
        for key in path:
            node = _resolved_properties(node, schema).get(key)
        return path, _resolved_properties(node, schema)
    if schema is None:
        return (), {}
    top = _resolved_properties(schema, schema)
    requested = set(request_properties(case, split_path(settings.request_envelope)))
    best: tuple[JsonPath, dict[str, JsonSchemaObject]] = ((), top)
    best_score = len(requested & set(top))
    for name, subschema in top.items():
        nested = _resolved_properties(subschema, schema)
        score = len(requested & set(nested))
        if score > best_score:
            best, best_score = ((name,), nested), score
    return best


def unwrap(body: dict[str, Any] | None, envelope: JsonPath) -> dict[str, Any] | None:
    inner = dig(body, envelope)
    return inner if isinstance(inner, dict) else None


def is_identifier(name: str, case: Case) -> bool:
    lowered = name.lower()
    if lowered in ("id", "_id", "uuid", "guid") or lowered.endswith("_id") or name.endswith(("Id", "ID")):
        return True
    return name in (case.path_parameters or {})


def iter_targets(
    case: Case, response_properties: Mapping[str, JsonSchemaObject], settings: MassAssignmentConfig | None = None
) -> Iterator[Target]:
    settings = settings or MassAssignmentConfig()
    request_path = split_path(settings.request_envelope)
    writable, forbidden = _request_writable(case, request_path)
    raw_request = request_properties(case, request_path)
    inner = dig(case.body, request_path)
    sent = set(inner) if isinstance(inner, dict) else set()
    excluded = set(settings.exclude_fields)
    seen: set[str] = set()
    for name, value in settings.fields.items():
        if name not in excluded:
            seen.add(name)
            yield Target(
                name=name, origin=FieldOrigin.CONFIGURED, schema=None, value=value, unique=is_unique_value(value)
            )

    def keep(name: str) -> bool:
        return (
            name not in seen
            and name not in excluded
            and name not in writable
            and name not in sent
            and not is_identifier(name, case)
        )

    for name in sorted(forbidden):
        if keep(name):
            seen.add(name)
            schema = response_properties.get(name) or _resolve(raw_request.get(name), raw_request)
            yield Target(name=name, origin=FieldOrigin.READ_ONLY, schema=schema)
    for name, schema in response_properties.items():
        if keep(name) and name not in forbidden and not schema.get("writeOnly"):
            seen.add(name)
            yield Target(name=name, origin=FieldOrigin.RESPONSE_ONLY, schema=schema)
    if not settings.privilege_fields:
        return
    for name in PRIVILEGE_FIELDS:
        if keep(name) and name not in forbidden and name not in response_properties:
            seen.add(name)
            yield Target(name=name, origin=FieldOrigin.UNDOCUMENTED, schema=None)


def _baseline_value(name: str, *bodies: Mapping[str, Any] | None) -> object:
    for body in bodies:
        if body is not None and name in body:
            return body[name]
    return _MISSING


def _schema_type(schema: JsonSchemaObject) -> str | None:
    declared = schema.get("type")
    if isinstance(declared, str):
        return declared
    if isinstance(declared, list):
        return next((item for item in declared if isinstance(item, str) and item != "null"), None)
    if "enum" in schema:
        return None
    if "properties" in schema:
        return "object"
    return None


def choose_value(target: Target, token: str) -> tuple[object, bool]:
    """Pick a value that differs from the baseline."""
    if target.schema is None:
        value = PRIVILEGE_FIELDS[target.name]
        if target.baseline is not _MISSING and target.baseline == value:
            return _MISSING, False
        return value, isinstance(value, int) and not isinstance(value, bool)
    return _value_from_schema(target.schema, target.baseline, token)


def _value_from_schema(schema: JsonSchemaObject, baseline: object, token: str) -> tuple[object, bool]:
    enum = schema.get("enum")
    if isinstance(enum, list):
        choices = [item for item in enum if item is not None and item != baseline]
        if not choices:
            return _MISSING, False
        privileged = [item for item in PRIVILEGED_ENUM_VALUES if item in choices]
        return (privileged[0] if privileged else choices[0]), False
    if "const" in schema:
        return _MISSING, False
    kind = _schema_type(schema)
    if kind == "boolean":
        if isinstance(baseline, bool):
            return not baseline, False
        return True, False
    if kind in ("integer", "number"):
        value = MARKER_NUMBER if baseline != MARKER_NUMBER else MARKER_NUMBER + 1
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        if (isinstance(minimum, (int, float)) and value < minimum) or (
            isinstance(maximum, (int, float)) and value > maximum
        ):
            return _MISSING, False
        return value, True
    if kind == "string":
        if "pattern" in schema:
            return _MISSING, False
        text = _string_value(schema.get("format"), token)
        max_length = schema.get("maxLength")
        min_length = schema.get("minLength")
        if (isinstance(max_length, int) and len(text) > max_length) or (
            isinstance(min_length, int) and len(text) < min_length
        ):
            return _MISSING, False
        if text == baseline:
            return _MISSING, False
        return text, True
    return _MISSING, False


def _string_value(format: object, token: str) -> str:
    marker = f"st-ma-{token}"
    if format == "date-time":
        return MARKER_DATE_TIME
    if format == "date":
        return MARKER_DATE
    if format == "email":
        return f"{marker}@example.com"
    if format == "uuid":
        return str(uuid.UUID(f"{token}{'0' * 24}"))
    if format in ("uri", "url"):
        return f"https://example.com/{marker}"
    return marker


def uniquify(body: dict[str, Any], properties: Mapping[str, Any], token: str) -> dict[str, Any]:
    """Change likely-unique fields so the probe is not a duplicate."""
    result = dict(body)
    for name, value in body.items():
        schema = properties.get(name)
        if not isinstance(value, str) or not isinstance(schema, dict):
            continue
        if "enum" in schema or "pattern" in schema or "const" in schema:
            continue
        format = schema.get("format")
        if format == "email" and "@" in value:
            local, _, domain = value.rpartition("@")
            new = f"{local}+{token}@{domain}"
        elif format is None and name.lower() in UNIQUE_FIELD_HINTS:
            new = f"{value}-{token}"
        else:
            continue
        max_length = schema.get("maxLength")
        if isinstance(max_length, int) and len(new) > max_length:
            continue
        result[name] = new
    return result


_TRUTHY = (True, 1, "true", "True", "TRUE", "1")
_FALSY = (False, 0, "false", "False", "FALSE", "0")


def matches(observed: object, expected: object) -> bool:
    """Compare values, allowing for common server re-encoding."""
    if isinstance(expected, bool):
        # SQLite returns 1 and 0 for booleans.
        accepted = _TRUTHY if expected else _FALSY
        return any(observed is item or (type(observed) is type(item) and observed == item) for item in accepted)
    if isinstance(observed, bool):
        return matches(expected, observed) if isinstance(expected, (int, str)) else False
    if observed == expected:
        return True
    if isinstance(expected, (int, float)) and isinstance(observed, str):
        try:
            return float(observed) == float(expected)
        except ValueError:
            return False
    if isinstance(observed, str) and isinstance(expected, str):
        if expected == MARKER_DATE_TIME:
            return observed.startswith(MARKER_DATE_TIME[:-1])
        return observed.lower() == expected.lower()
    if isinstance(observed, (int, float)) and isinstance(expected, (int, float)):
        return float(observed) == float(expected)
    return False


def evaluate(
    targets: list[Target],
    *,
    probe_body: Mapping[str, Any] | None,
    read_body: Mapping[str, Any] | None,
    read_label: str | None,
) -> Iterator[Finding]:
    for target in targets:
        baseline_differs = target.baseline is not _MISSING and not matches(target.baseline, target.value)
        if read_body is not None and read_label is not None and target.name in read_body:
            # The read shows what was stored, so it decides alone.
            if matches(read_body[target.name], target.value) and (target.unique or baseline_differs):
                yield Finding(target=target, observed_with=read_label)
            continue
        # Needs a baseline so an API that echoes the request does not fail.
        if (
            probe_body is not None
            and target.name in probe_body
            and matches(probe_body[target.name], target.value)
            and baseline_differs
        ):
            yield Finding(target=target, observed_with="the write response")


def resource_read(case: Case, response: Response, settings: MassAssignmentConfig | None = None) -> Case | None:
    """Build a GET for the resource that `case` wrote."""
    settings = settings or MassAssignmentConfig()
    schema = case.operation.schema
    path = case.operation.path
    path_parameters = dict(case.path_parameters or {})
    id_field = split_path(settings.id_field)
    if settings.read is not None:
        operation = schema.find_operation_by_label(settings.read)
        if operation is None:
            return None
        parameter = naming.trailing_path_parameter(operation.path)
        if parameter is not None and (parameter not in path_parameters or id_field):
            value = _created_identifier(response, parameter, id_field)
            if value is not None:
                path_parameters[parameter] = value
        return _read_case(case, operation, path_parameters)
    item_path = None
    if naming.trailing_path_parameter(path) is not None and schema_has(case.operation, "GET", path):
        item_path = path
    else:
        for candidate in _item_paths(case.operation):
            parameter = naming.trailing_path_parameter(candidate)
            assert parameter is not None
            value = _created_identifier(response, parameter, id_field)
            if value is not None:
                path_parameters[parameter] = value
                item_path = candidate
                break
    if item_path is None:
        return None
    operation = schema.find_operation_by_label(f"GET {item_path}")
    if operation is None:
        return None
    return _read_case(case, operation, path_parameters)


def _read_case(case: Case, operation: APIOperation, path_parameters: Mapping[str, Any]) -> Case | None:
    names = [parameter.name for parameter in operation.path_parameters]
    if any(name not in path_parameters for name in names):
        return None
    read = operation.Case(
        path_parameters={name: path_parameters[name] for name in names},
        headers=dict(case.headers or {}),
        cookies=dict(case.cookies or {}),
    )
    read._auth = case._auth
    read._has_explicit_auth = case._has_explicit_auth
    read._auth_identity = case.auth_identity
    return read


def schema_has(operation: APIOperation, method: str, path: str) -> bool:
    return operation.schema.find_operation_by_label(f"{method} {path}") is not None


def _item_paths(operation: APIOperation) -> Iterator[str]:
    """Item paths below a collection, e.g. `/users/{id}` for `/users`."""
    prefix = operation.path.rstrip("/") + "/"
    paths = operation.schema.raw_schema.get("paths", {})
    if not isinstance(paths, dict):
        return
    for candidate in paths:
        if not isinstance(candidate, str) or not candidate.startswith(prefix):
            continue
        rest = candidate[len(prefix) :].rstrip("/")
        if (
            "/" not in rest
            and naming.trailing_path_parameter(candidate) is not None
            and schema_has(operation, "GET", candidate)
        ):
            yield candidate


def _created_identifier(response: Response, parameter: str, id_field: JsonPath = ()) -> str | int | None:
    if id_field:
        value = dig(_json_object(response), id_field)
        return value if isinstance(value, (str, int)) and not isinstance(value, bool) else None
    location = response.headers.get("location")
    if location:
        segment = urlparse(location[0]).path.rstrip("/").rsplit("/", 1)[-1]
        if segment:
            return unquote(segment)
    body = _json_object(response)
    if body is None:
        return None
    for container in (body, *(value for value in body.values() if isinstance(value, dict))):
        for key in (parameter, "id"):
            value = container.get(key)
            if isinstance(value, (str, int)) and not isinstance(value, bool):
                return value
    return None


def _created_new(case: Case, read: Case) -> bool:
    return read.operation.path != case.operation.path


def _cleanup(ctx: CheckContext, case: Case, read: Case) -> None:
    """Delete the resource a probe created."""
    operation = case.operation.schema.find_operation_by_label(f"DELETE {read.operation.path}")
    if operation is None:
        return
    delete = operation.Case(
        path_parameters=dict(read.path_parameters or {}),
        headers=dict(read.headers or {}),
        cookies=dict(read.cookies or {}),
    )
    delete._auth = read._auth
    delete._has_explicit_auth = read._has_explicit_auth
    delete._auth_identity = read.auth_identity
    try:
        _send(ctx, case, delete)
    except Exception:  # noqa: BLE001
        # Best effort.
        pass


def _describe(value: object) -> str:
    return json.dumps(value, default=str)


def raise_failure(case: Case, probe: Case, read: Case | None, findings: list[Finding]) -> None:
    method = case.operation.method.upper()
    lines = []
    width = max(len(finding.target.name) for finding in findings)
    value_width = max(len(_describe(finding.target.value)) for finding in findings)
    for finding in findings:
        target = finding.target
        value = _describe(target.value)
        lines.append(
            f"{target.name:<{width}}  {value:<{value_width}}  ({target.origin}, seen in {finding.observed_with})"
        )
    names = ", ".join(f"`{finding.target.name}`" for finding in findings)
    message = (
        f"The API stored {names}, which clients must not set\n\n"
        f"Write: {method} {probe.formatted_path}\n"
        + (f"Read:  GET {read.formatted_path}\n" if read is not None else "")
        + "\n"
        + "\n".join(lines)
    )
    raise MassAssignment(
        operation=case.operation.label,
        message=message,
        fields=[finding.target.name for finding in findings],
        read_case_id=read.id if read is not None else None,
        case_id=probe.id,
    )
