from __future__ import annotations

import graphlib
import math
import os
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Literal, get_args

from schemathesis.config._env import resolve
from schemathesis.config._error import ConfigError
from schemathesis.core.transforms import format_body_path

EntryValue = str | int | float

TypeKey = Literal["string", "integer", "number"]
SUPPORTED_TYPE_KEYS: tuple[TypeKey, ...] = get_args(TypeKey)

DictionaryLocationPrefix = Literal["path", "query", "header", "cookie", "body"]
SUPPORTED_LOCATION_PREFIXES: tuple[DictionaryLocationPrefix, ...] = get_args(DictionaryLocationPrefix)
BODY_PREFIX = "body."

# Matches any type, field or list element in a binding key.
WILDCARD = "*"
_GRAPHQL_NAME = re.compile(r"[_A-Za-z][_0-9A-Za-z]*")


@dataclass(slots=True, frozen=True)
class GraphQLBindingPath:
    """A `<Type>.<field>.<argument path>` binding key, with `*` standing for any name."""

    type_name: str
    field_name: str
    argument: tuple[str, ...]


def is_graphql_binding_key(key: str) -> bool:
    """Tell a GraphQL argument binding from an OpenAPI location-shaped one."""
    return key.partition(".")[0] not in SUPPORTED_LOCATION_PREFIXES and key.count(".") >= 2


def parse_body_path(expr: str) -> str:
    """Translate a `body.<path>` binding key to a JSON Pointer with `*` wildcards."""
    rest = expr[len(BODY_PREFIX) :]
    if not rest:
        raise ConfigError(f"Body binding key has empty path: `{expr}`")
    return format_body_path(_parse_path_segments(rest.split("."), expr, _is_valid_body_segment))


def parse_graphql_path(expr: str) -> GraphQLBindingPath:
    """Translate a `<Type>.<field>.<argument path>` binding key into its parts."""
    type_name, _, rest = expr.partition(".")
    field_name, _, argument = rest.partition(".")
    if not argument:
        raise ConfigError(
            f"GraphQL binding key must be `<Type>.<field>.<argument>` (use `*` for any type or field): `{expr}`"
        )
    for name in (type_name, field_name):
        if name != WILDCARD and not _is_valid_graphql_name(name):
            raise ConfigError(f"Invalid segment `{name}` in GraphQL binding key: `{expr}`")
    return GraphQLBindingPath(
        type_name=type_name,
        field_name=field_name,
        argument=tuple(_parse_path_segments(argument.split("."), expr, _is_valid_graphql_name)),
    )


def _parse_path_segments(parts: list[str], expr: str, is_valid: Callable[[str], bool]) -> list[str]:
    segments: list[str] = []
    for part in parts:
        if not part:
            raise ConfigError(f"Empty segment in path: `{expr}`")
        if part == "[*]":
            segments.append(WILDCARD)
            continue
        name, bracket, after = part.partition("[")
        if not is_valid(name):
            raise ConfigError(f"Invalid segment `{name}` in path: `{expr}`")
        segments.append(name)
        if bracket:
            if after != "*]":
                raise ConfigError(f"Only `[*]` is supported in paths (got `[{after}` in `{expr}`)")
            segments.append(WILDCARD)
    return segments


def _is_valid_body_segment(name: str) -> bool:
    if not name:
        return False
    return all(c.isalnum() or c in "_-" for c in name)


def _is_valid_graphql_name(name: str) -> bool:
    return bool(_GRAPHQL_NAME.fullmatch(name))


@dataclass(slots=True, frozen=True)
class EntrySchema:
    """A JSON Schema in place of a value: each use of the entry draws a value the schema admits."""

    # Canonical JSON text, so that equal schemas are one entry.
    text: str


@dataclass(slots=True, frozen=True)
class DictionaryEntry:
    value: EntryValue | EntrySchema
    index: int
    # Values of other dictionaries in the request that fetched this entry, by dictionary name.
    origin: dict[str, EntryValue] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class OperationSource:
    """An API operation whose response fills a dictionary before testing starts."""

    # Operation label, e.g. `GET /api/parents`.
    operation: str
    # JSON Pointer to the values in the response body; `*` matches every array item.
    pointer: str
    # Request parameters keyed by location-prefixed names, e.g. `query.limit`, or GraphQL variables of `query`.
    # A binding takes one request per value of the bound dictionary.
    parameters: dict[str, EntryValue | bool | ParameterDictionaryBinding]
    # GraphQL query document the request sends.
    query: str | None = None


@dataclass(slots=True)
class DictionaryDefinition:
    name: str
    # Empty for `from-operation` dictionaries until the setup phase fetches them.
    entries: tuple[DictionaryEntry, ...]
    source_kind: str
    source_path: str | None
    source: OperationSource | None = None
    # Dictionaries named in every entry's `origin`, including the origins of those dictionaries.
    origin: frozenset[str] = frozenset()


