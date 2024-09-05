from __future__ import annotations

import json
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Generator, Set, Type, TypeVar, cast

import jsonschema
from hypothesis import strategies as st
from hypothesis.errors import InvalidArgument, Unsatisfiable
from hypothesis_jsonschema import from_schema
from hypothesis_jsonschema._canonicalise import canonicalish

from schemathesis.constants import NOT_SET

from ._hypothesis import combine_strategies, get_single_example
from ._methods import DataGenerationMethod

BUFFER_SIZE = 8 * 1024
FLOAT_STRATEGY: st.SearchStrategy = st.floats(allow_nan=False, allow_infinity=False).map(lambda x: x or 0.0)
NUMERIC_STRATEGY: st.SearchStrategy = st.integers() | FLOAT_STRATEGY
JSON_STRATEGY: st.SearchStrategy = st.recursive(
    st.none() | st.booleans() | NUMERIC_STRATEGY | st.text(),
    lambda strategy: st.lists(strategy, max_size=3) | st.dictionaries(st.text(), strategy, max_size=3),
)
ARRAY_STRATEGY: st.SearchStrategy = st.lists(JSON_STRATEGY)
OBJECT_STRATEGY: st.SearchStrategy = st.dictionaries(st.text(), JSON_STRATEGY)

UNKNOWN_PROPERTY_KEY = "x-schemathesis-unknown-property"
UNKNOWN_PROPERTY_VALUE = 42


@dataclass
class GeneratedValue:
    value: Any
    data_generation_method: DataGenerationMethod

    __slots__ = ("value", "data_generation_method")

    @classmethod
    def with_positive(cls, value: Any) -> GeneratedValue:
        return cls(value, DataGenerationMethod.positive)

    @classmethod
    def with_negative(cls, value: Any) -> GeneratedValue:
        return cls(value, DataGenerationMethod.negative)


PositiveValue = GeneratedValue.with_positive
NegativeValue = GeneratedValue.with_negative


@lru_cache(maxsize=128)
def cached_draw(strategy: st.SearchStrategy) -> Any:
    return get_single_example(strategy)


@dataclass
class CoverageContext:
    data_generation_methods: list[DataGenerationMethod] = field(default_factory=DataGenerationMethod.all)

    @classmethod
    def with_positive(cls) -> CoverageContext:
        return CoverageContext(data_generation_methods=[DataGenerationMethod.positive])

    @classmethod
    def with_negative(cls) -> CoverageContext:
        return CoverageContext(data_generation_methods=[DataGenerationMethod.negative])

    def generate_from(self, strategy: st.SearchStrategy, cached: bool = False) -> Any:
        if cached:
            value = cached_draw(strategy)
        else:
            value = get_single_example(strategy)
        return value

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
                yield PositiveValue(value)
        elif const is not NOT_SET:
            yield PositiveValue(const)
        elif ty is not None:
            if ty == "null":
                yield PositiveValue(None)
            elif ty == "boolean":
                yield PositiveValue(True)
                yield PositiveValue(False)
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
    ref_error: Type[Exception] = jsonschema.RefResolutionError,
    schema_error: Type[Exception] = jsonschema.SchemaError,
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


def cover_schema_iter(ctx: CoverageContext, schema: dict | bool) -> Generator[GeneratedValue, None, None]:
    if isinstance(schema, bool):
        types = ["null", "boolean", "string", "number", "array", "object"]
        schema = {}
    else:
        types = schema.get("type", [])
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
        seen: Set[Any | tuple[type, str]] = set()
        for key, value in schema.items():
            with _ignore_unfixable():
                if key == "enum":
                    yield from _negative_enum(ctx, value)
                elif key == "const":
                    yield from _negative_enum(ctx, [value])
                elif key == "type":
                    yield from _negative_type(ctx, seen, value)
                elif key == "properties":
                    template = template or ctx.generate_from_schema(_get_template_schema(schema, "object"))
                    yield from _negative_properties(ctx, template, value)
                elif key == "pattern":
                    yield from _negative_pattern(ctx, value)
                elif key == "format" and ("string" in types or not types):
                    yield from _negative_format(ctx, schema, value)
                elif key == "maximum":
                    next = value + 1
                    yield NegativeValue(next)
                    seen.add(next)
                elif key == "minimum":
                    next = value - 1
                    yield NegativeValue(next)
                    seen.add(next)
                elif key == "exclusiveMaximum" or key == "exclusiveMinimum" and value not in seen:
                    yield NegativeValue(value)
                    seen.add(value)
                elif key == "multipleOf":
                    yield from _negative_multiple_of(ctx, schema, value)
                elif key == "minLength" and 0 < value < BUFFER_SIZE:
                    with suppress(InvalidArgument):
                        yield NegativeValue(
                            ctx.generate_from_schema({**schema, "minLength": value - 1, "maxLength": value - 1})
                        )
                elif key == "maxLength" and value < BUFFER_SIZE:
                    with suppress(InvalidArgument):
                        yield NegativeValue(
                            ctx.generate_from_schema({**schema, "minLength": value + 1, "maxLength": value + 1})
                        )
                elif key == "uniqueItems" and value:
                    yield from _negative_unique_items(ctx, schema)
                elif key == "required":
                    template = template or ctx.generate_from_schema(_get_template_schema(schema, "object"))
                    yield from _negative_required(ctx, template, value)
                elif key == "additionalProperties" and not value:
                    template = template or ctx.generate_from_schema(_get_template_schema(schema, "object"))
                    yield NegativeValue({**template, UNKNOWN_PROPERTY_KEY: UNKNOWN_PROPERTY_VALUE})
                elif key == "allOf":
                    nctx = ctx.with_negative()
                    if len(value) == 1:
                        yield from cover_schema_iter(nctx, value[0])
                    else:
                        with _ignore_unfixable():
                            canonical = canonicalish(schema)
                            yield from cover_schema_iter(nctx, canonical)
                elif key == "anyOf" or key == "oneOf":
                    nctx = ctx.with_negative()
                    # NOTE: Other sub-schemas are not filtered out
                    for sub_schema in value:
                        yield from cover_schema_iter(nctx, sub_schema)


