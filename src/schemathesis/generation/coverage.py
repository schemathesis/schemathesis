from __future__ import annotations

import functools
import json
import re
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from functools import lru_cache, partial
from itertools import combinations
from typing import Any, Generator, Iterator, TypeVar, cast

import jsonschema
from hypothesis import strategies as st
from hypothesis.errors import InvalidArgument, Unsatisfiable
from hypothesis_jsonschema import from_schema
from hypothesis_jsonschema._canonicalise import canonicalish

from ..constants import NOT_SET
from ..internal.copy import fast_deepcopy
from ..specs.openapi.converter import update_pattern_in_schema
from ..specs.openapi.patterns import update_quantifier
from ._hypothesis import get_single_example
from ._methods import DataGenerationMethod


def _replace_zero_with_nonzero(x: float) -> float:
    return x or 0.0


def json_recursive_strategy(strategy: st.SearchStrategy) -> st.SearchStrategy:
    return st.lists(strategy, max_size=3) | st.dictionaries(st.text(), strategy, max_size=3)


BUFFER_SIZE = 8 * 1024
FLOAT_STRATEGY: st.SearchStrategy = st.floats(allow_nan=False, allow_infinity=False).map(_replace_zero_with_nonzero)
NUMERIC_STRATEGY: st.SearchStrategy = st.integers() | FLOAT_STRATEGY
JSON_STRATEGY: st.SearchStrategy = st.recursive(
    st.none() | st.booleans() | NUMERIC_STRATEGY | st.text(), json_recursive_strategy
)
ARRAY_STRATEGY: st.SearchStrategy = st.lists(JSON_STRATEGY)
OBJECT_STRATEGY: st.SearchStrategy = st.dictionaries(st.text(), JSON_STRATEGY)

UNKNOWN_PROPERTY_KEY = "x-schemathesis-unknown-property"
UNKNOWN_PROPERTY_VALUE = 42


@dataclass
class GeneratedValue:
    value: Any
    data_generation_method: DataGenerationMethod
    description: str

    __slots__ = ("value", "data_generation_method", "description")

    @classmethod
    def with_positive(cls, value: Any, *, description: str) -> GeneratedValue:
        return cls(value=value, data_generation_method=DataGenerationMethod.positive, description=description)

    @classmethod
    def with_negative(cls, value: Any, *, description: str) -> GeneratedValue:
        return cls(value=value, data_generation_method=DataGenerationMethod.negative, description=description)


PositiveValue = GeneratedValue.with_positive
NegativeValue = GeneratedValue.with_negative


@lru_cache(maxsize=128)
def cached_draw(strategy: st.SearchStrategy) -> Any:
    return get_single_example(strategy)


@dataclass
class CoverageContext:
    data_generation_methods: list[DataGenerationMethod]

    __slots__ = ("data_generation_methods",)

    def __init__(self, data_generation_methods: list[DataGenerationMethod] | None = None) -> None:
        self.data_generation_methods = (
            data_generation_methods if data_generation_methods is not None else DataGenerationMethod.all()
        )

    @classmethod
    def with_positive(cls) -> CoverageContext:
        return CoverageContext(data_generation_methods=[DataGenerationMethod.positive])

    @classmethod
    def with_negative(cls) -> CoverageContext:
        return CoverageContext(data_generation_methods=[DataGenerationMethod.negative])

    def generate_from(self, strategy: st.SearchStrategy) -> Any:
        return cached_draw(strategy)

    def generate_from_schema(self, schema: dict) -> Any:
        return self.generate_from(from_schema(schema))


T = TypeVar("T")


def _to_hashable_key(value: T) -> T | tuple[type, str]:
    if isinstance(value, (dict, list)):
        serialized = json.dumps(value, sort_keys=True)
        return (type(value), serialized)
    return value


def _cover_positive_for_type(
    ctx: CoverageContext, schema: dict, ty: str | None
) -> Generator[GeneratedValue, None, None]:
    if ty == "object" or ty == "array":
        template_schema = _get_template_schema(schema, ty)
        template = ctx.generate_from_schema(template_schema)
    else:
        template = None
    if DataGenerationMethod.positive in ctx.data_generation_methods:
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