@dataclass(slots=True, frozen=True)
class DictionaryBinding:
    dictionary: str
    probability: float


@dataclass(slots=True, frozen=True)
class ParameterDictionaryBinding:
    dictionary: str
    probability: float


def require_known_dictionary(source: str, name: str, dictionaries: dict[str, DictionaryDefinition]) -> None:
    if name not in dictionaries:
        raise ConfigError(f"{source} references unknown dictionary `{name}`")


def lookup_parameter(parameters: dict[str, object], *, name: str, location: str) -> object:
    """Return the override entry for (`location`, `name`); qualified key wins over bare."""
    qualified = parameters.get(f"{location}.{name}")
    if qualified is not None:
        return qualified
    return parameters.get(name)


def parse_dictionaries(data: dict, *, base_dir: str | None) -> dict[str, DictionaryDefinition]:
    raw: dict[str, dict] = data.get("dictionaries") or {}
    definitions = {name: _parse_one_dictionary(name, payload, base_dir=base_dir) for name, payload in raw.items()}
    _set_origins(definitions)
    return definitions


def _set_origins(definitions: dict[str, DictionaryDefinition]) -> None:
    dependencies: dict[str, set[str]] = {}
    for name, definition in definitions.items():
        dependencies[name] = set()
        for key, binding in source_bindings(definition).items():
            require_known_dictionary(f"Dictionary `{name}` parameter `{key}`", binding.dictionary, definitions)
            dependencies[name].add(binding.dictionary)
    try:
        order = list(graphlib.TopologicalSorter(dependencies).static_order())
    except graphlib.CycleError as exc:
        cycle = " -> ".join(f"`{name}`" for name in exc.args[1])
        raise ConfigError(f"Dictionary source parameters form a cycle: {cycle}") from None
    for name in order:
        definitions[name].origin = frozenset(
            ancestor for dependency in dependencies[name] for ancestor in (dependency, *definitions[dependency].origin)
        )


def source_bindings(definition: DictionaryDefinition) -> dict[str, ParameterDictionaryBinding]:
    """Source parameters that take their values from other dictionaries, by parameter key."""
    if definition.source is None:
        return {}
    return {
        key: value
        for key, value in definition.source.parameters.items()
        if isinstance(value, ParameterDictionaryBinding)
    }


def pair_groups(
    definitions: Iterable[DictionaryDefinition],
) -> list[tuple[DictionaryDefinition, tuple[DictionaryDefinition, ...]]]:
    """Group dictionaries so each leader's entries carry the values of its followers in their `origin`."""
    unique = list({definition.name: definition for definition in definitions}.values())
    grouped: set[str] = set()
    groups = []
    # The deepest dictionary leads, since its entries remember the most other values.
    for leader in sorted(unique, key=lambda definition: len(definition.origin), reverse=True):
        if leader.name in grouped:
            continue
        followers = tuple(
            definition for definition in unique if definition.name in leader.origin and definition.name not in grouped
        )
        grouped.update((leader.name, *(follower.name for follower in followers)))
        groups.append((leader, followers))
    return groups


def _parse_one_dictionary(name: str, payload: dict, *, base_dir: str | None) -> DictionaryDefinition:
    if "values" in payload:
        entries = tuple(DictionaryEntry(value=item, index=index) for index, item in enumerate(payload["values"]))
        return DictionaryDefinition(name=name, entries=entries, source_kind="values", source_path=None)
    if "from-operation" in payload:
        return DictionaryDefinition(
            name=name,
            entries=(),
            source_kind="from-operation",
            source_path=None,
            source=_parse_operation_source(name, payload),
        )
    path_str: str = payload["from-file"]
    resolved = _resolve_dictionary_path(path_str, base_dir=base_dir)
    file_entries = _parse_libfuzzer_file(resolved, name=name)
    if not file_entries:
        raise ConfigError(f"Dictionary `{name}` file `{path_str}` contains no entries")
    return DictionaryDefinition(name=name, entries=tuple(file_entries), source_kind="from-file", source_path=resolved)


_SOURCE_PARAMETER_LOCATIONS = ("path", "query", "header", "cookie")


def _parse_operation_source(name: str, payload: dict) -> OperationSource:
    query = payload.get("query")
    parameters: dict[str, EntryValue | bool | ParameterDictionaryBinding] = {}
    for key, value in payload.get("parameters", {}).items():
        if query is not None:
            if not _is_valid_graphql_name(key):
                raise ConfigError(
                    f"Dictionary `{name}` parameter `{key}` must be a GraphQL variable name, e.g. `limit` for `$limit`"
                )
        else:
            location, _, parameter = key.partition(".")
            if location not in _SOURCE_PARAMETER_LOCATIONS or not parameter:
                raise ConfigError(
                    f"Dictionary `{name}` parameter `{key}` must start with `path.`, `query.`, `header.` or `cookie.`"
                )
        if isinstance(value, dict):
            parameters[key] = ParameterDictionaryBinding(dictionary=value["dictionary"], probability=1.0)
        else:
            parameters[key] = resolve(value)
    return OperationSource(
        operation=payload["from-operation"], pointer=payload["pointer"], parameters=parameters, query=query
    )


