from __future__ import annotations

import functools
import re
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from functools import lru_cache, partial
from itertools import combinations

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
from typing import Any, TypeVar, cast
from urllib.parse import quote_plus

import jsonschema.protocols
from hypothesis import strategies as st
from hypothesis.errors import InvalidArgument, Unsatisfiable
from hypothesis_jsonschema import from_schema
from hypothesis_jsonschema._canonicalise import canonicalish
from hypothesis_jsonschema._from_schema import STRING_FORMATS as BUILT_IN_STRING_FORMATS

from schemathesis.core import INTERNAL_BUFFER_SIZE, NOT_SET
from schemathesis.core.compat import RefResolutionError, RefResolver
from schemathesis.core.jsonschema.types import JsonSchema, JsonSchemaObject
from schemathesis.core.parameters import ParameterLocation
from schemathesis.core.transforms import deepclone
from schemathesis.core.validation import contains_unicode_surrogate_pair, has_invalid_characters, is_latin_1_encodable
from schemathesis.generation import GenerationMode
from schemathesis.generation.hypothesis import examples
from schemathesis.generation.meta import CoverageScenario
from schemathesis.openapi.generation.filters import is_invalid_path_parameter

from ..specs.openapi.converter import update_pattern_in_schema
from ..specs.openapi.formats import STRING_FORMATS, get_default_format_strategies
from ..specs.openapi.patterns import update_quantifier


def _replace_zero_with_nonzero(x: float) -> float:
    return x or 0.0


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


FORMAT_STRATEGIES = {**BUILT_IN_STRING_FORMATS, **get_default_format_strategies(), **STRING_FORMATS}

UNKNOWN_PROPERTY_KEY = "x-schemathesis-unknown-property"
UNKNOWN_PROPERTY_VALUE = 42
ADDITIONAL_PROPERTY_KEY_BASE = "x-schemathesis-additional"


def _generate_additional_property_key(existing_keys: set[str]) -> str:
    """Generate a key for additional properties that doesn't conflict with existing keys."""
    key = ADDITIONAL_PROPERTY_KEY_BASE
    counter = 0
    while key in existing_keys:
        counter += 1
        key = f"{ADDITIONAL_PROPERTY_KEY_BASE}{counter}"
    return key


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
    validator_cls: type[jsonschema.protocols.Validator]
    _resolver: RefResolver | None
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
        "_resolver",
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
        validator_cls: type[jsonschema.protocols.Validator],
        _resolver: RefResolver | None = None,
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
        self._resolver = _resolver
        self.allow_extra_parameters = allow_extra_parameters

    @property
    def resolver(self) -> RefResolver:
        """Lazy-initialized cached resolver."""
        if self._resolver is None:
            self._resolver = RefResolver.from_schema(self.root_schema)
        return cast(RefResolver, self._resolver)

    def resolve_ref(self, ref: str) -> dict | bool:
        """Resolve a $ref to its schema definition."""
        _, resolved = self.resolver.resolve(ref)
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
            _resolver=self._resolver,
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
            _resolver=self._resolver,
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
        return self.location in ("query", "path", "header", "cookie") or (
            self.location == "body"
            and self.media_type
            in frozenset(
                [
                    ("multipart", "form-data"),
                    ("application", "x-www-form-urlencoded"),
                ]
            )
        )

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
            return 0
        keys = sorted([k for k in schema if not k.startswith("x-") and k not in ["description", "example", "examples"]])
        if keys == ["type"]:
            return cached_draw(get_strategy_for_type(schema["type"]))
        if keys == ["format", "type"]:
            if schema["type"] != "string":
                return cached_draw(get_strategy_for_type(schema["type"]))
            elif schema["format"] in FORMAT_STRATEGIES:
                return cached_draw(FORMAT_STRATEGIES[schema["format"]])
        if (keys == ["maxLength", "minLength", "type"] or keys == ["maxLength", "type"]) and schema["type"] == "string":
            return cached_draw(st.text(min_size=schema.get("minLength", 0), max_size=schema["maxLength"]))
        if (
            keys == ["properties", "required", "type"]
            or keys == ["properties", "required"]
            or keys == ["properties", "type"]
            or keys == ["properties"]
        ):
            obj = {}
            for key, sub_schema in schema["properties"].items():
                if isinstance(sub_schema, dict) and "const" in sub_schema:
                    obj[key] = sub_schema["const"]
                else:
                    obj[key] = self.generate_from_schema(sub_schema)
            return obj
        if (
            keys == ["maximum", "minimum", "type"] or keys == ["maximum", "type"] or keys == ["minimum", "type"]
        ) and schema["type"] == "integer":
            return cached_draw(st.integers(min_value=schema.get("minimum"), max_value=schema.get("maximum")))
        if "enum" in schema:
            return cached_draw(st.sampled_from(schema["enum"]))
        if keys == ["multipleOf", "type"] and schema["type"] in ("integer", "number"):
            step = schema["multipleOf"]
            return cached_draw(st.integers().map(step.__mul__))
        if "pattern" in schema:
            pattern = schema["pattern"]
            try:
                re.compile(pattern)
            except re.error:
                raise Unsatisfiable from None
            if "minLength" in schema or "maxLength" in schema:
                min_length = schema.get("minLength")
                max_length = schema.get("maxLength")
                pattern = update_quantifier(pattern, min_length, max_length)
            return cached_draw(st.from_regex(pattern))
        if (keys == ["items", "type"] or keys == ["items", "minItems", "type"]) and isinstance(schema["items"], dict):
            items = schema["items"]
            min_items = schema.get("minItems", 0)
            if "enum" in items:
                return cached_draw(st.lists(st.sampled_from(items["enum"]), min_size=min_items))
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

        # Deep clone to prevent hypothesis_jsonschema from mutating the original schema
        return self.generate_from(from_schema(deepclone(schema), custom_formats=self.custom_formats))


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


