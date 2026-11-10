from __future__ import annotations

import difflib
import enum
import re
from collections import defaultdict
from collections.abc import Callable, Collection, Iterable, Iterator, Mapping
from dataclasses import asdict, dataclass
from functools import lru_cache
from typing import Any, TypeAlias, TypeVar

from schemathesis.core.errors import InvalidSchema, RefResolutionError
from schemathesis.core.jsonschema.resolver import Resolver
from schemathesis.core.jsonschema.types import JsonSchema
from schemathesis.core.parameters import ParameterLocation, iter_path_parameters
from schemathesis.core.text import to_pascal_case
from schemathesis.core.transforms import (
    encode_pointer,
    get_template_fields,
    iter_decoded_pointer_segments,
)
from schemathesis.resources.descriptors import Cardinality
from schemathesis.specs.openapi.adapter.references import maybe_resolve_with_resolver
from schemathesis.specs.openapi.stateful.dependencies.naming import (
    KeyKind,
    from_path,
    key_kind,
    normalize_for_matching,
    owning_resource,
    parameter_names_source,
    strip_version_prefix,
    trailing_path_parameter,
)
from schemathesis.specs.openapi.stateful.links import SCHEMATHESIS_LINK_EXTENSION


@dataclass(slots=True)
class DependencyGraph:
    """Graph of API operations and their resource dependencies."""

    operations: OperationMap
    resources: ResourceMap

    def serialize(self) -> dict[str, Any]:
        serialized = asdict(self)

        for operation in serialized["operations"].values():
            del operation["method"]
            del operation["path"]
            for input in operation["inputs"]:
                input["resource"] = input["resource"]["name"]
                del input["takes_array"]
                if not input["echoes_response"]:
                    del input["echoes_response"]
                if not input["is_listed_identifier"]:
                    del input["is_listed_identifier"]
                if not input["is_parent_key"]:
                    del input["is_parent_key"]
                del input["is_named_after_collection"]
                del input["body_path"]
            for output in operation["outputs"]:
                output["resource"] = output["resource"]["name"]
                del output["response_fields"]
                if output.get("identifier_pointer") is None:
                    output.pop("identifier_pointer", None)
                if output.get("path_parameter") is None:
                    output.pop("path_parameter", None)
                if output.get("body_field") is None:
                    output.pop("body_field", None)

        for resource in serialized["resources"].values():
            del resource["name"]
            del resource["source"]
            del resource["inherited_fields"]
            for fk_field in [*resource["fk_fields"], *resource["nested_fk_fields"]]:
                del fk_field["holds_array"]
            # Simplify FK fields for readability
            if not resource["fk_fields"]:
                del resource["fk_fields"]
            if not resource["nested_fk_fields"]:
                del resource["nested_fk_fields"]

        return serialized

    def iter_links(self) -> Iterator[ResponseLinks]:
        """Generate OpenAPI Links connecting producer and consumer operations.

        Creates links from operations that produce resources to operations that
        consume them. For example: `POST /users` (creates `User`) -> `GET /users/{id}`
        (needs `User.id` parameter).
        """
        encoded_paths = {id(op): encode_pointer(op.path) for op in self.operations.values()}
        template_fields = {id(op): get_template_fields(op.path) for op in self.operations.values()}

        # Precomputed data for the cross-namespace scope guard (bare {id} collision check).
        # All string work happens once here rather than inside the hot inner loop.
        path_resource_names: dict[int, str | None] = {}
        # Stripped producer path per operation
        stripped_paths: dict[int, str] = {}
        # (consumer_collection, consumer_collection + "/") for ops with a bare {id} segment
        id_collection_paths: dict[int, tuple[str, str] | None] = {}
        for op in self.operations.values():
            op_id = id(op)
            path_resource_names[op_id] = from_path(op.path)
            stripped_paths[op_id] = strip_version_prefix(op.path)
            segments = op.path.split("/")
            for i, seg in enumerate(segments):
                if seg == "{id}":
                    collection = strip_version_prefix("/".join(segments[:i]) or "/")
                    id_collection_paths[op_id] = (collection, collection + "/")
                    break
            else:
                id_collection_paths[op_id] = None

        # Index consumers by resource
        consumers_by_resource: dict[int, dict[int, tuple[OperationNode, list[InputSlot]]]] = defaultdict(dict)
        for consumer in self.operations.values():
            consumer_id = id(consumer)
            for input_slot in consumer.inputs:
                resource_id = id(input_slot.resource)
                if consumer_id not in consumers_by_resource[resource_id]:
                    consumers_by_resource[resource_id][consumer_id] = (consumer, [])
                consumers_by_resource[resource_id][consumer_id][1].append(input_slot)

        # Resources each path returns as a whole; only keys crossing collections need it, so it is built on demand
        returned_by_path: dict[str, set[int]] | None = None

        path_identifiers: dict[str, set[str]] = {}
        for operation in self.operations.values():
            for input_slot in operation.inputs:
                if input_slot.parameter_location == ParameterLocation.PATH and input_slot.resource_field is not None:
                    path_identifiers.setdefault(input_slot.resource.name, set()).add(input_slot.resource_field)

        for producer in self.operations.values():
            producer_path = encoded_paths[id(producer)]
            producer_id = id(producer)

            for output_slot in producer.outputs:
                # Only iterate over consumers that match this resource
                relevant_consumers = consumers_by_resource.get(id(output_slot.resource), {})
                confirmed = _confirmed_input(producer, output_slot)

                for consumer_id, (consumer, input_slots) in relevant_consumers.items():
                    # Skip self-references
                    if consumer_id == producer_id:
                        continue

                    consumer_path = encoded_paths[consumer_id]
                    links: dict[str, LinkDefinition] = {}
                    # Keys this consumer gives to another namespace's items, so the producer's own are not forwarded
                    foreign_keys: set[str | int] = set()
                    # Links with a binding other than a field named after a collection
                    forwarding: set[str] = set()

                    for input_slot in input_slots:
                        if confirmed is not None and not _takes_confirmed_value(input_slot, confirmed):
                            continue
                        # A parent key is read from an item's own field, and only by producers not under that parent.
                        if input_slot.is_parent_key and (
                            input_slot.parameter_name in template_fields[producer_id]
                            or output_slot.path_parameter is not None
                            or output_slot.body_field is not None
                            or output_slot.is_primitive_identifier
                        ):
                            continue
                        # Scope guard: skip bare {id} links where the producer is from a
                        # different namespace. Triggered only when the resource name matches the
                        # producer's path-derived name — i.e., the collision is path-based, not
                        # schema-based (POST /something -> CategoryFields is left untouched).
                        if (
                            input_slot.parameter_name == "id"
                            and output_slot.resource.name == path_resource_names[producer_id]
                        ):
                            consumer_data = id_collection_paths[consumer_id]
                            if consumer_data is not None:
                                consumer_collection, consumer_collection_prefix = consumer_data
                                producer_collection = stripped_paths[producer_id]
                                # Sub-paths are fine: /users/{userId}/messages -> /users/{id}, and so is a
                                # collection nested under a parent: /lessons/{lessonId}/bookmarks -> /bookmarks/{id}
                                if (
                                    consumer_collection != producer_collection
                                    and not producer_collection.startswith(consumer_collection_prefix)
                                    and not producer_collection.endswith(consumer_collection)
                                ):
                                    foreign_keys.add(input_slot.parameter_name)
                                    continue

                        if output_slot.path_parameter is not None:
                            # Path-keyed producer: the resource value lives in the producer's
                            # request URL, not the response body (e.g. POST /products/{productName}).
                            value_expr = f"$request.path.{output_slot.path_parameter}"
                        elif output_slot.body_field is not None:
                            # Body-keyed producer: the resource value lives in the producer's
                            # request body (e.g. POST /sessions with body {sessionId: ...}).
                            value_expr = f"$request.body#/{output_slot.body_field}"
                        elif output_slot.is_primitive_identifier:
                            # A bare id (POST's created slug, GET's list of ids) fills only identifier fields.
                            identifiers = path_identifiers.get(output_slot.resource.name)
                            field = input_slot.resource_field
                            if (
                                identifiers
                                and field is not None
                                and field not in identifiers
                                and key_kind(field) != KeyKind.IDENTIFIER
                            ):
                                continue
                            # A single created value fills a name only as the key of the created item: `/tags/{tagName}`.
                            if (
                                output_slot.cardinality == Cardinality.ONE
                                and isinstance(input_slot.parameter_name, str)
                                and key_kind(input_slot.parameter_name) == KeyKind.NAME
                                and (
                                    f"/{{{input_slot.parameter_name}}}" not in consumer.path
                                    or trailing_path_parameter(producer.path) is not None
                                )
                            ):
                                continue
                            pointer = output_slot.pointer
                            if output_slot.cardinality == Cardinality.MANY:
                                pointer = pointer.rstrip("/") + "/*"
                            value_expr = f"$response.body#{pointer}"
                        elif input_slot.resource_field is not None:
                            # A name filter's prefix says whose name it takes (`owner_name`); other names are wrong.
                            if (
                                input_slot.parameter_location == ParameterLocation.QUERY
                                and isinstance(input_slot.parameter_name, str)
                                and key_kind(input_slot.parameter_name) == KeyKind.NAME
                                and (
                                    not parameter_names_source(
                                        parameter=input_slot.parameter_name,
                                        field=input_slot.resource_field,
                                        resource=output_slot.resource.name,
                                        pointer=output_slot.pointer,
                                    )
                                    # `Rule.TemplateName` beside `Rule.Name` names a template, not the rule.
                                    or (
                                        any(normalize_for_matching(f) == "name" for f in output_slot.resource.fields)
                                        and all(
                                            output.resource is not output_slot.resource for output in consumer.outputs
                                        )
                                        and not normalize_for_matching(output_slot.resource.name).startswith(
                                            normalize_for_matching(input_slot.resource_field).removesuffix("name")
                                        )
                                    )
                                )
                            ):
                                continue
                            # A pointer to an undeclared response field always resolves to nothing.
                            if (
                                output_slot.resource.source >= DefinitionSource.SCHEMA_WITH_PROPERTIES
                                and input_slot.resource_field not in output_slot.declared_fields
                            ):
                                identifier_pointer = output_slot.identifier_pointer_for(input_slot.resource_field)
                                if identifier_pointer is None:
                                    continue
                                value_expr = f"$response.body#{identifier_pointer}"
                            else:
                                body_pointer = extend_pointer(
                                    output_slot.pointer, input_slot.resource_field, output_slot.cardinality
                                )
                                value_expr = f"$response.body#{body_pointer}"
                        else:
                            # No resource field means use the whole resource
                            value_expr = f"$response.body#{output_slot.pointer}"
                        if value_expr.startswith("$response.body") and output_slot.status_code in ("204", "205"):
                            # These statuses forbid a body, whatever the schema declares.
                            continue
                        link_name = _link_name(consumer.method, input_slot, path_resource_names[consumer_id])
                        parameters, request_body = _link_values(input_slot, value_expr)
                        if not input_slot.is_named_after_collection:
                            forwarding.add(link_name)
                        existing = links.get(link_name)
                        if existing is not None:
                            existing.parameters.update(parameters)
                            existing.request_body = merge_request_body(existing.request_body, request_body)
                            continue
                        links[link_name] = LinkDefinition(
                            operation_ref=f"#/paths/{consumer_path}/{consumer.method}",
                            parameters=parameters,
                            request_body=request_body,
                        )

                    # Propagate shared path parameters that weren't mapped via resources
                    # For /users/{userId}/posts -> /users/{userId}/posts/{postId}, userId is shared
                    # but only postId gets mapped from the response. We need to propagate userId.
                    if links:
                        producer_fields = template_fields[producer_id]
                        listed_identifier = any(slot.is_listed_identifier for slot in input_slots)
                        shared_params = []
                        for name in template_fields[consumer_id]:
                            if name not in producer_fields or (
                                listed_identifier and _keys_different_items(producer, consumer, name)
                            ):
                                continue
                            if _keys_different_collections(producer, consumer, name) or (
                                name in foreign_keys and not _same_parents(producer.path, consumer.path)
                            ):
                                # Both paths return the producer's items, so a key the consumer takes for them is the
                                # same value in a view of that collection (`/patients/{id}`, `/patients_summary/{id}`).
                                if (
                                    output_slot.pointer != "/"
                                    or not _collection_view(producer, consumer, name)
                                    or not any(
                                        slot.parameter_location == ParameterLocation.PATH
                                        and slot.parameter_name == name
                                        for slot in input_slots
                                    )
                                ):
                                    continue
                                if returned_by_path is None:
                                    returned_by_path = _returned_by_path(self.operations.values())
                                if id(output_slot.resource) not in returned_by_path.get(consumer.path, ()):
                                    continue
                            shared_params.append(name)

                        # A link bound only by field names forwards the keys both paths reach through the same
                        # parents (`{owner}`, `{repo}`), not the item keys of their different collections (`{index}`).
                        parent_params = (
                            [name for name in shared_params if not _keys_different_items(producer, consumer, name)]
                            if not forwarding.issuperset(links)
                            else []
                        )
                        for link_name, link_def in links.items():
                            # Find which path parameters are already mapped
                            mapped_params = {name for name, _ in iter_path_parameters(link_def.parameters)}
                            # Add $request.path.X for shared params not already mapped
                            for param in shared_params if link_name in forwarding else parent_params:
                                if param in mapped_params:
                                    continue
                                link_def.parameters[f"path.{param}"] = f"$request.path.{param}"

                        yield ResponseLinks(
                            producer_operation_ref=f"#/paths/{producer_path}/{producer.method}",
                            status_code=output_slot.status_code,
                            links=links,
                        )

        # Generate links from FK fields (e.g., customer_id -> GET /customers/{id})
        yield from self._iter_fk_links(encoded_paths)

    def _iter_fk_links(self, encoded_paths: dict[int, str]) -> Iterator[ResponseLinks]:
        """Generate links from foreign key fields in producer responses.

        For example, if GET /orders/{id} returns {"id": "...", "customer_id": "..."},
        this creates a link to GET /customers/{id} using the customer_id field value.
        """
        # Consumers by the resource field they take; FK values never fill body parameters
        consumers_by_field: dict[tuple[int, str | None], dict[int, tuple[OperationNode, list[InputSlot]]]] = (
            defaultdict(dict)
        )
        for consumer in self.operations.values():
            consumer_id = id(consumer)
            for input_slot in consumer.inputs:
                if input_slot.parameter_location == ParameterLocation.BODY:
                    continue
                consumers = consumers_by_field[(id(input_slot.resource), input_slot.resource_field)]
                if consumer_id not in consumers:
                    consumers[consumer_id] = (consumer, [])
                consumers[consumer_id][1].append(input_slot)

        for producer in self.operations.values():
            producer_path = encoded_paths[id(producer)]
            producer_id = id(producer)

            for output_slot in producer.outputs:
                # Skip primitive identifiers (they don't have FK fields)
                if output_slot.is_primitive_identifier:
                    continue
                # Skip path-keyed and body-keyed outputs (no response body to extract FK fields from)
                if output_slot.path_parameter is not None or output_slot.body_field is not None:
                    continue
                # These statuses forbid a body, whatever the schema declares.
                if output_slot.status_code in ("204", "205"):
                    continue

                resource = output_slot.resource
                fk_links: dict[str, LinkDefinition] = {}
                # Next suffix to try per link name, as names taken once stay taken
                next_suffixes: dict[str, int] = {}
                # The resource merges every schema that described it; this response may declare only some fields.
                declared = output_slot.response_fields

                for fk_field in resource.fk_fields:
                    if declared is not None and fk_field.field_name not in declared:
                        continue
                    # Find the target resource in our resource map
                    target_resource = self.resources.get(fk_field.target_resource)
                    if target_resource is None:
                        continue

                    # Find consumers expecting the field the FK suffix names, e.g. customer_id -> id, user_uuid -> uuid
                    relevant_consumers = consumers_by_field.get((id(target_resource), fk_field.target_field), {})

                    for consumer_id, (consumer, input_slots) in relevant_consumers.items():
                        # Skip self-references
                        if consumer_id == producer_id:
                            continue

                        consumer_path = encoded_paths[consumer_id]

                        for input_slot in input_slots:
                            # A whole array of identifiers fits only a parameter taking an array.
                            if fk_field.holds_array and not input_slot.takes_array:
                                continue
                            # Build the body pointer to the FK field
                            if fk_field.is_array:
                                # For array FK, fan out across every element: /data/*/site_ids/*
                                body_pointer = extend_pointer(
                                    output_slot.pointer, fk_field.field_name, output_slot.cardinality
                                )
                                body_pointer += "/*"  # Fan out across every element of the FK array
                            else:
                                body_pointer = extend_pointer(
                                    output_slot.pointer, fk_field.field_name, output_slot.cardinality
                                )

                            parameters = {
                                f"{input_slot.parameter_location.value}.{input_slot.parameter_name}": f"$response.body#{body_pointer}",
                            }
                            operation_ref = f"#/paths/{consumer_path}/{consumer.method}"
                            link_name = _unique_link_name(
                                f"{consumer.method.capitalize()}{fk_field.target_resource}",
                                fk_links,
                                operation_ref,
                                next_suffixes,
                            )
                            if link_name is None:
                                continue

                            fk_links[link_name] = LinkDefinition(
                                operation_ref=operation_ref,
                                parameters=parameters,
                                request_body={},
                            )

                # Process nested FK fields (e.g., shipping.warehouse_id, line_items[].product_id)
                for nested_fk in resource.nested_fk_fields:
                    if declared is not None and next(iter_decoded_pointer_segments(nested_fk.pointer)) not in declared:
                        continue
                    target_resource = self.resources.get(nested_fk.target_resource)
                    if target_resource is None:
                        continue

                    relevant_consumers = consumers_by_field.get((id(target_resource), nested_fk.target_field), {})

                    for consumer_id, (consumer, input_slots) in relevant_consumers.items():
                        if consumer_id == producer_id:
                            continue

                        consumer_path = encoded_paths[consumer_id]

                        for input_slot in input_slots:
                            if nested_fk.holds_array and not input_slot.takes_array:
                                continue
                            # Build pointer: output_slot.pointer + nested_fk.pointer
                            if output_slot.cardinality == Cardinality.MANY:
                                base = output_slot.pointer.rstrip("/") + "/*"
                            else:
                                base = output_slot.pointer.rstrip("/")
                            body_pointer = base + nested_fk.pointer
                            if nested_fk.is_array:
                                body_pointer += "/*"

                            operation_ref = f"#/paths/{consumer_path}/{consumer.method}"
                            link_name = _unique_link_name(
                                f"{consumer.method.capitalize()}{nested_fk.target_resource}",
                                fk_links,
                                operation_ref,
                                next_suffixes,
                            )
                            if link_name is None:
                                continue

                            fk_links[link_name] = LinkDefinition(
                                operation_ref=operation_ref,
                                parameters={
                                    f"{input_slot.parameter_location.value}.{input_slot.parameter_name}": f"$response.body#{body_pointer}",
                                },
                                request_body={},
                            )

                if fk_links:
                    yield ResponseLinks(
                        producer_operation_ref=f"#/paths/{producer_path}/{producer.method}",
                        status_code=output_slot.status_code,
                        links=fk_links,
                    )

    def assert_fieldless_resources(self, key: str, known: dict[str, frozenset[str]]) -> None:  # pragma: no cover
        """Verify all resources have at least one field."""
        # Fieldless resources usually indicate failed schema extraction, which can be caused by a bug
        known_fieldless = known.get(key, frozenset())

        for name, resource in self.resources.items():
            if not resource.fields and name not in known_fieldless:
                raise AssertionError(f"Resource {name} has no fields")

    def assert_incorrect_field_mappings(self, key: str, known: dict[str, frozenset[str]]) -> None:
        """Verify all input slots reference valid fields in their resources."""
        known_mismatches = known.get(key, frozenset())

        for operation in self.operations.values():
            for input in operation.inputs:
                # Skip unreliable definition sources
                if input.resource.source < DefinitionSource.SCHEMA_WITH_PROPERTIES:
                    continue
                resource = self.resources[input.resource.name]
                if (
                    input.resource_field not in resource.fields
                    and resource.name not in known_mismatches
                    and input.resource_field is not None
                ):  # pragma: no cover
                    message = (
                        f"Operation '{operation.method.upper()} {operation.path}': "
                        f"InputSlot references field '{input.resource_field}' "
                        f"not found in resource '{resource.name}'"
                    )
                    matches = difflib.get_close_matches(input.resource_field, resource.fields, n=1, cutoff=0.6)
                    if matches:
                        message += f". Closest field - `{matches[0]}`"
                    if resource.fields:
                        message += f". Available fields - {', '.join(resource.fields)}"
                    else:
                        message += ". Resource has no fields"
                    raise AssertionError(message)


