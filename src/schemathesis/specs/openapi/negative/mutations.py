"""Schema mutations."""
import enum
from typing import Callable, Tuple

from hypothesis import strategies as st
from hypothesis.strategies._internal.featureflags import FeatureStrategy
from hypothesis_jsonschema._canonicalise import canonicalish

from .types import Draw, Schema
from .utils import get_type


class MutationResult(enum.Enum):
    """The result of applying some mutation to some schema.

    Failing to mutate something means that by applying some mutation, it is not possible to change
    the schema in the way, so it covers inputs not covered by the "positive" strategy.

    Knowing this, we know when the schema is mutated and whether we need to apply more mutations.
    """

    SUCCESS = 1
    FAILURE = 2


Mutation = Callable[[Draw, Schema], MutationResult]


def remove_required_property(draw: Draw, schema: Schema) -> MutationResult:
    """Remove a required property.

    Effect: Some property won't be generated.
    Applicable types: object
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
    # This property still can be generated via `patternProperties`, but this implementation doesn't cover this case
    # Its probability is relatively low, and the complete solution compatible with Draft 4 will require extra complexity
    # The output filter covers cases like this
    return MutationResult.SUCCESS


def change_schema_type(draw: Draw, schema: Schema) -> MutationResult:
    """Change type of values accepted by a schema.

    Applicable types: any
    """
    if "type" not in schema:
        # The absence of this keyword means that the schema values can be of any type;
        # Therefore, we can't choose a different type
        return MutationResult.FAILURE
    types = set(get_type(schema))
    candidates = {"string", "integer", "number", "object", "array", "boolean", "null"} - types
    if not candidates:
        # Schema covers all possible types, not possible to choose something else
        return MutationResult.FAILURE
    # TODO. apply swarm testing here, but avoid FAILURE result
    # otherwise, it will be possible to not have any mutations at all on the top level
    schema["type"] = draw(st.sampled_from(sorted(candidates)))
    return MutationResult.SUCCESS


def change_properties(draw: Draw, schema: Schema) -> MutationResult:
    """Mutate individual object schema properties.

    Effect: Some properties will not validate the original schema
    Applicable types: object
    """
    properties = sorted(schema.get("properties", {}).items())
    if not properties:
        # No properties to mutate
        return MutationResult.FAILURE
    # TODO. make all mutated properties required - otherwise, it is possible that the effect of successful mutations
    # will not be visible in the generated data.
    # Order properties randomly and iterate over them until at least one mutation is successfully applied to at least
    # one property
    ordered_properties = draw(st.lists(st.sampled_from(properties), min_size=len(properties), unique_by=lambda x: x[0]))
    for property_name, property_schema in ordered_properties:
        if apply_mutations(draw, property_schema) == MutationResult.SUCCESS:
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
            for mutation in get_mutations(property_schema):
                if features.is_enabled(mutation.__name__):
                    mutation(draw, property_schema)
    return MutationResult.SUCCESS


def apply_mutations(draw: Draw, schema: Schema) -> MutationResult:
    for mutation in get_mutations(schema):
        if mutation(draw, schema) == MutationResult.SUCCESS:
            return MutationResult.SUCCESS
    return MutationResult.FAILURE


def negate_constraints(draw: Draw, schema: Schema) -> MutationResult:
    """Negate schema constrains while keeping the original type."""
    copied = schema.copy()
    schema.clear()
    is_negated = False
    for key, value in copied.items():
        if key in ("type",):  # TODO. more?
            schema[key] = value
        else:
            # TODO. Swarm testing to negate only certain keywords?
            is_negated = True
            negated = schema.setdefault("not", {})
            negated[key] = value
    if is_negated:
        return MutationResult.SUCCESS
    return MutationResult.FAILURE


def negate_schema(draw: Draw, schema: Schema) -> MutationResult:
    """Negate the schema with JSON Schema's `not` keyword.

    It is the least effective mutation as it negates the whole schema without trying to change its small parts.

    Applicable types: any
    """
    if canonicalish(schema) == {}:
        return MutationResult.FAILURE
    inner = schema.copy()  # Shallow copy is OK
    schema.clear()
    schema["not"] = inner
    return MutationResult.SUCCESS


def get_mutations(schema: Schema) -> Tuple[Mutation, ...]:
    """Get mutations possible for a schema.

    Mutations are sorted by their anticipated effectiveness. The ordering is essential when we need to unconditionally
    apply at least one mutation.
    """
    types = get_type(schema)
    # On the top-level of Open API schemas, types are always strings, but inside "schema" objects, they are the same as
    # in JSON Schema, where it could be either a string or an array of strings.
    # TODO. How to handle multiple types? Maybe each mutation can have a guard that will return FAILURE on a type that
    # is not applicable
    if "object" in types:
        return change_properties, negate_constraints, remove_required_property, change_schema_type, negate_schema
    # TODO. add some for arrays - mutate "items" (if an object) or particular items (if an array)?
    return negate_constraints, change_schema_type, negate_schema
