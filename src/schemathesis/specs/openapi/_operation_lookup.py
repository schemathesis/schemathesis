from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from schemathesis.core.compat import RefResolutionError
from schemathesis.core.errors import OperationNotFound
from schemathesis.core.jsonschema.resolver import resolve_reference

if TYPE_CHECKING:
    import jsonschema_rs

    from schemathesis.specs.openapi.schemas import APIOperation, OpenApiSchema


@dataclass(frozen=True)
class OperationLookupEntry:
    path: str
    method: str
    scope: str
    resolver: jsonschema_rs.Resolver
    definition: dict[str, Any]
    shared_parameters: tuple[dict[str, Any], ...]
    __slots__ = ("path", "method", "scope", "resolver", "definition", "shared_parameters")


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
        root_resolver = self.schema.root_resolver
        default_scope = root_resolver.base_uri
        for path, path_item in paths.items():
            if not isinstance(path_item, dict):
                continue
            if "$ref" in path_item:
                resolved_resolver, resolved_path_item = resolve_reference(root_resolver, path_item["$ref"])
                scope = resolved_resolver.base_uri
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
                    resolver=resolved_resolver if "$ref" in path_item else root_resolver,
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
            resolved_resolver, definition = resolve_reference(self.schema.root_resolver, reference)
        except RefResolutionError:
            raise OperationNotFound(f"Operation '{reference}' not found", reference) from None
        scope = resolved_resolver.base_uri
        path, method = _parse_reference_path_method(reference)
        parent_ref, _ = reference.rsplit("/", maxsplit=1)
        _, path_item = resolve_reference(self.schema.root_resolver, parent_ref)
        shared_parameters = tuple(path_item.get("parameters", []))
        entry = OperationLookupEntry(
            path=path,
            method=method,
            scope=scope,
            resolver=resolved_resolver,
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
        parameters = self.schema._iter_parameters(entry.definition, entry.shared_parameters, resolver=entry.resolver)
        return self.schema.make_operation(
            entry.path, entry.method, parameters, entry.definition, entry.scope, resolver=entry.resolver
        )

    @staticmethod
    def _canonical_operation_reference(path: str, method: str) -> str:
        encoded_path = path.replace("~", "~0").replace("/", "~1")
        return f"#/paths/{encoded_path}/{method}"


def _parse_reference_path_method(reference: str) -> tuple[str, str]:
    marker = "#/paths/"
    _, separator, suffix = reference.partition(marker)
    if not separator:
        raise OperationNotFound(f"Operation '{reference}' not found", reference)
    encoded_path, method = suffix.rsplit("/", maxsplit=1)
    return encoded_path.replace("~1", "/").replace("~0", "~"), method
