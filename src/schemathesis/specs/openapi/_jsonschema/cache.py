from __future__ import annotations

from dataclasses import dataclass, field

from .types import MovedSchemas, ObjectSchema, Resolved, SchemaKey, ParameterReferenceCacheKey


@dataclass
class TransformCache:
    # Schemas that were referenced and therefore moved to the root of the schema
    moved_schemas: MovedSchemas = field(default_factory=dict)
    replaced_references: dict[str, str] = field(default_factory=dict)
    # Known recursive references
    recursive_references: set[str] = field(default_factory=set)
    # Already transformed schemas
    transformed_references: dict[str, ObjectSchema] = field(default_factory=dict)
    # Already inlined schemas
    inlined_schemas: set[SchemaKey] = field(default_factory=set)
    # References to parameter definitions
    parameter_lookups: dict[ParameterReferenceCacheKey, Resolved] = field(default_factory=dict)