def _to_hashable_key(value: T, _encode: Callable = _encode) -> tuple[type, str | T]:
    if isinstance(value, (dict, list)):
        serialized = _encode(value)
        return (type(value), serialized)
    return (type(value), value)


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


def _cover_positive_for_type(
    ctx: CoverageContext, schema: JsonSchemaObject, ty: str | None, seen: HashSet | None = None
) -> Generator[GeneratedValue, None, None]:
    if ty == "object" or ty == "array":
        template_schema = _get_template_schema(schema, ty)
        template = ctx.generate_from_schema(template_schema)
    elif "properties" in schema or "required" in schema:
        template_schema = _get_template_schema(schema, "object")
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
                for sub_schema in sub_schemas:
                    yield from cover_schema_iter(ctx, sub_schema)
        all_of = schema.get("allOf")
        if all_of is not None:
            if len(all_of) == 1:
                yield from cover_schema_iter(ctx, all_of[0])
            else:
                with suppress(jsonschema.SchemaError):
                    for idx, sub_schema in enumerate(all_of):
                        if isinstance(sub_schema, dict) and "$ref" in sub_schema:
                            all_of[idx] = ctx.resolve_ref(sub_schema["$ref"])
                    canonical = canonicalish(schema)
                    yield from cover_schema_iter(ctx, canonical)
        if enum is not NOT_SET:
            for value in enum:
                yield PositiveValue(value, scenario=CoverageScenario.ENUM_VALUE, description="Enum value")
        elif const is not NOT_SET:
            yield PositiveValue(const, scenario=CoverageScenario.CONST_VALUE, description="Const value")
        elif ty is not None:
            if ty == "null":
                yield PositiveValue(None, scenario=CoverageScenario.NULL_VALUE, description="Value null value")
            elif ty == "boolean":
                yield PositiveValue(True, scenario=CoverageScenario.VALID_BOOLEAN, description="Valid boolean value")
                yield PositiveValue(False, scenario=CoverageScenario.VALID_BOOLEAN, description="Valid boolean value")
            elif ty == "string":
                yield from _positive_string(ctx, schema)
            elif ty == "integer" or ty == "number":
                yield from _positive_number(ctx, schema)
            elif ty == "array":
                yield from _positive_array(ctx, schema, cast(list, template))
            elif ty == "object":
                yield from _positive_object(ctx, schema, cast(dict, template))
        elif "properties" in schema or "required" in schema:
            yield from _positive_object(ctx, schema, cast(dict, template))
        elif "not" in schema and isinstance(schema["not"], (dict, bool)):
            # For 'not' schemas: generate negative cases of inner schema (violations)
            # These violations are positive for the outer schema, so flip the mode
            nctx = ctx.with_negative()
            yield from _flip_generation_mode_for_not(cover_schema_iter(nctx, schema["not"], seen))


