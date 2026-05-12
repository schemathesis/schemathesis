"""JSON Schema constraint walking.

Produces positive and negative coverage values for individual schema constructs
(`type`, `enum`, `pattern`, `minimum`, `oneOf`, ...).
"""

from __future__ import annotations

import re
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache, partial
from itertools import combinations
from math import inf, nextafter

from schemathesis.core.jsonschema import (
    FANCY_REGEX_OPTIONS,
    VALIDATED_FORMATS_BY_DRAFT,
    is_valid,
    make_validator,
    make_validator_for,
)
from schemathesis.core.jsonschema.bundler import BUNDLE_STORAGE_KEY
from schemathesis.core.jsonschema.keywords import ALL_KEYWORDS

try:
    from json.encoder import _make_iterencode  # type: ignore[attr-defined]
except ImportError:
    _make_iterencode = None

try:
    from json.encoder import c_make_encoder  # type: ignore[attr-defined]
except ImportError:
    c_make_encoder = None

from collections.abc import Callable, Generator, Iterator
from json.encoder import JSONEncoder, encode_basestring_ascii
from typing import Any, TypeGuard, TypeVar, cast
from urllib.parse import quote_plus

import jsonschema_rs
from hypothesis import strategies as st
from hypothesis.errors import InvalidArgument, Unsatisfiable
from hypothesis_jsonschema import from_schema
from hypothesis_jsonschema._canonicalise import canonicalish
from hypothesis_jsonschema._from_schema import STRING_FORMATS as BUILT_IN_STRING_FORMATS

from schemathesis.core import INTERNAL_BUFFER_SIZE, NOT_SET
from schemathesis.core.compat import RefResolutionError
from schemathesis.core.jsonschema.resolver import Resolver, make_root_resolver, resolve_reference
from schemathesis.core.jsonschema.types import JsonSchema, JsonSchemaObject, get_type, to_json_type_name
from schemathesis.core.media_types import is_form_parts, is_xml_parts
from schemathesis.core.parameters import ParameterLocation
from schemathesis.core.transforms import deepclone
from schemathesis.core.validation import contains_unicode_surrogate_pair, has_invalid_characters, is_latin_1_encodable
from schemathesis.generation import GenerationMode
from schemathesis.generation._cache import schema_cache_key
from schemathesis.generation.hypothesis import examples
from schemathesis.generation.meta import CoverageScenario
from schemathesis.openapi.generation.filters import is_invalid_path_parameter
from schemathesis.transport.serialization import contains_binary

VALIDATED_FORMATS = frozenset(
    {
        "date",
        "date-time",
        "duration",
        "email",
        "hostname",
        "idn-email",
        "idn-hostname",
        "ipv4",
        "ipv6",
        "iri",
        "iri-reference",
        "json-pointer",
        "regex",
        "relative-json-pointer",
        "time",
        "uri",
        "uri-reference",
        "uri-template",
        "uuid",
    }
)

_FORMAT_VALIDATORS: dict[tuple[str, type], jsonschema_rs.Validator] = {}


def _get_format_validator(format: str, validator_cls: type[jsonschema_rs.Validator]) -> jsonschema_rs.Validator:
    """Get or create a cached validator for checking a specific format."""
    key = (format, validator_cls)
    if key not in _FORMAT_VALIDATORS:
        _FORMAT_VALIDATORS[key] = make_validator({"type": "string", "format": format}, validator_cls)
    return _FORMAT_VALIDATORS[key]


def conforms_to_format(value: object, format: str, validator_cls: type[jsonschema_rs.Validator]) -> bool:
    """Check if a value conforms to a JSON Schema format."""
    return _get_format_validator(format, validator_cls).is_valid(value)


def _remove_examples(schema: dict[str, Any]) -> dict[str, Any]:
    """Recursively remove 'examples' field from a schema for jsonschema-rs compatibility."""
    result = {}
    for key, value in schema.items():
        if key == "examples":
            continue
        if isinstance(value, dict):
            result[key] = _remove_examples(value)
        elif isinstance(value, list):
            result[key] = [_remove_examples(item) if isinstance(item, dict) else item for item in value]  # type: ignore[assignment]
        else:
            result[key] = value
    return result


def _replace_zero_with_nonzero(x: float) -> float:
    return x or 0.0


def _is_strictly_valid(value: Any, schema: dict[str, Any], ctx: CoverageContext) -> bool:
    # Fails closed: when no validator can be built (e.g. cross-draft keyword combos rejected
    # by both the auto-detected draft and the spec's `validator_cls`), treat the value as
    # unchecked so callers drop it rather than ship it as a valid positive coverage body.
    full_schema: JsonSchema = schema
    if BUNDLE_STORAGE_KEY in ctx.root_schema:
        full_schema = {**schema, BUNDLE_STORAGE_KEY: ctx.root_schema[BUNDLE_STORAGE_KEY]}
    try:
        return make_validator_for(full_schema).is_valid(value)
    except Exception:
        pass
    try:
        return make_validator(full_schema, ctx.validator_cls).is_valid(value)
    except Exception:
        return False


def _accept_spec_value(value: Any, schema: dict[str, Any], ctx: CoverageContext) -> Any:
    # Spec examples reflecting the response shape may carry `readOnly` keys that
    # request-side schemas forbid; dropping those keys recovers the curated value.
    if _is_strictly_valid(value, schema, ctx):
        return value
    if not isinstance(value, dict):
        return NOT_SET
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return NOT_SET
    forbidden = {k for k, sub in properties.items() if isinstance(sub, dict) and sub.get("not") == {}}
    if not forbidden or forbidden.isdisjoint(value):
        return NOT_SET
    cleaned = {k: v for k, v in value.items() if k not in forbidden}
    if _is_strictly_valid(cleaned, schema, ctx):
        return cleaned
    return NOT_SET


def json_recursive_strategy(strategy: st.SearchStrategy) -> st.SearchStrategy:
    return st.lists(strategy, max_size=2) | st.dictionaries(st.text(), strategy, max_size=2)


NEGATIVE_MODE_MAX_LENGTH_WITH_PATTERN = 100
NEGATIVE_MODE_MAX_ITEMS = 15
FLOAT_STRATEGY: st.SearchStrategy = st.floats(allow_nan=False, allow_infinity=False).map(_replace_zero_with_nonzero)
NUMERIC_STRATEGY: st.SearchStrategy = st.integers() | FLOAT_STRATEGY
JSON_STRATEGY: st.SearchStrategy = st.recursive(
    st.none() | st.booleans() | NUMERIC_STRATEGY | st.text(max_size=16),
    json_recursive_strategy,
    max_leaves=2,
)
ARRAY_STRATEGY: st.SearchStrategy = st.lists(JSON_STRATEGY, min_size=2, max_size=3)
OBJECT_STRATEGY: st.SearchStrategy = st.dictionaries(st.text(max_size=16), JSON_STRATEGY, max_size=2)


STRATEGIES_FOR_TYPE = {
    "integer": st.integers(),
    "number": NUMERIC_STRATEGY,
    "boolean": st.booleans(),
    "null": st.none(),
    "string": st.text(),
    "array": ARRAY_STRATEGY,
    "object": OBJECT_STRATEGY,
}


def get_strategy_for_type(ty: str | list[str]) -> st.SearchStrategy:
    if isinstance(ty, str):
        return STRATEGIES_FOR_TYPE[ty]
    return st.one_of(STRATEGIES_FOR_TYPE[t] for t in ty if t in STRATEGIES_FOR_TYPE)


UNKNOWN_PROPERTY_KEY = "x-schemathesis-unknown-property"
UNKNOWN_PROPERTY_VALUE = 42
ADDITIONAL_PROPERTY_KEY_BASE = "x-schemathesis-additional"


def _generate_additional_property_key(existing_keys: set[str]) -> str:
    key = ADDITIONAL_PROPERTY_KEY_BASE
    counter = 0
    while key in existing_keys:
        counter += 1
        key = f"{ADDITIONAL_PROPERTY_KEY_BASE}{counter}"
    return key


def _supports_format_generation(format: str, custom_formats: dict[str, st.SearchStrategy]) -> bool:
    return format in BUILT_IN_STRING_FORMATS or format in custom_formats


@dataclass
class GeneratedValue:
    value: Any
    generation_mode: GenerationMode
    scenario: CoverageScenario
    description: str
    parameter: str | None
    location: str | None

    __slots__ = ("value", "generation_mode", "scenario", "description", "parameter", "location")

    @classmethod
    def with_positive(cls, value: Any, *, scenario: CoverageScenario, description: str) -> GeneratedValue:
        return cls(
            value=value,
            generation_mode=GenerationMode.POSITIVE,
            scenario=scenario,
            description=description,
            location=None,
            parameter=None,
        )

    @classmethod
    def with_negative(
        cls, value: Any, *, scenario: CoverageScenario, description: str, location: str, parameter: str | None = None
    ) -> GeneratedValue:
        return cls(
            value=value,
            generation_mode=GenerationMode.NEGATIVE,
            scenario=scenario,
            description=description,
            location=location,
            parameter=parameter,
        )


PositiveValue = GeneratedValue.with_positive
NegativeValue = GeneratedValue.with_negative


@lru_cache(maxsize=128)
def cached_draw(strategy: st.SearchStrategy) -> Any:
    return examples.generate_one(strategy)