# FK field suffix -> (target_field, is_array)
# Maps FK naming conventions to the identifier field they reference
FK_SUFFIX_MAP: dict[str, tuple[str, bool]] = {
    "_ids": ("id", True),
    "_uuids": ("uuid", True),
    "_guids": ("guid", True),
    "_id": ("id", False),
    "_uuid": ("uuid", False),
    "_guid": ("guid", False),
}

# camelCase boundaries; longest plural suffix first so `Ids` matches before `Id`.
_CAMEL_FK_SUFFIXES: tuple[tuple[str, str, bool], ...] = (
    ("Ids", "id", True),
    ("Uuids", "uuid", True),
    ("Guids", "guid", True),
    ("Id", "id", False),
    ("Uuid", "uuid", False),
    ("Guid", "guid", False),
)
# Floor below which the leading lowercase run is unlikely to name a real resource;
# blocks `bId`/`fId`/`lId` style fragments while still permitting names like `Order`.
_CAMEL_FK_MIN_BASE_LEN = 3


@lru_cache(maxsize=2048)
def infer_fk_target(field: str) -> tuple[str, str, bool] | None:
    """Extract target resource name and field from a FK field name.

    Returns (resource_name, target_field, is_array) or None if not a FK field.

    Examples:
        customer_id -> ("Customer", "id", False)
        site_ids -> ("Site", "id", True)
        user_uuid -> ("User", "uuid", False)
        session_guids -> ("Session", "guid", True)
        locationId -> ("Location", "id", False)
        orderGuids -> ("Order", "guid", True)

    """
    field_lower = field.lower()

    # snake_case path. Check suffixes (longer ones first to match _ids before _id).
    for suffix, (target_field, is_array) in FK_SUFFIX_MAP.items():
        # Skip bare identifier fields (not FKs, they're primary identifiers)
        if field_lower == suffix.lstrip("_"):
            return None
        if field_lower.endswith(suffix) and len(field) > len(suffix):
            base_name = field[: -len(suffix)]
            if base_name:
                return to_pascal_case(base_name), target_field, is_array

    # camelCase path. Base must start with an ASCII lowercase letter; rules out
    # PascalCase types and prevents the bare `Id` from matching as a FK.
    if not field or not ("a" <= field[0] <= "z"):
        return None
    for suffix, target_field, is_array in _CAMEL_FK_SUFFIXES:
        if not field.endswith(suffix):
            continue
        base_name = field[: -len(suffix)]
        if len(base_name) >= _CAMEL_FK_MIN_BASE_LEN and base_name.isascii() and base_name.isalnum():
            return to_pascal_case(base_name), target_field, is_array

    return None