@contextmanager
def _ignore_unfixable(
    *,
    # Cache exception types here as `jsonschema` uses a custom `__getattr__` on the module level
    # and it may cause errors during the interpreter shutdown
    ref_error: type[Exception] = jsonschema.RefResolutionError,
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
    ctx: CoverageContext, schema: dict | bool, seen: set[Any | tuple[type, str]] | None = None
) -> Generator[GeneratedValue, None, None]:
    if seen is None:
        seen = set()
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
    if DataGenerationMethod.negative in ctx.data_generation_methods:
        template = None
        for key, value in schema.items():
            with _ignore_unfixable():
                if key == "enum":
                    yield from _negative_enum(ctx, value)
                elif key == "const":
                    for value_ in _negative_enum(ctx, [value]):
                        k = _to_hashable_key(value_.value)
                        if k not in seen:
                            yield value_
                            seen.add(k)
                elif key == "type":
                    yield from _negative_type(ctx, seen, value)
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
                    if next not in seen:
                        yield NegativeValue(next, description="Value greater than maximum")
                        seen.add(next)
                elif key == "minimum":
                    next = value - 1
                    if next not in seen:
                        yield NegativeValue(next, description="Value smaller than minimum")
                        seen.add(next)
                elif key == "exclusiveMaximum" or key == "exclusiveMinimum" and value not in seen:
                    verb = "greater" if key == "exclusiveMaximum" else "smaller"
                    limit = "maximum" if key == "exclusiveMaximum" else "minimum"
                    yield NegativeValue(value, description=f"Value {verb} than {limit}")
                    seen.add(value)
                elif key == "multipleOf":
                    for value_ in _negative_multiple_of(ctx, schema, value):
                        k = _to_hashable_key(value_.value)
                        if k not in seen:
                            yield value_
                            seen.add(k)
                elif key == "minLength" and 0 < value < BUFFER_SIZE:
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
                        k = _to_hashable_key(value)
                        if k not in seen:
                            yield NegativeValue(value, description="String smaller than minLength")
                            seen.add(k)
                elif key == "maxLength" and value < BUFFER_SIZE:
                    with suppress(InvalidArgument, Unsatisfiable):
                        min_length = max_length = value + 1
                        new_schema = {**schema, "minLength": min_length, "maxLength": max_length}
                        new_schema.setdefault("type", "string")
                        if "pattern" in new_schema:
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
                        k = _to_hashable_key(value)
                        if k not in seen:
                            yield NegativeValue(value, description="String larger than maxLength")
                            seen.add(k)
                elif key == "uniqueItems" and value:
                    yield from _negative_unique_items(ctx, schema)
                elif key == "required":
                    template = template or ctx.generate_from_schema(_get_template_schema(schema, "object"))
                    yield from _negative_required(ctx, template, value)
                elif key == "additionalProperties" and not value:
                    template = template or ctx.generate_from_schema(_get_template_schema(schema, "object"))
                    yield NegativeValue(
                        {**template, UNKNOWN_PROPERTY_KEY: UNKNOWN_PROPERTY_VALUE},
                        description="Object with unexpected properties",
                    )
                elif key == "allOf":
                    nctx = ctx.with_negative()
                    if len(value) == 1:
                        yield from cover_schema_iter(nctx, value[0], seen)
                    else:
                        with _ignore_unfixable():
                            canonical = canonicalish(schema)
                            yield from cover_schema_iter(nctx, canonical, seen)
                elif key == "anyOf" or key == "oneOf":
                    nctx = ctx.with_negative()
                    # NOTE: Other sub-schemas are not filtered out
                    for sub_schema in value:
                        yield from cover_schema_iter(nctx, sub_schema, seen)


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
        _schema = fast_deepcopy(schema)
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


