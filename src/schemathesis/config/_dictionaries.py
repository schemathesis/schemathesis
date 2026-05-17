from __future__ import annotations

import math
import os
from dataclasses import dataclass
from typing import Literal, get_args

from schemathesis.config._error import ConfigError

EntryValue = str | int | float

TypeKey = Literal["string", "integer", "number"]
SUPPORTED_TYPE_KEYS: tuple[TypeKey, ...] = get_args(TypeKey)

DictionaryLocationPrefix = Literal["path", "query", "header", "cookie"]
SUPPORTED_LOCATION_PREFIXES: tuple[DictionaryLocationPrefix, ...] = get_args(DictionaryLocationPrefix)


@dataclass(slots=True, frozen=True)
class DictionaryEntry:
    value: EntryValue
    index: int


@dataclass(slots=True)
class DictionaryDefinition:
    name: str
    entries: tuple[DictionaryEntry, ...]
    source_kind: str
    source_path: str | None


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
    return {name: _parse_one_dictionary(name, payload, base_dir=base_dir) for name, payload in raw.items()}


def _parse_one_dictionary(name: str, payload: dict, *, base_dir: str | None) -> DictionaryDefinition:
    if "values" in payload:
        entries = tuple(DictionaryEntry(value=item, index=index) for index, item in enumerate(payload["values"]))
        return DictionaryDefinition(name=name, entries=entries, source_kind="values", source_path=None)
    path_str: str = payload["from-file"]
    resolved = _resolve_dictionary_path(path_str, base_dir=base_dir)
    file_entries = _parse_libfuzzer_file(resolved, name=name)
    if not file_entries:
        raise ConfigError(f"Dictionary `{name}` file `{path_str}` contains no entries")
    return DictionaryDefinition(name=name, entries=tuple(file_entries), source_kind="from-file", source_path=resolved)


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
    for ch in value:
        if not (ch.isalnum() or ch in "_-."):
            return False
    return True


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