def declared_types(schema: Mapping[str, Any]) -> frozenset[str] | None:
    """Types the `type` keyword of `schema` lists; `None` without one."""
    declared = schema.get("type")
    if isinstance(declared, str):
        return frozenset((declared,))
    if isinstance(declared, list):
        return frozenset(item for item in declared if isinstance(item, str))
    return None


def holds_only_arrays(schemas: Iterable[Mapping[str, Any]]) -> bool:
    """Whether one of `schemas` declares arrays and maybe `null`, nothing else."""
    return any(
        types is not None and "array" in types and types <= {"array", "null"} for types in map(declared_types, schemas)
    )


Scope = TypeVar("Scope")


def iter_all_of_chain(
    schema: object, scope: Scope, resolve: Callable[[dict[str, Any], Scope], tuple[Scope, object] | None]
) -> Iterator[dict[str, Any]]:
    """`schema`, its references and its `allOf` parents: the schemas every value of `schema` matches.

    `resolve` follows a `$ref` from the scope it appears in to the target and the target's scope.
    """
    pending: list[tuple[Scope, object]] = [(scope, schema)]
    # `allOf` chains may be recursive.
    seen: set[int] = set()
    while pending:
        current_scope, node = pending.pop()
        if not isinstance(node, dict) or id(node) in seen:
            continue
        seen.add(id(node))
        yield node
        if "$ref" in node:
            target = resolve(node, current_scope)
            if target is not None:
                pending.append(target)
        branches = node.get("allOf")
        if isinstance(branches, list):
            pending.extend((current_scope, branch) for branch in branches)