def _positive_string(ctx: CoverageContext, schema: dict) -> Generator[GeneratedValue, None, None]:
    """Generate positive string values."""
    # Boundary and near boundary values
    min_length = schema.get("minLength")
    if min_length == 0:
        min_length = None
    max_length = schema.get("maxLength")
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
    elif not min_length and not max_length:
        # Default positive value
        yield PositiveValue(ctx.generate_from_schema(schema), description="Valid string")
    elif "pattern" in schema:
        # Without merging `maxLength` & `minLength` into a regex it is problematic
        # to generate a valid value as the unredlying machinery will resort to filtering
        # and it is unlikely that it will generate a string of that length
        yield PositiveValue(ctx.generate_from_schema(schema), description="Valid string")
        return

    seen = set()

    if min_length is not None and min_length < BUFFER_SIZE:
        # Exactly the minimum length
        yield PositiveValue(
            ctx.generate_from_schema({**schema, "maxLength": min_length}), description="Minimum length string"
        )
        seen.add(min_length)

        # One character more than minimum if possible
        larger = min_length + 1
        if larger < BUFFER_SIZE and larger not in seen and (not max_length or larger <= max_length):
            yield PositiveValue(
                ctx.generate_from_schema({**schema, "minLength": larger, "maxLength": larger}),
                description="Near-boundary length string",
            )
            seen.add(larger)

    if max_length is not None:
        # Exactly the maximum length
        if max_length < BUFFER_SIZE and max_length not in seen:
            yield PositiveValue(
                ctx.generate_from_schema({**schema, "minLength": max_length}), description="Maximum length string"
            )
            seen.add(max_length)

        # One character less than maximum if possible
        smaller = max_length - 1
        if (
            smaller < BUFFER_SIZE
            and smaller not in seen
            and (smaller > 0 and (min_length is None or smaller >= min_length))
        ):
            yield PositiveValue(
                ctx.generate_from_schema({**schema, "minLength": smaller, "maxLength": smaller}),
                description="Near-boundary length string",
            )
            seen.add(smaller)


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
    elif not minimum and not maximum:
        # Default positive value
        yield PositiveValue(ctx.generate_from_schema(schema), description="Valid number")

    seen = set()

    if minimum is not None:
        # Exactly the minimum
        if multiple_of is not None:
            smallest = closest_multiple_greater_than(minimum, multiple_of)
        else:
            smallest = minimum
        seen.add(smallest)
        yield PositiveValue(smallest, description="Minimum value")

        # One more than minimum if possible
        if multiple_of is not None:
            larger = smallest + multiple_of
        else:
            larger = minimum + 1
        if larger not in seen and (not maximum or larger <= maximum):
            seen.add(larger)
            yield PositiveValue(larger, description="Near-boundary number")

    if maximum is not None:
        # Exactly the maximum
        if multiple_of is not None:
            largest = maximum - (maximum % multiple_of)
        else:
            largest = maximum
        if largest not in seen:
            seen.add(largest)
            yield PositiveValue(largest, description="Maximum value")

        # One less than maximum if possible
        if multiple_of is not None:
            smaller = largest - multiple_of
        else:
            smaller = maximum - 1
        if smaller not in seen and (smaller > 0 and (minimum is None or smaller >= minimum)):
            seen.add(smaller)
            yield PositiveValue(smaller, description="Near-boundary number")


def _positive_array(ctx: CoverageContext, schema: dict, template: list) -> Generator[GeneratedValue, None, None]:
    seen = set()
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
        yield PositiveValue(template, description="Valid array")
    seen.add(len(template))

    # Boundary and near-boundary sizes
    min_items = schema.get("minItems")
    max_items = schema.get("maxItems")
    if min_items is not None:
        # Do not generate an array with `minItems` length, because it is already covered by `template`

        # One item more than minimum if possible
        larger = min_items + 1
        if larger not in seen and (max_items is None or larger <= max_items):
            yield PositiveValue(
                ctx.generate_from_schema({**schema, "minItems": larger, "maxItems": larger}),
                description="Near-boundary items array",
            )
            seen.add(larger)

    if max_items is not None:
        if max_items < BUFFER_SIZE and max_items not in seen:
            yield PositiveValue(
                ctx.generate_from_schema({**schema, "minItems": max_items}),
                description="Maximum items array",
            )
            seen.add(max_items)

        # One item smaller than maximum if possible
        smaller = max_items - 1
        if (
            smaller < BUFFER_SIZE
            and smaller > 0
            and smaller not in seen
            and (min_items is None or smaller >= min_items)
        ):
            yield PositiveValue(
                ctx.generate_from_schema({**schema, "minItems": smaller, "maxItems": smaller}),
                description="Near-boundary items array",
            )
            seen.add(smaller)


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
    seen = set()
    for name, sub_schema in properties.items():
        seen.add(_to_hashable_key(template.get(name)))
        for new in cover_schema_iter(ctx, sub_schema):
            key = _to_hashable_key(new.value)
            if key not in seen:
                yield PositiveValue(
                    {**template, name: new.value}, description=f"Object with valid '{name}' value: {new.description}"
                )
                seen.add(key)
        seen.clear()


def select_combinations(optional: list[str]) -> Iterator[tuple[str, ...]]:
    for size in range(2, len(optional)):
        yield next(combinations(optional, size))


def _negative_enum(ctx: CoverageContext, value: list) -> Generator[GeneratedValue, None, None]:
    def is_not_in_value(x: Any) -> bool:
        return x not in value

    strategy = JSON_STRATEGY.filter(is_not_in_value)
    # The exact negative value is not important here
    yield NegativeValue(ctx.generate_from(strategy), description="Invalid enum value")


