"""Schema mutations."""
import enum
from functools import wraps
from typing import Any, Callable, Dict, List, Sequence, Set, Tuple, TypeVar

import attr
from hypothesis import strategies as st
from hypothesis.strategies._internal.featureflags import FeatureStrategy
from hypothesis_jsonschema._canonicalise import canonicalish

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


Mutation = Callable[[Draw, Schema, str], MutationResult]


def for_types(*allowed_types: str) -> Callable[[Mutation], Mutation]:
    """Immediately return FAILURE for schemas with types not from ``allowed_types``."""

    _allowed_types = set(allowed_types)

    def wrapper(mutation: Mutation) -> Mutation:
        @wraps(mutation)
        def inner(draw: Draw, schema: Schema, location: str) -> MutationResult:
            types = get_type(schema)
            if _allowed_types & set(types):
                return mutation(draw, schema, location)
            return MutationResult.FAILURE

        return inner

    return wrapper


@for_types("object")
def remove_required_property(draw: Draw, schema: Schema, location: str) -> MutationResult:
    """Remove a required property.

    Effect: Some property won't be generated.
    """
    required = schema.get("required")
    if not required:
        # No required properties - can't mutate
        return MutationResult.FAILURE
    # TODO: apply swarm testing here
    property_name = draw(st.sampled_from(sorted(required)))
    required.remove(property_name)
    if not required:
        # In JSON Schema Draft 4, `required` must contain at least one string
        # To keep the schema conformant, remove the `required` key completely
        del schema["required"]
    # An optional property still can be generated, and to avoid it, we need to remove it from other keywords.
    properties = schema.get("properties", {})
    properties.pop(property_name, None)
    schema["type"] = "object"
    # This property still can be generated via `patternProperties`, but this implementation doesn't cover this case
    # Its probability is relatively low, and the complete solution compatible with Draft 4 will require extra complexity
    # The output filter covers cases like this
    return MutationResult.SUCCESS


def change_type(draw: Draw, schema: Schema, location: str) -> MutationResult:
    """Change type of values accepted by a schema."""
    if "type" not in schema:
        # The absence of this keyword means that the schema values can be of any type;
        # Therefore, we can't choose a different type
        return MutationResult.FAILURE
    candidates = _get_type_candidates(schema, location)
    if not candidates:
        # Schema covers all possible types, not possible to choose something else
        return MutationResult.FAILURE
    # TODO. apply swarm testing here, but avoid FAILURE result
    # otherwise, it will be possible to not have any mutations at all on the top level
    schema["type"] = draw(st.sampled_from(sorted(candidates)))
    return MutationResult.SUCCESS


def _get_type_candidates(schema: Schema, location: str) -> Set[str]:
    types = set(get_type(schema))
    if location == "path":
        candidates = {"string", "integer", "number", "boolean", "null"} - types
    else:
        candidates = {"string", "integer", "number", "object", "array", "boolean", "null"} - types
    if "integer" in types and "number" in candidates:
        # Do not change "integer" to "number" as any integer is also a number
        candidates.remove("number")
    return candidates


@for_types("object")
def change_properties(draw: Draw, schema: Schema, location: str) -> MutationResult:
    """Mutate individual object schema properties.

    Effect: Some properties will not validate the original schema
    """
    properties = sorted(schema.get("properties", {}).items())
    if not properties:
        # TODO. check boolean schemas
        # No properties to mutate
        return MutationResult.FAILURE
    # Order properties randomly and iterate over them until at least one mutation is successfully applied to at least
    # one property
    ordered_properties = draw(ordered(properties, unique_by=lambda x: x[0]))
    for property_name, property_schema in ordered_properties:
        if apply_mutations(draw, property_schema, location) == MutationResult.SUCCESS:
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
    features = draw(st.shared(FeatureStrategy(), key="properties"))  # type: ignore
    for name, property_schema in properties:
        # Skip already mutated property
        if name == property_name:  # pylint: disable=undefined-loop-variable
            # Pylint: `properties` variable has at least one element as it is checked at the beginning of the function
            # Then those properties are ordered and iterated over, therefore `property_name` is always defined
            continue
        # The `features` strategy is reused for property names and mutation names for simplicity as it is not likely
        # to have an overlap between them.
        if features.is_enabled(name):
            mutator = Mutator()
            for mutation in get_mutations(draw, property_schema):
                if mutator.can_apply(mutation) and features.is_enabled(mutation.__name__):
                    mutator.apply(mutation, draw, property_schema, location)
    return MutationResult.SUCCESS


