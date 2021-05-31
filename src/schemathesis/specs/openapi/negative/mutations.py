"""Schema mutations."""
import enum
from copy import deepcopy
from functools import wraps
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple, TypeVar

import attr
from hypothesis import reject
from hypothesis import strategies as st
from hypothesis.strategies._internal.featureflags import FeatureStrategy
from hypothesis_jsonschema._canonicalise import canonicalish

from ..utils import is_header_location, set_keyword_on_properties
from .types import Draw, Schema
from .utils import get_type

T = TypeVar("T")


class MutationResult(enum.Enum):
    """The result of applying some mutation to some schema.

    Failing to mutate something means that by applying some mutation, it is not possible to change
    the schema in the way, so it covers inputs not covered by the "positive" strategy.

    Knowing this, we know when the schema is mutated and whether we need to apply more mutations.
    """

    SUCCESS = 1
    FAILURE = 2

    @property
    def is_success(self) -> bool:
        return self == MutationResult.SUCCESS

    @property
    def is_failure(self) -> bool:
        return self == MutationResult.FAILURE

    def __ior__(self, other: Any) -> "MutationResult":
        return self | other

    def __or__(self, other: Any) -> "MutationResult":
        # Syntactic sugar to simplify handling of multiple results
        if not isinstance(other, MutationResult):
            return NotImplemented
        if self.is_success:
            return self
        return other


Mutation = Callable[["MutationContext", Draw, Schema], MutationResult]
ALL_KEYWORDS = (
    "additionalItems",
    "additionalProperties",
    "allOf",
    "anyOf",
    "const",
    "contains",
    "contentEncoding",
    "contentMediaType",
    "dependencies",
    "enum",
    "else",
    "exclusiveMaximum",
    "exclusiveMinimum",
    "format",
    "if",
    "items",
    "maxItems",
    "maxLength",
    "maxProperties",
    "maximum",
    "minItems",
    "minLength",
    "minProperties",
    "minimum",
    "multipleOf",
    "not",
    "oneOf",
    "pattern",
    "patternProperties",
    "properties",
    "propertyNames",
    "$ref",
    "required",
    "then",
    "type",
    "uniqueItems",
)


@attr.s(slots=True)
class MutationContext:
    """Meta information about the current mutation state."""

    # The original schema
    schema: Schema = attr.ib()
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
            mutations = draw(ordered(get_mutations(draw, self.schema)))
        keywords, non_keywords = split_schema(self.schema)
        # Deep copy all keywords to avoid modifying the original schema
        new_schema = deepcopy(keywords)
        mutator = Mutator()
        enabled_mutations = draw(st.shared(FeatureStrategy(), key="mutations"))  # type: ignore
        result = MutationResult.FAILURE
        for mutation in mutations:
            if mutator.can_apply(mutation) and enabled_mutations.is_enabled(mutation.__name__):
                result |= mutator.apply(self, mutation, draw, new_schema)
        if result.is_failure:
            # If we failed to apply anything, then reject the whole case
            reject()  # type: ignore
        new_schema.update(non_keywords)
        if self.is_header_location:
            new_schema["propertyNames"] = {"format": "_header_name"}
            set_keyword_on_properties(new_schema, type="string", format="_header_value")
            if draw(st.booleans()):
                # In headers, `additionalProperties` are False by default, which means that Schemathesis won't generate
                # any headers that are not defined. This change adds the possibility of generating valid extra headers
                new_schema["additionalProperties"] = {
                    "propertyNames": {"format": "_header_name"},
                    "type": "string",
                    "format": "_header_value",
                }
        return new_schema


def split_schema(schema: Schema) -> Tuple[Schema, Schema]:
    """Split the schema into two parts.

    The first one contains only validation JSON Schema keywords, the second one everything else.
    """
    keywords, non_keywords = {}, {}
    for keyword, value in schema.items():
        if keyword in ALL_KEYWORDS:
            keywords[keyword] = value
        else:
            non_keywords[keyword] = value
    return keywords, non_keywords


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
        # TODO. What about headers defined as non-strings. Changing it to "string" is a valid mutation
        return MutationResult.FAILURE
    candidates = _get_type_candidates(context, schema)
    if not candidates:
        # Schema covers all possible types, not possible to choose something else
        return MutationResult.FAILURE
    if len(candidates) == 1:
        schema["type"] = candidates.pop()
        return MutationResult.SUCCESS
    # Choose one type that will be present in the final candidates list
    candidate = draw(st.sampled_from(sorted(candidates)))
    candidates.remove(candidate)
    enabled_types = draw(st.shared(FeatureStrategy(), key="types"))  # type: ignore
    remaining_candidates = [candidate] + sorted(
        [candidate for candidate in candidates if enabled_types.is_enabled(candidate)]
    )
    schema["type"] = draw(st.sampled_from(remaining_candidates))
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
        if apply_until_success(context, draw, property_schema).is_success:
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
            mutator = Mutator()
            for mutation in get_mutations(draw, property_schema):
                if mutator.can_apply(mutation) and enabled_mutations.is_enabled(mutation.__name__):
                    mutator.apply(context, mutation, draw, property_schema)
    return MutationResult.SUCCESS


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
    # TODO. swarm testing
    mutator = Mutator()
    result = MutationResult.FAILURE
    for mutation in get_mutations(draw, items):
        if mutator.can_apply(mutation):
            result |= mutator.apply(context, mutation, draw, items)
    if result.is_failure:
        return MutationResult.FAILURE
    min_items = schema.get("minItems", 0)
    schema["minItems"] = max(min_items, 1)
    return MutationResult.SUCCESS