@dataclass
class CoverageContext:
    root_schema: dict[str, Any]
    generation_modes: list[GenerationMode]
    location: ParameterLocation
    media_type: tuple[str, str] | None
    is_required: bool
    path: list[str | int]
    custom_formats: dict[str, st.SearchStrategy]
    validator_cls: type[jsonschema_rs.Validator]
    update_pattern: Callable[[str, int | None, int | None], str] | None
    _resolver: Resolver | None
    _schema_generation_cache: dict[tuple[str, ...], Any]
    allow_extra_parameters: bool

    __slots__ = (
        "root_schema",
        "location",
        "media_type",
        "generation_modes",
        "is_required",
        "path",
        "custom_formats",
        "validator_cls",
        "update_pattern",
        "_resolver",
        "_schema_generation_cache",
        "allow_extra_parameters",
    )

    def __init__(
        self,
        *,
        root_schema: dict[str, Any],
        location: ParameterLocation,
        media_type: tuple[str, str] | None,
        generation_modes: list[GenerationMode] | None = None,
        is_required: bool,
        path: list[str | int] | None = None,
        custom_formats: dict[str, st.SearchStrategy],
        validator_cls: type[jsonschema_rs.Validator],
        update_pattern: Callable[[str, int | None, int | None], str] | None = None,
        _resolver: Resolver | None = None,
        _schema_generation_cache: dict[tuple[str, ...], Any] | None = None,
        allow_extra_parameters: bool = True,
    ) -> None:
        self.root_schema = root_schema
        self.location = location
        self.media_type = media_type
        self.generation_modes = generation_modes if generation_modes is not None else list(GenerationMode)
        self.is_required = is_required
        self.path = path or []
        self.custom_formats = custom_formats
        self.validator_cls = validator_cls
        self.update_pattern = update_pattern
        self._resolver = _resolver
        self._schema_generation_cache = _schema_generation_cache if _schema_generation_cache is not None else {}
        self.allow_extra_parameters = allow_extra_parameters

    @property
    def resolver(self) -> Resolver:
        """Lazy-initialized cached resolver."""
        if self._resolver is None:
            self._resolver = make_root_resolver(self.root_schema)
        return self._resolver

    def resolve_ref(self, ref: str) -> dict | bool:
        """Resolve a $ref to its schema definition."""
        _, resolved = resolve_reference(self.resolver, ref)
        return resolved

    @contextmanager
    def at(self, key: str | int) -> Generator[None, None, None]:
        self.path.append(key)
        try:
            yield
        finally:
            self.path.pop()

    @property
    def current_path(self) -> str:
        return "/" + "/".join(str(key) for key in self.path)

    def with_positive(self) -> CoverageContext:
        return CoverageContext(
            root_schema=self.root_schema,
            location=self.location,
            media_type=self.media_type,
            generation_modes=[GenerationMode.POSITIVE],
            is_required=self.is_required,
            path=self.path,
            custom_formats=self.custom_formats,
            validator_cls=self.validator_cls,
            update_pattern=self.update_pattern,
            _resolver=self._resolver,
            _schema_generation_cache=self._schema_generation_cache,
            allow_extra_parameters=self.allow_extra_parameters,
        )

    def with_negative(self) -> CoverageContext:
        return CoverageContext(
            root_schema=self.root_schema,
            location=self.location,
            media_type=self.media_type,
            generation_modes=[GenerationMode.NEGATIVE],
            is_required=self.is_required,
            path=self.path,
            custom_formats=self.custom_formats,
            validator_cls=self.validator_cls,
            update_pattern=self.update_pattern,
            _resolver=self._resolver,
            _schema_generation_cache=self._schema_generation_cache,
            allow_extra_parameters=self.allow_extra_parameters,
        )

    def is_valid_for_location(self, value: Any) -> bool:
        if self.location in ("header", "cookie") and isinstance(value, str):
            return not value or (is_latin_1_encodable(value) and not has_invalid_characters("", value))
        elif self.location == "path":
            return not is_invalid_path_parameter(value)
        return True

    def leads_to_negative_test_case(self, value: Any) -> bool:
        if self.location == "query":
            # Some values will not be serialized into the query string
            if isinstance(value, list) and not self.is_required:
                # Optional parameters should be present
                return any(item not in [{}, []] for item in value)
        return True

    def will_be_serialized_to_string(self) -> bool:
        if self.location in ("query", "path", "header", "cookie"):
            return True
        if self.location == "body" and self.media_type is not None:
            if is_form_parts(self.media_type):
                return True
            if is_xml_parts(self.media_type):
                return True
        return False

    def can_be_negated(self, schema: JsonSchemaObject) -> bool:
        # Path, query, header, and cookie parameters will be stringified anyway
        # If there are no constraints, then anything will match the original schema after serialization
        if self.will_be_serialized_to_string():
            cleaned = {
                k: v
                for k, v in schema.items()
                if not k.startswith("x-") and k not in ["description", "example", "examples"]
            }
            return cleaned not in [{}, {"type": "string"}]
        return True

    def generate_from(self, strategy: st.SearchStrategy) -> Any:
        return cached_draw(strategy)

    def generate_from_schema(self, schema: JsonSchema) -> Any:
        if isinstance(schema, dict) and "$ref" in schema:
            reference = schema["$ref"]
            # Deep clone to avoid circular references in Python objects
            schema = deepclone(self.resolve_ref(reference))
        if isinstance(schema, bool):
            if not schema:
                raise Unsatisfiable
            return 0
        # Prefer spec-declared concrete values when valid: example > examples[0] > default.
        # Surfaces author intent into recursively-generated templates; without this, nested
        # properties whose schemas declare `example`/`default` get synthetic Hypothesis values.
        if isinstance(schema, dict):
            example = schema.get("example", NOT_SET)
            if example is not NOT_SET:
                accepted = _accept_spec_value(example, schema, self)
                if accepted is not NOT_SET:
                    return accepted
            examples = schema.get("examples")
            if isinstance(examples, list):
                for candidate in examples:
                    accepted = _accept_spec_value(candidate, schema, self)
                    if accepted is not NOT_SET:
                        return accepted
            default = schema.get("default", NOT_SET)
            if default is not NOT_SET:
                accepted = _accept_spec_value(default, schema, self)
                if accepted is not NOT_SET:
                    return accepted
        keys = sorted([k for k in schema if not k.startswith("x-") and k not in ["description", "example", "examples"]])
        if keys == ["type"]:
            return cached_draw(get_strategy_for_type(schema["type"]))
        if keys == ["format", "type"]:
            if schema["type"] != "string":
                return cached_draw(get_strategy_for_type(schema["type"]))
            fmt = schema["format"]
            if fmt in self.custom_formats:
                return cached_draw(self.custom_formats[fmt])
            if fmt in BUILT_IN_STRING_FORMATS:
                return cached_draw(BUILT_IN_STRING_FORMATS[fmt])
        if (keys == ["maxLength", "minLength", "type"] or keys == ["maxLength", "type"]) and schema["type"] == "string":
            return cached_draw(st.text(min_size=schema.get("minLength", 0), max_size=schema["maxLength"]))
        if (
            keys == ["properties", "required", "type"]
            or keys == ["properties", "required"]
            or keys == ["properties", "type"]
            or keys == ["properties"]
        ) and schema.get("type", "object") == "object":
            obj = {}
            properties = schema["properties"]
            for key, sub_schema in properties.items():
                if isinstance(sub_schema, dict) and "const" in sub_schema:
                    obj[key] = sub_schema["const"]
                else:
                    try:
                        obj[key] = self.generate_from_schema(sub_schema)
                    except Unsatisfiable:
                        pass
            for key in schema.get("required", []):
                if key not in properties:
                    try:
                        obj[key] = self.generate_from_schema({})
                    except Unsatisfiable:
                        pass
            if any(key not in obj for key in schema.get("required", [])):
                raise Unsatisfiable
            return obj
        if (
            keys == ["maximum", "minimum", "type"] or keys == ["maximum", "type"] or keys == ["minimum", "type"]
        ) and schema["type"] == "integer":
            return cached_draw(st.integers(min_value=schema.get("minimum"), max_value=schema.get("maximum")))
        if "enum" in schema:
            enum_values = [v for v in schema["enum"] if is_valid(v, schema)]
            if not enum_values:
                raise Unsatisfiable
            return cached_draw(st.sampled_from(enum_values))
        if keys == ["multipleOf", "type"] and schema["type"] in ("integer", "number"):
            step = schema["multipleOf"]
            return cached_draw(st.integers().map(step.__mul__))
        if "pattern" in schema and "string" in get_type(schema):
            pattern = schema["pattern"]
            try:
                re.compile(pattern)
            except re.error:
                raise Unsatisfiable from None
            min_length = schema.get("minLength")
            max_length = schema.get("maxLength")
            if (min_length is not None or max_length is not None) and self.update_pattern is not None:
                pattern = self.update_pattern(pattern, min_length, max_length)
            strategy = st.from_regex(pattern, fullmatch=True)
            if min_length is not None and max_length is not None:
                strategy = strategy.filter(lambda s: min_length <= len(s) <= max_length)
            elif min_length is not None:
                strategy = strategy.filter(lambda s: len(s) >= min_length)
            elif max_length is not None:
                strategy = strategy.filter(lambda s: len(s) <= max_length)
            if (fmt := schema.get("format")) in VALIDATED_FORMATS:
                validator = make_validator_for({"type": "string", "format": fmt})
                strategy = strategy.filter(validator.is_valid)
            return cached_draw(strategy)
        if (
            (keys == ["items", "type"] or keys == ["items", "minItems", "type"])
            and isinstance(schema["items"], dict)
            and "array" in get_type(schema)
        ):
            items = schema["items"]
            min_items = schema.get("minItems", 0)
            if "enum" in items:
                return cached_draw(st.lists(st.sampled_from(items["enum"]), min_size=min_items))
            # Recurse so `items`-level `example`/`examples`/`default` reach generation.
            if any(k in items for k in ("example", "examples", "default")):
                size = max(min_items, 1)
                return [self.generate_from_schema(items) for _ in range(size)]
            sub_keys = sorted([k for k in items if not k.startswith("x-") and k not in ["description", "example"]])
            if sub_keys == ["type"] and items["type"] == "string":
                return cached_draw(st.lists(st.text(), min_size=min_items))
            if (
                sub_keys == ["properties", "required", "type"]
                or sub_keys == ["properties", "type"]
                or sub_keys == ["properties"]
            ):
                return cached_draw(
                    st.lists(
                        st.fixed_dictionaries(
                            {
                                key: from_schema(sub_schema, custom_formats=self.custom_formats)
                                for key, sub_schema in items["properties"].items()
                            }
                        ),
                        min_size=min_items,
                    )
                )

        if keys == ["allOf"]:
            for idx, sub_schema in enumerate(schema["allOf"]):
                if isinstance(sub_schema, dict) and "$ref" in sub_schema:
                    schema["allOf"][idx] = self.resolve_ref(sub_schema["$ref"])

            schema = canonicalish(schema)
            if isinstance(schema, dict) and "allOf" not in schema:
                return self.generate_from_schema(schema)

        if isinstance(schema, dict) and "examples" in schema:
            # Examples may contain binary data which will fail the canonicalisation process in `hypothesis-jsonschema`
            schema = {key: value for key, value in schema.items() if key != "examples"}
        # Prevent some hard to satisfy schemas
        if isinstance(schema, dict) and schema.get("additionalProperties") is False and "required" in schema:
            # Set required properties to any value to simplify generation
            schema = dict(schema)
            properties = schema.setdefault("properties", {})
            for key in schema["required"]:
                properties.setdefault(key, {})

        # Add bundled schemas if any
        if isinstance(schema, dict) and BUNDLE_STORAGE_KEY in self.root_schema:
            schema = dict(schema)
            schema[BUNDLE_STORAGE_KEY] = self.root_schema[BUNDLE_STORAGE_KEY]

        cache_key = schema_cache_key(schema)
        cached = self._schema_generation_cache.get(cache_key, NOT_SET)
        if cached is not NOT_SET:
            return deepclone(cached) if isinstance(cached, (dict, list)) else cached

        # Deep clone to prevent hypothesis_jsonschema from mutating the original schema
        cloned = deepclone(schema)
        if isinstance(cloned, dict) and BUNDLE_STORAGE_KEY in cloned:
            _apply_pattern_optimizations(cloned[BUNDLE_STORAGE_KEY], self.update_pattern)
        strategy = from_schema(cloned, custom_formats=self.custom_formats)
        # Keep generation consistent with the validator draft semantics used by this operation.
        # This avoids producing positive values that the validator for the same schema would reject.
        if (
            isinstance(schema, dict)
            and (fmt := schema.get("format")) in VALIDATED_FORMATS
            and _supports_format_generation(fmt, self.custom_formats)
        ):
            validator = _get_format_validator(fmt, self.validator_cls)
            strategy = strategy.filter(lambda v: not isinstance(v, str) or validator.is_valid(v))
        generated = self.generate_from(strategy)
        self._schema_generation_cache[cache_key] = (
            deepclone(generated) if isinstance(generated, (dict, list)) else generated
        )
        return generated


def _update_schema_pattern(
    schema: dict[str, Any], update_pattern: Callable[[str, int | None, int | None], str]
) -> None:
    pattern = schema.get("pattern")
    # Meta-schemas (e.g. Kubernetes CRD `JSONSchemaProps`) carry property *names*
    # `pattern` / `minLength` / `maxLength` whose values are sub-schema dicts; skip
    # optimization unless these slots actually hold a regex string and integer bounds.
    if not isinstance(pattern, str) or not pattern:
        return
    min_length = schema.get("minLength")
    max_length = schema.get("maxLength")
    if not isinstance(min_length, int) or isinstance(min_length, bool):
        min_length = None
    if not isinstance(max_length, int) or isinstance(max_length, bool):
        max_length = None
    if min_length or max_length:
        new_pattern = update_pattern(pattern, min_length, max_length)
        if new_pattern != pattern:
            schema.pop("minLength", None)
            schema.pop("maxLength", None)
            schema["pattern"] = new_pattern


def _apply_pattern_optimizations(
    obj: object, update_pattern: Callable[[str, int | None, int | None], str] | None
) -> None:
    if update_pattern is None:
        return
    if isinstance(obj, dict):
        _update_schema_pattern(obj, update_pattern)
        for value in obj.values():
            _apply_pattern_optimizations(value, update_pattern)
    elif isinstance(obj, list):
        for item in obj:
            _apply_pattern_optimizations(item, update_pattern)


T = TypeVar("T")


if c_make_encoder is not None:
    _iterencode = c_make_encoder(None, None, encode_basestring_ascii, None, ":", ",", True, False, False)
elif _make_iterencode is not None:
    _iterencode = _make_iterencode(
        None, None, encode_basestring_ascii, None, float.__repr__, ":", ",", True, False, True
    )
else:
    encoder = JSONEncoder(skipkeys=False, sort_keys=False, indent=None, separators=(":", ","))
    _iterencode = encoder.iterencode


def _encode(o: Any) -> str:
    return "".join(_iterencode(o, False))


