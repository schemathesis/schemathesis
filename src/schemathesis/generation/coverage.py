from __future__ import annotations

import functools
import re
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from functools import lru_cache, partial
from itertools import combinations
from json.encoder import _make_iterencode, c_make_encoder, encode_basestring_ascii  # type: ignore
from typing import Any, Callable, Generator, Iterator, TypeVar, cast
from urllib.parse import quote_plus

import jsonschema.protocols
from hypothesis import strategies as st
from hypothesis.errors import InvalidArgument, Unsatisfiable
from hypothesis_jsonschema import from_schema
from hypothesis_jsonschema._canonicalise import canonicalish
from hypothesis_jsonschema._from_schema import STRING_FORMATS as BUILT_IN_STRING_FORMATS

from schemathesis.core import INTERNAL_BUFFER_SIZE, NOT_SET
from schemathesis.core.compat import RefResolutionError
from schemathesis.core.transforms import deepclone
from schemathesis.core.validation import contains_unicode_surrogate_pair, has_invalid_characters, is_latin_1_encodable
from schemathesis.generation import GenerationMode
from schemathesis.generation.hypothesis import examples
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
FORMAT_STRATEGIES = {**BUILT_IN_STRING_FORMATS, **get_default_format_strategies(), **STRING_FORMATS}

UNKNOWN_PROPERTY_KEY = "x-schemathesis-unknown-property"
UNKNOWN_PROPERTY_VALUE = 42