def _negative_properties(
    ctx: CoverageContext, template: dict, properties: dict
) -> Generator[GeneratedValue, None, None]:
    nctx = ctx.with_negative()
    for key, sub_schema in properties.items():
        for value in cover_schema_iter(nctx, sub_schema):
            yield NegativeValue(
                {**template, key: value.value},
                description=f"Object with invalid '{key}' value: {value.description}",
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
        for value in cover_schema_iter(nctx, sub_schema):
            yield NegativeValue(
                {**template, key: value.value},
                description=f"Object with invalid pattern key '{key}' ('{pattern}') value: {value.description}",
            )


def _negative_items(ctx: CoverageContext, schema: dict[str, Any] | bool) -> Generator[GeneratedValue, None, None]:
    """Arrays not matching the schema."""
    nctx = ctx.with_negative()
    for value in cover_schema_iter(nctx, schema):
        yield NegativeValue(
            [value.value],
            description=f"Array with invalid items: {value.description}",
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
            st.text(min_size=min_length or 0, max_size=max_length).filter(
                partial(_not_matching_pattern, pattern=compiled)
            )
        ),
        description=f"Value not matching the '{pattern}' pattern",
    )


def _with_negated_key(schema: dict, key: str, value: Any) -> dict:
    return {"allOf": [{k: v for k, v in schema.items() if k != key}, {"not": {key: value}}]}


def _negative_multiple_of(
    ctx: CoverageContext, schema: dict, multiple_of: int | float
) -> Generator[GeneratedValue, None, None]:
    yield NegativeValue(
        ctx.generate_from_schema(_with_negated_key(schema, "multipleOf", multiple_of)),
        description=f"Non-multiple of {multiple_of}",
    )


def _negative_unique_items(ctx: CoverageContext, schema: dict) -> Generator[GeneratedValue, None, None]:
    unique = ctx.generate_from_schema({**schema, "type": "array", "minItems": 1, "maxItems": 1})
    yield NegativeValue(unique + unique, description="Non-unique items")


def _negative_required(
    ctx: CoverageContext, template: dict, required: list[str]
) -> Generator[GeneratedValue, None, None]:
    for key in required:
        yield NegativeValue(
            {k: v for k, v in template.items() if k != key},
            description=f"Missing required property: {key}",
        )


def _is_invalid_hostname(v: Any) -> bool:
    return v == "" or not jsonschema.Draft202012Validator.FORMAT_CHECKER.conforms(v, "hostname")


def _is_invalid_format(v: Any, format: str) -> bool:
    return not jsonschema.Draft202012Validator.FORMAT_CHECKER.conforms(v, format)


def _negative_format(ctx: CoverageContext, schema: dict, format: str) -> Generator[GeneratedValue, None, None]:
    # Hypothesis-jsonschema does not canonicalise it properly right now, which leads to unsatisfiable schema
    without_format = {k: v for k, v in schema.items() if k != "format"}
    without_format.setdefault("type", "string")
    strategy = from_schema(without_format)
    if format in jsonschema.Draft202012Validator.FORMAT_CHECKER.checkers:
        if format == "hostname":
            strategy = strategy.filter(_is_invalid_hostname)
        else:
            strategy = strategy.filter(functools.partial(_is_invalid_format, format=format))
    yield NegativeValue(ctx.generate_from(strategy), description=f"Value not matching the '{format}' format")


def _is_non_integer_float(x: float) -> bool:
    return x != int(x)


def _negative_type(ctx: CoverageContext, seen: set, ty: str | list[str]) -> Generator[GeneratedValue, None, None]:
    strategies = {
        "integer": st.integers(),
        "number": NUMERIC_STRATEGY,
        "boolean": st.booleans(),
        "null": st.none(),
        "string": st.text(),
        "array": ARRAY_STRATEGY,
        "object": OBJECT_STRATEGY,
    }
    if isinstance(ty, str):
        types = [ty]
    else:
        types = ty
    for ty_ in types:
        strategies.pop(ty_)
    if "number" in types:
        del strategies["integer"]
    if "integer" in types:
        strategies["number"] = FLOAT_STRATEGY.filter(_is_non_integer_float)
    for strat in strategies.values():
        value = ctx.generate_from(strat)
        hashed = _to_hashable_key(value)
        if hashed in seen:
            continue
        yield NegativeValue(value, description="Incorrect type")
        seen.add(hashed)


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