def _convert_bytes_for_hashing(value: Any) -> Any:
    """Convert bytes/non-string keys to a hashable string representation for JSON encoding."""
    if isinstance(value, bytes):
        return f"__bytes__:{value.hex()}"
    if isinstance(value, dict):
        return {(k if isinstance(k, str) else str(k)): _convert_bytes_for_hashing(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_convert_bytes_for_hashing(v) for v in value]
    return value


def _to_hashable_key(value: T, _encode: Callable = _encode) -> tuple[type, str | T]:
    if isinstance(value, (dict, list)):
        # Convert bytes to a hashable representation before JSON encoding
        converted = _convert_bytes_for_hashing(value)
        serialized = _encode(converted)
        return type(value), serialized
    return type(value), value


class HashSet:
    """Helper to track already generated values."""

    __slots__ = ("_data",)

    def __init__(self) -> None:
        self._data: set[tuple] = set()

    def insert(self, value: Any) -> bool:
        key = _to_hashable_key(value)
        before = len(self._data)
        self._data.add(key)
        return len(self._data) > before

    def clear(self) -> None:
        self._data.clear()


_COMBINATOR_KEYS = frozenset({"anyOf", "oneOf", "allOf", "not", "if", "then", "else"})


def _with_effective_required(schema: JsonSchemaObject) -> JsonSchemaObject:
    existing_required: list[str] = schema.get("required", [])
    properties = schema.get("properties", {})
    if not properties:
        return schema
    for key in ("anyOf", "oneOf"):
        sub_schemas = schema.get(key)
        if sub_schemas:
            for sub_schema in sub_schemas:
                if isinstance(sub_schema, dict) and "required" in sub_schema:
                    extra = [f for f in sub_schema["required"] if f not in existing_required and f in properties]
                    if extra:
                        return {**schema, "required": list(existing_required) + extra}
                    break
    return schema


def _resolve_sub_schema(ctx: CoverageContext, sub: JsonSchema) -> JsonSchema:
    """Resolve a $ref sub-schema to its concrete content before merging."""
    if not isinstance(sub, dict) or "$ref" not in sub:
        return sub
    try:
        resolved = ctx.resolve_ref(sub["$ref"])
        if not isinstance(resolved, dict):
            return resolved
        # Deep-merge so sibling `properties`/`required` augment the resolved schema
        # rather than wiping it; the discriminator-pinning rewrite produces exactly
        # this shape (sibling pins the discriminator key, resolved carries the rest).
        merged = {**resolved}
        for key, value in sub.items():
            if key == "$ref":
                continue
            if key == "properties" and isinstance(value, dict) and isinstance(merged.get("properties"), dict):
                merged["properties"] = {**merged["properties"], **value}
            elif key == "required" and isinstance(value, list) and isinstance(merged.get("required"), list):
                merged["required"] = list(dict.fromkeys(merged["required"] + value))
            else:
                merged[key] = value
        return merged
    except RefResolutionError:
        # Schemas are bundled, so this should not happen in practice
        return sub


def _has_array_sibling(sub_schemas: list) -> bool:
    for sub in sub_schemas:
        if isinstance(sub, dict):
            ty = sub.get("type")
            if ty == "array" or (isinstance(ty, list) and "array" in ty):
                return True
    return False


def _merge_with_parent_context(parent: JsonSchemaObject, sub: JsonSchema) -> JsonSchema:
    if not isinstance(sub, dict):
        return sub
    result: dict[str, Any] = {
        k: (deepclone(v) if k == "properties" else v) for k, v in parent.items() if k not in _COMBINATOR_KEYS
    }
    for key, value in sub.items():
        if key == "required" and "required" in result:
            parent_req: list[str] = result["required"] if isinstance(result["required"], list) else [result["required"]]
            sub_req: list[str] = value if isinstance(value, list) else [value]
            result["required"] = list(dict.fromkeys(parent_req + sub_req))
        elif key == "properties" and "properties" in result:
            result["properties"] = {**result["properties"], **value}
        else:
            result[key] = value
    return result


def _cover_positive_for_type(
    ctx: CoverageContext, schema: JsonSchemaObject, ty: str | None, seen: HashSet | None = None
) -> Generator[GeneratedValue, None, None]:
    # In negative-only mode this function never yields values.
    # Avoid expensive template generation in that case.
    if GenerationMode.POSITIVE not in ctx.generation_modes:
        return

    if ty == "object" or ty == "array":
        template_schema = _get_template_schema(schema, ty, ctx)
        template = ctx.generate_from_schema(template_schema)
    elif _implies_object_type(schema):
        template_schema = _get_template_schema(schema, "object", ctx)
        template = ctx.generate_from_schema(template_schema)
    else:
        template = None
    if GenerationMode.POSITIVE in ctx.generation_modes:
        ctx = ctx.with_positive()
        enum = schema.get("enum", NOT_SET)
        const = schema.get("const", NOT_SET)
        for key in ("anyOf", "oneOf"):
            sub_schemas = schema.get(key)
            if sub_schemas is not None:
                if key == "oneOf":
                    resolved_schemas = [
                        ctx.resolve_ref(s["$ref"]) if isinstance(s, dict) and "$ref" in s else s for s in sub_schemas
                    ]
                    one_of_validators: list[jsonschema_rs.Validator] | None = _make_branch_validators(
                        resolved_schemas, ctx
                    )
                else:
                    one_of_validators = None
                # A branch may generate values that satisfy the branch schema but violate
                # parent-level constraints (e.g. parent `properties.discriminator: const value`,
                # or parent `type` excluding the branch's type). Only gate body schemas:
                # header/query/cookie/path adapters inject type:string for serialization, which
                # would incorrectly filter null values from nullable anyOf branches.
                parent_validator: jsonschema_rs.Validator | None = None
                if ctx.location == ParameterLocation.BODY and (
                    "type" in schema or "properties" in schema or "required" in schema
                ):
                    try:
                        parent_validator = make_validator_for(schema)
                    except Exception:
                        pass
                # For non-body params, an empty bare string serializes to the same wire form as an
                # empty array (`?p=`), so the string branch never disambiguates from a sibling array
                # branch. Force non-empty strings.
                disambiguate_string_branch = (
                    ctx.location != ParameterLocation.BODY
                    and isinstance(sub_schemas, list)
                    and _has_array_sibling(sub_schemas)
                )
                for idx, sub_schema in enumerate(sub_schemas):
                    effective = _resolve_sub_schema(ctx, sub_schema)
                    if (
                        disambiguate_string_branch
                        and isinstance(effective, dict)
                        and effective.get("type") == "string"
                        and "minLength" not in effective
                    ):
                        effective = {**effective, "minLength": 1}
                    if isinstance(effective, dict) and "properties" in effective:
                        # See GH-3584
                        # Sub-schema defines its own properties — treat as a complete type, do not inject parent properties.
                        # Exception: required fields absent from the branch but defined in the parent must be injected
                        # (or referenced from the branch's own properties) so they are still honoured.
                        parent_props = schema.get("properties", {}) if isinstance(schema, dict) else {}
                        parent_required_raw = schema.get("required", []) if isinstance(schema, dict) else []
                        parent_required: list = parent_required_raw if isinstance(parent_required_raw, list) else []
                        branch_props = effective.get("properties", {})
                        branch_required_raw = effective.get("required", [])
                        branch_required: list = branch_required_raw if isinstance(branch_required_raw, list) else []
                        to_inject = {
                            f: parent_props[f]
                            for f in set(branch_required) | set(parent_required)
                            if f not in branch_props and f in parent_props
                        }
                        # Required keys the branch should honour: its own plus parent-required keys
                        # that the branch can already satisfy (or that we inject above).
                        merged_required = list(
                            dict.fromkeys(
                                list(branch_required)
                                + [
                                    f
                                    for f in parent_required
                                    if f not in branch_required and (f in branch_props or f in to_inject)
                                ]
                            )
                        )
                        if to_inject or merged_required != branch_required:
                            effective = {
                                **effective,
                                "properties": {**branch_props, **to_inject} if to_inject else branch_props,
                                "required": merged_required,
                            }
                        gen = cover_schema_iter(ctx, effective)
                    else:
                        # See GH-3520
                        # Additive constraint — merge parent context so sub-schema knows field definitions
                        gen = cover_schema_iter(ctx, _merge_with_parent_context(schema, effective))
                    if one_of_validators is not None:
                        # Only yield values valid for exactly this one branch
                        for v in gen:
                            if not is_valid_for_others(v.value, idx, one_of_validators):
                                if parent_validator is None or (
                                    not contains_binary(v.value) and parent_validator.is_valid(v.value)
                                ):
                                    yield v
                    else:
                        for v in gen:
                            if parent_validator is None or (
                                not contains_binary(v.value) and parent_validator.is_valid(v.value)
                            ):
                                yield v
        all_of = schema.get("allOf")
        # Set when canonicalish is used for allOf: the canonical schema covers the full merged
        # constraints, so the outer schema's type/properties generation must be skipped to avoid
        # producing cases that violate allOf's required fields.
        allof_handles_all = False
        if all_of is not None:
            # When the outer schema also has its own properties or required fields, those constraints
            # must be merged with allOf to avoid generating cases that violate allOf's required fields.
            outer_has_properties = bool(schema.get("properties") or schema.get("required"))
            if len(all_of) == 1 and not outer_has_properties:
                yield from cover_schema_iter(ctx, all_of[0])
            else:
                with suppress(jsonschema_rs.ValidationError):
                    _inline_allof_refs(schema, ctx)
                    canonical = canonicalish(schema)
                    if "allOf" not in canonical:
                        yield from cover_schema_iter(ctx, canonical)
                allof_handles_all = True
        if not allof_handles_all:
            if enum is not NOT_SET:
                for value in enum:
                    if is_valid(value, schema):
                        yield PositiveValue(value, scenario=CoverageScenario.ENUM_VALUE, description="Enum value")
            elif const is not NOT_SET:
                if is_valid(const, schema):
                    yield PositiveValue(const, scenario=CoverageScenario.CONST_VALUE, description="Const value")
            elif ty is not None:
                if ty == "null":
                    yield PositiveValue(None, scenario=CoverageScenario.NULL_VALUE, description="Value null value")
                elif ty == "boolean":
                    yield PositiveValue(
                        True, scenario=CoverageScenario.VALID_BOOLEAN, description="Valid boolean value"
                    )
                    yield PositiveValue(
                        False, scenario=CoverageScenario.VALID_BOOLEAN, description="Valid boolean value"
                    )
                elif ty == "string":
                    yield from _positive_string(ctx, schema)
                elif ty == "integer" or ty == "number":
                    yield from _positive_number(ctx, schema)
                elif ty == "array":
                    yield from _positive_array(ctx, schema, cast(list, template))
                elif ty == "object":
                    yield from _filter_against_combinators(
                        _positive_object(ctx, _with_effective_required(schema), cast(dict, template)),
                        schema,
                    )
            elif _implies_object_type(schema):
                yield from _filter_against_combinators(
                    _positive_object(ctx, _with_effective_required(schema), cast(dict, template)),
                    schema,
                )
        if "not" in schema and isinstance(schema["not"], dict | bool):
            # For 'not' schemas: generate negative cases of inner schema (violations)
            # These violations are positive for the outer schema, so flip the mode
            nctx = ctx.with_negative()
            yield from _flip_generation_mode_for_not(cover_schema_iter(nctx, schema["not"], seen))


def _inline_allof_refs(schema: dict, ctx: CoverageContext, seen: frozenset[str] = frozenset()) -> None:
    # canonicalish merges two $ref-only siblings by keeping the first and dropping the second,
    # losing required fields from the dropped ref.  Resolving refs first gives it concrete schemas.
    all_of = schema.get("allOf")
    if not all_of:
        return
    for idx, sub_schema in enumerate(all_of):
        if isinstance(sub_schema, dict) and "$ref" in sub_schema:
            ref = sub_schema["$ref"]
            if ref not in seen:
                resolved = deepclone(ctx.resolve_ref(ref))
                all_of[idx] = resolved
                if isinstance(resolved, dict):
                    _inline_allof_refs(resolved, ctx, seen | {ref})
        elif isinstance(sub_schema, dict):
            _inline_allof_refs(sub_schema, ctx, seen)


@contextmanager
def _ignore_unfixable(
    *,
    ref_error: type[Exception] = RefResolutionError,
) -> Generator:
    try:
        yield
    except (Unsatisfiable, ref_error, jsonschema_rs.ValidationError):
        pass
    except InvalidArgument as exc:
        message = str(exc)
        if "Cannot create non-empty" not in message and "is not in the specified alphabet" not in message:
            raise
    except TypeError as exc:
        if "first argument must be string or compiled pattern" not in str(exc):
            raise


def _pick_property_name(schema: dict, existing_keys: set[str], ctx: CoverageContext) -> str | None:
    """Return a key valid under propertyNames, or fall back to a synthetic key.

    Returns None if a conforming key can't be found or propertyNames forbids all keys.
    """
    property_names = schema.get("propertyNames")
    if property_names is False:
        # No property name can satisfy `false` — adding any key would be invalid.
        return None
    if isinstance(property_names, dict):
        try:
            key = ctx.generate_from_schema(property_names)
            # Degenerate schemas (e.g. `{}`) may yield non-strings; skip rather than corrupt.
            if isinstance(key, str) and key not in existing_keys:
                return key
            return None
        except Exception:
            return None
    return _generate_additional_property_key(existing_keys)


def cover_schema_iter(
    ctx: CoverageContext, schema: JsonSchema, seen: HashSet | None = None
) -> Generator[GeneratedValue, None, None]:
    if seen is None:
        seen = HashSet()

    if isinstance(schema, dict) and "$ref" in schema:
        reference = schema["$ref"]
        try:
            resolved = ctx.resolve_ref(reference)
            if isinstance(resolved, dict):
                merged = {**resolved}
                for k, v in schema.items():
                    if k == "$ref":
                        continue
                    if k == "properties" and isinstance(v, dict) and isinstance(merged.get("properties"), dict):
                        # Deep-merge: resolved's properties take lower priority than sibling properties,
                        # but both must be present so that 'required' fields from the resolved schema
                        # are included in the merged properties dict.
                        merged["properties"] = {**merged["properties"], **v}
                    elif k == "required" and isinstance(v, list) and isinstance(merged.get("required"), list):
                        merged["required"] = list(dict.fromkeys(merged["required"] + v))
                    else:
                        merged[k] = v
                # Draft 4 silently drops `$ref` siblings; the merged form is what generation
                # walks but the body validator only honors the bare ref target. Build a view
                # validator from the un-merged schema and skip negative values it accepts.
                unmerged_validator: jsonschema_rs.Validator | None = None
                if any(k != "$ref" and k in ALL_KEYWORDS for k in schema):
                    bundle = ctx.root_schema.get(BUNDLE_STORAGE_KEY) if isinstance(ctx.root_schema, dict) else None
                    check_schema = schema if bundle is None else {**schema, BUNDLE_STORAGE_KEY: bundle}
                    try:
                        unmerged_validator = ctx.validator_cls(check_schema, pattern_options=FANCY_REGEX_OPTIONS)
                    except Exception:
                        pass
                for generated in cover_schema_iter(ctx, merged, seen):
                    if (
                        unmerged_validator is not None
                        and generated.generation_mode == GenerationMode.NEGATIVE
                        and not contains_binary(generated.value)
                        and unmerged_validator.is_valid(generated.value)
                    ):
                        continue
                    yield generated
            else:
                yield from cover_schema_iter(ctx, resolved, seen)
            return
        except RefResolutionError:
            # Can't resolve a reference - at this point, we can't generate anything useful as `$ref` is in the current schema root
            return

    if schema is True:
        types = ["null", "boolean", "string", "number", "array", "object"]
        schema = {}
    elif schema is False:
        types = []
        schema = {"not": {}}
    elif not any(k in ALL_KEYWORDS for k in schema):
        types = ["null", "boolean", "string", "number", "array", "object"]
    else:
        types = schema.get("type", [])
    push_examples_to_properties(schema)
    if not isinstance(types, list):
        types = [types]  # type: ignore[unreachable]
    if not types:
        with _ignore_unfixable():
            yield from _cover_positive_for_type(ctx, schema, None)
    for ty in types:
        with _ignore_unfixable():
            yield from _cover_positive_for_type(ctx, schema, ty)
    if GenerationMode.NEGATIVE in ctx.generation_modes:
        template = None
        if not ctx.can_be_negated(schema):
            return
        # `enum`/`const` without a sibling `type` (e.g. `canonicalish` strips `type` from
        # `{type: string, enum: [...]}` because the enum values already pin the type) would
        # otherwise miss type-violation negatives. Infer the type from the values once so the
        # `enum`/`const` branches below can dispatch `_negative_type` alongside `_negative_enum`.
        inferred_types: list[str] | None = None
        if "type" not in schema:
            if "enum" in schema and isinstance(schema["enum"], list) and schema["enum"]:
                inferred_types = sorted({to_json_type_name(v) for v in schema["enum"]})
            elif "const" in schema:
                inferred_types = [to_json_type_name(schema["const"])]
        for key, value in schema.items():
            with _ignore_unfixable(), ctx.at(key):
                if key == "enum":
                    yield from _negative_enum(ctx, value, seen)
                    if inferred_types:
                        yield from _negative_type(ctx, inferred_types, seen, schema)
                elif key == "const":
                    for value_ in _negative_enum(ctx, [value], seen):
                        yield value_
                    if inferred_types:
                        yield from _negative_type(ctx, inferred_types, seen, schema)
                elif key == "type":
                    yield from _negative_type(ctx, value, seen, schema)
                elif key == "properties":
                    template = template or ctx.generate_from_schema(_get_template_schema(schema, "object", ctx))
                    yield from _negative_properties(ctx, template, value)
                elif key == "patternProperties":
                    template = template or ctx.generate_from_schema(_get_template_schema(schema, "object", ctx))
                    yield from _negative_pattern_properties(ctx, template, value)
                elif key == "propertyNames" and isinstance(value, dict):
                    template = template or ctx.generate_from_schema(_get_template_schema(schema, "object", ctx))
                    if isinstance(template, dict):
                        yield from _negative_property_names(ctx, template, value)
                elif key == "items" and isinstance(value, dict):
                    yield from _negative_items(ctx, value)
                elif key == "items" and isinstance(value, list):
                    yield from _negative_prefix_items(ctx, value)
                elif key == "pattern":
                    min_length = schema.get("minLength")
                    max_length = schema.get("maxLength")
                    yield from _negative_pattern(ctx, value, min_length=min_length, max_length=max_length)
                elif key == "format" and ("string" in types or not types):
                    # Binary formats accept any bytes - no meaningful format violations
                    if value not in ("binary", "byte"):
                        yield from _negative_format(ctx, schema, value)
                elif key == "maximum":
                    next = value + 1
                    if seen.insert(next):
                        yield NegativeValue(
                            next,
                            scenario=CoverageScenario.VALUE_ABOVE_MAXIMUM,
                            description="Value greater than maximum",
                            location=ctx.current_path,
                        )
                elif key == "minimum":
                    next = value - 1
                    if seen.insert(next):
                        yield NegativeValue(
                            next,
                            scenario=CoverageScenario.VALUE_BELOW_MINIMUM,
                            description="Value smaller than minimum",
                            location=ctx.current_path,
                        )
                elif key == "exclusiveMaximum" or key == "exclusiveMinimum" and seen.insert(value):
                    if isinstance(value, bool):
                        continue
                    verb = "greater" if key == "exclusiveMaximum" else "smaller"
                    limit = "maximum" if key == "exclusiveMaximum" else "minimum"
                    scenario = (
                        CoverageScenario.VALUE_ABOVE_MAXIMUM
                        if key == "exclusiveMaximum"
                        else CoverageScenario.VALUE_BELOW_MINIMUM
                    )
                    yield NegativeValue(
                        value, scenario=scenario, description=f"Value {verb} than {limit}", location=ctx.current_path
                    )
                elif key == "multipleOf":
                    for value_ in _negative_multiple_of(ctx, schema, value):
                        if seen.insert(value_.value):
                            yield value_
                elif key == "minLength" and 0 < value < INTERNAL_BUFFER_SIZE:
                    # minLength only constrains strings; skip when schema explicitly excludes string type
                    if "string" in get_type(schema):
                        if value == 1:
                            # In this case, the only possible negative string is an empty one
                            # The `pattern` value may require an non-empty one and the generation will fail
                            # However, it is fine to violate `pattern` here as it is negative string generation anyway
                            value = ""
                            if ctx.is_valid_for_location(value) and seen.insert(value):
                                yield NegativeValue(
                                    value,
                                    scenario=CoverageScenario.STRING_BELOW_MIN_LENGTH,
                                    description="String smaller than minLength",
                                    location=ctx.current_path,
                                )
                        else:
                            with suppress(InvalidArgument):
                                min_length = max_length = value - 1
                                new_schema = {**schema, "minLength": min_length, "maxLength": max_length}
                                new_schema.pop("enum", None)
                                new_schema.pop("const", None)
                                new_schema["type"] = "string"
                                if "pattern" in new_schema and ctx.update_pattern is not None:
                                    new_schema["pattern"] = ctx.update_pattern(
                                        schema["pattern"], min_length, max_length
                                    )
                                    if new_schema["pattern"] == schema["pattern"]:
                                        # Pattern wasn't updated, try to generate a valid value then shrink the string to the required length
                                        del new_schema["minLength"]
                                        del new_schema["maxLength"]
                                        value = ctx.generate_from_schema(new_schema)[:max_length]
                                    else:
                                        value = ctx.generate_from_schema(new_schema)
                                else:
                                    value = ctx.generate_from_schema(new_schema)
                                if ctx.is_valid_for_location(value) and seen.insert(value):
                                    yield NegativeValue(
                                        value,
                                        scenario=CoverageScenario.STRING_BELOW_MIN_LENGTH,
                                        description="String smaller than minLength",
                                        location=ctx.current_path,
                                    )
                elif key == "maxLength" and value < INTERNAL_BUFFER_SIZE:
                    # maxLength only constrains strings; skip when schema explicitly excludes string type
                    if "string" in get_type(schema):
                        try:
                            min_length = max_length = value + 1
                            new_schema = {**schema, "minLength": min_length, "maxLength": max_length}
                            new_schema.pop("enum", None)
                            new_schema.pop("const", None)
                            new_schema["type"] = "string"
                            if "pattern" in new_schema:
                                if value > NEGATIVE_MODE_MAX_LENGTH_WITH_PATTERN:
                                    # Large `maxLength` value can be extremely slow to generate when combined with `pattern`
                                    del new_schema["pattern"]
                                    value = ctx.generate_from_schema(new_schema)
                                elif ctx.update_pattern is not None:
                                    new_schema["pattern"] = ctx.update_pattern(
                                        schema["pattern"], min_length, max_length
                                    )
                                    if new_schema["pattern"] == schema["pattern"]:
                                        # Pattern wasn't updated, try to generate a valid value then extend the string to the required length
                                        del new_schema["minLength"]
                                        del new_schema["maxLength"]
                                        value = ctx.generate_from_schema(new_schema).ljust(max_length, "0")
                                    else:
                                        value = ctx.generate_from_schema(new_schema)
                                else:
                                    value = ctx.generate_from_schema(new_schema)
                            else:
                                value = ctx.generate_from_schema(new_schema)
                            if seen.insert(value):
                                yield NegativeValue(
                                    value,
                                    scenario=CoverageScenario.STRING_ABOVE_MAX_LENGTH,
                                    description="String larger than maxLength",
                                    location=ctx.current_path,
                                )
                        except (InvalidArgument, Unsatisfiable):
                            pass
                elif key == "uniqueItems" and value:
                    yield from _negative_unique_items(ctx, schema)
                elif key == "required":
                    template = template or ctx.generate_from_schema(_get_template_schema(schema, "object", ctx))
                    yield from _negative_required(ctx, template, value)
                elif key == "maxItems" and isinstance(value, int) and value < INTERNAL_BUFFER_SIZE:
                    if value > NEGATIVE_MODE_MAX_ITEMS:
                        # It could be extremely slow to generate large arrays
                        # Generate values up to the limit and reuse them to construct the final array
                        new_schema = {
                            **schema,
                            "minItems": NEGATIVE_MODE_MAX_ITEMS,
                            "maxItems": NEGATIVE_MODE_MAX_ITEMS,
                            "type": "array",
                        }
                        array_value: list = []
                        if "items" in schema and isinstance(schema["items"], dict):
                            # The schema may have another large array nested, therefore generate covering cases
                            # and use them to build an array for the current schema
                            negative = [case.value for case in cover_schema_iter(ctx, schema["items"])]
                            positive = [case.value for case in cover_schema_iter(ctx.with_positive(), schema["items"])]
                            # Interleave positive & negative values. Empty if either list is empty —
                            # fall back to direct generation below so the yielded array is non-empty.
                            array_value = [value for pair in zip(positive, negative, strict=False) for value in pair][
                                :NEGATIVE_MODE_MAX_ITEMS
                            ]
                        if not array_value:
                            try:
                                array_value = ctx.generate_from_schema(new_schema)
                            except (InvalidArgument, Unsatisfiable):
                                continue

                        # Extend the array to be of length value + 1 by repeating its own elements
                        diff = value + 1 - len(array_value)
                        if diff > 0 and array_value:
                            array_value += (
                                array_value * (diff // len(array_value)) + array_value[: diff % len(array_value)]
                            )
                        if seen.insert(array_value):
                            yield NegativeValue(
                                array_value,
                                scenario=CoverageScenario.ARRAY_ABOVE_MAX_ITEMS,
                                description="Array with more items than allowed by maxItems",
                                location=ctx.current_path,
                            )
                    else:
                        try:
                            # Force the array to have one more item than allowed
                            new_schema = {**schema, "minItems": value + 1, "maxItems": value + 1, "type": "array"}
                            array_value = ctx.generate_from_schema(new_schema)
                            if seen.insert(array_value):
                                yield NegativeValue(
                                    array_value,
                                    scenario=CoverageScenario.ARRAY_ABOVE_MAX_ITEMS,
                                    description="Array with more items than allowed by maxItems",
                                    location=ctx.current_path,
                                )
                        except (InvalidArgument, Unsatisfiable):
                            pass
                elif key == "minItems" and isinstance(value, int) and value > 0:
                    try:
                        # Drop spec hints: they describe valid shapes, so `generate_from_schema`
                        # would short-circuit to the example (vacuously accepted when a sibling
                        # `$ref` blocks validator construction) and skip the bound we install.
                        new_schema = {k: v for k, v in schema.items() if k not in ("example", "examples", "default")}
                        new_schema.update({"minItems": value - 1, "maxItems": value - 1, "type": "array"})
                        array_value = ctx.generate_from_schema(new_schema)
                        if seen.insert(array_value):
                            yield NegativeValue(
                                array_value,
                                scenario=CoverageScenario.ARRAY_BELOW_MIN_ITEMS,
                                description="Array with fewer items than allowed by minItems",
                                location=ctx.current_path,
                            )
                    except (InvalidArgument, Unsatisfiable):
                        pass
                elif key == "additionalProperties" and schema.get("type") in ["object", None]:
                    if value is False and "pattern" not in schema:
                        # additionalProperties: false - add unexpected property
                        if not ctx.allow_extra_parameters and ctx.location in (
                            ParameterLocation.QUERY,
                            ParameterLocation.HEADER,
                            ParameterLocation.COOKIE,
                            ParameterLocation.BODY,
                        ):
                            continue
                        template = template or ctx.generate_from_schema(_get_template_schema(schema, "object", ctx))
                        yield NegativeValue(
                            {**template, UNKNOWN_PROPERTY_KEY: UNKNOWN_PROPERTY_VALUE},
                            scenario=CoverageScenario.OBJECT_UNEXPECTED_PROPERTIES,
                            description="Object with unexpected properties",
                            location=ctx.current_path,
                        )
                    elif isinstance(value, dict):
                        # additionalProperties with schema - generate invalid values for the schema
                        template = template or ctx.generate_from_schema(_get_template_schema(schema, "object", ctx))
                        existing_keys = set(schema.get("properties", {}).keys()) | set(template.keys())
                        additional_key = _pick_property_name(schema, existing_keys, ctx)
                        if additional_key is None:
                            continue
                        nctx = ctx.with_negative()
                        with nctx.at(additional_key):
                            for invalid in cover_schema_iter(nctx, value):
                                yield NegativeValue(
                                    {**template, additional_key: invalid.value},
                                    scenario=invalid.scenario,
                                    description=f"Object with invalid additional property: {invalid.description}",
                                    location=nctx.current_path,
                                )
                elif key == "maxProperties" and isinstance(value, int) and value >= 0:
                    additional_properties = schema.get("additionalProperties", True)
                    # Skip if additionalProperties is false - can't add more properties cleanly
                    if additional_properties is False:
                        continue
                    template = template or ctx.generate_from_schema(_get_template_schema(schema, "object", ctx))
                    obj_value = dict(template)
                    existing_keys = set(obj_value.keys())
                    needed = value + 1 - len(existing_keys)
                    if needed > 0:
                        for _ in range(needed):
                            new_key = _pick_property_name(schema, existing_keys, ctx)
                            if new_key is None:
                                break
                            existing_keys.add(new_key)
                            # Generate value based on additionalProperties schema, or use a default
                            if isinstance(additional_properties, dict):
                                obj_value[new_key] = ctx.generate_from_schema(additional_properties)
                            else:
                                obj_value[new_key] = UNKNOWN_PROPERTY_VALUE
                    if len(obj_value) > value and seen.insert(obj_value):
                        yield NegativeValue(
                            obj_value,
                            scenario=CoverageScenario.OBJECT_ABOVE_MAX_PROPERTIES,
                            description="Object with more properties than allowed by maxProperties",
                            location=ctx.current_path,
                        )
                elif key == "minProperties" and isinstance(value, int) and value > 0:
                    try:
                        required = schema.get("required", [])
                        if value == 1 and not required:
                            # Only use empty object if no required properties
                            obj_value = {}
                        else:
                            new_schema = {**schema, "minProperties": value - 1, "maxProperties": value - 1}
                            obj_value = ctx.generate_from_schema(new_schema)
                        if seen.insert(obj_value):
                            yield NegativeValue(
                                obj_value,
                                scenario=CoverageScenario.OBJECT_BELOW_MIN_PROPERTIES,
                                description="Object with fewer properties than allowed by minProperties",
                                location=ctx.current_path,
                            )
                    except (InvalidArgument, Unsatisfiable):
                        pass
                elif key == "allOf":
                    nctx = ctx.with_negative()
                    if len(value) == 1:
                        with nctx.at(0):
                            yield from cover_schema_iter(nctx, value[0], seen)
                    else:
                        with _ignore_unfixable():
                            canonical = canonicalish(schema)
                            yield from cover_schema_iter(nctx, canonical, seen)
                elif key == "anyOf":
                    nctx = ctx.with_negative()
                    resolved_schemas = [
                        ctx.resolve_ref(s["$ref"]) if isinstance(s, dict) and "$ref" in s else s for s in value
                    ]
                    validators = _make_branch_validators(resolved_schemas, ctx)
                    # Body fields in multipart/form-urlencoded are serialized as strings via str().
                    # Query/path/header parameters are also stringified, but servers parse them
                    # back to their declared type before validation, so str() doesn't make them
                    # valid for explicitly string-typed branches in that case.
                    stringify_body_fields = ctx.location == ParameterLocation.BODY and is_form_parts(ctx.media_type)
                    for idx, sub_schema in enumerate(value):
                        with nctx.at(idx):
                            for value in cover_schema_iter(nctx, sub_schema, seen):
                                # Negative value for this schema could be a positive value for another one
                                if is_valid_for_others(
                                    value.value, idx, validators, resolved_schemas, stringify_body_fields
                                ):
                                    continue
                                yield value
                elif key == "oneOf":
                    nctx = ctx.with_negative()
                    resolved_schemas = [
                        ctx.resolve_ref(s["$ref"]) if isinstance(s, dict) and "$ref" in s else s for s in value
                    ]
                    validators = _make_branch_validators(resolved_schemas, ctx)
                    for idx, sub_schema in enumerate(value):
                        with nctx.at(idx):
                            for value in cover_schema_iter(nctx, sub_schema, seen):
                                if is_invalid_for_oneOf(value.value, idx, validators):
                                    yield value
                elif key == "not" and isinstance(value, dict | bool):
                    # For 'not' schemas: generate positive cases of inner schema (valid values)
                    # These valid values are negative for the outer schema, so flip the mode
                    pctx = ctx.with_positive()
                    yield from _flip_generation_mode_for_not(cover_schema_iter(pctx, value, seen))


def is_valid_for_others(
    value: Any,
    idx: int,
    validators: list[jsonschema_rs.Validator],
    schemas: list[dict | bool] | None = None,
    will_be_serialized_to_string: bool = False,
) -> bool:
    if contains_binary(value):
        return False
    for vidx, validator in enumerate(validators):
        if idx == vidx:
            # This one is being negated
            continue
        if validator.is_valid(value):
            return True
        # In serialized contexts (multipart, form-urlencoded, path/query/header), non-string
        # values are converted via str() before transmission. Only skip if the other branch
        # explicitly requires string type — schemas without a type constraint accept strings
        # vacuously (e.g. `minimum` doesn't apply to strings), which would be a false match.
        if will_be_serialized_to_string and not isinstance(value, str) and schemas is not None:
            other = schemas[vidx]
            if isinstance(other, dict):
                explicit_type = other.get("type")
                has_string = explicit_type == "string" or (
                    isinstance(explicit_type, list) and "string" in explicit_type
                )
                if has_string and validator.is_valid(str(value)):
                    return True
    return False


def is_invalid_for_oneOf(value: object, idx: int, validators: list[jsonschema_rs.Validator]) -> bool:
    if contains_binary(value):
        # Binary values cannot be validated by jsonschema_rs; treat as not matching any other sub-schema
        return True
    valid_count = 0
    for vidx, validator in enumerate(validators):
        if idx == vidx:
            # This one is being negated
            continue
        if validator.is_valid(value):
            valid_count += 1
            # Should circuit - no need to validate more, it is already invalid
            if valid_count > 1:
                return True
    # No matching at all - we successfully generated invalid value
    return valid_count == 0


def _filter_against_combinators(
    cases: Generator[GeneratedValue, None, None], schema: JsonSchema
) -> Generator[GeneratedValue, None, None]:
    """Drop outer-only object values that violate `anyOf`/`oneOf` on the same schema.

    `_positive_object` generates from outer `properties` without consulting sibling `anyOf`/`oneOf`
    constraints (e.g. a branch tightening a property's enum). When such a combinator is present,
    validate each generated value against the full schema and drop the ones no branch accepts.
    """
    if not isinstance(schema, dict) or ("anyOf" not in schema and "oneOf" not in schema):
        yield from cases
        return
    try:
        validator = make_validator_for(schema)
    except Exception:
        yield from cases
        return
    for case in cases:
        try:
            if validator.is_valid(case.value):
                yield case
        except Exception:
            yield case


def _is_valid_with_formats(value: object, schema: JsonSchema, ctx: CoverageContext) -> bool:
    """Return True if value satisfies schema including format constraints at all nesting levels."""
    if not isinstance(schema, dict):
        return True
    full_schema: JsonSchema = schema
    if BUNDLE_STORAGE_KEY in ctx.root_schema:
        full_schema = {**schema, BUNDLE_STORAGE_KEY: ctx.root_schema[BUNDLE_STORAGE_KEY]}
    # Auto-detection picks the latest draft (wider format coverage); fall back to the spec's
    # draft so Draft-4-only constructs still validate instead of silently passing.
    try:
        return make_validator_for(full_schema).is_valid(value)
    except Exception:
        pass
    try:
        return make_validator(full_schema, ctx.validator_cls).is_valid(value)
    except Exception:
        return True


def _make_branch_validators(schemas: list[JsonSchema], ctx: CoverageContext) -> list[jsonschema_rs.Validator]:
    bundle = ctx.root_schema.get(BUNDLE_STORAGE_KEY)
    result = []
    for schema in schemas:
        if bundle is not None and isinstance(schema, dict):
            schema = {**schema, BUNDLE_STORAGE_KEY: bundle}
        result.append(make_validator_for(schema))
    return result


def _get_properties(schema: JsonSchema, ctx: CoverageContext) -> JsonSchema:
    if isinstance(schema, dict):
        if "example" in schema:
            example = schema["example"]
            if _is_valid_with_formats(example, schema, ctx):
                return {"const": example}
        if "default" in schema:
            default = schema["default"]
            if _is_valid_with_formats(default, schema, ctx):
                return {"const": default}
        if schema.get("examples"):
            valid = [ex for ex in schema["examples"] if _is_valid_with_formats(ex, schema, ctx)]
            if valid:
                return {"enum": valid}
        if schema.get("type") == "object":
            return _get_template_schema(schema, "object", ctx)
        _schema = deepclone(schema)
        if ctx.update_pattern is not None:
            _update_schema_pattern(_schema, ctx.update_pattern)
        # Strip format-invalid hints so hypothesis-jsonschema does not use them as generation seeds.
        if "default" in _schema and not _is_valid_with_formats(_schema["default"], _schema, ctx):
            del _schema["default"]
        if "example" in _schema and not _is_valid_with_formats(_schema["example"], _schema, ctx):
            del _schema["example"]
        if "examples" in _schema:
            valid_examples = [ex for ex in _schema["examples"] if _is_valid_with_formats(ex, _schema, ctx)]
            if valid_examples:
                _schema["examples"] = valid_examples
            else:
                del _schema["examples"]
        return _schema
    return schema


_FAST_PATH_KEYS = frozenset({"properties", "required", "type"})


_OBJECT_ONLY_KEYWORDS = ("properties", "required", "patternProperties", "propertyNames", "dependencies")


def _implies_object_type(schema: JsonSchemaObject) -> bool:
    # `additionalProperties: {schema}` implicitly types the value as an object even when
    # `type: object` is omitted (common in Azure swagger 2.0 tag maps); without this the
    # positive object generator never runs and the keyword stays uncovered.
    if any(key in schema for key in _OBJECT_ONLY_KEYWORDS):
        return True
    additional = schema.get("additionalProperties")
    if isinstance(additional, dict):
        return True
    return False


def _get_template_schema(schema: JsonSchemaObject, ty: str, ctx: CoverageContext) -> JsonSchemaObject:
    if ty == "object":
        properties = schema.get("properties")
        if properties is not None:
            required = schema.get("required", [])
            if schema.get("additionalProperties") is not False:
                extra: dict[str, JsonSchemaObject] = {k: {} for k in required if k not in properties}
            else:
                extra = {}
            all_properties = {
                **{k: _get_properties(v, ctx) for k, v in properties.items()},
                **extra,
            }
            # When the fast path fires, required is used to decide what's truly required;
            # keep it at the schema's original required to avoid aborting on optional
            # properties with unsatisfiable schemas.  Otherwise inflate to all_properties
            # so every defined property appears in the generated template.
            # Ignore non-structural keys (annotations like `title`, OpenAPI `nullable`,
            # `readOnly`, `x-*` extensions); only JSON Schema keywords gate the choice.
            schema_keys = {k for k in schema if k in ALL_KEYWORDS}
            if schema_keys <= _FAST_PATH_KEYS:
                required_for_template = [k for k in required if k in all_properties]
            else:
                required_for_template = list(all_properties)
            return {
                **schema,
                "required": required_for_template,
                "type": ty,
                "properties": all_properties,
            }
    return {**schema, "type": ty}


def _get_not_schema(schema: JsonSchemaObject) -> JsonSchemaObject:
    """Safely get the 'not' schema as a dict, handling boolean schemas."""
    not_schema = schema.get("not", {})
    if isinstance(not_schema, dict):
        return not_schema.copy()
    return {}


def _ensure_valid_path_parameter_schema(schema: JsonSchemaObject) -> JsonSchemaObject:
    # Path parameters should have at least 1 character length and don't contain any characters with special treatment
    # on the transport level.
    # The implementation below sneaks into `not` to avoid clashing with existing `pattern` keyword
    not_ = _get_not_schema(schema)
    not_["pattern"] = r"[/{}]"
    min_length = max(schema.get("minLength", 0), 1)
    return {**schema, "minLength": min_length, "not": not_}


def _ensure_valid_headers_schema(schema: JsonSchemaObject) -> JsonSchemaObject:
    # Reject any character that is not A-Z, a-z, or 0-9 for simplicity
    not_ = _get_not_schema(schema)
    not_["pattern"] = r"[^A-Za-z0-9]"
    return {**schema, "not": not_}


def _positive_string(ctx: CoverageContext, schema: JsonSchemaObject) -> Generator[GeneratedValue, None, None]:
    """Generate positive string values."""
    # Pin type to "string"; for unions like ["string","null"] the dispatcher yields null separately,
    # without this override generation here may pick null and drop the boundary-length variants.
    schema = {**schema, "type": "string"}
    min_length = schema.get("minLength")
    if min_length == 0:
        min_length = None
    max_length = schema.get("maxLength")
    if ctx.location == "path" and not ("format" in schema and schema["format"] in ctx.custom_formats):
        schema = _ensure_valid_path_parameter_schema(schema)
    elif ctx.location in ("header", "cookie") and not (
        "format" in schema and (schema["format"] in ctx.custom_formats or schema["format"] in BUILT_IN_STRING_FORMATS)
    ):
        # Don't apply it for known formats - they will insure the correct format during generation
        schema = _ensure_valid_headers_schema(schema)

    # Sentinel-based reads so falsy spec hints (`default: 0`, `example: ""`) and explicit
    # `default: null` / `example: null` aren't confused with "key absent".
    example = schema.get("example", NOT_SET)
    examples = schema.get("examples")
    default = schema.get("default", NOT_SET)

    # Two-layer check to avoid potentially expensive data generation using schema constraints as a key
    seen_values = HashSet()
    seen_constraints: set[tuple] = set()

    if example is not NOT_SET or examples or default is not NOT_SET:
        has_valid_example = False
        if (
            example is not NOT_SET
            and _is_valid_with_formats(example, schema, ctx)
            and ctx.is_valid_for_location(example)
            and seen_values.insert(example)
        ):
            has_valid_example = True
            yield PositiveValue(example, scenario=CoverageScenario.EXAMPLE_VALUE, description="Example value")
        if examples:
            for example in examples:
                if (
                    _is_valid_with_formats(example, schema, ctx)
                    and ctx.is_valid_for_location(example)
                    and seen_values.insert(example)
                ):
                    has_valid_example = True
                    yield PositiveValue(example, scenario=CoverageScenario.EXAMPLE_VALUE, description="Example value")
        if (
            default is not NOT_SET
            and not (example is not NOT_SET and default == example)
            and not (examples is not None and any(default == ex for ex in examples))
            and _is_valid_with_formats(default, schema, ctx)
            and ctx.is_valid_for_location(default)
            and seen_values.insert(default)
        ):
            has_valid_example = True
            yield PositiveValue(default, scenario=CoverageScenario.DEFAULT_VALUE, description="Default value")
        if not has_valid_example:
            if not min_length and not max_length or "pattern" in schema:
                value = ctx.generate_from_schema(schema)
                seen_values.insert(value)
                seen_constraints.add((min_length, max_length))
                yield PositiveValue(value, scenario=CoverageScenario.VALID_STRING, description="Valid string")
    elif not min_length and not max_length or "pattern" in schema:
        value = ctx.generate_from_schema(schema)
        seen_values.insert(value)
        seen_constraints.add((min_length, max_length))
        yield PositiveValue(value, scenario=CoverageScenario.VALID_STRING, description="Valid string")

    if min_length is not None and min_length < INTERNAL_BUFFER_SIZE:
        # Exactly the minimum length
        key = (min_length, min_length)
        if key not in seen_constraints:
            seen_constraints.add(key)
            with _ignore_unfixable():
                value = ctx.generate_from_schema({**schema, "maxLength": min_length})
                if seen_values.insert(value):
                    yield PositiveValue(
                        value, scenario=CoverageScenario.MINIMUM_LENGTH_STRING, description="Minimum length string"
                    )

        # One character more than minimum if possible
        larger = min_length + 1
        key = (larger, larger)
        if larger < INTERNAL_BUFFER_SIZE and key not in seen_constraints and (not max_length or larger <= max_length):
            seen_constraints.add(key)
            with _ignore_unfixable():
                value = ctx.generate_from_schema({**schema, "minLength": larger, "maxLength": larger})
                if seen_values.insert(value):
                    yield PositiveValue(
                        value,
                        scenario=CoverageScenario.NEAR_BOUNDARY_LENGTH_STRING,
                        description="Near-boundary length string",
                    )

    if max_length is not None:
        # Exactly the maximum length
        key = (max_length, max_length)
        if max_length < INTERNAL_BUFFER_SIZE and key not in seen_constraints:
            seen_constraints.add(key)
            with _ignore_unfixable():
                value = ctx.generate_from_schema({**schema, "minLength": max_length, "maxLength": max_length})
                if seen_values.insert(value):
                    yield PositiveValue(
                        value, scenario=CoverageScenario.MAXIMUM_LENGTH_STRING, description="Maximum length string"
                    )

        # One character less than maximum if possible
        smaller = max_length - 1
        key = (smaller, smaller)
        if (
            smaller < INTERNAL_BUFFER_SIZE
            and key not in seen_constraints
            and (smaller > 0 and (min_length is None or smaller >= min_length))
        ):
            seen_constraints.add(key)
            with _ignore_unfixable():
                value = ctx.generate_from_schema({**schema, "minLength": smaller, "maxLength": smaller})
                if seen_values.insert(value):
                    yield PositiveValue(
                        value,
                        scenario=CoverageScenario.NEAR_BOUNDARY_LENGTH_STRING,
                        description="Near-boundary length string",
                    )


def closest_multiple_greater_than(y: int, x: int) -> int:
    """Find the closest multiple of X that is greater than Y."""
    quotient, remainder = divmod(y, x)
    if remainder == 0:
        return y
    return x * (quotient + 1)


def _shift_by_multiple(value: int | float, step: int | float, *, direction: int) -> int | float:
    # IEEE-754 subtraction (e.g. `99999.99 - 0.01`) drifts by `~1e-12`, making the result
    # fail `multipleOf`. Decimal arithmetic via the canonical `repr` keeps the value exact
    # for fractions whose decimal form is short.
    if isinstance(value, int) and isinstance(step, int):
        return value + direction * step
    return float(Decimal(str(value)) + direction * Decimal(str(step)))


def _largest_multiple_within(value: int | float, step: int | float) -> int | float:
    if isinstance(value, int) and isinstance(step, int):
        return value - (value % step)
    decimal_step = Decimal(str(step))
    return float(Decimal(str(value)) - (Decimal(str(value)) % decimal_step))


def _is_numeric_bound(value: Any) -> TypeGuard[int | float]:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _adjust_numeric_bound(value: int | float, *, is_integer: bool, direction: int) -> int | float:
    if is_integer:
        return value + direction
    return nextafter(float(value), inf if direction > 0 else -inf)


def _positive_number(ctx: CoverageContext, schema: JsonSchemaObject) -> Generator[GeneratedValue, None, None]:
    """Generate positive integer values."""
    # Pin type to "integer" or "number"; for unions like ["string","number","null"] the
    # dispatcher yields the other branches separately, and without this override generation
    # here would draw from the union and miss the numeric variant entirely.
    declared = schema.get("type")
    declared_types = declared if isinstance(declared, list) else [declared]
    pinned = "integer" if "integer" in declared_types else "number"
    schema = {**schema, "type": pinned}
    is_integer = pinned == "integer"
    minimum = schema.get("minimum")
    maximum = schema.get("maximum")
    exclusive_minimum = schema.get("exclusiveMinimum")
    exclusive_maximum = schema.get("exclusiveMaximum")
    if isinstance(exclusive_minimum, bool):
        if exclusive_minimum and _is_numeric_bound(minimum):
            minimum = _adjust_numeric_bound(minimum, is_integer=is_integer, direction=1)
    elif _is_numeric_bound(exclusive_minimum):
        minimum = _adjust_numeric_bound(exclusive_minimum, is_integer=is_integer, direction=1)
    if isinstance(exclusive_maximum, bool):
        if exclusive_maximum and _is_numeric_bound(maximum):
            maximum = _adjust_numeric_bound(maximum, is_integer=is_integer, direction=-1)
    elif _is_numeric_bound(exclusive_maximum):
        maximum = _adjust_numeric_bound(exclusive_maximum, is_integer=is_integer, direction=-1)
    multiple_of = schema.get("multipleOf")
    example = schema.get("example", NOT_SET)
    examples = schema.get("examples")
    default = schema.get("default", NOT_SET)

    seen = HashSet()

    def _within_adjusted_bounds(value: int | float) -> bool:
        return (minimum is None or value >= minimum) and (maximum is None or value <= maximum)

    if example is not NOT_SET or examples or default is not NOT_SET:
        has_valid_example = False
        if example is not NOT_SET and _is_valid_with_formats(example, schema, ctx) and seen.insert(example):
            has_valid_example = True
            yield PositiveValue(example, scenario=CoverageScenario.EXAMPLE_VALUE, description="Example value")
        if examples:
            for example in examples:
                if _is_valid_with_formats(example, schema, ctx) and seen.insert(example):
                    has_valid_example = True
                    yield PositiveValue(example, scenario=CoverageScenario.EXAMPLE_VALUE, description="Example value")
        if (
            default is not NOT_SET
            and not (example is not NOT_SET and default == example)
            and not (examples is not None and any(default == ex for ex in examples))
            and _is_valid_with_formats(default, schema, ctx)
            and seen.insert(default)
        ):
            has_valid_example = True
            yield PositiveValue(default, scenario=CoverageScenario.DEFAULT_VALUE, description="Default value")
        if not has_valid_example and minimum is None and maximum is None:
            value = ctx.generate_from_schema(schema)
            if seen.insert(value):
                yield PositiveValue(value, scenario=CoverageScenario.VALID_NUMBER, description="Valid number")
    elif minimum is None and maximum is None:
        value = ctx.generate_from_schema(schema)
        seen.insert(value)
        yield PositiveValue(value, scenario=CoverageScenario.VALID_NUMBER, description="Valid number")

    if minimum is not None:
        # Exactly the minimum
        if multiple_of is not None:
            smallest = closest_multiple_greater_than(minimum, multiple_of)
        else:
            smallest = minimum
        if _within_adjusted_bounds(smallest) and seen.insert(smallest):
            yield PositiveValue(smallest, scenario=CoverageScenario.MINIMUM_VALUE, description="Minimum value")

        # One more than minimum if possible
        if multiple_of is not None:
            larger = _shift_by_multiple(smallest, multiple_of, direction=1)
        else:
            larger = minimum + 1
        if (maximum is None or larger <= maximum) and seen.insert(larger):
            yield PositiveValue(
                larger, scenario=CoverageScenario.NEAR_BOUNDARY_NUMBER, description="Near-boundary number"
            )

    if maximum is not None:
        # Exactly the maximum
        if multiple_of is not None:
            largest = _largest_multiple_within(maximum, multiple_of)
        else:
            largest = maximum
        if _within_adjusted_bounds(largest) and seen.insert(largest):
            yield PositiveValue(largest, scenario=CoverageScenario.MAXIMUM_VALUE, description="Maximum value")

        # One less than maximum if possible
        if multiple_of is not None:
            smaller = _shift_by_multiple(largest, multiple_of, direction=-1)
        else:
            smaller = maximum - 1
        if (minimum is None or smaller >= minimum) and seen.insert(smaller):
            yield PositiveValue(
                smaller, scenario=CoverageScenario.NEAR_BOUNDARY_NUMBER, description="Near-boundary number"
            )


def _positive_array(
    ctx: CoverageContext, schema: JsonSchemaObject, template: list
) -> Generator[GeneratedValue, None, None]:
    example = schema.get("example", NOT_SET)
    examples = schema.get("examples")
    default = schema.get("default", NOT_SET)

    seen = HashSet()
    seen_constraints: set[tuple] = set()

    if example is not NOT_SET or examples or default is not NOT_SET:
        if example is not NOT_SET and _is_valid_with_formats(example, schema, ctx) and seen.insert(example):
            yield PositiveValue(example, scenario=CoverageScenario.EXAMPLE_VALUE, description="Example value")
        if examples:
            for example in examples:
                if _is_valid_with_formats(example, schema, ctx) and seen.insert(example):
                    yield PositiveValue(example, scenario=CoverageScenario.EXAMPLE_VALUE, description="Example value")
        if (
            default is not NOT_SET
            and not (example is not NOT_SET and default == example)
            and not (examples is not None and any(default == ex for ex in examples))
            and _is_valid_with_formats(default, schema, ctx)
            and seen.insert(default)
        ):
            yield PositiveValue(default, scenario=CoverageScenario.DEFAULT_VALUE, description="Default value")
    elif seen.insert(template):
        yield PositiveValue(template, scenario=CoverageScenario.VALID_ARRAY, description="Valid array")

    # Boundary and near-boundary sizes
    min_items = schema.get("minItems")
    max_items = schema.get("maxItems")
    if min_items is not None:
        # Do not generate an array with `minItems` length, because it is already covered by `template`
        # One item more than minimum if possible
        larger = min_items + 1
        if (max_items is None or larger <= max_items) and larger not in seen_constraints:
            seen_constraints.add(larger)
            value = ctx.generate_from_schema({**schema, "minItems": larger, "maxItems": larger})
            if seen.insert(value):
                yield PositiveValue(
                    value, scenario=CoverageScenario.NEAR_BOUNDARY_ITEMS_ARRAY, description="Near-boundary items array"
                )

    if max_items is not None:
        if max_items < INTERNAL_BUFFER_SIZE and max_items not in seen_constraints:
            seen_constraints.add(max_items)
            value = ctx.generate_from_schema({**schema, "minItems": max_items})
            if seen.insert(value):
                yield PositiveValue(
                    value, scenario=CoverageScenario.MAXIMUM_ITEMS_ARRAY, description="Maximum items array"
                )

        # One item smaller than maximum if possible
        smaller = max_items - 1
        if (
            INTERNAL_BUFFER_SIZE > smaller > 0
            and (min_items is None or smaller >= min_items)
            and smaller not in seen_constraints
        ):
            value = ctx.generate_from_schema({**schema, "minItems": smaller, "maxItems": smaller})
            if seen.insert(value):
                yield PositiveValue(
                    value, scenario=CoverageScenario.NEAR_BOUNDARY_ITEMS_ARRAY, description="Near-boundary items array"
                )

    if (
        "items" in schema
        and isinstance(schema["items"], dict)
        and "enum" in schema["items"]
        and isinstance(schema["items"]["enum"], list)
        and max_items != 0
    ):
        # Ensure there is enough items to pass `minItems` if it is specified
        length = min_items or 1
        item_schema = schema["items"]
        enum_values = [v for v in item_schema["enum"] if is_valid(v, item_schema)]
        if schema.get("uniqueItems") and length > 1:
            for i, variant in enumerate(enum_values):
                others = [enum_values[j] for j in range(len(enum_values)) if j != i]
                value = [variant] + others[: length - 1]
                if seen.insert(value):
                    yield PositiveValue(
                        value,
                        scenario=CoverageScenario.ENUM_VALUE_ITEMS_ARRAY,
                        description="Enum value from available for items array",
                    )
        else:
            for variant in enum_values:
                value = [variant] * length
                if seen.insert(value):
                    yield PositiveValue(
                        value,
                        scenario=CoverageScenario.ENUM_VALUE_ITEMS_ARRAY,
                        description="Enum value from available for items array",
                    )
    elif (
        "items" in schema
        and isinstance(schema["items"], dict)
        and (min_items is None or min_items <= 1)
        and (max_items is None or max_items >= 1)
    ):
        # Single-item arrays exercise each items-schema branch individually.
        # `maxItems`-sized boundary arrays (above) repeat one shape and miss multi-branch coverage.
        sub_schema = schema["items"]
        for item in cover_schema_iter(ctx, sub_schema):
            candidate = [item.value]
            if seen.insert(candidate):
                yield PositiveValue(
                    candidate,
                    scenario=CoverageScenario.VALID_ARRAY,
                    description=f"Single-item array: {item.description}",
                )


def _positive_object(
    ctx: CoverageContext, schema: JsonSchemaObject, template: dict
) -> Generator[GeneratedValue, None, None]:
    example = schema.get("example", NOT_SET)
    examples = schema.get("examples")
    default = schema.get("default", NOT_SET)

    properties = schema.get("properties", {})
    required = set(schema.get("required", []))
    optional = sorted(set(properties) - required, key=str)
    min_props = schema.get("minProperties")

    # A required property absent from the template makes every derived combination schema-invalid.
    template_complete = not (required - set(template))
    # Whole-object dedup. Empty/partial templates make several scenarios
    # (Valid object, subset-of-optional, only-required) collapse to the same value.
    outer_seen = HashSet()

    if example is not NOT_SET or examples or default is not NOT_SET:
        if example is not NOT_SET and _is_valid_with_formats(example, schema, ctx):
            yield PositiveValue(example, scenario=CoverageScenario.EXAMPLE_VALUE, description="Example value")
        if examples:
            for example in examples:
                if _is_valid_with_formats(example, schema, ctx):
                    yield PositiveValue(example, scenario=CoverageScenario.EXAMPLE_VALUE, description="Example value")
        if (
            default is not NOT_SET
            and not (example is not NOT_SET and default == example)
            and not (examples is not None and any(default == ex for ex in examples))
            and _is_valid_with_formats(default, schema, ctx)
        ):
            yield PositiveValue(default, scenario=CoverageScenario.DEFAULT_VALUE, description="Default value")
    elif template_complete and (template or not (ctx.is_required and is_form_parts(ctx.media_type))):
        outer_seen.insert(template)
        yield PositiveValue(template, scenario=CoverageScenario.VALID_OBJECT, description="Valid object")

    if not template_complete:
        return

    # Generate combinations with required properties and one optional property
    for name in optional:
        combo = {k: v for k, v in template.items() if k in required or k == name}
        if combo != template and (min_props is None or len(combo) >= min_props) and outer_seen.insert(combo):
            yield PositiveValue(
                combo,
                scenario=CoverageScenario.OBJECT_REQUIRED_AND_OPTIONAL,
                description=f"Object with all required properties and '{name}'",
            )
    # Generate one combination for each size from 2 to N-1
    for selection in select_combinations(optional):
        combo = {k: v for k, v in template.items() if k in required or k in selection}
        if (min_props is None or len(combo) >= min_props) and outer_seen.insert(combo):
            yield PositiveValue(
                combo,
                scenario=CoverageScenario.OBJECT_REQUIRED_AND_OPTIONAL,
                description="Object with all required and a subset of optional properties",
            )
    # Generate only required properties
    if set(properties) != required:
        only_required = {k: v for k, v in template.items() if k in required}
        # Skip empty object for required form bodies - {} serializes to no content
        # which violates requestBody.required
        if (
            (min_props is None or len(only_required) >= min_props)
            and (only_required or not (ctx.is_required and is_form_parts(ctx.media_type)))
            and outer_seen.insert(only_required)
        ):
            yield PositiveValue(
                only_required,
                scenario=CoverageScenario.OBJECT_ONLY_REQUIRED,
                description="Object with only required properties",
            )
    seen = HashSet()
    for name, sub_schema in properties.items():
        seen.insert(template.get(name))
        for new in cover_schema_iter(ctx, sub_schema):
            if seen.insert(new.value):
                yield PositiveValue(
                    {**template, name: new.value},
                    scenario=CoverageScenario.VALID_OBJECT,
                    description=f"Object with valid '{name}' value: {new.description}",
                )
        seen.clear()
    # Handle additionalProperties with schema
    additional_properties = schema.get("additionalProperties")
    if isinstance(additional_properties, dict):
        max_properties = schema.get("maxProperties")
        if isinstance(max_properties, int) and len(template) + 1 > max_properties:
            return
        existing_keys = set(properties.keys()) | set(template.keys())
        additional_key = _pick_property_name(schema, existing_keys, ctx)
        if additional_key is None:
            return
        for new in cover_schema_iter(ctx, additional_properties):
            if seen.insert(new.value):
                yield PositiveValue(
                    {**template, additional_key: new.value},
                    scenario=CoverageScenario.OBJECT_ADDITIONAL_PROPERTY,
                    description=f"Object with additional property: {new.description}",
                )


def select_combinations(optional: list[str]) -> Iterator[tuple[str, ...]]:
    for size in range(2, len(optional)):
        yield next(combinations(optional, size))


def _negative_enum(ctx: CoverageContext, value: list, seen: HashSet) -> Generator[GeneratedValue, None, None]:
    def is_not_in_value(x: Any) -> bool:
        if x in value or not ctx.is_valid_for_location(x):
            return False
        return seen.insert(x)

    strategy = (
        st.text(alphabet=st.characters(min_codepoint=65, max_codepoint=122, categories=["L"]), min_size=3)
        | st.none()
        | st.booleans()
        | NUMERIC_STRATEGY
    ).filter(is_not_in_value)
    yield NegativeValue(
        ctx.generate_from(strategy),
        scenario=CoverageScenario.INVALID_ENUM_VALUE,
        description="Invalid enum value",
        location=ctx.current_path,
    )


def _negative_properties(
    ctx: CoverageContext, template: dict, properties: dict
) -> Generator[GeneratedValue, None, None]:
    nctx = ctx.with_negative()
    is_form = ctx.location == ParameterLocation.BODY and is_form_parts(ctx.media_type)
    is_xml = ctx.location == ParameterLocation.BODY and ctx.media_type is not None and is_xml_parts(ctx.media_type)
    bundle = ctx.root_schema.get(BUNDLE_STORAGE_KEY) if isinstance(ctx.root_schema, dict) else None
    for key, sub_schema in properties.items():
        validator: jsonschema_rs.Validator | None = None
        # Draft 4 ignores siblings of `$ref`, so generation against `{$ref, sibling}` may yield
        # values the body validator silently accepts; filter those out below.
        sub_has_ref = isinstance(sub_schema, dict) and "$ref" in sub_schema
        if isinstance(sub_schema, dict):
            # Include bundled definitions so $ref references in sub_schema resolve
            validator_schema = sub_schema if bundle is None else {**sub_schema, BUNDLE_STORAGE_KEY: bundle}
            try:
                validator = make_validator(validator_schema, ctx.validator_cls)
            except Exception:
                pass
        with nctx.at(key):
            for value in cover_schema_iter(nctx, sub_schema):
                if validator is not None:
                    v = value.value
                    # Form bodies (urlencoded and multipart) stringify scalar property values
                    # on the wire; any non-string whose `str(v)` satisfies the property schema
                    # is a no-op mutation.
                    if is_form and not isinstance(v, str) and validator.is_valid(str(v)):
                        continue
                    # XML text content stringifies primitives; objects/arrays keep structure.
                    if is_xml and isinstance(v, (bool, int, float)) and validator.is_valid(str(v)):
                        continue
                    # Empty dict/None both serialize to empty string in XML
                    if is_xml and (v == {} or v is None) and validator.is_valid(""):
                        continue
                    # `{$ref, sibling}` only honors the ref target on Draft 4 — drop mutations
                    # against the silenced siblings that the bare target accepts vacuously.
                    if sub_has_ref and not is_form and not is_xml and validator.is_valid(v):
                        continue
                inner = value.description or ""
                # Build path notation: "a -> b: leaf" for nested, "a: leaf" for direct
                description = f"{key} -> {inner}" if ": " in inner else f"{key}: {inner}"
                yield NegativeValue(
                    {**template, key: value.value},
                    scenario=value.scenario,
                    description=description,
                    location=nctx.current_path,
                    parameter=key,
                )


def _negative_property_names(
    ctx: CoverageContext, template: dict, property_names_schema: dict
) -> Generator[GeneratedValue, None, None]:
    """Objects with an extra key violating the `propertyNames` sub-schema."""
    nctx = ctx.with_negative()
    for value in cover_schema_iter(nctx, property_names_schema):
        bad_key = value.value
        # JSON object keys are always strings; non-string negatives can't be carried on the wire.
        if not isinstance(bad_key, str) or bad_key in template:
            continue
        candidate = {**template, bad_key: ""}
        if not ctx.leads_to_negative_test_case(candidate):
            continue
        yield NegativeValue(
            candidate,
            scenario=CoverageScenario.OBJECT_INVALID_PROPERTY_NAME,
            description=f"Object with property name violating propertyNames: {value.description}",
            location=nctx.current_path,
        )


def _negative_pattern_properties(
    ctx: CoverageContext, template: dict, pattern_properties: dict
) -> Generator[GeneratedValue, None, None]:
    nctx = ctx.with_negative()
    for pattern, sub_schema in pattern_properties.items():
        try:
            key = ctx.generate_from(st.from_regex(pattern))
        except re.error:
            continue
        with nctx.at(pattern):
            for value in cover_schema_iter(nctx, sub_schema):
                yield NegativeValue(
                    {**template, key: value.value},
                    scenario=value.scenario,
                    description=f"Object with invalid pattern key '{key}' ('{pattern}') value: {value.description}",
                    location=nctx.current_path,
                )


def _negative_items(ctx: CoverageContext, schema: JsonSchema) -> Generator[GeneratedValue, None, None]:
    """Arrays not matching the schema."""
    nctx = ctx.with_negative()
    for value in cover_schema_iter(nctx, schema):
        items = [value.value]
        if ctx.leads_to_negative_test_case(items):
            yield NegativeValue(
                items,
                scenario=value.scenario,
                description=f"Array with invalid items: {value.description}",
                location=nctx.current_path,
            )


def _negative_prefix_items(
    ctx: CoverageContext, item_schemas: list[JsonSchema]
) -> Generator[GeneratedValue, None, None]:
    """Arrays with invalid items at specific positions (tuple validation)."""
    if not item_schemas:
        return
    # Generate valid values for each position
    pctx = ctx.with_positive()
    valid_items = []
    for item_schema in item_schemas:
        try:
            valid_items.append(pctx.generate_from_schema(item_schema))
        except (InvalidArgument, Unsatisfiable):
            return
    # For each position, generate negative values and yield arrays with one invalid item
    nctx = ctx.with_negative()
    for idx, item_schema in enumerate(item_schemas):
        for neg_value in cover_schema_iter(nctx, item_schema):
            items = valid_items.copy()
            items[idx] = neg_value.value
            if ctx.leads_to_negative_test_case(items):
                yield NegativeValue(
                    items,
                    scenario=neg_value.scenario,
                    description=f"Array with invalid item at index {idx}: {neg_value.description}",
                    location=nctx.current_path,
                )


def _not_matching_pattern(value: str, pattern: re.Pattern) -> bool:
    return pattern.search(value) is None


def _negative_pattern(
    ctx: CoverageContext, pattern: str, min_length: int | None = None, max_length: int | None = None
) -> Generator[GeneratedValue, None, None]:
    try:
        compiled = re.compile(pattern)
    except re.error:
        return
    try:
        validator: jsonschema_rs.Validator | None = ctx.validator_cls(
            {"type": "string", "pattern": pattern}, pattern_options=FANCY_REGEX_OPTIONS
        )
    except Exception:
        validator = None
    strategy = (
        st.text(min_size=min_length or 0, max_size=max_length)
        .filter(partial(_not_matching_pattern, pattern=compiled))
        .filter(ctx.is_valid_for_location)
    )
    if validator is not None:
        strategy = strategy.filter(lambda v, _v=validator: not _v.is_valid(v))
    yield NegativeValue(
        ctx.generate_from(strategy),
        scenario=CoverageScenario.INVALID_PATTERN,
        description=f"Value not matching the '{pattern}' pattern",
        location=ctx.current_path,
    )


def _with_negated_key(schema: JsonSchemaObject, key: str, value: Any) -> JsonSchemaObject:
    return {"allOf": [{k: v for k, v in schema.items() if k != key}, {"not": {key: value}}]}


def _negative_multiple_of(
    ctx: CoverageContext, schema: dict, multiple_of: int | float
) -> Generator[GeneratedValue, None, None]:
    yield NegativeValue(
        ctx.generate_from_schema(_with_negated_key(schema, "multipleOf", multiple_of)),
        scenario=CoverageScenario.NOT_MULTIPLE_OF,
        description=f"Non-multiple of {multiple_of}",
        location=ctx.current_path,
    )


def _negative_unique_items(ctx: CoverageContext, schema: JsonSchemaObject) -> Generator[GeneratedValue, None, None]:
    unique = jsonify(ctx.generate_from_schema({**schema, "type": "array", "minItems": 1, "maxItems": 1}))
    yield NegativeValue(
        unique + unique,
        scenario=CoverageScenario.NON_UNIQUE_ITEMS,
        description="Non-unique items",
        location=ctx.current_path,
    )


def _negative_required(
    ctx: CoverageContext, template: dict, required: list[str]
) -> Generator[GeneratedValue, None, None]:
    for key in required:
        yield NegativeValue(
            {k: v for k, v in template.items() if k != key},
            scenario=CoverageScenario.OBJECT_MISSING_REQUIRED_PROPERTY,
            description=f"Missing required property: {key}",
            location=ctx.current_path,
            parameter=key,
        )


def _violates_format(value: object, format: str, validator_cls: type[jsonschema_rs.Validator]) -> bool:
    return not conforms_to_format(value, format, validator_cls)


def _violates_hostname(value: object, validator_cls: type[jsonschema_rs.Validator]) -> bool:
    return value == "" or not conforms_to_format(value, "hostname", validator_cls)


def _negative_format(
    ctx: CoverageContext, schema: JsonSchemaObject, format: str
) -> Generator[GeneratedValue, None, None]:
    # Only generate negative format cases for formats that have validation semantics.
    # In OpenAPI 3.0, `format` is an annotation and does NOT impose validation constraints by itself.
    # Formats like "password" have no validation - any string is valid.
    # We can only generate truly invalid data for formats in VALIDATED_FORMATS (e.g., "email", "uri", "uuid").
    if format not in VALIDATED_FORMATS:
        return
    # The active draft determines which formats actually validate (Draft 4 treats
    # iri-reference / json-pointer / etc. as annotation-only). Skip when the format
    # is not validated — the strategy below would be unsatisfiable for every property.
    validator_cls = ctx.validator_cls
    if format not in VALIDATED_FORMATS_BY_DRAFT.get(validator_cls, frozenset()):
        return
    # Hypothesis-jsonschema does not canonicalise it properly right now, which leads to unsatisfiable schema
    without_format = {k: v for k, v in schema.items() if k != "format"}
    without_format["type"] = "string"
    if ctx.location == "path":
        # Empty path parameters are invalid
        without_format["minLength"] = 1
    if format == "hostname":
        filter_fn = partial(_violates_hostname, validator_cls=validator_cls)
    else:
        filter_fn = partial(_violates_format, format=format, validator_cls=validator_cls)
    strategy = from_schema(without_format).filter(filter_fn)
    yield NegativeValue(
        examples.generate_one(strategy),
        scenario=CoverageScenario.INVALID_FORMAT,
        description=f"Value not matching the '{format}' format",
        location=ctx.current_path,
    )


def _is_non_integer_float(x: float) -> bool:
    return x != int(x)


def _is_not_numeric_string(x: str) -> bool:
    try:
        float(x)
        return False
    except (ValueError, TypeError):
        return True


def is_valid_header_value(value: object) -> bool:
    value = str(value)
    if not is_latin_1_encodable(value):
        return False
    if has_invalid_characters("A", value):
        return False
    return True


def jsonify(value: Any) -> Any:
    if isinstance(value, bool):
        return "true" if value else "false"
    elif value is None:
        return "null"

    stack: list = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            for key, sub_item in item.items():
                if isinstance(sub_item, bool):
                    item[key] = "true" if sub_item else "false"
                elif sub_item is None:
                    item[key] = "null"
                elif isinstance(sub_item, dict):
                    stack.append(sub_item)
                elif isinstance(sub_item, list):
                    stack.extend(item)
        elif isinstance(item, list):
            for idx, sub_item in enumerate(item):
                if isinstance(sub_item, bool):
                    item[idx] = "true" if sub_item else "false"
                elif sub_item is None:
                    item[idx] = "null"
                else:
                    stack.extend(item)
    return value


def quote_path_parameter(value: Any) -> str:
    if isinstance(value, str):
        if value == ".":
            return "%2E"
        elif value == "..":
            return "%2E%2E"
        else:
            return quote_plus(value)
    if isinstance(value, list):
        return ",".join(map(str, value))
    return str(value)


def _negative_type(
    ctx: CoverageContext, ty: str | list[str], seen: HashSet, schema: dict[str, Any]
) -> Generator[GeneratedValue, None, None]:
    if isinstance(ty, str):
        types = [ty]
    else:
        types = ty
    # Root-level binary/byte format with non-JSON content types - type mutations don't produce meaningful wire violations
    # Path is ['type'] at root level, vs ['properties', 'fieldname', 'type'] for nested properties
    if (
        "string" in types
        and ctx.location == ParameterLocation.BODY
        and schema.get("format") in ("binary", "byte")
        and ctx.path == ["type"]
        and ctx.media_type is not None
        and ctx.media_type[1] != "json"
    ):
        return
    # Form/multipart body-level type mutations don't yield reliable wire violations:
    # form-urlencoded serializes to empty body; multipart renders as boundaries around
    # str(value), which permissive servers accept as zero-part multipart.
    if "object" in types and ctx.location == ParameterLocation.BODY and is_form_parts(ctx.media_type):
        return
    # Form-parts stringify every value; non-strings sent for a string-typed property
    # read as valid strings server-side, collapsing into the enum/format/range negation.
    if "string" in types and ctx.location == ParameterLocation.BODY and is_form_parts(ctx.media_type):
        return
    strategies = {ty: strategy for ty, strategy in STRATEGIES_FOR_TYPE.items() if ty not in types}

    filter_func = {
        "path": lambda x: not is_invalid_path_parameter(x),
        "header": is_valid_header_value,
        "cookie": is_valid_header_value,
        "query": lambda x: not contains_unicode_surrogate_pair(x),
    }.get(ctx.location)

    if "number" in types:
        strategies.pop("integer", None)
    if "integer" in types:
        strategies["number"] = FLOAT_STRATEGY.filter(_is_non_integer_float)
    # For path/query parameters, numeric strings like "9" serialize identically to integer 9 in the URL,
    # making them indistinguishable and causing false positive failures
    if ctx.location in (ParameterLocation.PATH, ParameterLocation.QUERY) and ("integer" in types or "number" in types):
        if "string" in strategies:
            strategies["string"] = strategies["string"].filter(_is_not_numeric_string)
    if ctx.location == ParameterLocation.QUERY:
        strategies.pop("object", None)
    # Form-urlencoded property-level mutations with null/array/object serialize to empty
    if ctx.location == ParameterLocation.BODY and ctx.media_type == ("application", "x-www-form-urlencoded"):
        strategies.pop("null", None)
        strategies.pop("array", None)
        strategies.pop("object", None)
    # XML body: null and empty string both serialize to an empty element (<RootTag></RootTag>),
    # indistinguishable from an empty object {} at the wire level
    if (
        "object" in types
        and ctx.location == ParameterLocation.BODY
        and ctx.media_type is not None
        and is_xml_parts(ctx.media_type)
    ):
        strategies.pop("null", None)
        strategies.pop("string", None)
    if filter_func is not None:
        for ty, strategy in strategies.items():
            strategies[ty] = strategy.filter(filter_func)

    pattern = schema.get("pattern")
    if pattern is not None:
        try:
            re.compile(pattern)
        except re.error:
            schema = schema.copy()
            del schema["pattern"]
            return

    if isinstance(schema, dict) and BUNDLE_STORAGE_KEY in ctx.root_schema:
        schema = dict(schema)
        schema[BUNDLE_STORAGE_KEY] = ctx.root_schema[BUNDLE_STORAGE_KEY]

    schema = _remove_examples(schema)

    try:
        validator = ctx.validator_cls(schema, validate_formats=True, pattern_options=FANCY_REGEX_OPTIONS)
        is_valid = validator.is_valid
        is_valid(None)
        apply_validation = True
    except Exception:
        # Schema is not correct and we can't validate the generated instances.
        # In such a scenario it is better to generate at least something with some chances to have a false
        # positive failure
        apply_validation = False

        def is_valid(x: object) -> bool:
            return True

    def _does_not_match_the_original_schema(value: Any) -> bool:
        # For XML, None serializes to "" (empty element content), not to "None"
        if ctx.media_type is not None and is_xml_parts(ctx.media_type) and value is None:
            return not is_valid("")
        return not is_valid(str(value))

    if ctx.location == ParameterLocation.PATH:
        for ty, strategy in strategies.items():
            strategies[ty] = strategy.map(jsonify).map(quote_path_parameter)
    elif ctx.location == ParameterLocation.QUERY:
        for ty, strategy in strategies.items():
            strategies[ty] = strategy.map(jsonify)

    if apply_validation and ctx.will_be_serialized_to_string():
        for ty, strategy in strategies.items():
            strategies[ty] = strategy.filter(_does_not_match_the_original_schema)
    for strategy in strategies.values():
        value = ctx.generate_from(strategy)
        if seen.insert(value) and ctx.is_valid_for_location(value):
            yield NegativeValue(
                value, scenario=CoverageScenario.INCORRECT_TYPE, description="Incorrect type", location=ctx.current_path
            )


def _flip_generation_mode_for_not(
    values: Generator[GeneratedValue, None, None],
) -> Generator[GeneratedValue, None, None]:
    """Flip generation mode for values from 'not' schemas.

    For 'not' schemas, the semantic is inverted:
    - Positive values for the inner schema are negative for the outer schema
    - Negative values for the inner schema are positive for the outer schema
    """
    for value in values:
        flipped_mode = (
            GenerationMode.NEGATIVE if value.generation_mode == GenerationMode.POSITIVE else GenerationMode.POSITIVE
        )
        yield GeneratedValue(
            value=value.value,
            generation_mode=flipped_mode,
            scenario=value.scenario,
            description=value.description,
            location=value.location,
            parameter=value.parameter,
        )


def push_examples_to_properties(schema: JsonSchemaObject) -> None:
    """Push examples from the top-level 'examples' field to the corresponding properties."""
    if "examples" in schema and "properties" in schema:
        properties = schema["properties"]
        for example in schema["examples"]:
            if isinstance(example, dict):
                for prop, value in example.items():
                    if prop in properties and isinstance(properties[prop], dict):
                        if "examples" not in properties[prop]:
                            properties[prop]["examples"] = []
                        if value not in properties[prop]["examples"]:
                            properties[prop]["examples"].append(value)