def _get_properties(schema: dict | bool) -> dict | bool:
    if isinstance(schema, dict):
        if "example" in schema:
            return {"const": schema["example"]}
        if "examples" in schema and schema["examples"]:
            return {"enum": schema["examples"]}
        if schema.get("type") == "object":
            return _get_template_schema(schema, "object")
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
    max_length = schema.get("maxLength")
    example = schema.get("example")
    examples = schema.get("examples")
    if example or examples:
        if example:
            yield PositiveValue(example)
        if examples:
            for example in examples:
                yield PositiveValue(example)
    elif not min_length and not max_length:
        # Default positive value
        yield PositiveValue(ctx.generate_from_schema(schema))

    seen = set()

    if min_length is not None and min_length < BUFFER_SIZE:
        # Exactly the minimum length
        yield PositiveValue(ctx.generate_from_schema({**schema, "maxLength": min_length}))
        seen.add(min_length)

        # One character more than minimum if possible
        larger = min_length + 1
        if larger < BUFFER_SIZE and larger not in seen and (not max_length or larger <= max_length):
            yield PositiveValue(ctx.generate_from_schema({**schema, "minLength": larger, "maxLength": larger}))
            seen.add(larger)

    if max_length is not None:
        # Exactly the maximum length
        if max_length < BUFFER_SIZE and max_length not in seen:
            yield PositiveValue(ctx.generate_from_schema({**schema, "minLength": max_length}))
            seen.add(max_length)

        # One character less than maximum if possible
        smaller = max_length - 1
        if (
            smaller < BUFFER_SIZE
            and smaller not in seen
            and (smaller > 0 and (min_length is None or smaller >= min_length))
        ):
            yield PositiveValue(ctx.generate_from_schema({**schema, "minLength": smaller, "maxLength": smaller}))
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

    if example or examples:
        if example:
            yield PositiveValue(example)
        if examples:
            for example in examples:
                yield PositiveValue(example)
    elif not minimum and not maximum:
        # Default positive value
        yield PositiveValue(ctx.generate_from_schema(schema))

    seen = set()

    if minimum is not None:
        # Exactly the minimum
        if multiple_of is not None:
            smallest = closest_multiple_greater_than(minimum, multiple_of)
        else:
            smallest = minimum
        seen.add(smallest)
        yield PositiveValue(smallest)

        # One more than minimum if possible
        if multiple_of is not None:
            larger = smallest + multiple_of
        else:
            larger = minimum + 1
        if larger not in seen and (not maximum or larger <= maximum):
            seen.add(larger)
            yield PositiveValue(larger)

    if maximum is not None:
        # Exactly the maximum
        if multiple_of is not None:
            largest = maximum - (maximum % multiple_of)
        else:
            largest = maximum
        if largest not in seen:
            seen.add(largest)
            yield PositiveValue(largest)

        # One less than maximum if possible
        if multiple_of is not None:
            smaller = largest - multiple_of
        else:
            smaller = maximum - 1
        if smaller not in seen and (smaller > 0 and (minimum is None or smaller >= minimum)):
            seen.add(smaller)
            yield PositiveValue(smaller)


