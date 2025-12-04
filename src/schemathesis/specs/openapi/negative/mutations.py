"""Schema mutations."""

from __future__ import annotations

import enum
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import wraps
from typing import Any, TypeAlias, TypeVar

from hypothesis import reject
from hypothesis import strategies as st
from hypothesis.strategies._internal.featureflags import FeatureStrategy
from hypothesis_jsonschema._canonicalise import canonicalish

from schemathesis.core.jsonschema import BUNDLE_STORAGE_KEY, get_type
from schemathesis.core.jsonschema.types import JsonSchemaObject
from schemathesis.core.parameters import ParameterLocation
from schemathesis.core.transforms import deepclone

from .types import Draw, Schema
from .utils import can_negate, is_binary_format

T = TypeVar("T")


@dataclass
class MutationMetadata:
    """Metadata about a mutation that was applied."""

    parameter: str | None
    description: str | None
    location: str | None

    __slots__ = ("parameter", "description", "location")


class MutationResult(int, enum.Enum):
    """The result of applying some mutation to some schema.

    Failing to mutate something means that by applying some mutation, it is not possible to change
    the schema in the way, so it covers inputs not covered by the "positive" strategy.

    Knowing this, we know when the schema is mutated and whether we need to apply more mutations.
    """

    SUCCESS = 1
    FAILURE = 2

    def __ior__(self, other: Any) -> MutationResult:
        return self | other

    def __or__(self, other: Any) -> MutationResult:
        # Syntactic sugar to simplify handling of multiple results
        if self == MutationResult.SUCCESS:
            return self
        return other


Mutation: TypeAlias = Callable[["MutationContext", Draw, Schema], tuple[MutationResult, MutationMetadata | None]]
ANY_TYPE_KEYS = {"$ref", "allOf", "anyOf", "const", "else", "enum", "if", "not", "oneOf", "then", "type"}
TYPE_SPECIFIC_KEYS = {
    "number": ("multipleOf", "maximum", "exclusiveMaximum", "minimum", "exclusiveMinimum"),
    "integer": ("multipleOf", "maximum", "exclusiveMaximum", "minimum", "exclusiveMinimum"),
    "string": ("maxLength", "minLength", "pattern", "format", "contentEncoding", "contentMediaType"),
    "array": ("items", "additionalItems", "maxItems", "minItems", "uniqueItems", "contains"),
    "object": (
        "maxProperties",
        "minProperties",
        "required",
        "properties",
        "patternProperties",
        "additionalProperties",
        "dependencies",
        "propertyNames",
    ),
}


