from __future__ import annotations

import enum
from dataclasses import asdict, dataclass
from typing import Any, Iterator

from typing_extensions import TypeAlias

from schemathesis.core.parameters import ParameterLocation


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

        return serialized

    def iter_links(self) -> Iterator[tuple[str, str, dict[str, Any]]]:
        """Iterate over all available connections and build Open API links from them."""
        # Open API links connect producers with consumers
        for producer in self.operations.values():
            for output in producer.outputs:
                for consumer in self.operations.values():
                    if producer is consumer:
                        continue
                    # TODO: it should be a single input in the result
                    for input in consumer.inputs:
                        if input.resource is output.resource:
                            link = {
                                # TODO: Better naming
                                f"{producer.method} {producer.path} -> {output.status_code} -> {consumer.method} {consumer.path}": {
                                    # TODO: store `operationId` if possible / operationRef (borrow from `inference.py`)
                                    # TODO: Lookup should be faster - maybe just pointers to dicts, instead of initialized operations? to avoid repeated linear lookup
                                    "operationRef": f"#/paths/{consumer.path.replace('~', '~0').replace('/', '~1')}/{consumer.method}",
                                    "parameters": {
                                        # TODO: can it be from non-body?
                                        #       what about `MANY` cardinality?
                                        #       pointers should be encoded & properly joined
                                        f"{input.parameter_location.lower()}.{input.parameter_name}": f"$response.body#{output.pointer}{input.resource_field}",
                                    },
                                }
                            }
                            # TODO: It should be a better data structure
                            yield (
                                output.status_code,
                                f"#/paths/{producer.path.replace('~', '~0').replace('/', '~1')}/{producer.method}",
                                link,
                            )


class Cardinality(str, enum.Enum):
    """Whether there is one or many resources in a slot."""

    ONE = "ONE"
    MANY = "MANY"


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


@dataclass
class InputSlot:
    """A required input for an operation."""

    # Which resource is needed
    resource: ResourceDefinition
    # Which field from that resource (e.g., "id")
    resource_field: str
    # Where it goes in the request (e.g., "userId")
    parameter_name: str
    parameter_location: ParameterLocation

    __slots__ = ("resource", "resource_field", "parameter_name", "parameter_location")


@dataclass
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

    __slots__ = ("resource", "pointer", "cardinality", "status_code")


@dataclass
class ResourceDefinition:
    """A minimal description of a resource structure."""

    name: str
    # A sorted list of resource fields
    fields: list[str]
    # How this resource was created
    source: DefinitionSource

    __slots__ = ("name", "fields", "source")

    @classmethod
    def without_properties(cls, name: str) -> ResourceDefinition:
        return cls(name=name, fields=[], source=DefinitionSource.SCHEMA_WITHOUT_PROPERTIES)

    @classmethod
    def inferred_from_parameter(cls, name: str, parameter_name: str) -> ResourceDefinition:
        return cls(name=name, fields=[parameter_name], source=DefinitionSource.PARAMETER_INFERENCE)


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
