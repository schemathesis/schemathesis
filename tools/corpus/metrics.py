from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

import schemathesis
from schemathesis.core.jsonschema.types import JsonSchemaObject
from schemathesis.specs.openapi.stateful import dependencies


@dataclass(frozen=True, slots=True)
class DependencyMetrics:
    resources: int = 0
    inputs: int = 0
    outputs: int = 0
    links: int = 0
    # Resources with no fields at all, which mean schema extraction failed for them
    fieldless_resources: int = 0


def collect_dependency_metrics(raw_schema: JsonSchemaObject) -> DependencyMetrics:
    schema = schemathesis.openapi.from_dict(raw_schema)
    graph = dependencies.analyze(schema)

    inputs = 0
    outputs = 0
    links = 0
    for operation in graph.operations.values():
        inputs += len(operation.inputs)
        outputs += len(operation.outputs)
    for response_links in graph.iter_links():
        links += len(response_links.links)

    return DependencyMetrics(
        resources=len(graph.resources),
        inputs=inputs,
        outputs=outputs,
        links=links,
        fieldless_resources=sum(1 for resource in graph.resources.values() if not resource.fields),
    )


def add_dependency_metrics(items: Iterable[DependencyMetrics]) -> DependencyMetrics:
    resources = 0
    inputs = 0
    outputs = 0
    links = 0
    fieldless_resources = 0
    for item in items:
        resources += item.resources
        inputs += item.inputs
        outputs += item.outputs
        links += item.links
        fieldless_resources += item.fieldless_resources
    return DependencyMetrics(
        resources=resources,
        inputs=inputs,
        outputs=outputs,
        links=links,
        fieldless_resources=fieldless_resources,
    )