@dataclass
class MutationContext:
    """Meta information about the current mutation state."""

    # The original schema
    keywords: Schema  # only keywords
    non_keywords: Schema  # everything else
    # Schema location within API operation (header, query, etc)
    location: ParameterLocation
    # Payload media type, if available
    media_type: str | None
    # Whether generating unexpected parameters is permitted
    allow_extra_parameters: bool

    __slots__ = ("keywords", "non_keywords", "location", "media_type", "allow_extra_parameters")

    def __init__(
        self,
        *,
        keywords: Schema,
        non_keywords: Schema,
        location: ParameterLocation,
        media_type: str | None,
        allow_extra_parameters: bool,
    ) -> None:
        self.keywords = keywords
        self.non_keywords = non_keywords
        self.location = location
        self.media_type = media_type
        self.allow_extra_parameters = allow_extra_parameters

    @property
    def is_path_location(self) -> bool:
        return self.location == ParameterLocation.PATH

    @property
    def is_query_location(self) -> bool:
        return self.location == ParameterLocation.QUERY

    def ensure_bundle(self, schema: Schema) -> None:
        """Ensure schema has the bundle from context if needed.

        This is necessary when working with nested schemas (e.g., property schemas)
        that may contain bundled references but don't have the x-bundled key themselves.
        """
        if BUNDLE_STORAGE_KEY in self.non_keywords and BUNDLE_STORAGE_KEY not in schema:
            schema[BUNDLE_STORAGE_KEY] = self.non_keywords[BUNDLE_STORAGE_KEY]

    def mutate(self, draw: Draw) -> tuple[Schema, MutationMetadata | None]:
        # On the top level, Schemathesis creates "object" schemas for all parameter "in" values except "body", which is
        # taken as-is. Therefore, we can only apply mutations that won't change the Open API semantics of the schema.
        mutations: list[Mutation]
        if self.location in (ParameterLocation.HEADER, ParameterLocation.COOKIE, ParameterLocation.QUERY):
            # These objects follow this pattern:
            # {
            #     "properties": properties,
            #     "additionalProperties": False,
            #     "type": "object",
            #     "required": required
            # }
            # Open API semantics expect mapping; therefore, they should have the "object" type.
            # We can:
            #   - remove required parameters
            #   - negate constraints (only `additionalProperties` in this case)
            #   - mutate individual properties
            mutations = draw(ordered((remove_required_property, negate_constraints, change_properties)))
        elif self.is_path_location:
            # The same as above, but we can only mutate individual properties as their names are predefined in the
            # path template, and all of them are required.
            mutations = [change_properties]
        else:
            # Body can be of any type and does not have any specific type semantic.
            mutations = draw(ordered(get_mutations(draw, self.keywords)))
        # Deep copy all keywords to avoid modifying the original schema
        new_schema = deepclone(self.keywords)
        # Add x-bundled before mutations so they can resolve bundled references
        if BUNDLE_STORAGE_KEY in self.non_keywords:
            new_schema[BUNDLE_STORAGE_KEY] = self.non_keywords[BUNDLE_STORAGE_KEY]
        enabled_mutations = draw(st.shared(FeatureStrategy(), key="mutations"))
        # Always apply at least one mutation, otherwise everything is rejected, and we'd like to avoid it
        # for performance reasons
        always_applied_mutation = draw(st.sampled_from(mutations))
        result, metadata = always_applied_mutation(self, draw, new_schema)
        num_successful = 1 if result == MutationResult.SUCCESS else 0
        for mutation in mutations:
            if mutation is not always_applied_mutation and enabled_mutations.is_enabled(mutation.__name__):
                mut_result, mut_metadata = mutation(self, draw, new_schema)
                result |= mut_result
                if mut_result == MutationResult.SUCCESS:
                    num_successful += 1
                    if metadata is None:
                        metadata = mut_metadata
        # When multiple mutations succeed, they can conflict (e.g., one mutates a property, another removes it).
        # Merging metadata from multiple mutations is non-trivial, so clear the description to avoid misleading
        # error messages. We preserve `parameter` and `parameter_location` as they're used for auth exclusion logic
        if num_successful > 1 and metadata is not None:
            metadata = MutationMetadata(
                parameter=metadata.parameter,
                description=None,
                location=metadata.location,
            )
        if result == MutationResult.FAILURE:
            # If we failed to apply anything, then reject the whole case
            reject()
        new_schema.update(self.non_keywords)
        if self.location.is_in_header:
            # All headers should have names that can be sent over network
            new_schema["propertyNames"] = {"type": "string", "format": "_header_name"}
            for sub_schema in new_schema.get("properties", {}).values():
                sub_schema["type"] = "string"
                if len(sub_schema) == 1:
                    sub_schema["format"] = "_header_value"
            if self.allow_extra_parameters and draw(st.booleans()):
                # In headers, `additionalProperties` are False by default, which means that Schemathesis won't generate
                # any headers that are not defined. This change adds the possibility of generating valid extra headers
                new_schema["additionalProperties"] = {"type": "string", "format": "_header_value"}
        # Empty array or objects may match the original schema
        if "array" in get_type(new_schema) and new_schema.get("items") and "minItems" not in new_schema.get("not", {}):
            new_schema.setdefault("minItems", 1)
        if (
            "object" in get_type(new_schema)
            and new_schema.get("properties")
            and "minProperties" not in new_schema.get("not", {})
        ):
            new_schema.setdefault("minProperties", 1)
        return new_schema, metadata


def for_types(*allowed_types: str) -> Callable[[Mutation], Mutation]:
    """Immediately return FAILURE for schemas with types not from ``allowed_types``."""
    _allowed_types = set(allowed_types)

    def wrapper(mutation: Mutation) -> Mutation:
        @wraps(mutation)
        def inner(ctx: MutationContext, draw: Draw, schema: Schema) -> tuple[MutationResult, MutationMetadata | None]:
            types = get_type(schema)
            if _allowed_types & set(types):
                return mutation(ctx, draw, schema)
            return MutationResult.FAILURE, None

        return inner

    return wrapper


