from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING

from schemathesis.core.jsonschema.resolver import Resolver
from schemathesis.specs.openapi.adapter.parameters import ParameterLocation
from schemathesis.specs.openapi.stateful.dependencies import naming
from schemathesis.specs.openapi.stateful.dependencies.models import (
    CanonicalizationCache,
    Cardinality,
    InputSlot,
    OutputSlot,
    ResourceMap,
)
from schemathesis.specs.openapi.stateful.dependencies.resources import (
    ResponseResourceCache,
    cached_resources_from_responses,
)

if TYPE_CHECKING:
    from schemathesis.specs.openapi.schemas import APIOperation


# HTTP methods whose 2xx response confirms a newly-created resource keyed by
# the trailing path parameter (e.g. `POST /products/{productName}`). PUT is
# usually an updater, so it only counts when nothing POSTs to the parent collection.
_PATH_KEYED_PRODUCER_METHODS = frozenset({"post"})


def extract_outputs(
    *,
    operation: APIOperation,
    inputs: list[InputSlot],
    resources: ResourceMap,
    updated_resources: set[str],
    resolver: Resolver,
    canonicalization_cache: CanonicalizationCache,
    response_resource_cache: ResponseResourceCache,
) -> Iterator[OutputSlot]:
    """Extract resources from API operation's responses."""
    extracted_resource_names: set[str] = set()
    for response, extracted in cached_resources_from_responses(
        operation=operation,
        resources=resources,
        updated_resources=updated_resources,
        resolver=resolver,
        canonicalization_cache=canonicalization_cache,
        cache=response_resource_cache,
    ):
        extracted_resource_names.add(extracted.resource.name)
        yield OutputSlot(
            resource=extracted.resource,
            pointer=extracted.pointer,
            cardinality=extracted.cardinality,
            status_code=response.status_code,
            is_primitive_identifier=extracted.is_primitive_identifier,
            extract_object_keys=extracted.extract_object_keys,
            response_fields=extracted.response_fields,
            identifier_pointer=extracted.identifier_pointer,
        )

    yield from _path_keyed_outputs(
        operation=operation,
        inputs=inputs,
        already_extracted=extracted_resource_names,
    )

    yield from _body_keyed_outputs(
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
    trailing = naming.trailing_path_parameter(operation.path)
    if trailing is None:
        return

    method = operation.method.lower()
    if method not in _PATH_KEYED_PRODUCER_METHODS and not (
        method == "put" and not _has_post_creator(operation, trailing)
    ):
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


def _has_post_creator(operation: APIOperation, trailing: str) -> bool:
    # With a `POST` on the parent collection, `PUT` on the item is an updater; without one, it is the creator.
    parent = operation.path.rstrip("/")[: -len(f"/{{{trailing}}}")]
    paths = operation.schema.raw_schema.get("paths", {})
    return any("post" in item for path, item in paths.items() if path.rstrip("/") == parent)


def _body_keyed_outputs(
    *,
    operation: APIOperation,
    inputs: list[InputSlot],
    already_extracted: set[str],
) -> Iterator[OutputSlot]:
    """Emit a synthetic output for body-keyed creators with no response body.

    A 2xx `POST` to `/collection` whose request body carries an identifier-
    shaped field for the path-derived resource (e.g. `POST /sessions` with
    body `{sessionId: ...}`) confirms the resource exists once the request
    succeeds. Without this, the dependency graph has no producer edge for
    the resource and consumer operations get layered before the creator.
    """
    if operation.method.lower() not in _PATH_KEYED_PRODUCER_METHODS:
        return
    # Skip operations that already have a path-keyed identifier; those are
    # handled by `_path_keyed_outputs` and don't need a body-keyed echo.
    if naming.trailing_path_parameter(operation.path) is not None:
        return

    path_resource = naming.from_path(operation.path)
    if path_resource is None:
        return

    matching = next(
        (
            slot
            for slot in inputs
            if slot.parameter_location == ParameterLocation.BODY
            and isinstance(slot.parameter_name, str)
            and slot.resource.name == path_resource
        ),
        None,
    )
    if matching is None or not isinstance(matching.parameter_name, str):
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
        body_field=matching.parameter_name,
    )