def _resolve_reference(schema: dict[str, Any], resolver: Resolver) -> tuple[Resolver, Mapping[str, Any]] | None:
    try:
        return maybe_resolve_with_resolver(schema, resolver)
    except (RefResolutionError, InvalidSchema):
        # An unusable reference declares nothing.
        return None


def extract_fk_fields(properties: Mapping[str, tuple[Resolver, JsonSchema]]) -> list[FKField]:
    """Extract FK fields from the properties of a resource schema.

    Pre-computes FK information once during resource creation
    to avoid repeated inference during link generation.
    """
    result = []
    for field in sorted(properties):
        fk_info = infer_fk_target(field)
        if fk_info is not None:
            target_resource, target_field, is_array = fk_info
            field_resolver, subschema = properties[field]
            result.append(
                FKField(
                    field_name=field,
                    target_resource=target_resource,
                    target_field=target_field,
                    is_array=is_array,
                    holds_array=not is_array
                    and holds_only_arrays(iter_all_of_chain(subschema, field_resolver, _resolve_reference)),
                )
            )
    return result


def extract_nested_fk_fields(
    schema: Mapping[str, Any],
    resolver: Resolver,
    pointer: str = "",
    max_depth: int = 5,
) -> list[NestedFKField]:
    """Recursively extract FK fields from nested schema properties.

    This finds FK fields (like warehouse_id, product_ids) at any nesting depth
    within an object schema, including inside arrays.

    Args:
        schema: The JSON schema to scan
        resolver: JSON reference resolver
        pointer: Current JSON pointer path
        max_depth: Maximum recursion depth to prevent infinite loops

    Returns:
        List of NestedFKField objects for all FK fields found

    """
    if max_depth <= 0:
        return []

    result: list[NestedFKField] = []
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return result
    for field_name, field_schema in properties.items():
        if not isinstance(field_schema, dict):
            continue

        field_pointer = f"{pointer}/{encode_pointer(field_name)}"

        # Check if this field is a FK field
        fk_info = infer_fk_target(field_name)
        if fk_info is not None:
            # Skip top-level FK fields - they're already captured in fk_fields
            if pointer:
                target_resource, target_field, is_array = fk_info
                result.append(
                    NestedFKField(
                        pointer=field_pointer,
                        field_name=field_name,
                        target_resource=target_resource,
                        target_field=target_field,
                        is_array=is_array,
                        holds_array=not is_array
                        and holds_only_arrays(iter_all_of_chain(field_schema, resolver, _resolve_reference)),
                    )
                )
            continue  # Don't recurse into FK fields

        # Resolve nested schema
        field_resolver, resolved_field = maybe_resolve_with_resolver(field_schema, resolver)
        field_type = resolved_field.get("type")

        # Recurse into nested objects
        if field_type == "object" or "properties" in resolved_field:
            result.extend(extract_nested_fk_fields(resolved_field, field_resolver, field_pointer, max_depth - 1))

        # Recurse into array items
        elif field_type == "array":
            items = resolved_field.get("items")
            if isinstance(items, dict):
                items_resolver, resolved_items = maybe_resolve_with_resolver(items, field_resolver)
                if isinstance(resolved_items, dict):
                    # Use /* to fan out across every array element
                    items_pointer = f"{field_pointer}/*"
                    result.extend(
                        extract_nested_fk_fields(resolved_items, items_resolver, items_pointer, max_depth - 1)
                    )

    return result