@for_types("object")
def remove_required_property(
    ctx: MutationContext, draw: Draw, schema: Schema
) -> tuple[MutationResult, MutationMetadata | None]:
    """Remove a required property.

    Effect: Some property won't be generated.
    """
    required = schema.get("required")
    if not required:
        # No required properties - can't mutate
        return MutationResult.FAILURE, None
    if len(required) == 1:
        property_name = draw(st.sampled_from(sorted(required)))
    else:
        candidate = draw(st.sampled_from(sorted(required)))
        enabled_properties = draw(st.shared(FeatureStrategy(), key="properties"))
        candidates = [candidate, *sorted([prop for prop in required if enabled_properties.is_enabled(prop)])]
        property_name = draw(st.sampled_from(candidates))
    required.remove(property_name)
    if not required:
        # In JSON Schema Draft 4, `required` must contain at least one string
        # To keep the schema conformant, remove the `required` key completely
        del schema["required"]
    # An optional property still can be generated, and to avoid it, we need to remove it from other keywords.
    properties = schema.get("properties", {})
    properties.pop(property_name, None)
    if properties == {}:
        schema.pop("properties", None)
    schema["type"] = "object"
    # This property still can be generated via `patternProperties`, but this implementation doesn't cover this case
    # Its probability is relatively low, and the complete solution compatible with Draft 4 will require extra complexity
    # The output filter covers cases like this
    metadata = MutationMetadata(
        parameter=property_name,
        description="Required property removed",
        location=f"/properties/{property_name}",
    )
    return MutationResult.SUCCESS, metadata


def change_type(
    ctx: MutationContext, draw: Draw, schema: JsonSchemaObject
) -> tuple[MutationResult, MutationMetadata | None]:
    """Change type of values accepted by a schema."""
    if "type" not in schema:
        # The absence of this keyword means that the schema values can be of any type;
        # Therefore, we can't choose a different type
        return MutationResult.FAILURE, None
    if ctx.media_type == "application/x-www-form-urlencoded":
        # Form data should be an object, do not change it
        return MutationResult.FAILURE, None
    # For headers, query and path parameters, if the current type is string, then it already
    # includes all possible values as those parameters will be stringified before sending,
    # therefore it can't be negated.
    old_types = get_type(schema)
    if "string" in old_types and (ctx.location.is_in_header or ctx.is_path_location or ctx.is_query_location):
        return MutationResult.FAILURE, None
    # For binary format in body, type: string accepts any bytes data (no effective constraint).
    # Similar to stringified params, we can't generate truly invalid data with just type mutations.
    if "string" in old_types and is_binary_format(schema) and ctx.location == ParameterLocation.BODY:
        return MutationResult.FAILURE, None
    candidates = _get_type_candidates(ctx, schema)
    if not candidates:
        # Schema covers all possible types, not possible to choose something else
        return MutationResult.FAILURE, None
    if len(candidates) == 1:
        new_type = candidates.pop()
        schema["type"] = new_type
        _ensure_query_serializes_to_non_empty(ctx, schema)
        _ensure_path_string_not_numeric(ctx, schema, old_types)
        prevent_unsatisfiable_schema(schema, new_type)
    else:
        # Choose one type that will be present in the final candidates list
        candidate = draw(st.sampled_from(sorted(candidates)))
        candidates.remove(candidate)
        enabled_types = draw(st.shared(FeatureStrategy(), key="types"))
        remaining_candidates = [
            candidate,
            *sorted([candidate for candidate in candidates if enabled_types.is_enabled(candidate)]),
        ]
        new_type = draw(st.sampled_from(remaining_candidates))
        schema["type"] = new_type
        _ensure_query_serializes_to_non_empty(ctx, schema)
        _ensure_path_string_not_numeric(ctx, schema, old_types)
        prevent_unsatisfiable_schema(schema, new_type)

    old_type_str = " | ".join(sorted(old_types)) if len(old_types) > 1 else old_types[0]
    metadata = MutationMetadata(
        parameter=None,
        description=f"Invalid type {new_type} (expected {old_type_str})",
        location=None,
    )
    return MutationResult.SUCCESS, metadata


