from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

from schemathesis.core import media_types
from schemathesis.core.errors import MalformedMediaType
from schemathesis.core.jsonschema import maybe_resolve_bundled
from schemathesis.core.jsonschema.resolver import Resolver
from schemathesis.core.jsonschema.types import get_type
from schemathesis.core.parameters import ParameterLocation
from schemathesis.specs.openapi.adapter.parameters import resource_name_from_ref
from schemathesis.specs.openapi.stateful.dependencies import naming
from schemathesis.specs.openapi.stateful.dependencies.models import (
    CanonicalizationCache,
    DefinitionSource,
    InputSlot,
    OperationMap,
    OutputSlot,
    ResourceDefinition,
    ResourceMap,
    infer_fk_target,
)
from schemathesis.specs.openapi.stateful.dependencies.resources import extract_resources_from_responses

if TYPE_CHECKING:
    from schemathesis.specs.openapi.adapter.parameters import OpenApiBody
    from schemathesis.specs.openapi.schemas import APIOperation


def extract_inputs(
    *,
    operation: APIOperation,
    resources: ResourceMap,
    updated_resources: set[str],
    resolver: Resolver,
    canonicalization_cache: CanonicalizationCache,
    deferred_nested_fks: list[tuple[str, str, str]] | None = None,
    candidate_resource_names: frozenset[str] = frozenset(),
) -> Iterator[InputSlot]:
    """Extract resource dependencies for an API operation from its input parameters.

    Connects each parameter (e.g., `userId`) to its resource definition (`User`),
    creating placeholder resources if not yet discovered from their schemas.

    `deferred_nested_fks` collects nested-body FK lookups whose target resource
    isn't yet registered. The caller replays them after every operation has been
    scanned so the slot lands once the producer has been seen.

    `candidate_resource_names` gates `<word>_name` body-field synthetics: only
    creates a placeholder when the inferred name is backed by a path segment or
    component schema.
    """
    known_dependencies = set()
    for param in operation.iter_parameters():
        input_slot = _resolve_parameter_dependency(
            parameter_name=param.name,
            parameter_location=param.location,
            operation=operation,
            resources=resources,
            updated_resources=updated_resources,
            resolver=resolver,
            canonicalization_cache=canonicalization_cache,
        )
        if input_slot is not None:
            if input_slot.resource.source >= DefinitionSource.SCHEMA_WITH_PROPERTIES:
                known_dependencies.add(input_slot.resource.name)
            yield input_slot

    for body in operation.body:
        try:
            if media_types.is_json(body.media_type):
                yield from _resolve_body_dependencies(
                    body=body,
                    operation=operation,
                    resources=resources,
                    known_dependencies=known_dependencies,
                    deferred_nested_fks=deferred_nested_fks,
                    candidate_resource_names=candidate_resource_names,
                )
        except MalformedMediaType:
            continue


