from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING

from schemathesis.specs.openapi.stateful.dependencies.models import CanonicalizationCache, OutputSlot, ResourceMap
from schemathesis.specs.openapi.stateful.dependencies.resources import extract_resources_from_responses

if TYPE_CHECKING:
    from schemathesis.core.compat import RefResolver
    from schemathesis.specs.openapi.schemas import APIOperation


def extract_outputs(
    *,
    operation: APIOperation,
    resources: ResourceMap,
    updated_resources: set[str],
    resolver: RefResolver,
    canonicalization_cache: CanonicalizationCache,
) -> Iterator[OutputSlot]:
    """Extract resources from API operation's responses."""
    for response, extracted in extract_resources_from_responses(
        operation=operation,
        resources=resources,
        updated_resources=updated_resources,
        resolver=resolver,
        canonicalization_cache=canonicalization_cache,
    ):
        yield OutputSlot(
            resource=extracted.resource,
            pointer=extracted.pointer,
            cardinality=extracted.cardinality,
            status_code=response.status_code,
        )
