from __future__ import annotations

import difflib
import enum
from collections import defaultdict
from collections.abc import Iterator, Mapping
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any, TypeAlias

from schemathesis.core.parameters import ParameterLocation
from schemathesis.core.transforms import encode_pointer, get_template_fields
from schemathesis.resources.descriptors import Cardinality
from schemathesis.specs.openapi.stateful.dependencies.naming import to_pascal_case
from schemathesis.specs.openapi.stateful.links import SCHEMATHESIS_LINK_EXTENSION

if TYPE_CHECKING:
    from schemathesis.core.compat import RefResolver


@dataclass
class DependencyGraph:
    """Graph of API operations and their resource dependencies."""

    operations: OperationMap
    resources: ResourceMap

    __slots__ = ("operations", "resources")

    def serialize(self) -> dict[str, Any]:
        serialized = asdict(self)

        for operation in serialized["operations"].values():
            del operation["method"]
            del operation["path"]
            for input in operation["inputs"]:
                input["resource"] = input["resource"]["name"]
            for output in operation["outputs"]:
                output["resource"] = output["resource"]["name"]

        for resource in serialized["resources"].values():
            del resource["name"]
            del resource["source"]
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

        # Index consumers by resource
        consumers_by_resource: dict[int, dict[int, tuple[OperationNode, list[InputSlot]]]] = defaultdict(dict)
        for consumer in self.operations.values():
            consumer_id = id(consumer)
            for input_slot in consumer.inputs:
                resource_id = id(input_slot.resource)
                if consumer_id not in consumers_by_resource[resource_id]:
                    consumers_by_resource[resource_id][consumer_id] = (consumer, [])
                consumers_by_resource[resource_id][consumer_id][1].append(input_slot)

        for producer in self.operations.values():
            producer_path = encoded_paths[id(producer)]
            producer_id = id(producer)

            for output_slot in producer.outputs:
                # Only iterate over consumers that match this resource
                relevant_consumers = consumers_by_resource.get(id(output_slot.resource), {})

                for consumer_id, (consumer, input_slots) in relevant_consumers.items():
                    # Skip self-references
                    if consumer_id == producer_id:
                        continue

                    consumer_path = encoded_paths[consumer_id]
                    links: dict[str, LinkDefinition] = {}

                    for input_slot in input_slots:
                        if output_slot.is_primitive_identifier:
                            # Primitive identifier (e.g., string response from POST)
                            # The whole response IS the identifier value
                            body_pointer = output_slot.pointer
                        elif input_slot.resource_field is not None:
                            body_pointer = extend_pointer(
                                output_slot.pointer, input_slot.resource_field, output_slot.cardinality
                            )
                        else:
                            # No resource field means use the whole resource
                            body_pointer = output_slot.pointer
                        link_name = f"{consumer.method.capitalize()}{input_slot.resource.name}"
                        parameters = {}
                        request_body: dict[str, Any] | list = {}
                        # Data is extracted from response body
                        if input_slot.parameter_location == ParameterLocation.BODY:
                            if isinstance(input_slot.parameter_name, int):
                                request_body = [f"$response.body#{body_pointer}"]
                            else:
                                request_body = _build_nested_body(
                                    input_slot.parameter_name, f"$response.body#{body_pointer}"
                                )
                        else:
                            parameters = {
                                f"{input_slot.parameter_location.value}.{input_slot.parameter_name}": f"$response.body#{body_pointer}",
                            }
                        existing = links.get(link_name)
                        if existing is not None:
                            existing.parameters.update(parameters)
                            if isinstance(existing.request_body, dict) and isinstance(request_body, dict):
                                _merge_nested_body(existing.request_body, request_body)
                            else:
                                existing.request_body = request_body
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
                        producer_params = get_template_fields(producer.path)
                        consumer_params = get_template_fields(consumer.path)
                        shared_params = producer_params & consumer_params

                        for link_def in links.values():
                            # Find which path parameters are already mapped
                            mapped_params = {
                                key.split(".", 1)[1] for key in link_def.parameters if key.startswith("path.")
                            }
                            # Add $request.path.X for shared params not already mapped
                            for param in shared_params - mapped_params:
                                link_def.parameters[f"path.{param}"] = f"$request.path.{param}"

                        yield ResponseLinks(
                            producer_operation_ref=f"#/paths/{producer_path}/{producer.method}",
                            status_code=output_slot.status_code,
                            links=links,
                        )

        # Generate links from FK fields (e.g., customer_id -> GET /customers/{id})
        yield from self._iter_fk_links(encoded_paths, consumers_by_resource)

    def _iter_fk_links(
        self,
        encoded_paths: dict[int, str],
        consumers_by_resource: dict[int, dict[int, tuple[OperationNode, list[InputSlot]]]],
    ) -> Iterator[ResponseLinks]:
        """Generate links from foreign key fields in producer responses.

        For example, if GET /orders/{id} returns {"id": "...", "customer_id": "..."},
        this creates a link to GET /customers/{id} using the customer_id field value.
        """
        for producer in self.operations.values():
            producer_path = encoded_paths[id(producer)]
            producer_id = id(producer)

            for output_slot in producer.outputs:
                # Skip primitive identifiers (they don't have FK fields)
                if output_slot.is_primitive_identifier:
                    continue

                resource = output_slot.resource
                fk_links: dict[str, LinkDefinition] = {}

                for fk_field in resource.fk_fields:
                    # Find the target resource in our resource map
                    target_resource = self.resources.get(fk_field.target_resource)
                    if target_resource is None:
                        continue

                    # Find consumers that need the target resource
                    relevant_consumers = consumers_by_resource.get(id(target_resource), {})

                    for consumer_id, (consumer, input_slots) in relevant_consumers.items():
                        # Skip self-references
                        if consumer_id == producer_id:
                            continue

                        consumer_path = encoded_paths[consumer_id]

                        for input_slot in input_slots:
                            # Match FK suffix to consumer's expected field
                            # e.g., customer_id -> id, user_uuid -> uuid
                            if input_slot.resource_field != fk_field.target_field:
                                continue

                            # Skip body parameters for FK linking
                            if input_slot.parameter_location == ParameterLocation.BODY:
                                continue

                            # Build the body pointer to the FK field
                            if fk_field.is_array:
                                # For array FK, reference first element: /data/0/site_ids/0
                                body_pointer = extend_pointer(
                                    output_slot.pointer, fk_field.field_name, output_slot.cardinality
                                )
                                body_pointer += "/0"  # Access first element of the FK array
                            else:
                                body_pointer = extend_pointer(
                                    output_slot.pointer, fk_field.field_name, output_slot.cardinality
                                )

                            link_name = f"{consumer.method.capitalize()}{fk_field.target_resource}"
                            parameters = {
                                f"{input_slot.parameter_location.value}.{input_slot.parameter_name}": f"$response.body#{body_pointer}",
                            }

                            # Avoid duplicate links (same target operation)
                            if link_name in fk_links:
                                continue

                            fk_links[link_name] = LinkDefinition(
                                operation_ref=f"#/paths/{consumer_path}/{consumer.method}",
                                parameters=parameters,
                                request_body={},
                            )

                # Process nested FK fields (e.g., shipping.warehouse_id, line_items[].product_id)
                for nested_fk in resource.nested_fk_fields:
                    target_resource = self.resources.get(nested_fk.target_resource)
                    if target_resource is None:
                        continue

                    relevant_consumers = consumers_by_resource.get(id(target_resource), {})

                    for consumer_id, (consumer, input_slots) in relevant_consumers.items():
                        if consumer_id == producer_id:
                            continue

                        consumer_path = encoded_paths[consumer_id]

                        for input_slot in input_slots:
                            if input_slot.resource_field != nested_fk.target_field:
                                continue
                            if input_slot.parameter_location == ParameterLocation.BODY:
                                continue

                            # Build pointer: output_slot.pointer + nested_fk.pointer
                            if output_slot.cardinality == Cardinality.MANY:
                                base = output_slot.pointer.rstrip("/") + "/0"
                            else:
                                base = output_slot.pointer.rstrip("/")
                            body_pointer = base + nested_fk.pointer
                            if nested_fk.is_array:
                                body_pointer += "/0"

                            link_name = f"{consumer.method.capitalize()}{nested_fk.target_resource}"
                            if link_name in fk_links:
                                continue

                            fk_links[link_name] = LinkDefinition(
                                operation_ref=f"#/paths/{consumer_path}/{consumer.method}",
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


def infer_fk_target(field: str) -> tuple[str, str, bool] | None:
    """Extract target resource name and field from a FK field name.

    Returns (resource_name, target_field, is_array) or None if not a FK field.

    Examples:
        customer_id -> ("Customer", "id", False)
        site_ids -> ("Site", "id", True)
        user_uuid -> ("User", "uuid", False)
        session_guids -> ("Session", "guid", True)

    """
    field_lower = field.lower()

    # Check suffixes (longer ones first to match _ids before _id)
    for suffix, (target_field, is_array) in FK_SUFFIX_MAP.items():
        # Skip bare identifier fields (not FKs, they're primary identifiers)
        if field_lower == suffix.lstrip("_"):
            return None
        if field_lower.endswith(suffix) and len(field) > len(suffix):
            base_name = field[: -len(suffix)]
            if base_name:
                return to_pascal_case(base_name), target_field, is_array

    return None


def extract_fk_fields(fields: list[str]) -> list[FKField]:
    """Extract FK fields from a list of field names.

    Pre-computes FK information once during resource creation
    to avoid repeated inference during link generation.
    """
    result = []
    for field in fields:
        fk_info = infer_fk_target(field)
        if fk_info is not None:
            target_resource, target_field, is_array = fk_info
            result.append(
                FKField(
                    field_name=field,
                    target_resource=target_resource,
                    target_field=target_field,
                    is_array=is_array,
                )
            )
    return result


def extract_nested_fk_fields(
    schema: Mapping[str, Any],
    resolver: RefResolver,
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

    from schemathesis.specs.openapi.adapter.references import maybe_resolve

    result: list[NestedFKField] = []
    properties = schema.get("properties", {})
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
                    )
                )
            continue  # Don't recurse into FK fields

        # Resolve nested schema
        _, resolved_field = maybe_resolve(field_schema, resolver, "")
        field_type = resolved_field.get("type")

        # Recurse into nested objects
        if field_type == "object" or "properties" in resolved_field:
            result.extend(extract_nested_fk_fields(resolved_field, resolver, field_pointer, max_depth - 1))

        # Recurse into array items
        elif field_type == "array":
            items = resolved_field.get("items")
            if isinstance(items, dict):
                _, resolved_items = maybe_resolve(items, resolver, "")
                if isinstance(resolved_items, dict):
                    # Use /0 to indicate first array element
                    items_pointer = f"{field_pointer}/0"
                    result.extend(extract_nested_fk_fields(resolved_items, resolver, items_pointer, max_depth - 1))

    return result