def _resolve_parameter_dependency(
    *,
    parameter_name: str,
    parameter_location: ParameterLocation,
    operation: APIOperation,
    resources: ResourceMap,
    updated_resources: set[str],
    resolver: Resolver,
    canonicalization_cache: CanonicalizationCache,
) -> InputSlot | None:
    """Connect a parameter to its resource definition, creating placeholder if needed.

    Strategy:
    1. Infer resource name from parameter (`userId` -> `User`)
    2. Use existing resource if high-quality definition exists
    3. Try discovering from operation's response schemas
    4. Fall back to creating placeholder with a single field
    """
    resource_name = naming.from_parameter(parameter=parameter_name, path=operation.path)

    if resource_name is None:
        return None

    resource = resources.get(resource_name)

    # Upgrade low-quality resource definitions (e.g., from parameter inference)
    # by searching this operation's responses for actual schema
    if resource is None or resource.source < DefinitionSource.SCHEMA_WITH_PROPERTIES:
        resource = _find_resource_in_responses(
            operation=operation,
            resource_name=resource_name,
            resources=resources,
            updated_resources=updated_resources,
            resolver=resolver,
            canonicalization_cache=canonicalization_cache,
        )
        if resource is not None:
            resources[resource_name] = resource

    # Determine resource and its field
    is_suffix_matched = False
    if resource is None:
        # Try to find an existing resource with matching suffix
        # Example: parameter "file_name" -> inferred "File" not found -> check if "BackupFile" exists
        matched_resource, matched_field = _find_matching_resource_by_suffix(
            resource_name=resource_name,
            parameter_name=parameter_name,
            resources=resources,
        )
        if matched_resource is not None and matched_field is not None:
            resource = matched_resource
            resource_name = matched_resource.name
            field = matched_field
            is_suffix_matched = True
        else:
            # No schema found - create placeholder resource with inferred field
            #
            # Example: `DELETE /users/{userId}` with no response body -> `User` resource with "userId" field
            #
            # Later operations with schemas will upgrade this placeholder
            if resource_name in resources:
                # Resource exists but was empty - update with parameter field
                resources[resource_name].fields = [parameter_name]
                resources[resource_name].source = DefinitionSource.PARAMETER_INFERENCE
                updated_resources.add(resource_name)
                resource = resources[resource_name]
            else:
                resource = ResourceDefinition.inferred_from_parameter(
                    name=resource_name,
                    parameter_name=parameter_name,
                )
                resources[resource_name] = resource
            field = parameter_name
    else:
        # Match parameter to resource field (`userId` -> `id`, `Id` -> `ChannelId`, etc.)
        matched = naming.find_matching_field(
            parameter=parameter_name,
            resource=resource_name,
            fields=resource.fields,
        )
        if matched is not None:
            field = matched
        elif "id" in resource.fields:
            # Conventional fallback: `<resource>Id` parameters point at the resource's `id` field.
            field = "id"
        else:
            # Resource has no `id` field — use the parameter name itself so request-pool
            # captures from peer operations land in the same field this slot will read.
            field = parameter_name

    return InputSlot(
        resource=resource,
        resource_field=field,
        parameter_name=parameter_name,
        parameter_location=parameter_location,
        is_suffix_matched=is_suffix_matched,
    )


def _find_resource_in_responses(
    *,
    operation: APIOperation,
    resource_name: str,
    resources: ResourceMap,
    updated_resources: set[str],
    resolver: Resolver,
    canonicalization_cache: CanonicalizationCache,
) -> ResourceDefinition | None:
    """Search operation's successful responses for a specific resource definition.

    Used when a parameter references a resource not yet discovered. Scans this
    operation's response schemas hoping to find the resource definition.
    """
    for _, extracted in extract_resources_from_responses(
        operation=operation,
        resources=resources,
        updated_resources=updated_resources,
        resolver=resolver,
        canonicalization_cache=canonicalization_cache,
    ):
        if extracted.resource.name == resource_name:
            return extracted.resource

    return None


def _find_matching_resource_by_suffix(
    *,
    resource_name: str,
    parameter_name: str,
    resources: ResourceMap,
) -> tuple[ResourceDefinition, str] | tuple[None, None]:
    """Find a resource with matching suffix or prefix when exact match not found.

    When a parameter like "file_name" infers resource "File" but no "File" exists,
    check if a resource ending with "File" exists (e.g., "BackupFile") or starting
    with "File" (e.g., "FileSummary") and if the parameter can be matched to one
    of its fields.

    Suffix matching example: "file_name" -> "File" -> "BackupFile"
    Prefix matching example: "group_slug" -> "Group" -> "GroupSummary"

    Only considers high-quality resources (schema-defined with properties) and
    requires the parameter to match a field via find_matching_field.
    """
    # Normalize for case-insensitive matching
    resource_lower = resource_name.lower()

    for candidate_name, candidate_resource in resources.items():
        # Only consider schema-defined resources
        if candidate_resource.source < DefinitionSource.SCHEMA_WITH_PROPERTIES:
            continue

        candidate_lower = candidate_name.lower()

        # Check if candidate ends with OR starts with the inferred resource name
        # Suffix: "BackupFile".endswith("file") for resource_name="File"
        # Prefix: "GroupSummary".startswith("group") for resource_name="Group"
        if not (candidate_lower.endswith(resource_lower) or candidate_lower.startswith(resource_lower)):
            continue

        # Check if parameter can be matched to a field
        matched_field = naming.find_matching_field(
            parameter=parameter_name,
            resource=candidate_name,
            fields=candidate_resource.fields,
        )
        if matched_field is not None:
            return candidate_resource, matched_field

    return None, None