@dataclass
class GeneratedValue:
    value: Any
    generation_mode: GenerationMode
    description: str
    parameter: str | None
    location: str | None

    __slots__ = ("value", "generation_mode", "description", "parameter", "location")

    @classmethod
    def with_positive(cls, value: Any, *, description: str) -> GeneratedValue:
        return cls(
            value=value,
            generation_mode=GenerationMode.POSITIVE,
            description=description,
            location=None,
            parameter=None,
        )

    @classmethod
    def with_negative(
        cls, value: Any, *, description: str, location: str, parameter: str | None = None
    ) -> GeneratedValue:
        return cls(
            value=value,
            generation_mode=GenerationMode.NEGATIVE,
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
    generation_modes: list[GenerationMode]
    location: str
    is_required: bool
    path: list[str | int]
    custom_formats: dict[str, st.SearchStrategy]
    validator_cls: type[jsonschema.protocols.Validator]

    __slots__ = ("location", "generation_modes", "is_required", "path", "custom_formats", "validator_cls")

    def __init__(
        self,
        *,
        location: str,
        generation_modes: list[GenerationMode] | None = None,
        is_required: bool,
        path: list[str | int] | None = None,
        custom_formats: dict[str, st.SearchStrategy],
        validator_cls: type[jsonschema.protocols.Validator],
    ) -> None:
        self.location = location
        self.generation_modes = generation_modes if generation_modes is not None else list(GenerationMode)
        self.is_required = is_required
        self.path = path or []
        self.custom_formats = custom_formats
        self.validator_cls = validator_cls

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
            location=self.location,
            generation_modes=[GenerationMode.POSITIVE],
            is_required=self.is_required,
            path=self.path,
            custom_formats=self.custom_formats,
            validator_cls=self.validator_cls,
        )

    def with_negative(self) -> CoverageContext:
        return CoverageContext(
            location=self.location,
            generation_modes=[GenerationMode.NEGATIVE],
            is_required=self.is_required,
            path=self.path,
            custom_formats=self.custom_formats,
            validator_cls=self.validator_cls,
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
        return self.location in ("query", "path", "header", "cookie")

    def can_be_negated(self, schema: dict[str, Any]) -> bool:
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

    def generate_from_schema(self, schema: dict | bool) -> Any:
        if isinstance(schema, bool):
            return 0
        keys = sorted([k for k in schema if not k.startswith("x-") and k not in ["description", "example", "examples"]])
        if keys == ["type"] and isinstance(schema["type"], str) and schema["type"] in STRATEGIES_FOR_TYPE:
            return cached_draw(STRATEGIES_FOR_TYPE[schema["type"]])
        if keys == ["format", "type"]:
            if schema["type"] != "string":
                return cached_draw(STRATEGIES_FOR_TYPE[schema["type"]])
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
            schema = canonicalish(schema)
            if isinstance(schema, dict) and "allOf" not in schema:
                return self.generate_from_schema(schema)

        return self.generate_from(from_schema(schema, custom_formats=self.custom_formats))


T = TypeVar("T")


if c_make_encoder is not None:
    _iterencode = c_make_encoder(None, None, encode_basestring_ascii, None, ":", ",", True, False, False)
else:
    _iterencode = _make_iterencode(
        None, None, encode_basestring_ascii, None, float.__repr__, ":", ",", True, False, True
    )


def _encode(o: Any) -> str:
    return "".join(_iterencode(o, 0))


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
    ctx: CoverageContext, schema: dict, ty: str | None
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
                    canonical = canonicalish(schema)
                    yield from cover_schema_iter(ctx, canonical)
        if enum is not NOT_SET:
            for value in enum:
                yield PositiveValue(value, description="Enum value")
        elif const is not NOT_SET:
            yield PositiveValue(const, description="Const value")
        elif ty is not None:
            if ty == "null":
                yield PositiveValue(None, description="Value null value")
            elif ty == "boolean":
                yield PositiveValue(True, description="Valid boolean value")
                yield PositiveValue(False, description="Valid boolean value")
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
    ctx: CoverageContext, schema: dict | bool, seen: HashSet | None = None
) -> Generator[GeneratedValue, None, None]:
    if seen is None:
        seen = HashSet()
    if isinstance(schema, bool):
        types = ["null", "boolean", "string", "number", "array", "object"]
        schema = {}
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
                elif key == "pattern":
                    min_length = schema.get("minLength")
                    max_length = schema.get("maxLength")
                    yield from _negative_pattern(ctx, value, min_length=min_length, max_length=max_length)
                elif key == "format" and ("string" in types or not types):
                    yield from _negative_format(ctx, schema, value)
                elif key == "maximum":
                    next = value + 1
                    if seen.insert(next):
                        yield NegativeValue(next, description="Value greater than maximum", location=ctx.current_path)
                elif key == "minimum":
                    next = value - 1
                    if seen.insert(next):
                        yield NegativeValue(next, description="Value smaller than minimum", location=ctx.current_path)
                elif key == "exclusiveMaximum" or key == "exclusiveMinimum" and seen.insert(value):
                    verb = "greater" if key == "exclusiveMaximum" else "smaller"
                    limit = "maximum" if key == "exclusiveMaximum" else "minimum"
                    yield NegativeValue(value, description=f"Value {verb} than {limit}", location=ctx.current_path)
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
                        if seen.insert(value):
                            yield NegativeValue(
                                value, description="String smaller than minLength", location=ctx.current_path
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
                            if seen.insert(value):
                                yield NegativeValue(
                                    value, description="String smaller than minLength", location=ctx.current_path
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
                                value, description="String larger than maxLength", location=ctx.current_path
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
                            array_value = [value for pair in zip(positive, negative) for value in pair][
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
                                description="Array with fewer items than allowed by minItems",
                                location=ctx.current_path,
                            )
                    except (InvalidArgument, Unsatisfiable):
                        pass
                elif (
                    key == "additionalProperties"
                    and not value
                    and "pattern" not in schema
                    and schema.get("type") in ["object", None]
                ):
                    template = template or ctx.generate_from_schema(_get_template_schema(schema, "object"))
                    yield NegativeValue(
                        {**template, UNKNOWN_PROPERTY_KEY: UNKNOWN_PROPERTY_VALUE},
                        description="Object with unexpected properties",
                        location=ctx.current_path,
                    )
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
                    validators = [jsonschema.validators.validator_for(sub_schema)(sub_schema) for sub_schema in value]
                    for idx, sub_schema in enumerate(value):
                        with nctx.at(idx):
                            for value in cover_schema_iter(nctx, sub_schema, seen):
                                # Negative value for this schema could be a positive value for another one
                                if is_valid_for_others(value.value, idx, validators):
                                    continue
                                yield value
                elif key == "oneOf":
                    nctx = ctx.with_negative()
                    validators = [jsonschema.validators.validator_for(sub_schema)(sub_schema) for sub_schema in value]
                    for idx, sub_schema in enumerate(value):
                        with nctx.at(idx):
                            for value in cover_schema_iter(nctx, sub_schema, seen):
                                if is_invalid_for_oneOf(value.value, idx, validators):
                                    yield value


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


def _get_properties(schema: dict | bool) -> dict | bool:
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


def _get_template_schema(schema: dict, ty: str) -> dict:
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


def _ensure_valid_path_parameter_schema(schema: dict[str, Any]) -> dict[str, Any]:
    # Path parameters should have at least 1 character length and don't contain any characters with special treatment
    # on the transport level.
    # The implementation below sneaks into `not` to avoid clashing with existing `pattern` keyword
    not_ = schema.get("not", {}).copy()
    not_["pattern"] = r"[/{}]"
    return {**schema, "minLength": 1, "not": not_}


def _ensure_valid_headers_schema(schema: dict[str, Any]) -> dict[str, Any]:
    # Reject any character that is not A-Z, a-z, or 0-9 for simplicity
    not_ = schema.get("not", {}).copy()
    not_["pattern"] = r"[^A-Za-z0-9]"
    return {**schema, "not": not_}


def _positive_string(ctx: CoverageContext, schema: dict) -> Generator[GeneratedValue, None, None]:
    """Generate positive string values."""
    # Boundary and near boundary values
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
            yield PositiveValue(example, description="Example value")
        if examples:
            for example in examples:
                if ctx.is_valid_for_location(example) and seen_values.insert(example):
                    has_valid_example = True
                    yield PositiveValue(example, description="Example value")
        if (
            default
            and not (example is not None and default == example)
            and not (examples is not None and any(default == ex for ex in examples))
            and ctx.is_valid_for_location(default)
            and seen_values.insert(default)
        ):
            has_valid_example = True
            yield PositiveValue(default, description="Default value")
        if not has_valid_example:
            if not min_length and not max_length or "pattern" in schema:
                value = ctx.generate_from_schema(schema)
                seen_values.insert(value)
                seen_constraints.add((min_length, max_length))
                yield PositiveValue(value, description="Valid string")
    elif not min_length and not max_length or "pattern" in schema:
        value = ctx.generate_from_schema(schema)
        seen_values.insert(value)
        seen_constraints.add((min_length, max_length))
        yield PositiveValue(value, description="Valid string")

    if min_length is not None and min_length < INTERNAL_BUFFER_SIZE:
        # Exactly the minimum length
        key = (min_length, min_length)
        if key not in seen_constraints:
            seen_constraints.add(key)
            value = ctx.generate_from_schema({**schema, "maxLength": min_length})
            if seen_values.insert(value):
                yield PositiveValue(value, description="Minimum length string")

        # One character more than minimum if possible
        larger = min_length + 1
        key = (larger, larger)
        if larger < INTERNAL_BUFFER_SIZE and key not in seen_constraints and (not max_length or larger <= max_length):
            seen_constraints.add(key)
            value = ctx.generate_from_schema({**schema, "minLength": larger, "maxLength": larger})
            if seen_values.insert(value):
                yield PositiveValue(value, description="Near-boundary length string")

    if max_length is not None:
        # Exactly the maximum length
        key = (max_length, max_length)
        if max_length < INTERNAL_BUFFER_SIZE and key not in seen_constraints:
            seen_constraints.add(key)
            value = ctx.generate_from_schema({**schema, "minLength": max_length, "maxLength": max_length})
            if seen_values.insert(value):
                yield PositiveValue(value, description="Maximum length string")

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
                yield PositiveValue(value, description="Near-boundary length string")


def closest_multiple_greater_than(y: int, x: int) -> int:
    """Find the closest multiple of X that is greater than Y."""
    quotient, remainder = divmod(y, x)
    if remainder == 0:
        return y
    return x * (quotient + 1)


def _positive_number(ctx: CoverageContext, schema: dict) -> Generator[GeneratedValue, None, None]:
    """Generate positive integer values."""
    # Boundary and near boundary values
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
            yield PositiveValue(example, description="Example value")
        if examples:
            for example in examples:
                if seen.insert(example):
                    yield PositiveValue(example, description="Example value")
        if (
            default
            and not (example is not None and default == example)
            and not (examples is not None and any(default == ex for ex in examples))
            and seen.insert(default)
        ):
            yield PositiveValue(default, description="Default value")
    elif not minimum and not maximum:
        value = ctx.generate_from_schema(schema)
        seen.insert(value)
        yield PositiveValue(value, description="Valid number")

    if minimum is not None:
        # Exactly the minimum
        if multiple_of is not None:
            smallest = closest_multiple_greater_than(minimum, multiple_of)
        else:
            smallest = minimum
        if seen.insert(smallest):
            yield PositiveValue(smallest, description="Minimum value")

        # One more than minimum if possible
        if multiple_of is not None:
            larger = smallest + multiple_of
        else:
            larger = minimum + 1
        if (not maximum or larger <= maximum) and seen.insert(larger):
            yield PositiveValue(larger, description="Near-boundary number")

    if maximum is not None:
        # Exactly the maximum
        if multiple_of is not None:
            largest = maximum - (maximum % multiple_of)
        else:
            largest = maximum
        if seen.insert(largest):
            yield PositiveValue(largest, description="Maximum value")

        # One less than maximum if possible
        if multiple_of is not None:
            smaller = largest - multiple_of
        else:
            smaller = maximum - 1
        if (smaller > 0 and (minimum is None or smaller >= minimum)) and seen.insert(smaller):
            yield PositiveValue(smaller, description="Near-boundary number")


def _positive_array(ctx: CoverageContext, schema: dict, template: list) -> Generator[GeneratedValue, None, None]:
    example = schema.get("example")
    examples = schema.get("examples")
    default = schema.get("default")

    seen = HashSet()
    seen_constraints: set[tuple] = set()

    if example or examples or default:
        if example and seen.insert(example):
            yield PositiveValue(example, description="Example value")
        if examples:
            for example in examples:
                if seen.insert(example):
                    yield PositiveValue(example, description="Example value")
        if (
            default
            and not (example is not None and default == example)
            and not (examples is not None and any(default == ex for ex in examples))
            and seen.insert(default)
        ):
            yield PositiveValue(default, description="Default value")
    elif seen.insert(template):
        yield PositiveValue(template, description="Valid array")

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
                yield PositiveValue(value, description="Near-boundary items array")

    if max_items is not None:
        if max_items < INTERNAL_BUFFER_SIZE and max_items not in seen_constraints:
            seen_constraints.add(max_items)
            value = ctx.generate_from_schema({**schema, "minItems": max_items})
            if seen.insert(value):
                yield PositiveValue(value, description="Maximum items array")

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
                yield PositiveValue(value, description="Near-boundary items array")

    if "items" in schema and "enum" in schema["items"] and isinstance(schema["items"]["enum"], list) and max_items != 0:
        # Ensure there is enough items to pass `minItems` if it is specified
        length = min_items or 1
        for variant in schema["items"]["enum"]:
            value = [variant] * length
            if seen.insert(value):
                yield PositiveValue(value, description="Enum value from available for items array")
    elif min_items is None and max_items is None and "items" in schema and isinstance(schema["items"], dict):
        # Otherwise only an empty array is generated
        sub_schema = schema["items"]
        for item in cover_schema_iter(ctx, sub_schema):
            yield PositiveValue([item.value], description=f"Single-item array: {item.description}")


def _positive_object(ctx: CoverageContext, schema: dict, template: dict) -> Generator[GeneratedValue, None, None]:
    example = schema.get("example")
    examples = schema.get("examples")
    default = schema.get("default")

    if example or examples or default:
        if example:
            yield PositiveValue(example, description="Example value")
        if examples:
            for example in examples:
                yield PositiveValue(example, description="Example value")
        if (
            default
            and not (example is not None and default == example)
            and not (examples is not None and any(default == ex for ex in examples))
        ):
            yield PositiveValue(default, description="Default value")
    else:
        yield PositiveValue(template, description="Valid object")

    properties = schema.get("properties", {})
    required = set(schema.get("required", []))
    optional = list(set(properties) - required)
    optional.sort()

    # Generate combinations with required properties and one optional property
    for name in optional:
        combo = {k: v for k, v in template.items() if k in required or k == name}
        if combo != template:
            yield PositiveValue(combo, description=f"Object with all required properties and '{name}'")
    # Generate one combination for each size from 2 to N-1
    for selection in select_combinations(optional):
        combo = {k: v for k, v in template.items() if k in required or k in selection}
        yield PositiveValue(combo, description="Object with all required and a subset of optional properties")
    # Generate only required properties
    if set(properties) != required:
        only_required = {k: v for k, v in template.items() if k in required}
        yield PositiveValue(only_required, description="Object with only required properties")
    seen = HashSet()
    for name, sub_schema in properties.items():
        seen.insert(template.get(name))
        for new in cover_schema_iter(ctx, sub_schema):
            if seen.insert(new.value):
                yield PositiveValue(
                    {**template, name: new.value}, description=f"Object with valid '{name}' value: {new.description}"
                )
        seen.clear()


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
                    description=f"Object with invalid pattern key '{key}' ('{pattern}') value: {value.description}",
                    location=nctx.current_path,
                )


def _negative_items(ctx: CoverageContext, schema: dict[str, Any] | bool) -> Generator[GeneratedValue, None, None]:
    """Arrays not matching the schema."""
    nctx = ctx.with_negative()
    for value in cover_schema_iter(nctx, schema):
        items = [value.value]
        if ctx.leads_to_negative_test_case(items):
            yield NegativeValue(
                items,
                description=f"Array with invalid items: {value.description}",
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
        description=f"Value not matching the '{pattern}' pattern",
        location=ctx.current_path,
    )


def _with_negated_key(schema: dict, key: str, value: Any) -> dict:
    return {"allOf": [{k: v for k, v in schema.items() if k != key}, {"not": {key: value}}]}


def _negative_multiple_of(
    ctx: CoverageContext, schema: dict, multiple_of: int | float
) -> Generator[GeneratedValue, None, None]:
    yield NegativeValue(
        ctx.generate_from_schema(_with_negated_key(schema, "multipleOf", multiple_of)),
        description=f"Non-multiple of {multiple_of}",
        location=ctx.current_path,
    )


def _negative_unique_items(ctx: CoverageContext, schema: dict) -> Generator[GeneratedValue, None, None]:
    unique = jsonify(ctx.generate_from_schema({**schema, "type": "array", "minItems": 1, "maxItems": 1}))
    yield NegativeValue(unique + unique, description="Non-unique items", location=ctx.current_path)


def _negative_required(
    ctx: CoverageContext, template: dict, required: list[str]
) -> Generator[GeneratedValue, None, None]:
    for key in required:
        yield NegativeValue(
            {k: v for k, v in template.items() if k != key},
            description=f"Missing required property: {key}",
            location=ctx.current_path,
            parameter=key,
        )


def _is_invalid_hostname(v: Any) -> bool:
    return v == "" or not jsonschema.Draft202012Validator.FORMAT_CHECKER.conforms(v, "hostname")


def _is_invalid_format(v: Any, format: str) -> bool:
    return not jsonschema.Draft202012Validator.FORMAT_CHECKER.conforms(v, format)


def _negative_format(ctx: CoverageContext, schema: dict, format: str) -> Generator[GeneratedValue, None, None]:
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
    strategies = {ty: strategy for ty, strategy in STRATEGIES_FOR_TYPE.items() if ty not in types}

    filter_func = {
        "path": lambda x: not is_invalid_path_parameter(x),
        "header": is_valid_header_value,
        "cookie": is_valid_header_value,
        "query": lambda x: not contains_unicode_surrogate_pair(x),
    }.get(ctx.location)

    if "number" in types:
        del strategies["integer"]
    if "integer" in types:
        strategies["number"] = FLOAT_STRATEGY.filter(_is_non_integer_float)
    if ctx.location == "query":
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

    if ctx.location == "path":
        for ty, strategy in strategies.items():
            strategies[ty] = strategy.map(jsonify).map(quote_path_parameter)
    elif ctx.location == "query":
        for ty, strategy in strategies.items():
            strategies[ty] = strategy.map(jsonify)

    if apply_validation and ctx.will_be_serialized_to_string():
        for ty, strategy in strategies.items():
            strategies[ty] = strategy.filter(_does_not_match_the_original_schema)
    for strategy in strategies.values():
        value = ctx.generate_from(strategy)
        if seen.insert(value) and ctx.is_valid_for_location(value):
            yield NegativeValue(value, description="Incorrect type", location=ctx.current_path)


def push_examples_to_properties(schema: dict[str, Any]) -> None:
    """Push examples from the top-level 'examples' field to the corresponding properties."""
    if "examples" in schema and "properties" in schema:
        properties = schema["properties"]
        for example in schema["examples"]:
            if isinstance(example, dict):
                for prop, value in example.items():
                    if prop in properties:
                        if "examples" not in properties[prop]:
                            properties[prop]["examples"] = []
                        if value not in schema["properties"][prop]["examples"]:
                            properties[prop]["examples"].append(value)