def _confirmed_input(producer: OperationNode, output_slot: OutputSlot) -> InputSlot | None:
    """The request input whose value a path- or body-keyed output confirms."""
    if output_slot.path_parameter is not None:
        location, name = ParameterLocation.PATH, output_slot.path_parameter
    elif output_slot.body_field is not None:
        location, name = ParameterLocation.BODY, output_slot.body_field
    else:
        return None
    return find_input(producer.inputs, location, name)


def _takes_confirmed_value(input_slot: InputSlot, confirmed: InputSlot) -> bool:
    # The confirmed request value is a single scalar key, and a name never stands in for an identifier.
    if input_slot.echoes_response:
        return False
    field = input_slot.resource_field
    if field is not None and input_slot.resource.types.get(field) == {"array"}:
        return False
    return {_key_kind(input_slot.parameter_name), _key_kind(confirmed.parameter_name)} != {
        KeyKind.NAME,
        KeyKind.IDENTIFIER,
    }


def _link_name(method: str, input_slot: InputSlot, consumer_resource_name: str | None) -> str:
    resource_name = input_slot.resource.name
    # Unnamed subschemas are keyed by their pointer, which is not a name to show users.
    if "#/" in resource_name:
        resource_name = consumer_resource_name or "Resource"
    # A separate link, so other links keep generating these fields.
    suffix = "Echo" if input_slot.echoes_response else ""
    return f"{method.capitalize()}{resource_name}{suffix}"


