from __future__ import annotations

from typing import Any, Dict, Iterable, MutableMapping, NewType, Protocol, Set, Tuple, Union

from referencing import Registry

SchemaKey = NewType("SchemaKey", str)
ObjectSchema = MutableMapping[str, Any]
Schema = Union[bool, ObjectSchema]
MovedSchemas = Dict[SchemaKey, ObjectSchema]
ReferencesCache = Dict[str, Set[SchemaKey]]
# Either references available with the root schema scope, or scoped ones
ParameterReferenceCacheKey = Union[str, Tuple[str, str]]


class Resolved(Protocol):
    contents: Any
    resolver: Resolver


class Resolver(Protocol):
    def lookup(self, ref: str) -> Resolved: ...
    def dynamic_scope(self) -> Iterable[tuple[str, Registry]]: ...