GENERIC_FIELD_NAMES = frozenset(
    {
        "body",
        "text",
        "content",
        "message",
        "description",
    }
)


def _flatten_composition(schema: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Merge ``properties`` and ``required`` across ``allOf``/``oneOf``/``anyOf`` branches.

    First-seen property definition wins; required is a union across branches.
    """
    properties: dict[str, Any] = {}
    required: list[str] = []

    def merge(node: dict[str, Any]) -> None:
        node_properties = node.get("properties")
        if isinstance(node_properties, dict):
            for name, subschema in node_properties.items():
                properties.setdefault(name, subschema)
        node_required = node.get("required")
        if isinstance(node_required, list):
            required.extend(node_required)
        for key in ("allOf", "oneOf", "anyOf"):
            branches = node.get(key)
            if isinstance(branches, list):
                for branch in branches:
                    if isinstance(branch, dict):
                        merge(branch)

    merge(schema)
    return properties, required


def _resolve_body_dependencies(
    *,
    body: OpenApiBody,
    operation: APIOperation,
    resources: ResourceMap,
    known_dependencies: set[str],
    deferred_nested_fks: list[tuple[str, str, str]] | None = None,
    candidate_resource_names: frozenset[str] = frozenset(),
) -> Iterator[InputSlot]:
    schema = body.raw_schema
    if not isinstance(schema, dict):
        return

    resolved = maybe_resolve_bundled(schema)

    # For `items`, we'll inject an array with extracted resource
    items = resolved.get("items")
    if isinstance(items, dict):
        resource_name = body.resource_name or naming.from_path(operation.path)

        if "$ref" in items:
            schema_key = items["$ref"].split("/")[-1]
            original_ref = body.name_to_uri[schema_key]
            resource_name = resource_name_from_ref(original_ref)
        if resource_name is not None:
            resource = resources.get(resource_name)
            if resource is None:
                resource = ResourceDefinition.inferred_from_parameter(name=resource_name, parameter_name=None)
                resources[resource_name] = resource
                field = None
            else:
                field = None
            yield InputSlot(
                resource=resource,
                resource_field=field,
                parameter_name=0,
                parameter_location=ParameterLocation.BODY,
            )

    # Inspect each property that could be a part of some other resource.
    # Flatten composition keywords first so bodies whose top level is allOf/oneOf/anyOf still surface their fields.
    properties, required = _flatten_composition(resolved)
    if not properties:
        return
    path = operation.path
    for property_name, subschema in properties.items():
        resource_name = naming.from_parameter(property_name, path, body_field=True)
        # `_name` body fields are usually attributes; only invent a resource when its
        # name is backed by a path segment or component schema. Otherwise fall through
        # to the known-dependencies path so the field can still bind to a parent resource.
        gated = (
            resource_name is not None
            and resource_name not in resources
            and property_name.lower().endswith("_name")
            and resource_name not in candidate_resource_names
        )
        if resource_name is not None and not gated:
            resource = resources.get(resource_name)
            if resource is None:
                resource = ResourceDefinition.inferred_from_parameter(
                    name=resource_name,
                    parameter_name=property_name,
                )
                resources[resource_name] = resource
                field = property_name
            else:
                field = (
                    naming.find_matching_field(
                        parameter=property_name,
                        resource=resource_name,
                        fields=resource.fields,
                    )
                    or "id"
                )
            yield InputSlot(
                resource=resource,
                resource_field=field,
                parameter_name=property_name,
                parameter_location=ParameterLocation.BODY,
            )
            continue

        # Skip generic property names & optional fields (at least for now)
        if property_name in GENERIC_FIELD_NAMES or property_name not in required:
            continue

        # Find candidate resources among known dependencies that actually have this field
        candidates = [
            resources[dep] for dep in known_dependencies if dep in resources and property_name in resources[dep].fields
        ]

        # Skip ambiguous cases when multiple resources have same field name
        if len(candidates) != 1:
            continue

        resource = candidates[0]
        # Ensure the target field supports the same type
        if not resource.types[property_name] & set(get_type(subschema)):
            continue

        yield InputSlot(
            resource=resource,
            resource_field=property_name,
            parameter_name=property_name,
            parameter_location=ParameterLocation.BODY,
        )

    # Recursively find nested FK fields in request body
    yield from _extract_nested_body_fk_fields(resolved, resources, path="", deferred=deferred_nested_fks)


def _extract_nested_body_fk_fields(
    schema: dict[str, Any],
    resources: ResourceMap,
    path: str,
    max_depth: int = 5,
    deferred: list[tuple[str, str, str]] | None = None,
) -> Iterator[InputSlot]:
    """Recursively extract FK fields from nested request body schemas.

    Scans nested objects and array items to find FK fields like:
    - {shipping: {warehouse_id: "..."}} -> InputSlot for warehouse_id
    - {items: [{product_id: "..."}]} -> InputSlot for product_id

    When the FK target resource isn't yet registered, the lookup is appended to
    `deferred` so the caller can replay it once every operation has been scanned.
    """
    if max_depth <= 0:
        return

    properties, _ = _flatten_composition(schema)
    if not properties:
        return

    for property_name, subschema in properties.items():
        if not isinstance(subschema, dict):
            continue

        # Build the path for nested fields
        current_path = f"{path}/{property_name}" if path else property_name

        # Composition branches may carry the actual object shape, so flatten before treating as object.
        sub_properties, _ = _flatten_composition(subschema)
        prop_type = subschema.get("type")

        if prop_type == "object" or sub_properties:
            for nested_name, nested_schema in sub_properties.items():
                if not isinstance(nested_schema, dict):
                    continue

                nested_path = f"{current_path}/{nested_name}"

                # Check if nested field is a FK
                fk_info = infer_fk_target(nested_name)
                if fk_info is not None:
                    target_resource_name, target_field, _ = fk_info
                    resource = resources.get(target_resource_name)
                    if resource is not None:
                        yield InputSlot(
                            resource=resource,
                            resource_field=target_field,
                            parameter_name=nested_path,
                            parameter_location=ParameterLocation.BODY,
                        )
                    elif deferred is not None:
                        deferred.append((target_resource_name, target_field, nested_path))
                    continue

                deeper_properties, _ = _flatten_composition(nested_schema)
                if nested_schema.get("type") == "object" or deeper_properties:
                    yield from _extract_nested_body_fk_fields(
                        nested_schema, resources, nested_path, max_depth - 1, deferred=deferred
                    )

        elif prop_type == "array":
            # Check array items for FK fields
            items = subschema.get("items")
            if isinstance(items, dict):
                items_path = f"{current_path}/0"

                items_props, _ = _flatten_composition(items)
                for item_prop_name, item_prop_schema in items_props.items():
                    if not isinstance(item_prop_schema, dict):
                        continue

                    item_path = f"{items_path}/{item_prop_name}"

                    # Check if this item property is a FK
                    fk_info = infer_fk_target(item_prop_name)
                    if fk_info is not None:
                        target_resource_name, target_field, _ = fk_info
                        resource = resources.get(target_resource_name)
                        if resource is not None:
                            yield InputSlot(
                                resource=resource,
                                resource_field=target_field,
                                parameter_name=item_path,
                                parameter_location=ParameterLocation.BODY,
                            )
                        elif deferred is not None:
                            deferred.append((target_resource_name, target_field, item_path))

                # Recurse into array items
                if items.get("type") == "object" or items_props:
                    yield from _extract_nested_body_fk_fields(
                        items, resources, items_path, max_depth - 1, deferred=deferred
                    )


def update_input_field_bindings(resource_name: str, operations: OperationMap) -> None:
    """Update input slots field bindings after resource definition was upgraded.

    When a resource's fields change (e.g., `User` upgraded from `["userId"]` to `["id", "email"]`),
    existing input slots may reference stale field names. This re-evaluates field matching
    for all operations using this resource.

    Example:
        `DELETE /users/{userId}` created `InputSlot(resource_field="userId")`
        `POST /users` revealed actual fields `["id", "email"]`
        This updates DELETE's `InputSlot` to use `resource_field="id"`

    """
    # Re-evaluate field matching for all operations referencing this resource
    for operation in operations.values():
        for input_slot in operation.inputs:
            # Skip inputs not using this resource
            if input_slot.resource.name != resource_name or isinstance(input_slot.parameter_name, int):
                continue

            # Re-match parameter to upgraded resource fields
            new_field = naming.find_matching_field(
                parameter=input_slot.parameter_name,
                resource=resource_name,
                fields=input_slot.resource.fields,
            )
            if new_field is not None:
                input_slot.resource_field = new_field


def merge_related_resources(operations: OperationMap, resources: ResourceMap) -> None:
    """Merge parameter-inferred resources with schema-defined resources from related operations."""
    candidates = find_producer_consumer_candidates(operations)

    for producer_name, consumer_name in candidates:
        producer = operations[producer_name]
        consumer = operations[consumer_name]

        # Try to upgrade each input slot
        for input_slot in consumer.inputs:
            result = try_merge_input_resource(input_slot, producer.outputs, resources)

            if result is not None:
                new_resource_name, new_field_name = result
                # Update input slot to use the better resource definition
                input_slot.resource = resources[new_resource_name]
                input_slot.resource_field = new_field_name


def rebind_orphan_synthetics(operations: OperationMap, resources: ResourceMap) -> None:
    """Rebind body and query slots from producer-less synthetics to a same-operation parent.

    `<word>_id` fields produce a synthetic `<Word>` resource; when nothing else in
    the spec backs that name (no path, no schema, no producer) but the operation's
    own response describes a parent resource carrying the same field, the slot is
    really a self-FK (`spouse_id` on `POST /contacts`, `?sequence_id=` on `GET /events`).
    """
    producer_resources = {output.resource.name for operation in operations.values() for output in operation.outputs}
    for operation in operations.values():
        parent_name = naming.from_path(operation.path)
        if parent_name is None:
            continue
        parent = resources.get(parent_name)
        if parent is None or parent.source < DefinitionSource.SCHEMA_WITH_PROPERTIES:
            continue
        for input_slot in operation.inputs:
            if input_slot.parameter_location not in (ParameterLocation.BODY, ParameterLocation.QUERY):
                continue
            if input_slot.resource.source != DefinitionSource.PARAMETER_INFERENCE:
                continue
            if input_slot.resource.name in producer_resources:
                continue
            if not isinstance(input_slot.parameter_name, str):
                continue
            # Require an exact field-name match so we only rebind genuine self-FKs
            # (`spouse_id` on `POST /contacts` when Contact has a `spouse_id` field) and
            # leave ambiguous cases (`clientId` on `POST /applications`) alone.
            if input_slot.parameter_name in parent.fields:
                input_slot.resource = parent
                input_slot.resource_field = input_slot.parameter_name


def disambiguate_module_variants(operations: OperationMap, resources: ResourceMap) -> None:
    """Swap to a same-module sibling for spec-suffixed duplicates (`Group` / `Group1`).

    Without this, every consumer in the second module binds to the first module's
    variant via the path-derived lookup.
    """
    producer_modules: dict[str, set[str]] = {}
    for operation in operations.values():
        module = _module_of(operation.path)
        if not module:
            continue
        for output in operation.outputs:
            producer_modules.setdefault(output.resource.name, set()).add(module)

    by_stem: dict[str, set[str]] = {}
    for resource_name in producer_modules:
        by_stem.setdefault(_strip_trailing_digits(resource_name), set()).add(resource_name)

    for operation in operations.values():
        consumer_module = _module_of(operation.path)
        if not consumer_module:
            continue
        for input_slot in operation.inputs:
            modules = producer_modules.get(input_slot.resource.name)
            if not modules or consumer_module in modules:
                continue
            family = by_stem.get(_strip_trailing_digits(input_slot.resource.name), set())
            siblings = [
                name
                for name in family
                if name != input_slot.resource.name and consumer_module in producer_modules[name]
            ]
            if len(siblings) != 1:
                continue
            new_resource = resources[siblings[0]]
            new_field = (
                naming.find_matching_field(
                    parameter=input_slot.parameter_name if isinstance(input_slot.parameter_name, str) else "",
                    resource=new_resource.name,
                    fields=new_resource.fields,
                )
                or input_slot.resource_field
            )
            input_slot.resource = new_resource
            input_slot.resource_field = new_field


def _module_of(path: str) -> str:
    stripped = naming.strip_version_prefix(path).lstrip("/")
    head, _, _ = stripped.partition("/")
    return head


def _strip_trailing_digits(name: str) -> str:
    end = len(name)
    while end > 0 and name[end - 1].isdigit():
        end -= 1
    return name[:end] or name


def try_merge_input_resource(
    input_slot: InputSlot,
    producer_outputs: list[OutputSlot],
    resources: ResourceMap,
) -> tuple[str, str] | None:
    """Try to upgrade an input's resource to a producer's resource."""
    consumer_resource = input_slot.resource

    # Only upgrade parameter-inferred resources (low confidence) or suffix-matched inputs
    # Suffix-matched inputs may have matched a less specific resource (e.g., "Blog post" instead of "Blog post public")
    if consumer_resource.source != DefinitionSource.PARAMETER_INFERENCE and not input_slot.is_suffix_matched:
        return None

    # Try each producer output
    for output in producer_outputs:
        producer_resource = resources[output.resource.name]

        # Only merge to schema-defined resources (high confidence)
        if producer_resource.source != DefinitionSource.SCHEMA_WITH_PROPERTIES:
            continue

        # Try to match the input parameter to producer's fields
        param_name = input_slot.parameter_name
        if not isinstance(param_name, str):
            continue

        for resource_name in (input_slot.resource.name, producer_resource.name):
            matched_field = naming.find_matching_field(
                parameter=param_name,
                resource=resource_name,
                fields=producer_resource.fields,
            )

            if matched_field is not None:
                return producer_resource.name, matched_field

    return None


def find_producer_consumer_candidates(operations: OperationMap) -> list[tuple[str, str]]:
    """Find operation pairs that might produce/consume the same resource via REST patterns."""
    candidates = []

    # Group by base path to reduce comparisons
    paths: dict[str, list[str]] = {}
    for name, node in operations.items():
        base = _extract_base_path(node.path)
        paths.setdefault(base, []).append(name)

    # Within each path group, find POST/PUT -> GET/DELETE/PATCH patterns
    for names in paths.values():
        for producer_name in names:
            producer = operations[producer_name]
            # Producer must create/update and return data
            if producer.method not in ("post", "put") or not producer.outputs:
                continue

            for consumer_name in names:
                consumer = operations[consumer_name]
                # Consumer must have path parameters
                if not consumer.inputs:
                    continue
                # Paths must be related (collection + item pattern)
                if _is_collection_item_pattern(producer.path, consumer.path):
                    candidates.append((producer_name, consumer_name))

    return candidates


def _extract_base_path(path: str) -> str:
    """Extract collection path: /blog/posts/{id} -> /blog/posts."""
    parts = [p for p in path.split("/") if not p.startswith("{")]
    return "/".join(parts).rstrip("/")


def _is_collection_item_pattern(collection_path: str, item_path: str) -> bool:
    """Check if paths follow REST collection/item pattern."""
    # /blog/posts + /blog/posts/{postId}
    normalized_collection = collection_path.rstrip("/")
    normalized_item = item_path.rstrip("/")

    # Must start with collection path
    if not normalized_item.startswith(normalized_collection + "/"):
        return False

    # Extract the segment after collection path
    remainder = normalized_item[len(normalized_collection) + 1 :]

    # Must be a single path parameter: {paramName} with no slashes
    return (
        remainder.startswith("{")
        and remainder.endswith("}")
        and len(remainder) > 2  # Not empty {}
        and "/" not in remainder
    )