def _ensure_query_serializes_to_non_empty(ctx: MutationContext, schema: Schema) -> None:
    if ctx.is_query_location and schema.get("type") == "array":
        # Query parameters with empty arrays or arrays of `None` or empty arrays / objects will not appear in the final URL
        schema["minItems"] = schema.get("minItems") or 1
        schema.setdefault("items", {}).update({"not": {"enum": [None, [], {}]}})


def _ensure_path_string_not_numeric(ctx: MutationContext, schema: Schema, old_types: list[str]) -> None:
    """Exclude numeric strings when mutating integer/number to string for path parameters.

    Numeric strings like "7" serialize to the same URL as integer 7,
    making them indistinguishable and causing false positive failures.
    """
    if not ctx.is_path_location:
        return
    if schema.get("type") != "string":
        return
    if "integer" not in old_types and "number" not in old_types:
        return
    # Exclude strings that look like numbers (integers or floats, positive or negative)
    schema["not"] = {"pattern": r"^-?\d+\.?\d*$"}


def _get_type_candidates(ctx: MutationContext, schema: Schema) -> set[str]:
    types = set(get_type(schema))
    if ctx.is_path_location:
        candidates = {"string", "integer", "number", "boolean", "null"} - types
    else:
        candidates = {"string", "integer", "number", "object", "array", "boolean", "null"} - types
    if "integer" in types and "number" in candidates:
        # Do not change "integer" to "number" as any integer is also a number
        candidates.remove("number")
    return candidates


def prevent_unsatisfiable_schema(schema: Schema, new_type: str) -> None:
    """Adjust schema keywords to avoid unsatisfiable schemas."""
    drop_not_type_specific_keywords(schema, new_type)
    if "not" in schema:
        # The "not" sub-schema should be cleaned too
        drop_not_type_specific_keywords(schema["not"], new_type)
        if not schema["not"]:
            del schema["not"]


def drop_not_type_specific_keywords(schema: Schema, new_type: str) -> None:
    """Remove keywords that are not applicable to the defined type."""
    keywords = TYPE_SPECIFIC_KEYS.get(new_type, ())
    for keyword in tuple(schema):
        if keyword not in keywords and keyword not in ANY_TYPE_KEYS:
            schema.pop(keyword, None)


@for_types("object")
def change_properties(
    ctx: MutationContext, draw: Draw, schema: Schema
) -> tuple[MutationResult, MutationMetadata | None]:
    """Mutate individual object schema properties.

    Effect: Some properties will not validate the original schema
    """
    properties = sorted(schema.get("properties", {}).items())
    if not properties:
        # No properties to mutate
        return MutationResult.FAILURE, None
    # Order properties randomly and iterate over them until at least one mutation is successfully applied to at least
    # one property
    ordered_properties = [
        (name, canonicalish(subschema) if isinstance(subschema, bool) else subschema)
        for name, subschema in draw(ordered(properties, unique_by=lambda x: x[0]))
    ]
    nested_metadata = None
    for property_name, property_schema in ordered_properties:
        ctx.ensure_bundle(property_schema)
        result, nested_metadata = apply_until_success(ctx, draw, property_schema)
        if result == MutationResult.SUCCESS:
            # It is still possible to generate "positive" cases, for example, when this property is optional.
            # They are filtered out on the upper level anyway, but to avoid performance penalty we adjust the schema
            # so the generated samples are less likely to be "positive"
            required = schema.setdefault("required", [])
            if property_name not in required:
                required.append(property_name)
            # If `type` is already there, then it should contain "object" as we check it upfront
            # Otherwise restrict `type` to "object"
            if "type" not in schema:
                schema["type"] = "object"
            break
    else:
        # No successful mutations
        return MutationResult.FAILURE, None
    enabled_properties = draw(st.shared(FeatureStrategy(), key="properties"))
    enabled_mutations = draw(st.shared(FeatureStrategy(), key="mutations"))
    for name, property_schema in properties:
        # Skip already mutated property
        if name == property_name:
            # Pylint: `properties` variable has at least one element as it is checked at the beginning of the function
            # Then those properties are ordered and iterated over, therefore `property_name` is always defined
            continue
        if enabled_properties.is_enabled(name):
            ctx.ensure_bundle(property_schema)
            for mutation in get_mutations(draw, property_schema):
                if enabled_mutations.is_enabled(mutation.__name__):
                    mutation(ctx, draw, property_schema)

    # Use nested metadata description if available, otherwise use generic description
    if nested_metadata and nested_metadata.description:
        description = nested_metadata.description
    else:
        description = "Property constraint violated"

    metadata = MutationMetadata(
        parameter=property_name,
        description=description,
        location=f"/properties/{property_name}",
    )
    return MutationResult.SUCCESS, metadata


