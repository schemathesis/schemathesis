"""Schema mutations."""
import enum
from copy import deepcopy
from functools import wraps
from typing import Any, Callable, List, Optional, Sequence, Set, Tuple, TypeVar

import attr
from hypothesis import reject
from hypothesis import strategies as st
from hypothesis.strategies._internal.featureflags import FeatureStrategy

from ..utils import is_header_location
from .types import Draw, Schema
from .utils import can_negate, get_type

T = TypeVar("T")


class MutationResult(enum.Enum):
    """The result of applying some mutation to some schema.

    Failing to mutate something means that by applying some mutation, it is not possible to change
    the schema in the way, so it covers inputs not covered by the "positive" strategy.

    Knowing this, we know when the schema is mutated and whether we need to apply more mutations.
    """

    SUCCESS = 1
    FAILURE = 2

    def __ior__(self, other: Any) -> "MutationResult":
        return self | other

    def __or__(self, other: Any) -> "MutationResult":
        # Syntactic sugar to simplify handling of multiple results
        if self == MutationResult.SUCCESS:
            return self
        return other


Mutation = Callable[["MutationContext", Draw, Schema], MutationResult]
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


@attr.s(slots=True)
class MutationContext:
    """Meta information about the current mutation state."""

    # The original schema
    keywords: Schema = attr.ib()  # only keywords
    non_keywords: Schema = attr.ib()  # everything else
    # Schema location within API operation (header, query, etc)
    location: str = attr.ib()
    # Payload media type, if available
    media_type: Optional[str] = attr.ib()

    @property
    def is_header_location(self) -> bool:
        return is_header_location(self.location)

    @property
    def is_path_location(self) -> bool:
        return self.location == "path"

    def mutate(self, draw: Draw) -> Schema:
        # On the top level, Schemathesis creates "object" schemas for all parameter "in" values except "body", which is
        # taken as-is. Therefore we can only apply mutations that won't change the Open API semantics of the schema.
        mutations: List[Mutation]
        if self.location in ("header", "cookie", "query"):
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
        new_schema = deepcopy(self.keywords)
        enabled_mutations = draw(st.shared(FeatureStrategy(), key="mutations"))  # type: ignore
        result = MutationResult.FAILURE
        for mutation in mutations:
            if enabled_mutations.is_enabled(mutation.__name__):
                result |= mutation(self, draw, new_schema)
        if result == MutationResult.FAILURE:
            # If we failed to apply anything, then reject the whole case
            reject()  # type: ignore
        new_schema.update(self.non_keywords)
        if self.is_header_location:
            # All headers should have names that can be sent over network
            new_schema["propertyNames"] = {"type": "string", "format": "_header_name"}
            for sub_schema in new_schema.get("properties", {}).values():
                sub_schema["type"] = "string"
                if len(sub_schema) == 1:
                    sub_schema["format"] = "_header_value"
            if draw(st.booleans()):
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
        return new_schema


def for_types(*allowed_types: str) -> Callable[[Mutation], Mutation]:
    """Immediately return FAILURE for schemas with types not from ``allowed_types``."""

    _allowed_types = set(allowed_types)

    def wrapper(mutation: Mutation) -> Mutation:
        @wraps(mutation)
        def inner(context: MutationContext, draw: Draw, schema: Schema) -> MutationResult:
            types = get_type(schema)
            if _allowed_types & set(types):
                return mutation(context, draw, schema)
            return MutationResult.FAILURE

        return inner

    return wrapper


@for_types("object")
def remove_required_property(context: MutationContext, draw: Draw, schema: Schema) -> MutationResult:
    """Remove a required property.

    Effect: Some property won't be generated.
    """
    required = schema.get("required")
    if not required:
        # No required properties - can't mutate
        return MutationResult.FAILURE
    if len(required) == 1:
        property_name = draw(st.sampled_from(sorted(required)))
    else:
        candidate = draw(st.sampled_from(sorted(required)))
        enabled_properties = draw(st.shared(FeatureStrategy(), key="properties"))  # type: ignore
        candidates = [candidate] + sorted([prop for prop in required if enabled_properties.is_enabled(prop)])
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
    return MutationResult.SUCCESS