def _change_items_array(context: MutationContext, draw: Draw, schema: Schema, items: List) -> MutationResult:
    # TODO. swarm testing
    latest_success_index = None
    for idx, item in enumerate(items):
        mutator = Mutator()
        result = MutationResult.FAILURE
        for mutation in get_mutations(draw, item):
            if mutator.can_apply(mutation):
                result |= mutator.apply(context, mutation, draw, item)
        if result.is_success:
            latest_success_index = idx
    if latest_success_index is None:
        return MutationResult.FAILURE
    min_items = schema.get("minItems", 0)
    schema["minItems"] = max(min_items, latest_success_index + 1)
    return MutationResult.SUCCESS


def apply_until_success(context: MutationContext, draw: Draw, schema: Schema) -> MutationResult:
    for mutation in get_mutations(draw, schema):
        if mutation(context, draw, schema).is_success:
            return MutationResult.SUCCESS
    return MutationResult.FAILURE


def negate_constraints(context: MutationContext, draw: Draw, schema: Schema) -> MutationResult:
    """Negate schema constrains while keeping the original type."""
    if canonicalish(schema) == {}:
        return MutationResult.FAILURE
    copied = schema.copy()
    schema.clear()
    is_negated = False

    def is_mutation_candidate(k: str) -> bool:
        # Should we negate this key?
        return not (
            k in ("type", "properties", "items") or (k == "additionalProperties" and context.is_header_location)
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


def negate_schema(context: MutationContext, draw: Draw, schema: Schema) -> MutationResult:
    """Negate the schema with JSON Schema's `not` keyword.

    It is the least effective mutation as it negates the whole schema without trying to change its small parts.
    """
    if canonicalish(schema) == {}:
        return MutationResult.FAILURE
    if context.is_header_location and "string" in get_type(schema):
        # Can't make headers non-strings
        return MutationResult.FAILURE
    inner = schema.copy()  # Shallow copy is OK
    schema.clear()
    schema["not"] = inner
    if "type" in inner:
        if context.is_path_location:
            # Path should be a primitive object
            inner["type"] = [inner["type"]] if not isinstance(inner["type"], list) else inner["type"]
            for type_ in ("array", "object"):
                if type_ not in inner["type"]:
                    inner["type"].append(type_)
        if context.media_type == "application/x-www-form-urlencoded":
            # Form data should be an object, do not change it
            schema["type"] = inner.pop("type")

    return MutationResult.SUCCESS


def get_mutations(draw: Draw, schema: Schema) -> Tuple[Mutation, ...]:
    """Get mutations possible for a schema."""
    types = get_type(schema)
    # On the top-level of Open API schemas, types are always strings, but inside "schema" objects, they are the same as
    # in JSON Schema, where it could be either a string or an array of strings.
    options: List[Mutation] = [negate_constraints, change_type, negate_schema]
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


ALL_MUTATIONS = {
    remove_required_property,
    change_type,
    change_properties,
    change_items,
    negate_constraints,
    negate_schema,
}
# Some mutations applied to the same schema simultaneously may make the schema accept previously valid values
# Excluding some mutations reduces the amount of filtering required on the level above + increase the variety of
# generated data if applied carefully.
# TODO. Check if order is that important here
# TODO. `remove_required_property` may cancel `change_properties` - need to keep track of what properties were removed
# TODO. negating constraints + change type. If constraints were related only to the old type, then changing the type
#       lead to an unsatisfiable schema
# TODO. Is it possible to verify that excluding certain mutations there will not decrease the amount of possible values?
_INCOMPATIBLE_MUTATIONS = (
    # Schema: {"type": "string", "minLength": 5}
    # Mutated: {"not": {"type": "string", "not": {"minLength": 5}}}
    # Valid example: "ABCDE"
    (negate_schema, negate_constraints),
    # Schema: {"type": "string"}
    # Mutated: {"not": {"type": "array"}}
    # Valid example: "A"
    (negate_schema, change_type),
)

INCOMPATIBLE_MUTATIONS: Dict[Mutation, Set[Mutation]] = {}

for left, right in _INCOMPATIBLE_MUTATIONS:
    INCOMPATIBLE_MUTATIONS.setdefault(left, set()).add(right)
    INCOMPATIBLE_MUTATIONS.setdefault(right, set()).add(left)


@attr.s(slots=True)
class Mutator:
    """Helper to avoid combining incompatible mutations."""

    applicable_mutations: Set[Mutation] = attr.ib(factory=ALL_MUTATIONS.copy)

    def can_apply(self, mutation: Mutation) -> bool:
        """Whether the given mutation can be applied."""
        return mutation in self.applicable_mutations

    def apply(self, context: MutationContext, mutation: Mutation, draw: Draw, schema: Schema) -> MutationResult:
        result = mutation(context, draw, schema)
        # If mutation is successfully applied and has some incompatible ones, exclude them from the future use
        if result.is_success and mutation in INCOMPATIBLE_MUTATIONS:
            self.applicable_mutations -= INCOMPATIBLE_MUTATIONS[mutation]
        return result