@contextmanager
def _ignore_unfixable(
    *,
    # Cache exception types here as `jsonschema` uses a custom `__getattr__` on the module level
    # and it may cause errors during the interpreter shutdown
    ref_error: type[Exception] = RefResolutionError,
    schema_error: type[Exception] = jsonschema.SchemaError,
) -> Generator:
    try:
        yield
    except (Unsatisfiable, ref_error, schema_error):
        pass
    except InvalidArgument as exc:
        message = str(exc)
        if "Cannot create non-empty" not in message and "is not in the specified alphabet" not in message:
            raise
    except TypeError as exc:
        if "first argument must be string or compiled pattern" not in str(exc):
            raise


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
                schema = {**resolved, **{k: v for k, v in schema.items() if k != "$ref"}}
                yield from cover_schema_iter(ctx, schema, seen)
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
        for key, value in schema.items():
            with _ignore_unfixable(), ctx.at(key):
                if key == "enum":
                    yield from _negative_enum(ctx, value, seen)
                elif key == "const":
                    for value_ in _negative_enum(ctx, [value], seen):
                        yield value_
                elif key == "type":
                    yield from _negative_type(ctx, value, seen, schema)
                elif key == "properties":
                    template = template or ctx.generate_from_schema(_get_template_schema(schema, "object"))
                    yield from _negative_properties(ctx, template, value)
                elif key == "patternProperties":
                    template = template or ctx.generate_from_schema(_get_template_schema(schema, "object"))
                    yield from _negative_pattern_properties(ctx, template, value)
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
                            new_schema.setdefault("type", "string")
                            if "pattern" in new_schema:
                                new_schema["pattern"] = update_quantifier(schema["pattern"], min_length, max_length)
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
                    try:
                        min_length = max_length = value + 1
                        new_schema = {**schema, "minLength": min_length, "maxLength": max_length}
                        new_schema.setdefault("type", "string")
                        if "pattern" in new_schema:
                            if value > NEGATIVE_MODE_MAX_LENGTH_WITH_PATTERN:
                                # Large `maxLength` value can be extremely slow to generate when combined with `pattern`
                                del new_schema["pattern"]
                                value = ctx.generate_from_schema(new_schema)
                            else:
                                new_schema["pattern"] = update_quantifier(schema["pattern"], min_length, max_length)
                                if new_schema["pattern"] == schema["pattern"]:
                                    # Pattern wasn't updated, try to generate a valid value then extend the string to the required length
                                    del new_schema["minLength"]
                                    del new_schema["maxLength"]
                                    value = ctx.generate_from_schema(new_schema).ljust(max_length, "0")
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
                    template = template or ctx.generate_from_schema(_get_template_schema(schema, "object"))
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
                        if "items" in schema and isinstance(schema["items"], dict):
                            # The schema may have another large array nested, therefore generate covering cases
                            # and use them to build an array for the current schema
                            negative = [case.value for case in cover_schema_iter(ctx, schema["items"])]
                            positive = [case.value for case in cover_schema_iter(ctx.with_positive(), schema["items"])]
                            # Interleave positive & negative values
                            array_value = [value for pair in zip(positive, negative, strict=False) for value in pair][
                                :NEGATIVE_MODE_MAX_ITEMS
                            ]
                        else:
                            array_value = ctx.generate_from_schema(new_schema)

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
                        # Force the array to have one less item than the minimum
                        new_schema = {**schema, "minItems": value - 1, "maxItems": value - 1, "type": "array"}
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
                    if not value and "pattern" not in schema:
                        # additionalProperties: false - add unexpected property
                        if not ctx.allow_extra_parameters and ctx.location in (
                            ParameterLocation.QUERY,
                            ParameterLocation.HEADER,
                            ParameterLocation.COOKIE,
                        ):
                            continue
                        template = template or ctx.generate_from_schema(_get_template_schema(schema, "object"))
                        yield NegativeValue(
                            {**template, UNKNOWN_PROPERTY_KEY: UNKNOWN_PROPERTY_VALUE},
                            scenario=CoverageScenario.OBJECT_UNEXPECTED_PROPERTIES,
                            description="Object with unexpected properties",
                            location=ctx.current_path,
                        )
                    elif isinstance(value, dict):
                        # additionalProperties with schema - generate invalid values for the schema
                        template = template or ctx.generate_from_schema(_get_template_schema(schema, "object"))
                        existing_keys = set(schema.get("properties", {}).keys()) | set(template.keys())
                        additional_key = _generate_additional_property_key(existing_keys)
                        nctx = ctx.with_negative()
                        with nctx.at(additional_key):
                            for invalid in cover_schema_iter(nctx, value, seen):
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
                    template = template or ctx.generate_from_schema(_get_template_schema(schema, "object"))
                    obj_value = dict(template)
                    existing_keys = set(obj_value.keys())
                    needed = value + 1 - len(existing_keys)
                    if needed > 0:
                        for _ in range(needed):
                            new_key = _generate_additional_property_key(existing_keys)
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
                    resolver = ctx.resolver
                    # Use Draft7 for validation since schemas are converted to Draft7 format (prefixItems → items)
                    validators = [jsonschema.Draft7Validator(sub_schema, resolver=resolver) for sub_schema in value]
                    for idx, sub_schema in enumerate(value):
                        with nctx.at(idx):
                            for value in cover_schema_iter(nctx, sub_schema, seen):
                                # Negative value for this schema could be a positive value for another one
                                if is_valid_for_others(value.value, idx, validators):
                                    continue
                                yield value
                elif key == "oneOf":
                    nctx = ctx.with_negative()
                    resolver = ctx.resolver
                    # Use Draft7 for validation since schemas are converted to Draft7 format (prefixItems → items)
                    validators = [jsonschema.Draft7Validator(sub_schema, resolver=resolver) for sub_schema in value]
                    for idx, sub_schema in enumerate(value):
                        with nctx.at(idx):
                            for value in cover_schema_iter(nctx, sub_schema, seen):
                                if is_invalid_for_oneOf(value.value, idx, validators):
                                    yield value
                elif key == "not" and isinstance(value, (dict, bool)):
                    # For 'not' schemas: generate positive cases of inner schema (valid values)
                    # These valid values are negative for the outer schema, so flip the mode
                    pctx = ctx.with_positive()
                    yield from _flip_generation_mode_for_not(cover_schema_iter(pctx, value, seen))