def _link_values(input_slot: InputSlot, value_expr: str) -> tuple[dict[str, str], dict[str, Any] | list[Any]]:
    """Link parameters and request body that send `value_expr` to the input."""
    if input_slot.parameter_location == ParameterLocation.BODY:
        return {}, build_nested_body(input_slot.body_path or (input_slot.parameter_name,), value_expr)
    return {f"{input_slot.parameter_location.value}.{input_slot.parameter_name}": value_expr}, {}


def _names_identifier(field: str, resource_name: str) -> bool:
    # `id` or `<resource>Id`: the field that identifies the resource itself, not one it refers to
    return normalize_for_matching(field) in ("id", normalize_for_matching(resource_name) + "id")


def _key_kind(parameter_name: str | int) -> KeyKind | None:
    if isinstance(parameter_name, int):
        return None
    return key_kind(parameter_name)


def build_nested_body(path: BodyPath, value: object) -> dict[str, Any] | list[Any]:
    """Build a body holding `value` at `path`, integers marking array items.

    Examples:
        build_nested_body(("customer_id",), "$val") -> {"customer_id": "$val"}
        build_nested_body(("shipping", "warehouse_id"), "$val") -> {"shipping": {"warehouse_id": "$val"}}
        build_nested_body(("items", 0, "product_id"), "$val") -> {"items": [{"product_id": "$val"}]}
        build_nested_body((0, "product_id"), "$val") -> [{"product_id": "$val"}]

    """
    *parents, leaf = path
    body: dict[str, Any] | list[Any] = [value] if isinstance(leaf, int) else {leaf: value}
    for step in reversed(parents):
        body = [body] if isinstance(step, int) else {step: body}
    return body


def merge_request_body(
    target: dict[str, Any] | list[Any], source: dict[str, Any] | list[Any]
) -> dict[str, Any] | list[Any]:
    """Deep merge `source` into `target` and return the result; `source` wins when the shapes differ."""
    inner_target, inner_source = target, source
    if isinstance(target, list) and isinstance(source, list):
        # Fields of top-level array items merge like those of nested array items.
        inner_target, inner_source = target[0], source[0]
    if isinstance(inner_target, dict) and isinstance(inner_source, dict):
        _merge_nested_body(inner_target, inner_source)
        return target
    return source


def _merge_nested_body(target: dict[str, Any], source: dict[str, Any]) -> None:
    """Deep merge source into target dict."""
    for key, value in source.items():
        if key in target and isinstance(target[key], dict) and isinstance(value, dict):
            _merge_nested_body(target[key], value)
        elif key in target and isinstance(target[key], list) and isinstance(value, list):
            # Merge list items (for array body fields)
            _merge_nested_body(target[key][0], value[0])
        else:
            target[key] = value


def extend_pointer(base: str, field: str, parent_cardinality: Cardinality) -> str:
    if not base.endswith("/"):
        base += "/"
    if parent_cardinality == Cardinality.MANY:
        # Wildcard sentinel: pool extraction and link substitution fan out across every element.
        base += "*/"
    base += encode_pointer(field)
    return base


@dataclass(slots=True)
class LinkDefinition:
    """OpenAPI Link Object definition.

    Represents a single link from a producer operation's response to a
    consumer operation's input parameter.
    """

    operation_ref: str
    """Reference to target operation (e.g., '#/paths/~1users~1{id}/get')"""

    parameters: dict[str, str]
    """Parameter mappings (e.g., {'path.id': '$response.body#/id'})"""

    request_body: dict[str, str] | list
    """Request body (e.g., {'path.id': '$response.body#/id'})"""

    def to_openapi(self) -> dict[str, Any]:
        """Convert to OpenAPI Links format."""
        links: dict[str, Any] = {
            "operationRef": self.operation_ref,
            SCHEMATHESIS_LINK_EXTENSION: {"is_inferred": True},
        }
        if self.parameters:
            links["parameters"] = self.parameters
        if self.request_body:
            links["requestBody"] = self.request_body
            links[SCHEMATHESIS_LINK_EXTENSION]["merge_body"] = True
        return links


@dataclass(slots=True)
class ResponseLinks:
    """Collection of OpenAPI Links for a producer operation's response.

    Represents all links from a single response (e.g., POST /users -> 201)
    to consumer operations that can use the produced resource.

    Example:
        POST /users -> 201 might have links to:
        - GET /users/{id}
        - PATCH /users/{id}
        - DELETE /users/{id}

    """

    producer_operation_ref: str
    """Reference to producer operation (e.g., '#/paths/~1users/post')"""

    status_code: str
    """Response status code (e.g., '201', '200', 'default')"""

    links: dict[str, LinkDefinition]
    """Named links (e.g., {'GetUserById': LinkDefinition(...)})"""

    def to_openapi(self) -> dict[str, Any]:
        """Convert to OpenAPI response links format."""
        return {name: link_def.to_openapi() for name, link_def in self.links.items()}


@dataclass(slots=True)
class NormalizedLink:
    """Normalized representation of a link."""

    path: str
    method: str
    parameters: set[str]
    request_body: Any


@dataclass(slots=True)
class OperationNode:
    """An API operation with its input/output dependencies."""

    method: str
    path: str
    # What this operation NEEDS
    inputs: list[InputSlot]
    # What this operation PRODUCES
    outputs: list[OutputSlot]


def find_input(inputs: Iterable[InputSlot], location: ParameterLocation, name: str) -> InputSlot | None:
    """The first input bound to the `location` parameter called `name`."""
    return next((slot for slot in inputs if slot.parameter_location == location and slot.parameter_name == name), None)


# A body field location, integers marking array items.
BodyPath: TypeAlias = tuple[str | int, ...]
# FK fields behind a bundled reference, by the reference's original URI and the remaining depth.
NestedFKCache: TypeAlias = dict[tuple[str, int], list[tuple[BodyPath, str, str]]]


