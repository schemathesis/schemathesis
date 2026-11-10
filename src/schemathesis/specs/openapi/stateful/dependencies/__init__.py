"""Dependency detection between API operations for stateful testing.

Infers which operations must run before others by tracking resource creation and consumption across API operations.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeGuard

from schemathesis.core import NOT_SET
from schemathesis.core.errors import InvalidSchema, OperationNotFound, RefResolutionError
from schemathesis.core.jsonschema.types import JsonSchema, JsonSchemaObject, get_type
from schemathesis.core.result import Ok
from schemathesis.core.transforms import format_body_key
from schemathesis.specs.openapi.adapter.parameters import ParameterLocation
from schemathesis.specs.openapi.adapter.references import maybe_resolve_with_resolver
from schemathesis.specs.openapi.adapter.responses import OpenApiResponses
from schemathesis.specs.openapi.stateful.dependencies import naming
from schemathesis.specs.openapi.stateful.dependencies.inputs import (
    align_created_identifiers,
    bind_created_item_keys,
    bind_echoed_body_fields,
    bind_id_fields_to_returned_resources,
    bind_item_parameters_to_collections,
    bind_parent_keys_to_item_fields,
    declared_parameter_types,
    disambiguate_module_variants,
    disambiguate_path_suffix_matches,
    extract_inputs,
    merge_related_resources,
    rebind_body_fields_to_unique_producers,
    rebind_inherited_suffix_matches,
    rebind_orphan_synthetics,
    unbind_mistyped_values,
    update_input_field_bindings,
)
from schemathesis.specs.openapi.stateful.dependencies.models import (
    Cardinality,
    DeferredBindings,
    DefinitionSource,
    DependencyGraph,
    InputSlot,
    NormalizedLink,
    OperationMap,
    OperationNode,
    OutputSlot,
    ResourceDefinition,
    ResourceMap,
    path_shape,
)
from schemathesis.specs.openapi.stateful.dependencies.outputs import collections_with_post, extract_outputs
from schemathesis.specs.openapi.stateful.dependencies.resources import (
    InferenceState,
    referenced_schema_names,
    remove_unused_resources,
)
from schemathesis.specs.openapi.stateful.links import find_link_target

if TYPE_CHECKING:
    from schemathesis.core.jsonschema.resolver import Resolver
    from schemathesis.schemas import APIOperation
    from schemathesis.specs.openapi.schemas import OpenApiSchema

__all__ = [
    "analyze",
    "inject_links",
    "DependencyGraph",
    "InputSlot",
]


def analyze(schema: OpenApiSchema) -> DependencyGraph:
    """Build a dependency graph by inferring resource producers and consumers from API operations."""
    operations: OperationMap = {}
    state = InferenceState.for_schema(schema)
    resources = state.resources

    # Inputs that bind only after every operation is scanned, keyed by operation label.
    deferred: dict[str, DeferredBindings] = {}

    # Declared types of each operation's path parameters, so a bound value can be checked against them
    path_parameter_types: dict[str, dict[str, list[str]]] = {}
    post_collections = collections_with_post(schema)
    for result in schema.get_all_operations():
        if isinstance(result, Ok):
            operation = result.ok()
            path_parameter_types[operation.label] = {
                parameter.name: declared_parameter_types(parameter)
                for parameter in operation.iter_parameters()
                if parameter.location == ParameterLocation.PATH
            }
            bindings = DeferredBindings()
            inputs = list(extract_inputs(operation=operation, state=state, deferred=bindings))
            outputs = extract_outputs(
                operation=operation, inputs=inputs, state=state, post_collections=post_collections
            )
            operations[operation.label] = OperationNode(
                method=operation.method,
                path=operation.path,
                inputs=inputs,
                outputs=list(outputs),
            )
            deferred[operation.label] = bindings

    # Replay nested-FK lookups whose target resource was registered later in the scan -
    # producer paths can sort after their consumers (e.g. /departments alphabetises before
    # /locations) and the per-operation pass would otherwise drop those slots.
    for label, bindings in deferred.items():
        for target_resource_name, target_field, body_path in bindings.nested_fks:
            target_resource = resources.get(target_resource_name)
            if target_resource is None:
                continue
            operations[label].inputs.append(
                InputSlot(
                    resource=target_resource,
                    resource_field=target_field,
                    parameter_name=format_body_key(body_path),
                    parameter_location=ParameterLocation.BODY,
                    body_path=body_path,
                )
            )

    # Bind path parameters named after a field of the resource owning the path (`/projects/{code}` -> `Project.code`,
    # `/rounds/{round_seq}` -> `Round.seq`). Deferred to here so the resource's fields are known regardless of order.
    for label, bindings in deferred.items():
        for resource_name, parameter_name in bindings.field_named_path_parameters:
            owner = resources.get(resource_name)
            if owner is None or owner.source < DefinitionSource.SCHEMA_WITH_PROPERTIES:
                continue
            field = naming.find_matching_field(
                parameter=parameter_name, resource=owner.name, fields=owner.fields, types=owner.types
            )
            # `{project_key}` under `/project-members` names a project, not the member's `key`.
            if field is None or not naming.parameter_names_resource_field(
                parameter=parameter_name, field=field, resource=owner.name
            ):
                continue
            operations[label].inputs.append(
                InputSlot(
                    resource=owner,
                    resource_field=field,
                    parameter_name=parameter_name,
                    parameter_location=ParameterLocation.PATH,
                )
            )

    # Bind body fields named after a collection whose responses list the values they accept
    # (`country` <- `GET /countries`). Deferred to here so the producer is known.
    primitive_producers = set()
    object_collection_producers = set()
    for node in operations.values():
        for output in node.outputs:
            if output.is_primitive_identifier:
                primitive_producers.add(output.resource.name)
            elif output.cardinality == Cardinality.MANY:
                object_collection_producers.add(output.resource.name)
    for label, bindings in deferred.items():
        node = operations[label]
        bound = {slot.parameter_name for slot in node.inputs}
        for resource_name, property_name, property_schema in bindings.named_scalars:
            if property_name in bound or (
                resource_name not in primitive_producers and resource_name not in object_collection_producers
            ):
                continue
            target = resources[resource_name]
            if any(output.resource.name == resource_name for output in node.outputs):
                continue
            if resource_name in primitive_producers:
                field = target.fields[0] if target.fields else "id"
            elif _accepts_code(property_schema, target):
                field = "code"
            else:
                continue
            node.inputs.append(
                InputSlot(
                    resource=target,
                    resource_field=field,
                    parameter_name=property_name,
                    parameter_location=ParameterLocation.BODY,
                )
            )
            bound.add(property_name)

    # An integer field named after a listed collection of integer-keyed objects holds one of their keys (`part` <- the
    # `pk` of a `Part`) when the response, the field's title or the path says which collection it is.
    collections = _listed_collections(operations)
    for label, bindings in deferred.items():
        node = operations[label]
        taken = {(slot.parameter_location, slot.parameter_name) for slot in node.inputs}
        candidates = [(name, field_schema, ParameterLocation.BODY) for _, name, field_schema in bindings.named_scalars]
        candidates.extend(
            (name, field_schema, ParameterLocation.QUERY) for name, field_schema in bindings.named_query_parameters
        )
        references: dict[str, str] | None = None
        for name, field_schema, location in candidates:
            if (
                (location, name) in taken
                or not _is_plain_integer(field_schema)
                or naming.normalize_for_matching(name) in naming.PAGINATION_FIELDS
            ):
                continue
            # A list filter selects by a field of the items it lists; `?page=` and `?limit=` select by none.
            if location == ParameterLocation.QUERY and not any(
                name in (output.resource.fields if output.response_fields is None else output.response_fields)
                for output in _main_outputs(node)
                if output.cardinality == Cardinality.MANY
            ):
                continue
            if references is None:
                references = _referenced_schemas(schema, label, node)
            collection = _collection_named_by(name, field_schema, location, node, references, collections, resources)
            if collection is None:
                continue
            key = next((key for key in ("id", "pk") if key in collection.fields), None)
            if key is None or collection.types.get(key) != {"integer"}:
                continue
            node.inputs.append(
                InputSlot(
                    resource=collection,
                    resource_field=key,
                    parameter_name=name,
                    parameter_location=location,
                    is_named_after_collection=True,
                )
            )
            taken.add((location, name))

    rebind_inherited_suffix_matches(operations, resources)

    # Update input slots with improved resource definitions discovered during extraction
    #
    # Example:
    #   - `DELETE /users/{userId}` initially inferred `User.fields=["userId"]`
    #   - then `POST /users` response revealed `User.fields=["id", "email"]`
    update_input_field_bindings(state.updated_resources, operations)

    # Merge parameter-inferred resources with schema-defined ones
    merge_related_resources(operations, resources)

    # Bind required body fields to a producer only when the response field identifies it uniquely.
    rebind_body_fields_to_unique_producers(operations, deferred)

    # Rebind orphan synthetics (e.g. `Spouse` from `spouse_id`) to the path-derived parent.
    rebind_orphan_synthetics(operations, resources)

    # Prefer same-module variants for spec-suffixed duplicates (`Group` / `Group1`).
    disambiguate_module_variants(operations, resources)

    # Steer path slots away from cross-module suffix-match hits (`KeyDateResource` -> `ResourceItem`).
    disambiguate_path_suffix_matches(operations, resources)

    # Bind `/things/{id}` to what `GET /things` lists when nothing else can supply the id (`partition_id`, `{name}`).
    bind_item_parameters_to_collections(operations, path_parameter_types)

    # A bare id `POST /things` returns is what `/things/{id}` takes, whatever each response names its schema.
    align_created_identifiers(operations)

    # A key the client chooses on `POST /things` is what `/things/{key}` takes when the response does not echo it.
    bind_created_item_keys(schema, operations, deferred)

    # `fromAirportId` names what responses return under `fromAirport` (`AirportResponse.id`).
    bind_id_fields_to_returned_resources(operations)

    # An item carries the key of the parent it lives under (`Shipment.warehouseId`) when nothing produces the parent.
    bind_parent_keys_to_item_fields(schema, operations)

    # Bind update body fields to values only the item `GET` returns (`version`, `checksum`).
    bind_echoed_body_fields(schema, operations, deferred)

    # A body field taking an array of identifiers never takes one value (`customer_id: [1, 2]` <- `Customer.id`),
    # and a UUID parameter never takes a non-string field (`{id}` <- an integer `Group.pk`).
    unbind_mistyped_values(schema, operations, deferred)

    # Clean up orphaned resources
    remove_unused_resources(operations, resources)

    return DependencyGraph(operations=operations, resources=resources)


def _accepts_code(schema: JsonSchema, resource: ResourceDefinition) -> bool:
    # Listed objects are passed around by their plain `code` (`language` <- `GET /languages[*].code`). Ids, slugs
    # and formatted strings are not: fields named after a collection often take URLs or unrelated enums instead.
    return isinstance(schema, dict) and "format" not in schema and resource.types.get("code") == set(get_type(schema))


def _is_plain_integer(schema: JsonSchema) -> TypeGuard[JsonSchemaObject]:
    return (
        isinstance(schema, dict)
        and [kind for kind in get_type(schema) if kind != "null"] == ["integer"]
        and "enum" not in schema
        and "const" not in schema
    )


def _listed_collections(operations: OperationMap) -> dict[str, set[str]]:
    # What list operations return as their items, with the paths listing them; not arrays nested inside one object
    # (`tags: [NestedTag]`).
    listed: dict[str, set[str]] = {}
    for node in operations.values():
        if node.method != "get" or naming.trailing_path_parameter(node.path) is not None:
            continue
        for output in node.outputs:
            if (
                output.cardinality == Cardinality.MANY
                and "*" not in output.pointer
                and not output.is_primitive_identifier
            ):
                listed.setdefault(output.resource.name, set()).add(node.path)
    return listed


def _main_outputs(node: OperationNode) -> list[OutputSlot]:
    # The objects the operation returns, not the ones nested inside them.
    return [
        output
        for output in node.outputs
        if not any(
            other is not output and output.pointer.startswith(other.pointer.rstrip("/") + "/") for other in node.outputs
        )
    ]


def _referenced_schemas(schema: OpenApiSchema, label: str, node: OperationNode) -> dict[str, str]:
    operation = schema.find_operation_by_label(label)
    assert operation is not None
    references: dict[str, str] = {}
    for output in _main_outputs(node):
        # Outputs come from the operation's own responses.
        response = operation.responses.get(output.status_code)
        assert response is not None
        references.update(referenced_schema_names(response, output.pointer))
    return references


def _collection_named_by(
    field: str,
    field_schema: JsonSchemaObject,
    location: ParameterLocation,
    node: OperationNode,
    references: dict[str, str],
    collections: dict[str, set[str]],
    resources: ResourceMap,
) -> ResourceDefinition | None:
    word = naming.to_pascal_case(field)
    # The operation's own objects are not what its fields point to: `part` on manufacturer parts is not one of them.
    own = {output.resource.name for output in _main_outputs(node)}
    named = [name for name in collections if name.endswith(word) and name not in own]
    if not named:
        return None
    # The response returns the referenced object for it: `role` as a `NestedDeviceRole` or `rear_port` as a
    # `FrontPortRearPort` (a name ending in the collection), else `part_detail` as a `PartBrief` (starting with it).
    referenced = references.get(field) or references.get(f"{field}_detail")
    if referenced is not None:
        related = [name for name in collections if referenced.endswith(name) and name not in own] or [
            name for name in collections if _starts_with_word(referenced, name) and name not in own
        ]
        return resources[max(related, key=lambda name: (name == referenced, len(name)))] if related else None
    # Its title names one of them: `part` titled "Supplier Part".
    title = field_schema.get("title")
    if isinstance(title, str):
        normalized_title = naming.normalize_for_matching(title.replace(" ", ""))
        if normalized_title != naming.normalize_for_matching(field):
            titled = [name for name in named if naming.normalize_for_matching(name) == normalized_title]
            if titled:
                return resources[titled[0]]
    # The path picks one by whole words: `template` on `/parameters` is a `ParameterTemplate`, `type` on `/eventlog`
    # is no `EventType`. Not a collection kept under the operation's own items: `version` on documents is their
    # counter, not one of the `DocumentVersion` items under `/documents/{id}/versions`.
    own_collection = (
        naming.collection_path(node.path) if naming.trailing_path_parameter(node.path) else node.path.rstrip("/")
    )
    own_items = path_shape(own_collection) + "/{}/"
    words = _path_words(node.path)
    specific = [
        name
        for name in named
        if name != word
        and _is_word_run(naming.normalize_for_matching(name[: -len(word)]), words)
        and not any(path_shape(listing).startswith(own_items) for listing in collections[name])
    ]
    if len(specific) == 1:
        return resources[specific[0]]
    # A list filter on a field of its items may take the only collection of that name; a body field needs the
    # evidence above, as names like `version` hold counters, not keys.
    if not specific and location == ParameterLocation.QUERY and named == [word]:
        return resources[word]
    return None


def _starts_with_word(schema_name: str, collection: str) -> bool:
    # `PartBrief` is a view of `Part`; `Partition` is not.
    return schema_name.startswith(collection) and (
        len(schema_name) == len(collection) or schema_name[len(collection)].isupper()
    )


def _path_words(path: str) -> list[str]:
    return [
        naming.normalize_for_matching(naming.to_singular(word))
        for segment in path.split("/")
        if "{" not in segment
        for word in re.findall(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z0-9]+|[A-Z]+", segment)
    ]


def _is_word_run(text: str, words: list[str]) -> bool:
    return any(
        "".join(words[start:end]) == text for start in range(len(words)) for end in range(start + 1, len(words) + 1)
    )


def inject_links(schema: OpenApiSchema) -> int:
    injected = 0
    # Shared by producers and targets; the same operations recur across thousands of links.
    operation_cache: dict[str, APIOperation] = {}
    # A response is visited once per inferred link, so normalize its links once and index them by
    # target - only same-target links can be subsets. Values keep the mapping alive so `id` is unique.
    # Next to them, the next suffix to try per colliding link name, as names taken once stay taken.
    normalized_cache: dict[int, tuple[dict[str, Any], dict[tuple[str, str], list[NormalizedLink]], dict[str, int]]] = {}
    _unshare_responses(schema)
    # Operations built earlier still point at the response definitions that were just replaced
    schema._operations.clear_cache()
    for response_links in schema.analysis.dependency_graph.iter_links():
        operation = _find_operation_by_reference(schema, response_links.producer_operation_ref, operation_cache)
        response = operation.responses.get(response_links.status_code)
        links = response.source.setdefault(schema.adapter.links_keyword, {})

        entry = normalized_cache.get(id(links))
        if entry is None:
            index: dict[tuple[str, str], list[NormalizedLink]] = {}
            for link in links.values():
                try:
                    normalized = _normalize_link(link, response.resolver, schema, operation_cache)
                except (OperationNotFound, InvalidSchema, RefResolutionError):
                    # Dangling links are reported when the stateful phase validates them
                    continue
                index.setdefault((normalized.path, normalized.method), []).append(normalized)
            next_suffixes: dict[str, int] = {}
            normalized_cache[id(links)] = (links, index, next_suffixes)
        else:
            _, index, next_suffixes = entry

        # Compared against what was known before this response, not against each other.
        pending: list[NormalizedLink] = []
        for link_name, definition in response_links.links.items():
            inferred_link = definition.to_openapi()

            # Check if duplicate / subsets exists
            normalized = _normalize_link(inferred_link, response.resolver, schema, operation_cache)
            if any(
                _is_subset_link(normalized, existing)
                for existing in index.get((normalized.path, normalized.method), ())
            ):
                continue

            # Find unique name if collision exists
            final_name = _resolve_link_name_collision(link_name, links, next_suffixes)
            links[final_name] = inferred_link
            pending.append(normalized)
            injected += 1

        for normalized in pending:
            index.setdefault((normalized.path, normalized.method), []).append(normalized)
    return injected


def _unshare_responses(schema: OpenApiSchema) -> None:
    """Give each operation its own copy of a response it shares, so links inferred for one never reach another.

    A link may forward the producer's own path parameters, which another operation sharing the response lacks.
    """
    slots: list[tuple[dict[str, Any], str, Mapping[str, Any]]] = []
    uses: dict[int, int] = {}
    # Raw definitions, as building every operation costs far more than resolving its responses
    try:
        entries = schema._operation_lookup.entries()
    except RefResolutionError:
        # An unresolvable path item is reported as a schema error for its operations
        return
    for entry in entries:
        raw_responses = entry.definition.get("responses", {})
        try:
            responses = OpenApiResponses.from_definition(
                raw_responses, entry.resolver, entry.scope, schema.adapter, schema.response_schema_cache
            )
        except (InvalidSchema, RefResolutionError):
            continue
        # Resolving already rejected anything but an object
        assert isinstance(raw_responses, dict)
        keys = {str(key): key for key in raw_responses}
        for status_code, response in responses.items():
            uses[id(response.source)] = uses.get(id(response.source), 0) + 1
            # Copied elsewhere, its own references would resolve against the wrong document
            if response.resolver.base_uri == entry.resolver.base_uri:
                slots.append((raw_responses, keys[status_code], response.source))
    links_keyword = schema.adapter.links_keyword
    for raw_responses, key, definition in slots:
        if uses[id(definition)] > 1:
            raw_responses[key] = {**definition, links_keyword: dict(definition.get(links_keyword, {}))}


def _find_operation_by_reference(schema: OpenApiSchema, reference: str, cache: dict[str, APIOperation]) -> APIOperation:
    key = f"ref:{reference}"
    operation = cache.get(key)
    if operation is None:
        operation = schema.find_operation_by_reference(reference)
        cache[key] = operation
    return operation


def _normalize_link(
    link: Mapping[str, Any], resolver: Resolver, schema: OpenApiSchema, cache: dict[str, APIOperation]
) -> NormalizedLink:
    """Normalize a link definition for comparison."""
    _, link = maybe_resolve_with_resolver(link, resolver)
    operation = find_link_target(schema, link, cache)

    normalized_params = _normalize_parameter_keys(link.get("parameters", {}), operation)

    return NormalizedLink(
        path=operation.path,
        method=operation.method,
        parameters=normalized_params,
        request_body=link.get("requestBody", {}),
    )


def _normalize_parameter_keys(parameters: dict, operation: APIOperation) -> set[str]:
    """Normalize parameter keys to location.name format."""
    normalized = set()

    for parameter_name in parameters:
        # If already has location prefix, use as-is
        if "." in parameter_name:
            normalized.add(parameter_name)
            continue

        # Find the parameter and prepend location
        for parameter in operation.iter_parameters():
            if parameter.name == parameter_name:
                normalized.add(f"{parameter.location.value}.{parameter_name}")
                break

    return normalized


def _resolve_link_name_collision(
    proposed_name: str, existing_links: dict[str, Any], next_suffixes: dict[str, int]
) -> str:
    """Find unique link name if collision exists."""
    if proposed_name not in existing_links:
        return proposed_name

    suffix = next_suffixes.get(proposed_name, 0)
    while f"{proposed_name}_{suffix}" in existing_links:
        suffix += 1
    next_suffixes[proposed_name] = suffix + 1
    return f"{proposed_name}_{suffix}"


def _is_subset_link(inferred: NormalizedLink, existing: NormalizedLink) -> bool:
    """Check if inferred link is a subset of existing link."""
    # Must target the same operation
    if inferred.path != existing.path or inferred.method != existing.method:
        return False

    # Inferred parameters must be subset of existing parameters
    if not inferred.parameters.issubset(existing.parameters):
        return False

    # Inferred request body must be subset of existing body
    return _is_request_body_subset(inferred.request_body, existing.request_body)


def _is_request_body_subset(inferred_body: Any, existing_body: Any) -> bool:
    """Check if inferred body is a subset of existing body."""
    # Empty inferred body is always a subset
    if not inferred_body:
        return True

    # If existing is empty but inferred isn't, not a subset
    if not existing_body:
        return False

    # Both must be dicts for subset comparison, otherwise check for equality
    if not isinstance(inferred_body, dict) or not isinstance(existing_body, dict):
        return inferred_body == existing_body

    # Check if all inferred fields exist in existing with same values
    return all(existing_body.get(key, NOT_SET) == value for key, value in inferred_body.items())
