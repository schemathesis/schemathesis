from dataclasses import dataclass
from typing import Any, Iterator, Mapping

from referencing import Registry, Resource, Specification
from referencing._core import Resolver
from referencing.exceptions import Unresolvable
from referencing.typing import Retrieve

from schemathesis.core import HTTP_METHODS
from schemathesis.core.errors import InvalidSchema
from schemathesis.core.result import Err, Ok, Result
from schemathesis.specs.openapi.adapter import v2, v3_0, v3_1
from schemathesis.specs.openapi.adapter.protocol import SpecificationAdapter

ApiOperationResult = Result[tuple[str, str, dict[str, Any], list[dict[str, Any]], Resolver], InvalidSchema]


@dataclass
class SpecificationRoot:
    definition: Mapping[str, Any]
    registry: Registry
    adapter: SpecificationAdapter

    __slots__ = ("definition", "registry", "adapter")

    def __init__(self, definition: dict[str, Any], retrieve: Retrieve | None = None) -> None:
        self.definition = definition
        if "swagger" in definition:
            self.adapter = v2
        else:
            version = definition["openapi"]
            if version.startswith("3.1"):
                self.adapter = v3_1
            else:
                self.adapter = v3_0
        if retrieve is not None:
            registry = Registry(retrieve=retrieve)
        else:
            registry = Registry()
        self.registry = registry.with_resource("", Resource(contents=definition, specification=Specification.OPAQUE))

    def __iter__(self) -> Iterator[ApiOperationResult]:
        paths = self.definition.get("paths", {})
        resolver = self.registry.resolver()
        for path, item in paths.items():
            if not isinstance(item, dict):
                # There are real API schemas that have path items of incorrect types
                yield Err(InvalidSchema(f"Path item should be an object, got {type(item).__name__}: {item}", path=path))
            else:
                yield from self._iter_path_item(path, item, resolver)

    def _iter_path_item(self, path: str, path_item, resolver) -> Iterator[ApiOperationResult]:
        ref = path_item.get("$ref")
        if ref is not None:
            try:
                resolved = resolver.lookup(ref)
            except Unresolvable as exc:
                yield Err(InvalidSchema(f"Failed to resolve reference: {exc.ref}", path=path))
                return
            resolver = resolved.resolver
            path_item = resolved.contents
        # TODO:
        # resolved_path_item = cast(PathItem, path_item)
        resolved_path_item = path_item
        try:
            shared_parameters = resolved_path_item.get("parameters", [])
        except Unresolvable as exc:
            yield Err(InvalidSchema(f"Failed to resolve reference: {exc.ref}", path=path))
            return
        for method, operation in resolved_path_item.items():
            if method not in HTTP_METHODS:
                continue
            yield Ok((method, path, operation, shared_parameters, resolver))