@dataclass(slots=True)
class DeferredBindings:
    """Inputs of one operation that bind only once every operation has been scanned."""

    # Nested-body FK lookups whose target resource was not registered yet.
    nested_fks: list[tuple[str, str, BodyPath]]
    # Scalar body fields that may name the collection listing their values (`country` <- `GET /countries`).
    named_scalars: list[tuple[str, str, JsonSchema]]
    # Query parameters that may name the collection listing their values (`?part=` <- `GET /parts`).
    named_query_parameters: list[tuple[str, JsonSchema]]
    # Required scalar body fields.
    body_fields: list[tuple[str, JsonSchema]]
    # `(resource, parameter)` for path parameters named after a field of their owning resource (`/projects/{code}`).
    field_named_path_parameters: list[tuple[str, str]]
    # Body fields a client may send.
    writable_body_fields: dict[str, JsonSchema]
    # Body fields named after one identifier that take only arrays (`customer_id: [1, 2]`).
    identifier_array_fields: set[str]

    def __init__(self) -> None:
        self.nested_fks = []
        self.named_scalars = []
        self.named_query_parameters = []
        self.body_fields = []
        self.field_named_path_parameters = []
        self.writable_body_fields = {}
        self.identifier_array_fields = set()


@dataclass(slots=True)
class InputSlot:
    """A required input for an operation."""

    # Which resource is needed
    resource: ResourceDefinition
    # Which field from that resource (e.g., "id").
    # None if passing the whole resource
    resource_field: str | None
    # Where it goes in the request (e.g., "userId")
    # Integer means index in an array (only single items are supported)
    parameter_name: str | int
    parameter_location: ParameterLocation
    # Whether a parameter takes an array of values, e.g. `?ids=1&ids=2`
    takes_array: bool = False
    # Whether this input was matched via suffix matching (e.g., "file_name" -> "BackupFile")
    # Suffix-matched inputs can be upgraded by merge_related_resources if a producer exists
    is_suffix_matched: bool = False
    # Whether this input sends back a value a response returned, e.g. a `version` token for an update
    echoes_response: bool = False
    # Whether a parent key named after its collection takes the identifier the collection lists (`{warehouse}`)
    is_listed_identifier: bool = False
    # Whether this input takes the key of the parent an item lives under, read from the item (`Shipment.warehouseId`)
    is_parent_key: bool = False
    # Where a nested body field lands, e.g. `("items", 0, "product_id")`; empty for a top-level parameter
    body_path: BodyPath = ()
    # Whether only the field's name ties it to the collection (`part`), not an identifier suffix (`part_id`)
    is_named_after_collection: bool = False


@dataclass(slots=True)
class OutputSlot:
    """Describes how to extract a resource from an operation's response."""

    # Which resource type
    resource: ResourceDefinition
    # Where in response body (JSON pointer)
    pointer: str
    # Is this a single resource or an array?
    cardinality: Cardinality
    # HTTP status code
    status_code: str
    # True when response is a bare primitive (string/int) rather than an object
    is_primitive_identifier: bool = False
    # Set when the resource value comes from a request path parameter rather
    # than the response body (POST/PUT to `/products/{productName}` confirms
    # the product exists once the response is 2xx).
    path_parameter: str | None = None
    # Set when the resource value comes from a request body field rather than
    # the response body (POST `/sessions {sessionId: ...}` confirms the session
    # exists once the response is 2xx).
    body_field: str | None = None
    # True when the response body is a map keyed by identifier; capture every map key
    # as a resource instance (e.g. `{<teamKey>: {...}, ...}` from `GET /teams/statuses`).
    extract_object_keys: bool = False
    # Fields the response declares at `pointer`; `None` when it declares none.
    # Resources are shared by name, so this can differ from `resource.fields`.
    response_fields: frozenset[str] | None = None
    # Envelope field identifying the resource when the resource itself declares no identifier
    # (`/bookingid` in `{bookingid: 1, booking: {...}}`).
    identifier_pointer: str | None = None

    @property
    def declared_fields(self) -> Collection[str]:
        return self.resource.fields if self.response_fields is None else self.response_fields

    def identifier_pointer_for(self, field: str) -> str | None:
        """Envelope pointer holding `field`, when `field` is the resource's own identifier and the resource lacks it."""
        if (
            self.identifier_pointer is None
            or field in self.declared_fields
            or not _names_identifier(field, self.resource.name)
        ):
            return None
        return self.identifier_pointer


@dataclass(slots=True)
class FKField:
    """A top-level foreign key field in a resource schema."""

    # The FK field name (e.g., "customer_id")
    field_name: str
    # Target resource name inferred from field (e.g., "Customer")
    target_resource: str
    # Target field the FK references (e.g., "id", "uuid")
    target_field: str
    # Whether the FK field is an array (e.g., site_ids)
    is_array: bool
    # Whether a singular name holds a whole array of identifiers, e.g. `site_id: [1, 2]`
    holds_array: bool = False


@dataclass(slots=True)
class NestedFKField:
    """A foreign key field found at a nested path in a resource schema."""

    # JSON pointer path to the FK field (e.g., "/shipping/warehouse_id")
    pointer: str
    # The FK field name (e.g., "warehouse_id")
    field_name: str
    # Target resource name inferred from field (e.g., "Warehouse")
    target_resource: str
    # Target field the FK references (e.g., "id", "uuid")
    target_field: str
    # Whether the FK field is an array (e.g., site_ids)
    is_array: bool
    # Whether a singular name holds a whole array of identifiers, e.g. `site_id: [1, 2]`
    holds_array: bool = False