def is_valid_for_others(value: Any, idx: int, validators: list[jsonschema.Validator]) -> bool:
    for vidx, validator in enumerate(validators):
        if idx == vidx:
            # This one is being negated
            continue
        if validator.is_valid(value):
            return True
    return False


def is_invalid_for_oneOf(value: Any, idx: int, validators: list[jsonschema.Validator]) -> bool:
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


def _get_properties(schema: JsonSchema) -> JsonSchema:
    if isinstance(schema, dict):
        if "example" in schema:
            return {"const": schema["example"]}
        if "default" in schema:
            return {"const": schema["default"]}
        if schema.get("examples"):
            return {"enum": schema["examples"]}
        if schema.get("type") == "object":
            return _get_template_schema(schema, "object")
        _schema = deepclone(schema)
        update_pattern_in_schema(_schema)
        return _schema
    return schema


def _get_template_schema(schema: JsonSchemaObject, ty: str) -> JsonSchemaObject:
    if ty == "object":
        properties = schema.get("properties")
        if properties is not None:
            return {
                **schema,
                "required": list(properties),
                "type": ty,
                "properties": {k: _get_properties(v) for k, v in properties.items()},
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
    return {**schema, "minLength": 1, "not": not_}


def _ensure_valid_headers_schema(schema: JsonSchemaObject) -> JsonSchemaObject:
    # Reject any character that is not A-Z, a-z, or 0-9 for simplicity
    not_ = _get_not_schema(schema)
    not_["pattern"] = r"[^A-Za-z0-9]"
    return {**schema, "not": not_}


def _positive_string(ctx: CoverageContext, schema: JsonSchemaObject) -> Generator[GeneratedValue, None, None]:
    """Generate positive string values."""
    # Boundary and near boundary values
    schema = {"type": "string", **schema}
    min_length = schema.get("minLength")
    if min_length == 0:
        min_length = None
    max_length = schema.get("maxLength")
    if ctx.location == "path":
        schema = _ensure_valid_path_parameter_schema(schema)
    elif ctx.location in ("header", "cookie") and not ("format" in schema and schema["format"] in FORMAT_STRATEGIES):
        # Don't apply it for known formats - they will insure the correct format during generation
        schema = _ensure_valid_headers_schema(schema)

    example = schema.get("example")
    examples = schema.get("examples")
    default = schema.get("default")

    # Two-layer check to avoid potentially expensive data generation using schema constraints as a key
    seen_values = HashSet()
    seen_constraints: set[tuple] = set()

    if example or examples or default:
        has_valid_example = False
        if example and ctx.is_valid_for_location(example) and seen_values.insert(example):
            has_valid_example = True
            yield PositiveValue(example, scenario=CoverageScenario.EXAMPLE_VALUE, description="Example value")
        if examples:
            for example in examples:
                if ctx.is_valid_for_location(example) and seen_values.insert(example):
                    has_valid_example = True
                    yield PositiveValue(example, scenario=CoverageScenario.EXAMPLE_VALUE, description="Example value")
        if (
            default
            and not (example is not None and default == example)
            and not (examples is not None and any(default == ex for ex in examples))
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


def _positive_number(ctx: CoverageContext, schema: JsonSchemaObject) -> Generator[GeneratedValue, None, None]:
    """Generate positive integer values."""
    # Boundary and near boundary values
    schema = {"type": "number", **schema}
    minimum = schema.get("minimum")
    maximum = schema.get("maximum")
    exclusive_minimum = schema.get("exclusiveMinimum")
    exclusive_maximum = schema.get("exclusiveMaximum")
    if exclusive_minimum is not None:
        minimum = exclusive_minimum + 1
    if exclusive_maximum is not None:
        maximum = exclusive_maximum - 1
    multiple_of = schema.get("multipleOf")
    example = schema.get("example")
    examples = schema.get("examples")
    default = schema.get("default")

    seen = HashSet()

    if example or examples or default:
        if example and seen.insert(example):
            yield PositiveValue(example, scenario=CoverageScenario.EXAMPLE_VALUE, description="Example value")
        if examples:
            for example in examples:
                if seen.insert(example):
                    yield PositiveValue(example, scenario=CoverageScenario.EXAMPLE_VALUE, description="Example value")
        if (
            default
            and not (example is not None and default == example)
            and not (examples is not None and any(default == ex for ex in examples))
            and seen.insert(default)
        ):
            yield PositiveValue(default, scenario=CoverageScenario.DEFAULT_VALUE, description="Default value")
    elif not minimum and not maximum:
        value = ctx.generate_from_schema(schema)
        seen.insert(value)
        yield PositiveValue(value, scenario=CoverageScenario.VALID_NUMBER, description="Valid number")

    if minimum is not None:
        # Exactly the minimum
        if multiple_of is not None:
            smallest = closest_multiple_greater_than(minimum, multiple_of)
        else:
            smallest = minimum
        if seen.insert(smallest):
            yield PositiveValue(smallest, scenario=CoverageScenario.MINIMUM_VALUE, description="Minimum value")

        # One more than minimum if possible
        if multiple_of is not None:
            larger = smallest + multiple_of
        else:
            larger = minimum + 1
        if (not maximum or larger <= maximum) and seen.insert(larger):
            yield PositiveValue(
                larger, scenario=CoverageScenario.NEAR_BOUNDARY_NUMBER, description="Near-boundary number"
            )

    if maximum is not None:
        # Exactly the maximum
        if multiple_of is not None:
            largest = maximum - (maximum % multiple_of)
        else:
            largest = maximum
        if seen.insert(largest):
            yield PositiveValue(largest, scenario=CoverageScenario.MAXIMUM_VALUE, description="Maximum value")

        # One less than maximum if possible
        if multiple_of is not None:
            smaller = largest - multiple_of
        else:
            smaller = maximum - 1
        if (minimum is None or smaller >= minimum) and seen.insert(smaller):
            yield PositiveValue(
                smaller, scenario=CoverageScenario.NEAR_BOUNDARY_NUMBER, description="Near-boundary number"
            )


def _positive_array(
    ctx: CoverageContext, schema: JsonSchemaObject, template: list
) -> Generator[GeneratedValue, None, None]:
    example = schema.get("example")
    examples = schema.get("examples")
    default = schema.get("default")

    seen = HashSet()
    seen_constraints: set[tuple] = set()

    if example or examples or default:
        if example and seen.insert(example):
            yield PositiveValue(example, scenario=CoverageScenario.EXAMPLE_VALUE, description="Example value")
        if examples:
            for example in examples:
                if seen.insert(example):
                    yield PositiveValue(example, scenario=CoverageScenario.EXAMPLE_VALUE, description="Example value")
        if (
            default
            and not (example is not None and default == example)
            and not (examples is not None and any(default == ex for ex in examples))
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
            smaller < INTERNAL_BUFFER_SIZE
            and smaller > 0
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
        for variant in schema["items"]["enum"]:
            value = [variant] * length
            if seen.insert(value):
                yield PositiveValue(
                    value,
                    scenario=CoverageScenario.ENUM_VALUE_ITEMS_ARRAY,
                    description="Enum value from available for items array",
                )
    elif min_items is None and max_items is None and "items" in schema and isinstance(schema["items"], dict):
        # Otherwise only an empty array is generated
        sub_schema = schema["items"]
        for item in cover_schema_iter(ctx, sub_schema):
            yield PositiveValue(
                [item.value],
                scenario=CoverageScenario.VALID_ARRAY,
                description=f"Single-item array: {item.description}",
            )


def _positive_object(
    ctx: CoverageContext, schema: JsonSchemaObject, template: dict
) -> Generator[GeneratedValue, None, None]:
    example = schema.get("example")
    examples = schema.get("examples")
    default = schema.get("default")

    if example or examples or default:
        if example:
            yield PositiveValue(example, scenario=CoverageScenario.EXAMPLE_VALUE, description="Example value")
        if examples:
            for example in examples:
                yield PositiveValue(example, scenario=CoverageScenario.EXAMPLE_VALUE, description="Example value")
        if (
            default
            and not (example is not None and default == example)
            and not (examples is not None and any(default == ex for ex in examples))
        ):
            yield PositiveValue(default, scenario=CoverageScenario.DEFAULT_VALUE, description="Default value")
    elif template or not (
        ctx.is_required and ctx.media_type in (("application", "x-www-form-urlencoded"), ("multipart", "form-data"))
    ):
        yield PositiveValue(template, scenario=CoverageScenario.VALID_OBJECT, description="Valid object")

    properties = schema.get("properties", {})
    required = set(schema.get("required", []))
    optional = list(set(properties) - required)
    optional.sort()

    # Generate combinations with required properties and one optional property
    for name in optional:
        combo = {k: v for k, v in template.items() if k in required or k == name}
        if combo != template:
            yield PositiveValue(
                combo,
                scenario=CoverageScenario.OBJECT_REQUIRED_AND_OPTIONAL,
                description=f"Object with all required properties and '{name}'",
            )
    # Generate one combination for each size from 2 to N-1
    for selection in select_combinations(optional):
        combo = {k: v for k, v in template.items() if k in required or k in selection}
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
        if only_required or not (
            ctx.is_required and ctx.media_type in (("application", "x-www-form-urlencoded"), ("multipart", "form-data"))
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
        existing_keys = set(properties.keys()) | set(template.keys())
        additional_key = _generate_additional_property_key(existing_keys)
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
    for key, sub_schema in properties.items():
        with nctx.at(key):
            for value in cover_schema_iter(nctx, sub_schema):
                yield NegativeValue(
                    {**template, key: value.value},
                    scenario=value.scenario,
                    description=f"Object with invalid '{key}' value: {value.description}",
                    location=nctx.current_path,
                    parameter=key,
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
    yield NegativeValue(
        ctx.generate_from(
            st.text(min_size=min_length or 0, max_size=max_length)
            .filter(partial(_not_matching_pattern, pattern=compiled))
            .filter(ctx.is_valid_for_location)
        ),
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


def _is_invalid_hostname(v: Any) -> bool:
    return v == "" or not jsonschema.Draft202012Validator.FORMAT_CHECKER.conforms(v, "hostname")


def _is_invalid_format(v: Any, format: str) -> bool:
    return not jsonschema.Draft202012Validator.FORMAT_CHECKER.conforms(v, format)


def _negative_format(
    ctx: CoverageContext, schema: JsonSchemaObject, format: str
) -> Generator[GeneratedValue, None, None]:
    # Hypothesis-jsonschema does not canonicalise it properly right now, which leads to unsatisfiable schema
    without_format = {k: v for k, v in schema.items() if k != "format"}
    without_format.setdefault("type", "string")
    if ctx.location == "path":
        # Empty path parameters are invalid
        without_format["minLength"] = 1
    strategy = from_schema(without_format)
    if format in jsonschema.Draft202012Validator.FORMAT_CHECKER.checkers:
        if format == "hostname":
            strategy = strategy.filter(_is_invalid_hostname)
        else:
            strategy = strategy.filter(functools.partial(_is_invalid_format, format=format))
    yield NegativeValue(
        ctx.generate_from(strategy),
        scenario=CoverageScenario.INVALID_FORMAT,
        description=f"Value not matching the '{format}' format",
        location=ctx.current_path,
    )


def _is_non_integer_float(x: float) -> bool:
    return x != int(x)


def is_valid_header_value(value: Any) -> bool:
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
        and ctx.media_type[1] not in ("json",)
    ):
        return
    # Form-urlencoded body-level type mutations serialize to empty body
    if (
        "object" in types
        and ctx.location == ParameterLocation.BODY
        and ctx.media_type == ("application", "x-www-form-urlencoded")
    ):
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
    if ctx.location == ParameterLocation.QUERY:
        strategies.pop("object", None)
    # Form-urlencoded property-level mutations with null/array/object serialize to empty
    if ctx.location == ParameterLocation.BODY and ctx.media_type == ("application", "x-www-form-urlencoded"):
        strategies.pop("null", None)
        strategies.pop("array", None)
        strategies.pop("object", None)
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

    validator = ctx.validator_cls(
        schema,
        format_checker=jsonschema.Draft202012Validator.FORMAT_CHECKER,
    )
    is_valid = validator.is_valid
    try:
        is_valid(None)
        apply_validation = True
    except Exception:
        # Schema is not correct and we can't validate the generated instances.
        # In such a scenario it is better to generate at least something with some chances to have a false
        # positive failure
        apply_validation = False

    def _does_not_match_the_original_schema(value: Any) -> bool:
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