def apply_until_success(
    ctx: MutationContext, draw: Draw, schema: Schema
) -> tuple[MutationResult, MutationMetadata | None]:
    for mutation in get_mutations(draw, schema):
        result, metadata = mutation(ctx, draw, schema)
        if result == MutationResult.SUCCESS:
            return MutationResult.SUCCESS, metadata
    return MutationResult.FAILURE, None


@for_types("array")
def change_items(ctx: MutationContext, draw: Draw, schema: Schema) -> tuple[MutationResult, MutationMetadata | None]:
    """Mutate individual array items.

    Effect: Some items will not validate the original schema
    """
    items = schema.get("items", {})
    if not items:
        # No items to mutate
        return MutationResult.FAILURE, None
    # For query/path/header/cookie, string items cannot be meaningfully mutated
    # because all types serialize to strings anyway
    if ctx.location.is_in_header or ctx.is_path_location or ctx.is_query_location:
        items = schema.get("items", {})
        if isinstance(items, dict):
            items_types = get_type(items)
            if "string" in items_types:
                return MutationResult.FAILURE, None
    if isinstance(items, dict):
        return _change_items_object(ctx, draw, schema, items)
    if isinstance(items, list):
        return _change_items_array(ctx, draw, schema, items)
    return MutationResult.FAILURE, None


def _change_items_object(
    ctx: MutationContext, draw: Draw, schema: Schema, items: Schema
) -> tuple[MutationResult, MutationMetadata | None]:
    ctx.ensure_bundle(items)
    result = MutationResult.FAILURE
    metadata = None
    for mutation in get_mutations(draw, items):
        mut_result, mut_metadata = mutation(ctx, draw, items)
        result |= mut_result
        if metadata is None and mut_metadata is not None:
            metadata = mut_metadata
    if result == MutationResult.FAILURE:
        return MutationResult.FAILURE, None
    min_items = schema.get("minItems", 0)
    schema["minItems"] = max(min_items, 1)
    # Use nested metadata description if available, update location to show it's in array items
    if metadata:
        metadata = MutationMetadata(
            parameter=None,
            description=f"Array item: {metadata.description}",
            location="/items",
        )
    return MutationResult.SUCCESS, metadata


def _change_items_array(
    ctx: MutationContext, draw: Draw, schema: Schema, items: list
) -> tuple[MutationResult, MutationMetadata | None]:
    latest_success_index = None
    metadata = None
    for idx, item in enumerate(items):
        ctx.ensure_bundle(item)
        result = MutationResult.FAILURE
        for mutation in get_mutations(draw, item):
            mut_result, mut_metadata = mutation(ctx, draw, item)
            result |= mut_result
            if metadata is None and mut_metadata is not None:
                metadata = mut_metadata
        if result == MutationResult.SUCCESS:
            latest_success_index = idx
    if latest_success_index is None:
        return MutationResult.FAILURE, None
    min_items = schema.get("minItems", 0)
    schema["minItems"] = max(min_items, latest_success_index + 1)
    # Use nested metadata description if available, update location to show specific array index
    if metadata:
        metadata = MutationMetadata(
            parameter=None,
            description=f"Array item at index {latest_success_index}: {metadata.description}",
            location=f"/items/{latest_success_index}",
        )
    return MutationResult.SUCCESS, metadata


