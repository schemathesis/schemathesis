from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterator, Mapping, Sequence
from typing import TYPE_CHECKING

from schemathesis.config import InferenceAlgorithm
from schemathesis.core import NOT_SET, NotSet
from schemathesis.core.result import Ok
from schemathesis.core.schema_analysis import SchemaWarning
from schemathesis.resources import ExtraDataSource, ResourceRepository
from schemathesis.specs.openapi.extra_data_source import OpenApiExtraDataSource, build_parameter_requirements
from schemathesis.specs.openapi.resources import build_descriptors
from schemathesis.specs.openapi.stateful import dependencies
from schemathesis.specs.openapi.stateful.dependencies.layers import compute_dependency_layers
from schemathesis.specs.openapi.stateful.inference import LinkInferencer
from schemathesis.specs.openapi.warnings import (
    detect_missing_deserializers,
    detect_unsupported_regex,
    detect_unused_openapi_auth,
)

if TYPE_CHECKING:
    from schemathesis.resources import ResourceDescriptor
    from schemathesis.specs.openapi.schemas import OpenApiSchema


class OpenAPIAnalysis:
    """Aggregated derived data for an OpenAPI schema.

    Provides a central access point for expensive computations so that
    downstream features share cached results instead of recomputing them.
    """

    __slots__ = (
        "schema",
        "_links_injected",
        "_dependency_graph",
        "_dependency_layers",
        "_resource_descriptors",
        "_extra_data_source",
        "_inferencer",
        "_warnings_cache",
        "_schema_warnings_cache",
    )

    def __init__(self, schema: OpenApiSchema) -> None:
        self.schema = schema
        self._links_injected = False
        self._dependency_graph: dependencies.DependencyGraph | None = None
        self._dependency_layers: list[list[str]] | None | NotSet = NOT_SET
        self._resource_descriptors: Sequence[ResourceDescriptor] | None = None
        self._extra_data_source: ExtraDataSource | None | NotSet = NOT_SET
        self._inferencer: LinkInferencer | None = None
        self._warnings_cache: Mapping[str, Sequence[SchemaWarning]] | None = None
        self._schema_warnings_cache: Sequence[SchemaWarning] | None = None

    @property
    def dependency_graph(self) -> dependencies.DependencyGraph:
        """Graph of API operations and their resource dependencies."""
        if self._dependency_graph is None:
            self._dependency_graph = dependencies.analyze(self.schema)
        return self._dependency_graph

    @property
    def dependency_layers(self) -> list[list[str]] | None:
        """Operations grouped into layers based on dependencies.

        Each layer can execute in parallel, but layers must execute sequentially.
        Returns None if no useful ordering exists.

        Example:
            Layer 0: [POST /users, POST /products]  # No dependencies
            Layer 1: [GET /users/{id}, POST /orders]  # Depend on layer 0
            Layer 2: [GET /orders/{id}]  # Depends on layer 1

        """
        if self._dependency_layers is NOT_SET:
            self._dependency_layers = compute_dependency_layers(self.dependency_graph)
        assert not isinstance(self._dependency_layers, NotSet)
        return self._dependency_layers

    @property
    def resource_descriptors(self) -> Sequence[ResourceDescriptor]:
        """Descriptors identifying resources that can be captured from API responses."""
        if self._resource_descriptors is None:
            self._resource_descriptors = build_descriptors(self.schema)
        return self._resource_descriptors

    @property
    def extra_data_source(self) -> ExtraDataSource | None:
        """Extra data source for augmenting test generation with captured API responses.

        Returns None if no resource descriptors are available.
        """
        if self._extra_data_source is NOT_SET:
            descriptors = self.resource_descriptors
            if not descriptors:
                self._extra_data_source = None
            else:
                repository = ResourceRepository(descriptors)
                self._populate_from_response_examples(repository)
                requirements = build_parameter_requirements(self.dependency_graph)
                self._extra_data_source = OpenApiExtraDataSource(repository=repository, requirements=requirements)
        assert not isinstance(self._extra_data_source, NotSet)
        return self._extra_data_source

    def _populate_from_response_examples(self, repository: ResourceRepository) -> None:
        """Pre-populate the resource pool with examples from response schemas."""
        for result in self.schema.get_all_operations():
            if not isinstance(result, Ok):
                continue
            operation = result.ok()
            if not repository.descriptors_for_operation(operation.label):
                continue
            for response in operation.responses.iter_successful_responses():
                # Skip wildcard patterns like "2XX" - they rarely have useful examples
                if not response.status_code.isdigit():
                    continue
                status_code = int(response.status_code)
                for _name, example_value in response.iter_examples():
                    repository.record_response(
                        operation=operation.label,
                        status_code=status_code,
                        payload=example_value,
                    )

    @property
    def inferencer(self) -> LinkInferencer:
        """Link inferencer for runtime operation matching via URL routing."""
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
        injected = dependencies.inject_links(self.schema)
        self._links_injected = True
        return injected

    @property
    def links_injected(self) -> bool:
        """Check if links have been injected into the schema."""
        return self._links_injected

    def iter_warnings(self) -> Iterator[SchemaWarning]:
        """Iterate over all cached schema warnings."""
        # Operation-level warnings
        warnings_map = self._get_warnings_map()
        for warnings in warnings_map.values():
            yield from warnings
        # Schema-level warnings
        yield from self._get_schema_warnings()

    def _get_warnings_map(self) -> Mapping[str, Sequence[SchemaWarning]]:
        if self._warnings_cache is None:
            self._warnings_cache = self._collect_warnings()
        return self._warnings_cache

    def _get_schema_warnings(self) -> Sequence[SchemaWarning]:
        if self._schema_warnings_cache is None:
            self._schema_warnings_cache = self._collect_schema_warnings()
        return self._schema_warnings_cache

    def _collect_warnings(self) -> Mapping[str, Sequence[SchemaWarning]]:
        """Collect operation-level warnings."""
        warnings_map: dict[str, list[SchemaWarning]] = defaultdict(list)
        for result in self.schema.get_all_operations():
            if isinstance(result, Ok):
                operation = result.ok()
                for warning in detect_missing_deserializers(operation):
                    warnings_map[operation.label].append(warning)
                for regex_warning in detect_unsupported_regex(operation):
                    warnings_map[operation.label].append(regex_warning)
        return warnings_map

    def _collect_schema_warnings(self) -> Sequence[SchemaWarning]:
        """Collect schema-level warnings."""
        return detect_unused_openapi_auth(self.schema)
