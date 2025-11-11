from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Collection, Iterator, Mapping

from schemathesis.core.compat import RefResolutionError
from schemathesis.core.errors import OperationNotFound

if TYPE_CHECKING:
    from schemathesis.specs.openapi.references import ReferenceResolver
    from schemathesis.specs.openapi.schemas import APIOperation, OpenApiSchema


@dataclass(frozen=True)
class OperationLookupEntry:
    path: str
    method: str
    scope: str
    definition: dict[str, Any]
    shared_parameters: tuple[dict[str, Any], ...]
    __slots__ = ("path", "method", "scope", "definition", "shared_parameters")


class OperationLookup:
    """Caches OpenAPI operation lookups by id & reference for reuse."""

    __slots__ = (
        "schema",
        "_http_methods",
        "_operations_by_id",
        "_operations_by_reference",
    )

    def __init__(self, schema: OpenApiSchema, http_methods: Collection[str]) -> None:
        self.schema = schema
        self._http_methods = http_methods
        self._operations_by_id: dict[str, OperationLookupEntry] | None = None
        self._operations_by_reference: dict[str, OperationLookupEntry] | None = None

    def find_by_id(self, operation_id: str) -> APIOperation:
        entry = self._get_operations_by_id().get(operation_id)
        if entry is None:
            self.schema._on_missing_operation(operation_id, None, [])
        return self._make_operation(entry)

    def find_by_reference(self, reference: str) -> APIOperation:
        operations_by_reference = self._get_operations_by_reference()
        entry = operations_by_reference.get(reference)
        if entry is None:
            entry = self._resolve_reference_entry(reference)
        return self._make_operation(entry)

    def _get_operations_by_id(self) -> dict[str, OperationLookupEntry]:
        self._ensure_tables()
        assert self._operations_by_id is not None
        return self._operations_by_id

    def _get_operations_by_reference(self) -> dict[str, OperationLookupEntry]:
        self._ensure_tables()
        assert self._operations_by_reference is not None
        return self._operations_by_reference

    def _ensure_tables(self) -> None:
        if self._operations_by_id is not None and self._operations_by_reference is not None:
            return
        self._build_tables()

    def _build_tables(self) -> None:
        operations_by_id: dict[str, OperationLookupEntry] = {}
        operations_by_reference: dict[str, OperationLookupEntry] = {}
        paths = self.schema._get_paths()
        if paths is None:
            self._operations_by_id = operations_by_id
            self._operations_by_reference = operations_by_reference
            return
        resolve = self.schema.resolver.resolve
        default_scope = self.schema.resolver.resolution_scope
        for path, path_item in paths.items():
            if not isinstance(path_item, dict):
                continue
            if "$ref" in path_item:
                scope, resolved_path_item = resolve(path_item["$ref"])
            else:
                scope = default_scope
                resolved_path_item = path_item
            if not isinstance(resolved_path_item, dict):
                continue
            shared_parameters = tuple(resolved_path_item.get("parameters", []))
            for method, definition in resolved_path_item.items():
                if method not in self._http_methods:
                    continue
                entry = OperationLookupEntry(
                    path=path,
                    method=method,
                    scope=scope,
                    definition=definition,
                    shared_parameters=shared_parameters,
                )
                operation_id = definition.get("operationId")
                if operation_id is not None:
                    operations_by_id[operation_id] = entry
                reference = self._canonical_operation_reference(path, method)
                operations_by_reference[reference] = entry
        self._operations_by_id = operations_by_id
        self._operations_by_reference = operations_by_reference

    def _resolve_reference_entry(self, reference: str) -> OperationLookupEntry:
        try:
            scope, definition = self.schema.resolver.resolve(reference)
        except RefResolutionError:
            raise OperationNotFound(f"Operation '{reference}' not found", reference) from None
        path, method = scope.rsplit("/", maxsplit=2)[-2:]
        path = path.replace("~1", "/").replace("~0", "~")
        parent_ref, _ = reference.rsplit("/", maxsplit=1)
        _, path_item = self.schema.resolver.resolve(parent_ref)
        shared_parameters = tuple(path_item.get("parameters", []))
        entry = OperationLookupEntry(
            path=path,
            method=method,
            scope=scope,
            definition=definition,
            shared_parameters=shared_parameters,
        )
        operations_by_reference = self._get_operations_by_reference()
        operations_by_reference[reference] = entry
        canonical_reference = self._canonical_operation_reference(path, method)
        operations_by_reference.setdefault(canonical_reference, entry)
        operations_by_id = self._get_operations_by_id()
        operation_id = definition.get("operationId") if isinstance(definition, Mapping) else None
        if operation_id is not None:
            operations_by_id.setdefault(operation_id, entry)
        return entry

    def _make_operation(self, entry: OperationLookupEntry) -> APIOperation:
        with _in_scope(self.schema.resolver, entry.scope):
            parameters = self.schema._iter_parameters(entry.definition, entry.shared_parameters)
        return self.schema.make_operation(entry.path, entry.method, parameters, entry.definition, entry.scope)

    @staticmethod
    def _canonical_operation_reference(path: str, method: str) -> str:
        encoded_path = path.replace("~", "~0").replace("/", "~1")
        return f"#/paths/{encoded_path}/{method}"


@contextmanager
def _in_scope(resolver: ReferenceResolver, scope: str) -> Iterator[None]:
    resolver.push_scope(scope)
    try:
        yield
    finally:
        resolver.pop_scope()