def negate_constraints(
    ctx: MutationContext, draw: Draw, schema: Schema
) -> tuple[MutationResult, MutationMetadata | None]:
    """Negate schema constrains while keeping the original type."""
    ctx.ensure_bundle(schema)
    if not can_negate(schema):
        return MutationResult.FAILURE, None
    copied = schema.copy()
    # Preserve x-bundled before clearing
    bundled = schema.get(BUNDLE_STORAGE_KEY)
    schema.clear()
    if bundled is not None:
        schema[BUNDLE_STORAGE_KEY] = bundled
    is_negated = False
    negated_keys = []

    def is_mutation_candidate(k: str, v: Any) -> bool:
        # Should we negate this key?
        if k == "required":
            return v != []
        if k in ("example", "examples"):
            return False
        if ctx.is_path_location and k == "minLength" and v == 1:
            # Empty path parameter will be filtered out
            return False
        if (
            not ctx.allow_extra_parameters
            and k == "additionalProperties"
            and ctx.location in (ParameterLocation.QUERY, ParameterLocation.HEADER, ParameterLocation.COOKIE)
        ):
            return False
        return not (
            k in ("type", "properties", "items", "minItems")
            or (k == "additionalProperties" and ctx.location.is_in_header)
        )

    enabled_keywords = draw(st.shared(FeatureStrategy(), key="keywords"))
    candidates = []
    mutation_candidates = [key for key, value in copied.items() if is_mutation_candidate(key, value)]
    if mutation_candidates:
        # There should be at least one mutated keyword
        candidate = draw(st.sampled_from([key for key, value in copied.items() if is_mutation_candidate(key, value)]))
        candidates.append(candidate)
        # If the chosen candidate has dependency, then the dependency should also be present in the final schema
        if candidate in DEPENDENCIES:
            candidates.append(DEPENDENCIES[candidate])
    for key, value in copied.items():
        if is_mutation_candidate(key, value):
            if key in candidates or enabled_keywords.is_enabled(key):
                is_negated = True
                negated_keys.append(key)
                # `format` is handled specially: removing it allows generating arbitrary strings
                # that likely won't match the format. Using `not: {format: ...}` doesn't work
                # because hypothesis-jsonschema treats format as annotation-only.
                if key != "format":
                    negated = schema.setdefault("not", {})
                    negated[key] = value
                    if key in DEPENDENCIES:
                        # If this keyword has a dependency, then it should be also negated
                        dependency = DEPENDENCIES[key]
                        if dependency not in negated and dependency in copied:
                            negated[dependency] = copied[dependency]
        else:
            schema[key] = value
    if is_negated:
        # Build concise description from negated constraints
        descriptions = []
        parameter = None
        for key in negated_keys:
            value = copied[key]
            if key == "required" and len(value) == 1:
                parameter = value[0]
            # Special case: format required properties list nicely with quoted names
            if key == "required" and isinstance(value, list) and len(value) <= 3:
                props = ", ".join(f"`{prop}`" for prop in value)
                descriptions.append(f"`{key}` ({props})")
            else:
                # Default: show `key` (value) for all constraints
                descriptions.append(f"`{key}` ({value})")

        constraint_desc = ", ".join(descriptions)
        metadata = MutationMetadata(
            parameter=parameter,
            description=f"Violates {constraint_desc}",
            location=None,
        )
        return MutationResult.SUCCESS, metadata
    return MutationResult.FAILURE, None


DEPENDENCIES = {"exclusiveMaximum": "maximum", "exclusiveMinimum": "minimum"}


def get_mutations(draw: Draw, schema: JsonSchemaObject) -> tuple[Mutation, ...]:
    """Get mutations possible for a schema."""
    types = get_type(schema)
    # On the top-level of Open API schemas, types are always strings, but inside "schema" objects, they are the same as
    # in JSON Schema, where it could be either a string or an array of strings.
    options: list[Mutation]
    if list(schema) == ["type"]:
        # When there is only `type` in schema then `negate_constraints` is not applicable
        options = [change_type]
    else:
        options = [negate_constraints, change_type]
    if "object" in types:
        options.extend([change_properties, remove_required_property])
    elif "array" in types:
        options.append(change_items)
    return draw(ordered(options))


def ident(x: T) -> T:
    return x


def ordered(items: Sequence[T], unique_by: Callable[[T], Any] = ident) -> st.SearchStrategy[list[T]]:
    """Returns a strategy that generates randomly ordered lists of T.

    NOTE. Items should be unique.
    """
    return st.lists(st.sampled_from(items), min_size=len(items), unique_by=unique_by)
