from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING, Iterator

from schemathesis.config import InferenceAlgorithm
from schemathesis.core.result import Ok
from schemathesis.core.schema_analysis import SchemaWarning
from schemathesis.specs.openapi.stateful import dependencies
from schemathesis.specs.openapi.stateful.inference import LinkInferencer
from schemathesis.specs.openapi.warnings import detect_missing_deserializers

if TYPE_CHECKING:
    from schemathesis.specs.openapi.schemas import OpenApiSchema


class OpenAPIAnalysis:
    """Aggregated derived data for an OpenAPI schema.

    Provides a central access point for expensive computations so that
    downstream features share cached results instead of recomputing them.
    """

    __slots__ = ("schema", "_links_injected", "_dependency_graph", "_inferencer", "_warnings_cache")

    def __init__(self, schema: OpenApiSchema) -> None:
        self.schema = schema
        self._links_injected = False
        self._dependency_graph: dependencies.DependencyGraph | None = None
        self._inferencer: LinkInferencer | None = None
        self._warnings_cache: dict[str, list[SchemaWarning]] | None = None

    @property
    def dependency_graph(self) -> dependencies.DependencyGraph:
        """Lazily compute and cache the dependency graph."""
        if self._dependency_graph is None:
            self._dependency_graph = dependencies.analyze(self.schema)
        return self._dependency_graph

    @property
    def inferencer(self) -> LinkInferencer:
        """Lazily compute and cache the link inferencer with URL router."""
        if self._inferencer is None:
            self._inferencer = LinkInferencer.from_schema(self.schema)
        return self._inferencer

    def should_inject_links(self) -> bool:
        """Check if dependency-based link injection should be applied.

        Returns True if:
        - Stateful testing is enabled
        - Dependency analysis algorithm is enabled
        - Links have not been injected yet
        """
        return (
            self.schema.config.phases.stateful.enabled
            and self.schema.config.phases.stateful.inference.is_algorithm_enabled(
                InferenceAlgorithm.DEPENDENCY_ANALYSIS
            )
            and not self._links_injected
        )

    def inject_links(self) -> int:
        """Inject inferred links into the schema based on dependency analysis.

        Returns the number of links injected. Returns 0 if links were already injected.
        """
        if self._links_injected:
            return 0
        injected = dependencies._inject_links(self.schema, self.dependency_graph)
        self._links_injected = True
        return injected

    @property
    def links_injected(self) -> bool:
        """Check if links have been injected into the schema."""
        return self._links_injected

    def iter_warnings(self) -> Iterator[SchemaWarning]:
        """Iterate over all cached schema warnings."""
        warnings_map = self._get_warnings_map()
        for warnings in warnings_map.values():
            yield from warnings

    def _get_warnings_map(self) -> dict[str, list[SchemaWarning]]:
        if self._warnings_cache is None:
            self._warnings_cache = self._collect_warnings()
        return self._warnings_cache

    def _collect_warnings(self) -> dict[str, list[SchemaWarning]]:
        warnings_map: dict[str, list[SchemaWarning]] = defaultdict(list)
        for result in self.schema.get_all_operations():
            if isinstance(result, Ok):
                operation = result.ok()
                warnings_map[operation.label].extend(detect_missing_deserializers(operation))
        return warnings_map