def change_type(context: MutationContext, draw: Draw, schema: Schema) -> MutationResult:
    """Change type of values accepted by a schema."""
    if "type" not in schema:
        # The absence of this keyword means that the schema values can be of any type;
        # Therefore, we can't choose a different type
        return MutationResult.FAILURE
    if context.media_type == "application/x-www-form-urlencoded":
        # Form data should be an object, do not change it
        return MutationResult.FAILURE
    if context.is_header_location:
        return MutationResult.FAILURE
    candidates = _get_type_candidates(context, schema)
    if not candidates:
        # Schema covers all possible types, not possible to choose something else
        return MutationResult.FAILURE
    if len(candidates) == 1:
        new_type = candidates.pop()
        schema["type"] = new_type
        prevent_unsatisfiable_schema(schema, new_type)
        return MutationResult.SUCCESS
    # Choose one type that will be present in the final candidates list
    candidate = draw(st.sampled_from(sorted(candidates)))
    candidates.remove(candidate)
    enabled_types = draw(st.shared(FeatureStrategy(), key="types"))  # type: ignore
    remaining_candidates = [candidate] + sorted(
        [candidate for candidate in candidates if enabled_types.is_enabled(candidate)]
    )
    new_type = draw(st.sampled_from(remaining_candidates))
    schema["type"] = new_type
    prevent_unsatisfiable_schema(schema, new_type)
    return MutationResult.SUCCESS


def _get_type_candidates(context: MutationContext, schema: Schema) -> Set[str]:
    types = set(get_type(schema))
    if context.is_path_location:
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
def change_properties(context: MutationContext, draw: Draw, schema: Schema) -> MutationResult:
    """Mutate individual object schema properties.

    Effect: Some properties will not validate the original schema
    """
    properties = sorted(schema.get("properties", {}).items())
    if not properties:
        # No properties to mutate
        return MutationResult.FAILURE
    # Order properties randomly and iterate over them until at least one mutation is successfully applied to at least
    # one property
    ordered_properties = draw(ordered(properties, unique_by=lambda x: x[0]))
    for property_name, property_schema in ordered_properties:
        if apply_until_success(context, draw, property_schema) == MutationResult.SUCCESS:
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
        return MutationResult.FAILURE
    enabled_properties = draw(st.shared(FeatureStrategy(), key="properties"))  # type: ignore
    enabled_mutations = draw(st.shared(FeatureStrategy(), key="mutations"))  # type: ignore
    for name, property_schema in properties:
        # Skip already mutated property
        if name == property_name:  # pylint: disable=undefined-loop-variable
            # Pylint: `properties` variable has at least one element as it is checked at the beginning of the function
            # Then those properties are ordered and iterated over, therefore `property_name` is always defined
            continue
        if enabled_properties.is_enabled(name):
            for mutation in get_mutations(draw, property_schema):
                if enabled_mutations.is_enabled(mutation.__name__):
                    mutation(context, draw, property_schema)
    return MutationResult.SUCCESS


def apply_until_success(context: MutationContext, draw: Draw, schema: Schema) -> MutationResult:
    for mutation in get_mutations(draw, schema):
        if mutation(context, draw, schema) == MutationResult.SUCCESS:
            return MutationResult.SUCCESS
    return MutationResult.FAILURE


@for_types("array")
def change_items(context: MutationContext, draw: Draw, schema: Schema) -> MutationResult:
    """Mutate individual array items.

    Effect: Some items will not validate the original schema
    """
    items = schema.get("items", {})
    if not items:
        # No items to mutate
        return MutationResult.FAILURE
    if isinstance(items, dict):
        return _change_items_object(context, draw, schema, items)
    if isinstance(items, list):
        return _change_items_array(context, draw, schema, items)
    return MutationResult.FAILURE