def _build_nested_body(path: str, value: str) -> dict[str, Any]:
    """Build a nested dict structure from a path like 'shipping/warehouse_id'.

    Examples:
        _build_nested_body("customer_id", "$val") -> {"customer_id": "$val"}
        _build_nested_body("shipping/warehouse_id", "$val") -> {"shipping": {"warehouse_id": "$val"}}
        _build_nested_body("items/0/product_id", "$val") -> {"items": [{"product_id": "$val"}]}

    """
    if "/" not in path:
        return {path: value}

    parts = path.split("/")
    result: dict[str, Any] = {}
    current = result

    for i, part in enumerate(parts[:-1]):
        next_part = parts[i + 1]
        # Check if next part is an array index
        if next_part.isdigit():
            current[part] = [{}]
            current = current[part][0]
        elif part.isdigit():
            # Skip array indices - we're already inside the array
            continue
        else:
            current[part] = {}
            current = current[part]

    # Set the final value
    final_key = parts[-1]
    if not final_key.isdigit():
        current[final_key] = value

    return result


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


def extend_pointer(base: str, field: str, cardinality: Cardinality) -> str:
    if not base.endswith("/"):
        base += "/"
    if cardinality == Cardinality.MANY:
        # For arrays, reference first element: /data -> /data/0
        base += "0/"
    base += encode_pointer(field)
    return base


@dataclass
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

    __slots__ = ("operation_ref", "parameters", "request_body")

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


@dataclass
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

    __slots__ = ("producer_operation_ref", "status_code", "links")

    def to_openapi(self) -> dict[str, Any]:
        """Convert to OpenAPI response links format."""
        return {name: link_def.to_openapi() for name, link_def in self.links.items()}


@dataclass
class NormalizedLink:
    """Normalized representation of a link."""

    path: str
    method: str
    parameters: set[str]
    request_body: Any

    __slots__ = ("path", "method", "parameters", "request_body")


@dataclass
class OperationNode:
    """An API operation with its input/output dependencies."""

    method: str
    path: str
    # What this operation NEEDS
    inputs: list[InputSlot]
    # What this operation PRODUCES
    outputs: list[OutputSlot]

    __slots__ = ("method", "path", "inputs", "outputs")


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
    # Whether this input was matched via suffix matching (e.g., "file_name" -> "BackupFile")
    # Suffix-matched inputs can be upgraded by merge_related_resources if a producer exists
    is_suffix_matched: bool = False


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


@dataclass
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

    __slots__ = ("name", "fields", "types", "source", "fk_fields", "nested_fk_fields")

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