def _positive_array(ctx: CoverageContext, schema: dict, template: list) -> Generator[GeneratedValue, None, None]:
    seen = set()
    example = schema.get("example")
    examples = schema.get("examples")

    if example or examples:
        if example:
            yield PositiveValue(example)
        if examples:
            for example in examples:
                yield PositiveValue(example)
    else:
        yield PositiveValue(template)
    seen.add(len(template))

    # Boundary and near-boundary sizes
    min_items = schema.get("minItems")
    max_items = schema.get("maxItems")
    if min_items is not None:
        # Do not generate an array with `minItems` length, because it is already covered by `template`

        # One item more than minimum if possible
        larger = min_items + 1
        if larger not in seen and (max_items is None or larger <= max_items):
            yield PositiveValue(ctx.generate_from_schema({**schema, "minItems": larger, "maxItems": larger}))
            seen.add(larger)

    if max_items is not None:
        if max_items < BUFFER_SIZE and max_items not in seen:
            yield PositiveValue(ctx.generate_from_schema({**schema, "minItems": max_items}))
            seen.add(max_items)

        # One item smaller than maximum if possible
        smaller = max_items - 1
        if (
            smaller < BUFFER_SIZE
            and smaller > 0
            and smaller not in seen
            and (min_items is None or smaller >= min_items)
        ):
            yield PositiveValue(ctx.generate_from_schema({**schema, "minItems": smaller, "maxItems": smaller}))
            seen.add(smaller)


def _positive_object(ctx: CoverageContext, schema: dict, template: dict) -> Generator[GeneratedValue, None, None]:
    example = schema.get("example")
    examples = schema.get("examples")

    if example or examples:
        if example:
            yield PositiveValue(example)
        if examples:
            for example in examples:
                yield PositiveValue(example)
    else:
        yield PositiveValue(template)
    # Only required properties
    properties = schema.get("properties", {})
    if set(properties) != set(schema.get("required", {})):
        only_required = {k: v for k, v in template.items() if k in schema.get("required", [])}
        yield PositiveValue(only_required)
    seen = set()
    for name, sub_schema in properties.items():
        seen.add(_to_hashable_key(template.get(name)))
        for new in cover_schema_iter(ctx, sub_schema):
            key = _to_hashable_key(new.value)
            if key not in seen:
                yield PositiveValue({**template, name: new.value})
                seen.add(key)
        seen.clear()


def _negative_enum(ctx: CoverageContext, value: list) -> Generator[GeneratedValue, None, None]:
    strategy = JSON_STRATEGY.filter(lambda x: x not in value)
    # The exact negative value is not important here
    yield NegativeValue(ctx.generate_from(strategy, cached=True))


def _negative_properties(
    ctx: CoverageContext, template: dict, properties: dict
) -> Generator[GeneratedValue, None, None]:
    nctx = ctx.with_negative()
    for key, sub_schema in properties.items():
        for value in cover_schema_iter(nctx, sub_schema):
            yield NegativeValue({**template, key: value.value})


def _negative_pattern(ctx: CoverageContext, pattern: str) -> Generator[GeneratedValue, None, None]:
    yield NegativeValue(ctx.generate_from(st.text().filter(lambda x: x != pattern), cached=True))


def _with_negated_key(schema: dict, key: str, value: Any) -> dict:
    return {"allOf": [{k: v for k, v in schema.items() if k != key}, {"not": {key: value}}]}


def _negative_multiple_of(
    ctx: CoverageContext, schema: dict, multiple_of: int | float
) -> Generator[GeneratedValue, None, None]:
    yield NegativeValue(ctx.generate_from_schema(_with_negated_key(schema, "multipleOf", multiple_of)))


def _negative_unique_items(ctx: CoverageContext, schema: dict) -> Generator[GeneratedValue, None, None]:
    unique = ctx.generate_from_schema({**schema, "type": "array", "minItems": 1, "maxItems": 1})
    yield NegativeValue(unique + unique)


def _negative_required(
    ctx: CoverageContext, template: dict, required: list[str]
) -> Generator[GeneratedValue, None, None]:
    for key in required:
        yield NegativeValue({k: v for k, v in template.items() if k != key})


def _negative_format(ctx: CoverageContext, schema: dict, format: str) -> Generator[GeneratedValue, None, None]:
    # Hypothesis-jsonschema does not canonicalise it properly right now, which leads to unsatisfiable schema
    without_format = {k: v for k, v in schema.items() if k != "format"}
    without_format.setdefault("type", "string")
    strategy = from_schema(without_format)
    if format in jsonschema.Draft202012Validator.FORMAT_CHECKER.checkers:
        strategy = strategy.filter(
            lambda v: (format == "hostname" and v == "")
            or not jsonschema.Draft202012Validator.FORMAT_CHECKER.conforms(v, format)
        )
    yield NegativeValue(ctx.generate_from(strategy))


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
        strategies["number"] = FLOAT_STRATEGY.filter(lambda x: x != int(x))
    negative_strategy = combine_strategies(tuple(strategies.values())).filter(lambda x: _to_hashable_key(x) not in seen)
    value = ctx.generate_from(negative_strategy, cached=True)
    yield NegativeValue(value)
    seen.add(_to_hashable_key(value))