def _change_items_object(context: MutationContext, draw: Draw, schema: Schema, items: Schema) -> MutationResult:
    result = MutationResult.FAILURE
    for mutation in get_mutations(draw, items):
        result |= mutation(context, draw, items)
    if result == MutationResult.FAILURE:
        return MutationResult.FAILURE
    min_items = schema.get("minItems", 0)
    schema["minItems"] = max(min_items, 1)
    return MutationResult.SUCCESS


def _change_items_array(context: MutationContext, draw: Draw, schema: Schema, items: List) -> MutationResult:
    latest_success_index = None
    for idx, item in enumerate(items):
        result = MutationResult.FAILURE
        for mutation in get_mutations(draw, item):
            result |= mutation(context, draw, item)
        if result == MutationResult.SUCCESS:
            latest_success_index = idx
    if latest_success_index is None:
        return MutationResult.FAILURE
    min_items = schema.get("minItems", 0)
    schema["minItems"] = max(min_items, latest_success_index + 1)
    return MutationResult.SUCCESS


def negate_constraints(context: MutationContext, draw: Draw, schema: Schema) -> MutationResult:
    """Negate schema constrains while keeping the original type."""
    if not can_negate(schema):
        return MutationResult.FAILURE
    copied = schema.copy()
    schema.clear()
    is_negated = False

    def is_mutation_candidate(k: str) -> bool:
        # Should we negate this key?
        return not (
            k in ("type", "properties", "items", "minItems")
            or (k == "additionalProperties" and context.is_header_location)
        )

    enabled_keywords = draw(st.shared(FeatureStrategy(), key="keywords"))  # type: ignore
    candidates = []
    mutation_candidates = [key for key in copied if is_mutation_candidate(key)]
    if mutation_candidates:
        # There should be at least one mutated keyword
        candidate = draw(st.sampled_from([key for key in copied if is_mutation_candidate(key)]))
        candidates.append(candidate)
        # If the chosen candidate has dependency, then the dependency should also be present in the final schema
        if candidate in DEPENDENCIES:
            candidates.append(DEPENDENCIES[candidate])
    for key, value in copied.items():
        if is_mutation_candidate(key):
            if key in candidates or enabled_keywords.is_enabled(key):
                is_negated = True
                negated = schema.setdefault("not", {})
                negated[key] = value
                if key in DEPENDENCIES:
                    # If this keyword has a dependency, then it should be also negated
                    dependency = DEPENDENCIES[key]
                    if dependency not in negated:
                        negated[dependency] = copied[dependency]  # Assuming the schema is valid
        else:
            schema[key] = value
    if is_negated:
        return MutationResult.SUCCESS
    return MutationResult.FAILURE


DEPENDENCIES = {"exclusiveMaximum": "maximum", "exclusiveMinimum": "minimum"}


def get_mutations(draw: Draw, schema: Schema) -> Tuple[Mutation, ...]:
    """Get mutations possible for a schema."""
    types = get_type(schema)
    # On the top-level of Open API schemas, types are always strings, but inside "schema" objects, they are the same as
    # in JSON Schema, where it could be either a string or an array of strings.
    options: List[Mutation] = [negate_constraints, change_type]
    if "object" in types:
        options.extend([change_properties, remove_required_property])
    elif "array" in types:
        options.append(change_items)
    return draw(ordered(options))


def ident(x: T) -> T:
    return x


def ordered(items: Sequence[T], unique_by: Callable[[T], Any] = ident) -> st.SearchStrategy[List[T]]:
    """Returns a strategy that generates randomly ordered lists of T.

    NOTE. Items should be unique.
    """
    return st.lists(st.sampled_from(items), min_size=len(items), unique_by=unique_by)