@dataclass(slots=True)
class ResourceDefinition:
    """A minimal description of a resource structure."""

    name: str
    # A sorted list of resource fields
    fields: list[str]
    # Field types mapping
    types: dict[str, set[str]]
    # How this resource was created
    source: DefinitionSource
    # Top-level FK fields (e.g., customer_id, order_ids)
    fk_fields: list[FKField]
    # FK fields found at nested paths in the schema
    nested_fk_fields: list[NestedFKField]
    # Fields inherited through `allOf` rather than declared by the schema itself
    inherited_fields: frozenset[str] = frozenset()

    @classmethod
    def without_properties(cls, name: str) -> ResourceDefinition:
        return cls(
            name=name,
            fields=[],
            types={},
            source=DefinitionSource.SCHEMA_WITHOUT_PROPERTIES,
            fk_fields=[],
            nested_fk_fields=[],
        )

    @classmethod
    def inferred_from_parameter(cls, name: str, parameter_name: str | None) -> ResourceDefinition:
        fields = [parameter_name] if parameter_name is not None else []
        return cls(
            name=name,
            fields=fields,
            types={},
            source=DefinitionSource.PARAMETER_INFERENCE,
            fk_fields=[],
            nested_fk_fields=[],
        )


def _unique_link_name(
    base: str, links: dict[str, LinkDefinition], operation_ref: str, next_suffixes: dict[str, int]
) -> str | None:
    """Keep the first link's name, and give a different destination sharing it one of its own."""
    existing = links.get(base)
    if existing is None:
        return base
    if existing.operation_ref == operation_ref:
        return None
    suffix = next_suffixes.get(base, 2)
    while f"{base}{suffix}" in links:
        suffix += 1
    next_suffixes[base] = suffix + 1
    return f"{base}{suffix}"


class DefinitionSource(enum.IntEnum):
    """Quality level of resource information.

    Lower values are less reliable and should be replaced by higher values.
    Same values should be merged (union of fields).
    """

    # From spec but no structural information
    SCHEMA_WITHOUT_PROPERTIES = 0
    # Guessed from parameter names (not in spec)
    PARAMETER_INFERENCE = 1
    # From spec with actual field definitions
    SCHEMA_WITH_PROPERTIES = 2


OperationMap: TypeAlias = dict[str, OperationNode]
ResourceMap: TypeAlias = dict[str, ResourceDefinition]
CanonicalizationCache: TypeAlias = dict[str, Mapping[str, Any]]


def _returned_by_path(operations: Iterable[OperationNode]) -> dict[str, set[int]]:
    returned: dict[str, set[int]] = {}
    for operation in operations:
        for output_slot in operation.outputs:
            if output_slot.pointer == "/":
                returned.setdefault(operation.path, set()).add(id(output_slot.resource))
    return returned


def _keys_different_items(producer: OperationNode, consumer: OperationNode, parameter: str) -> bool:
    """Whether `{parameter}` sits under different parents, so each operation names a different item with it.

    `/warehouses/{name}` and `/warehouses/{warehouse}/shelves/{name}/bins` share `{name}`, but one is a warehouse
    and the other a shelf.
    """
    return _parents_differ(producer.path, consumer.path, parameter)


@lru_cache(maxsize=4096)
def _parents_differ(producer_path: str, consumer_path: str, parameter: str) -> bool:
    placeholder = "/{" + parameter + "}"
    return path_shape(producer_path.split(placeholder, 1)[0]) != path_shape(consumer_path.split(placeholder, 1)[0])


_GENERIC_KEYS = frozenset({"id", "ids", "pk", "uuid", "guid", "uid", "name"})


def _keys_different_collections(producer: OperationNode, consumer: OperationNode, parameter: str) -> bool:
    """Whether a generic `{parameter}` follows different collections, so each operation names a different item with it.

    `/customers/{id}` and `/orders/{id}` share `{id}`, but one is a customer and the other an order.
    """
    if normalize_for_matching(parameter) not in _GENERIC_KEYS:
        return False
    producer_owner = owning_resource(parameter, producer.path)
    consumer_owner = owning_resource(parameter, consumer.path)
    return (
        producer_owner is not None
        and consumer_owner is not None
        and normalize_for_matching(producer_owner) != normalize_for_matching(consumer_owner)
    )


def _same_parents(producer_path: str, consumer_path: str) -> bool:
    """Whether two paths may address the same item: equal static segments, and parameters at the same places.

    A parameter's label must match unless one side leaves it bare: `/teams/{team}/invoices/{id}` and
    `/{owner}/invoices/{id}` may name one team, `/slots/{slot}/...` and `/instances/{instance}/...` do not.
    """
    producer_segments = _labeled_segments(producer_path)
    consumer_segments = _labeled_segments(consumer_path)
    return len(producer_segments) == len(consumer_segments) and all(
        left == right or (left[0] == right[0] == "" and "" in (left[1], right[1]))
        for left, right in zip(producer_segments, consumer_segments, strict=True)
    )


@lru_cache(maxsize=1024)
def _labeled_segments(path: str) -> tuple[tuple[str, str], ...]:
    # `("", label)` for a parameter and the static segment naming it, `(segment, "")` for any other static segment.
    segments = [segment for segment in strip_version_prefix(path).split("/") if segment]
    labeled: list[tuple[str, str]] = []
    for index, segment in enumerate(segments):
        if segment.startswith("{"):
            label = segments[index - 1] if index > 0 and not segments[index - 1].startswith("{") else ""
            labeled.append(("", label))
        elif index + 1 == len(segments) or not segments[index + 1].startswith("{"):
            labeled.append((segment, ""))
    return tuple(labeled)


def _collection_view(producer: OperationNode, consumer: OperationNode, parameter: str) -> bool:
    """Whether one collection holding `{parameter}` is named as a view of the other (`patients_summary`, `patients`).

    Collections with the same name under different parents (`/targets/{target_id}/webhooks`, `/webhooks`) often hold
    different items even when they share a schema.
    """
    producer_name = normalize_for_matching(owning_resource(parameter, producer.path) or "")
    consumer_name = normalize_for_matching(owning_resource(parameter, consumer.path) or "")
    return (
        bool(producer_name)
        and bool(consumer_name)
        and producer_name != consumer_name
        and (producer_name.startswith(consumer_name) or consumer_name.startswith(producer_name))
    )


_PATH_PARAMETER = re.compile(r"\{[^}]*\}")


@lru_cache(maxsize=4096)
def path_shape(path: str) -> str:
    return _PATH_PARAMETER.sub("{}", path)
