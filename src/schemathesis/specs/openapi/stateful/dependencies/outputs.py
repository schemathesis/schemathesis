from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING

from schemathesis.specs.openapi.adapter.parameters import ParameterLocation
from schemathesis.specs.openapi.stateful.dependencies.models import (
    CanonicalizationCache,
    Cardinality,
    InputSlot,
    OutputSlot,
    ResourceMap,
)
from schemathesis.specs.openapi.stateful.dependencies.resources import extract_resources_from_responses

if TYPE_CHECKING:
    from schemathesis.core.compat import RefResolver
    from schemathesis.specs.openapi.schemas import APIOperation


# HTTP methods whose 2xx response confirms a newly-created resource keyed by
# the trailing path parameter (e.g. `POST /products/{productName}`). PUT is
# excluded because it is far more often an updater than an upserting creator,
# and we don't want a successful update to be mistaken for resource creation.
_PATH_KEYED_PRODUCER_METHODS = frozenset({"post"})


def extract_outputs(
    *,
    operation: APIOperation,
    inputs: list[InputSlot],
    resources: ResourceMap,
    updated_resources: set[str],
    resolver: RefResolver,
    canonicalization_cache: CanonicalizationCache,
) -> Iterator[OutputSlot]:
    """Extract resources from API operation's responses."""
    extracted_resource_names: set[str] = set()
    for response, extracted in extract_resources_from_responses(
        operation=operation,
        resources=resources,
        updated_resources=updated_resources,
        resolver=resolver,
        canonicalization_cache=canonicalization_cache,
    ):
        extracted_resource_names.add(extracted.resource.name)
        yield OutputSlot(
            resource=extracted.resource,
            pointer=extracted.pointer,
            cardinality=extracted.cardinality,
            status_code=response.status_code,
            is_primitive_identifier=extracted.is_primitive_identifier,
        )

    yield from _path_keyed_outputs(
        operation=operation,
        inputs=inputs,
        already_extracted=extracted_resource_names,
    )


def _path_keyed_outputs(
    *,
    operation: APIOperation,
    inputs: list[InputSlot],
    already_extracted: set[str],
) -> Iterator[OutputSlot]:
    """Emit a synthetic output for path-keyed creators with no body schema.

    A 2xx `POST` or `PUT` to a path ending in `{name}` confirms that the
    resource bound to `name` exists. Without this, an empty-response creator
    contributes no producer edges and downstream operations on the same
    resource never get linked.
    """
    if operation.method.lower() not in _PATH_KEYED_PRODUCER_METHODS:
        return

    trailing = _trailing_path_parameter(operation.path)
    if trailing is None:
        return

    matching = next(
        (
            slot
            for slot in inputs
            if slot.parameter_location == ParameterLocation.PATH and slot.parameter_name == trailing
        ),
        None,
    )
    if matching is None:
        return

    if matching.resource.name in already_extracted:
        return

    success_status = next(
        (response.status_code for response in operation.responses.iter_successful_responses()),
        None,
    )
    if success_status is None:
        return

    yield OutputSlot(
        resource=matching.resource,
        pointer="",
        cardinality=Cardinality.ONE,
        status_code=success_status,
        path_parameter=trailing,
    )


def _trailing_path_parameter(path: str) -> str | None:
    last = path.rstrip("/").rsplit("/", 1)[-1]
    if last.startswith("{") and last.endswith("}"):
        return last[1:-1]
    return None