@for_types("array")
def change_items(draw: Draw, schema: Schema, location: str) -> MutationResult:
    """Mutate individual array items.

    Effect: Some items will not validate the original schema
    """
    items = schema.get("items", {})
    if items is False:
        # As any items were forbidden, allowing at least one item of any type is a successful negation
        schema["items"] = {}
        min_items = schema.get("minItems", 0)
        schema["minItems"] = max(min_items, 1)
        return MutationResult.SUCCESS
    if not items:
        # No items to mutate
        return MutationResult.FAILURE
    if isinstance(items, dict):
        return _change_items_object(draw, schema, items, location)
    if isinstance(items, list):
        return _change_items_array(draw, schema, items, location)
    # `True` and invalid schemas go here
    return MutationResult.FAILURE


def _change_items_object(draw: Draw, schema: Schema, items: Schema, location: str) -> MutationResult:
    # TODO. swarm testing
    mutator = Mutator()
    result = MutationResult.FAILURE
    for mutation in get_mutations(draw, items):
        if mutator.can_apply(mutation):
            if mutator.apply(mutation, draw, items, location) == MutationResult.SUCCESS:
                result = MutationResult.SUCCESS
    if result == MutationResult.FAILURE:
        return MutationResult.FAILURE
    min_items = schema.get("minItems", 0)
    schema["minItems"] = max(min_items, 1)
    return MutationResult.SUCCESS


def _change_items_array(draw: Draw, schema: Schema, items: List, location: str) -> MutationResult:
    # TODO. swarm testing
    latest_success_index = None
    for idx, item in enumerate(items):
        mutator = Mutator()
        result = MutationResult.FAILURE
        for mutation in get_mutations(draw, item):
            if mutator.can_apply(mutation):
                if mutator.apply(mutation, draw, item, location) == MutationResult.SUCCESS:
                    result = MutationResult.SUCCESS
        if result == MutationResult.SUCCESS:
            latest_success_index = idx
    if latest_success_index is None:
        return MutationResult.FAILURE
    min_items = schema.get("minItems", 0)
    schema["minItems"] = max(min_items, latest_success_index + 1)
    return MutationResult.SUCCESS


def apply_mutations(draw: Draw, schema: Schema, location: str) -> MutationResult:
    for mutation in get_mutations(draw, schema):
        if mutation(draw, schema, location) == MutationResult.SUCCESS:
            return MutationResult.SUCCESS
    return MutationResult.FAILURE


def negate_constraints(draw: Draw, schema: Schema, location: str) -> MutationResult:
    """Negate schema constrains while keeping the original type."""
    if canonicalish(schema) == {}:
        return MutationResult.FAILURE
    copied = schema.copy()
    schema.clear()
    is_negated = False
    for key, value in copied.items():
        if key in ("type", "properties"):  # TODO. more? items when array mutations are implemented
            schema[key] = value
        else:
            # TODO. Swarm testing to negate only certain keywords?
            is_negated = True
            negated = schema.setdefault("not", {})
            negated[key] = value
    # TODO. should empty string be generated for path parameters?
    if is_negated:
        return MutationResult.SUCCESS
    return MutationResult.FAILURE


def negate_schema(draw: Draw, schema: Schema, location: str) -> MutationResult:
    """Negate the schema with JSON Schema's `not` keyword.

    It is the least effective mutation as it negates the whole schema without trying to change its small parts.
    """
    if canonicalish(schema) == {}:
        return MutationResult.FAILURE
    inner = schema.copy()  # Shallow copy is OK
    schema.clear()
    schema["not"] = inner
    if location == "path" and "type" in inner:
        # Path should be a primitive object
        inner["type"] = [inner["type"]] if not isinstance(inner["type"], list) else inner["type"]
        for type_ in ("array", "object"):
            if type_ not in inner["type"]:
                inner["type"].append(type_)
    return MutationResult.SUCCESS


def get_mutations(draw: Draw, schema: Schema) -> Tuple[Mutation, ...]:
    """Get mutations possible for a schema."""
    types = get_type(schema)
    # On the top-level of Open API schemas, types are always strings, but inside "schema" objects, they are the same as
    # in JSON Schema, where it could be either a string or an array of strings.
    # TODO. How to handle multiple types?
    if "object" in types:
        options = [change_properties, negate_constraints, remove_required_property, change_type, negate_schema]
    elif "array" in types:
        options = [change_items, negate_constraints, change_type, negate_schema]
    else:
        options = [negate_constraints, change_type, negate_schema]
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

    def apply(self, mutation: Mutation, draw: Draw, schema: Schema, location: str) -> MutationResult:
        # TODO maybe return FAILURE if it can't be applied?
        result = mutation(draw, schema, location)
        # If mutation is successfully applied and has some incompatible ones, exclude them from the future use
        if result == MutationResult.SUCCESS and mutation in INCOMPATIBLE_MUTATIONS:
            self.applicable_mutations -= INCOMPATIBLE_MUTATIONS[mutation]
        return result
