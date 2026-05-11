from schemathesis.core.error_feedback import ErrorFeedbackStore
from schemathesis.core.error_feedback.store import Observation, ObservationKind
from schemathesis.core.parameters import ParameterLocation
from schemathesis.specs.openapi.error_feedback import apply_adjustments
from schemathesis.specs.openapi.negative.mutations import compute_mutation_targets


def test_mutation_targets_cache_across_calls(ctx):
    schema = ctx.openapi.load_schema(
        {
            "/items": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["x"],
                                    "properties": {"x": {"type": "string", "pattern": "^a$"}},
                                }
                            }
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    body = next(schema.get_all_operations()).ok().body[0]
    assert body.mutation_targets is body.mutation_targets


def test_error_feedback_adjustment_grows_descriptor_set(ctx):
    # A 4xx-observed undeclared field gets synthesized into the schema; the adjusted
    # schema must expose it as a mutation target, not the cached pre-adjustment one.
    schema = ctx.openapi.load_schema(
        {
            "/items": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"x": {"type": "string"}},
                                }
                            }
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    operation = next(schema.get_all_operations()).ok()
    body = operation.body[0]

    feedback = ErrorFeedbackStore()
    observation = Observation(
        operation_label=operation.label,
        location=ParameterLocation.BODY,
        parameter_path=("y",),
        kind=ObservationKind.MUST_NOT_BE_BLANK,
        raw_message="y must not be blank",
    )
    feedback.record(observation)

    original_descriptors = compute_mutation_targets(body.optimized_schema)
    adjusted_schema = apply_adjustments(
        operation=operation,
        location=ParameterLocation.BODY,
        schema=body.optimized_schema,
        store=feedback,
    )
    adjusted_descriptors = compute_mutation_targets(adjusted_schema)

    assert adjusted_schema is not body.optimized_schema  # adjustment fired
    assert "y" in adjusted_schema.get("properties", {})
    assert len(adjusted_descriptors) > len(original_descriptors)  # synthesized target is reachable
