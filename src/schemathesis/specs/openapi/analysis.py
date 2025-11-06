from __future__ import annotations

from typing import TYPE_CHECKING

from schemathesis.config import InferenceAlgorithm
from schemathesis.specs.openapi.stateful import dependencies

if TYPE_CHECKING:
    from schemathesis.specs.openapi.schemas import BaseOpenAPISchema


class OpenAPIAnalysis:
    """Aggregated derived data for an OpenAPI schema.

    Provides a central access point for expensive computations so that
    downstream features share cached results instead of recomputing them.
    """

    __slots__ = ("schema", "_links_injected", "_dependency_graph")

    def __init__(self, schema: BaseOpenAPISchema) -> None:
        self.schema = schema
        self._links_injected = False
        self._dependency_graph: dependencies.DependencyGraph | None = None

    @property
    def dependency_graph(self) -> dependencies.DependencyGraph:
        """Lazily compute and cache the dependency graph."""
        if self._dependency_graph is None:
            self._dependency_graph = dependencies.analyze(self.schema)
        return self._dependency_graph

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
