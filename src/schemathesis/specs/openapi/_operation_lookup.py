from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from schemathesis.core.errors import InvalidSchema, OperationNotFound, RefResolutionError
from schemathesis.core.jsonschema.resolver import Resolver, resolve_reference
from schemathesis.specs.openapi.operations import (
    is_parsable_operation,
    iter_path_item_operations,
    operation_reference,
    parse_operation_reference,
    resolve_path_item,
    split_operation_reference,
)

if TYPE_CHECKING:
    from schemathesis.core.transport import HttpMethodSchema
    from schemathesis.specs.openapi.schemas import APIOperation, OpenApiSchema
    from schemathesis.specs.openapi.types import OperationObject


@dataclass(frozen=True, slots=True)
class OperationLookupEntry:
    path: str
    method: HttpMethodSchema
    scope: str
    resolver: Resolver
    definition: OperationObject
    shared_parameters: tuple[dict[str, Any], ...]
    path_item: Mapping[str, Any] | None


class OperationLookup:
    """Caches OpenAPI operation lookups by id & reference for reuse."""

    __slots__ = (
        "schema",
        "_operations_by_id",
        "_operations_by_reference",
    )

    def __init__(self, schema: OpenApiSchema) -> None:
        self.schema = schema
        self._operations_by_id: dict[str, OperationLookupEntry] | None = None
        self._operations_by_reference: dict[str, OperationLookupEntry] | None = None

    def entries(self) -> Collection[OperationLookupEntry]:
        return self._get_operations_by_reference().values()

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
        root_resolver = self.schema.root_resolver
        for path, path_item in paths.items():
            if not isinstance(path_item, dict):
                continue
            try:
                resolved_resolver, resolved_path_item = resolve_path_item(root_resolver, path_item)
            except InvalidSchema:
                continue
            scope = resolved_resolver.base_uri
            if not isinstance(resolved_path_item, dict):
                continue
            shared_parameters = tuple(resolved_path_item.get("parameters", []))
            for method, definition in iter_path_item_operations(resolved_path_item):
                if not is_parsable_operation(definition):
                    continue
                entry = OperationLookupEntry(
                    path=path,
                    method=method,
                    scope=scope,
                    resolver=resolved_resolver if "$ref" in path_item else root_resolver,
                    definition=definition,
                    shared_parameters=shared_parameters,
                    path_item=resolved_path_item,
                )
                operation_id = definition.get("operationId")
                if operation_id is not None:
                    operations_by_id[operation_id] = entry
                operations_by_reference[operation_reference(path, method)] = entry
        self._operations_by_id = operations_by_id
        self._operations_by_reference = operations_by_reference

    def _resolve_reference_entry(self, reference: str) -> OperationLookupEntry:
        try:
            resolved_resolver, definition = resolve_reference(self.schema.root_resolver, reference)
        except RefResolutionError:
            raise OperationNotFound(f"Operation '{reference}' not found", reference) from None
        scope = resolved_resolver.base_uri
        path, method = parse_operation_reference(reference)
        parent_ref, _ = split_operation_reference(reference)
        _, path_item = resolve_reference(self.schema.root_resolver, parent_ref)
        shared_parameters = tuple(path_item.get("parameters", []))
        entry = OperationLookupEntry(
            path=path,
            method=cast("HttpMethodSchema", method),
            scope=scope,
            resolver=resolved_resolver,
            definition=definition,
            shared_parameters=shared_parameters,
            path_item=path_item,
        )
        operations_by_reference = self._get_operations_by_reference()
        operations_by_reference[reference] = entry
        operations_by_reference.setdefault(operation_reference(path, method), entry)
        operations_by_id = self._get_operations_by_id()
        operation_id = definition.get("operationId") if isinstance(definition, Mapping) else None
        if operation_id is not None:
            operations_by_id.setdefault(operation_id, entry)
        return entry

    def _make_operation(self, entry: OperationLookupEntry) -> APIOperation:
        loader = self.schema._operations
        cached = loader.cached(entry.scope, entry.path, entry.method)
        if cached is not None:
            return cached
        parameters = loader._iter_parameters(entry.definition, entry.shared_parameters, resolver=entry.resolver)
        return loader.make_operation(
            entry.path,
            entry.method,
            parameters,
            entry.definition,
            entry.scope,
            resolver=entry.resolver,
            path_item=entry.path_item,
        )