def _resolve_dictionary_path(path: str, *, base_dir: str | None) -> str:
    if os.path.isabs(path) or base_dir is None:
        candidate = path
    else:
        candidate = os.path.join(base_dir, path)
    if not os.path.isfile(candidate):
        raise ConfigError(f"Dictionary file `{path}` not found")
    return os.path.abspath(candidate)


def _parse_libfuzzer_file(path: str, *, name: str) -> list[DictionaryEntry]:
    try:
        with open(path, encoding="utf-8") as fd:
            text = fd.read()
    except OSError as exc:
        raise ConfigError(f"Dictionary `{name}` file `{path}` is not readable: {exc}") from None
    entries: list[DictionaryEntry] = []
    for line_no, raw_line in enumerate(text.splitlines(), start=1):
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        decoded = _decode_libfuzzer_entry(stripped, name=name, line_no=line_no, path=path)
        entries.append(DictionaryEntry(value=decoded, index=len(entries)))
    return entries


def _decode_libfuzzer_entry(text: str, *, name: str, line_no: int, path: str) -> str:
    # Accepts both `"token"` and `name="token"` forms.
    if "=" in text and not text.startswith('"'):
        prefix, _, rest = text.partition("=")
        prefix = prefix.strip()
        if not _is_valid_libfuzzer_name(prefix):
            raise ConfigError(
                f"Dictionary `{name}` parse error at `{path}` line {line_no}: invalid entry name `{prefix}`"
            )
        text = rest.strip()
    if len(text) < 2 or text[0] != '"' or text[-1] != '"':
        raise ConfigError(f"Dictionary `{name}` parse error at `{path}` line {line_no}: entry must be a quoted string")
    body = text[1:-1]
    return _decode_escapes(body, name=name, line_no=line_no, path=path)


def _is_valid_libfuzzer_name(value: str) -> bool:
    if not value:
        return False
    return all(ch.isalnum() or ch in "_-." for ch in value)


_SIMPLE_ESCAPES = {"\\": "\\", '"': '"', "n": "\n", "r": "\r", "t": "\t"}


def _decode_escapes(body: str, *, name: str, line_no: int, path: str) -> str:
    def fail(detail: str) -> ConfigError:
        return ConfigError(f"Dictionary `{name}` parse error at `{path}` line {line_no}: {detail}")

    out: list[str] = []
    i = 0
    while i < len(body):
        ch = body[i]
        if ch != "\\":
            out.append(ch)
            i += 1
            continue
        if i + 1 >= len(body):
            raise fail("trailing backslash")
        nxt = body[i + 1]
        if nxt in _SIMPLE_ESCAPES:
            out.append(_SIMPLE_ESCAPES[nxt])
            i += 2
        elif nxt == "x":
            if i + 3 >= len(body):
                raise fail("truncated \\x escape")
            hex_pair = body[i + 2 : i + 4]
            try:
                out.append(chr(int(hex_pair, 16)))
            except ValueError:
                raise fail(f"invalid hex escape `\\x{hex_pair}`") from None
            i += 4
        else:
            raise fail(f"unknown escape `\\{nxt}`")
    return "".join(out)


def coerce_entries_for_type(entries: tuple[DictionaryEntry, ...], ty: str) -> tuple[tuple[int, EntryValue], ...]:
    coerced: list[tuple[int, EntryValue]] = []
    for entry in entries:
        # A schema is drawn from where it is bound by name; it has no value to convert.
        if isinstance(entry.value, EntrySchema):
            continue
        eligible = _coerce_one(entry.value, ty)
        if eligible is not None:
            coerced.append((entry.index, eligible))
    return tuple(coerced)


def _coerce_one(value: EntryValue, ty: str) -> EntryValue | None:
    if ty == "string":
        return value if isinstance(value, str) else str(value)
    if ty == "integer":
        if isinstance(value, int):
            return value
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return None
            try:
                return int(stripped, 10)
            except ValueError:
                return None
        return None
    # ty == "number": entries are pre-filtered so direct int/float are always finite.
    if isinstance(value, (int, float)):
        return value
    stripped = value.strip()
    if not stripped:
        return None
    try:
        parsed = float(stripped)
    except ValueError:
        return None
    return parsed if math.isfinite(parsed) else None
