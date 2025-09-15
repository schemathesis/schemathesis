from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar, Iterator, Protocol, TypedDict, cast

from referencing import Registry, Resource, Specification
from referencing._core import Resolver
from referencing.exceptions import Unresolvable
from referencing.typing import Retrieve

from schemathesis.core import HTTP_METHODS
from schemathesis.core.errors import InvalidSchema
from schemathesis.core.result import Err, Ok, Result

ApiOperationResult = Result[Any, InvalidSchema]
PathItem = dict
Reference = TypedDict("Reference", {"$ref": str})
Operation = TypedDict("Operation", {"responses": dict})


class SpecificationAdapter(Protocol):
    example_field: ClassVar[str]
    examples_field: ClassVar[str]
    links_field: ClassVar[str]


@dataclass
class V2:
    produces: list[str]
    consumes: list[str]
    example_field: ClassVar[str] = "x-example"
    examples_field: ClassVar[str] = "x-examples"
    links_field: ClassVar[str] = "x-links"

    __slots__ = ("produces", "consumes")


class V3:
    example_field: ClassVar[str] = "example"
    examples_field: ClassVar[str] = "examples"
    links_field: ClassVar[str] = "links"

    __slots__ = ()


@dataclass
class OpenApi:
    _raw: dict[str, Any]
    _registry: Registry
    _adapter: SpecificationAdapter

    __slots__ = ("_raw", "_registry", "_adapter")

    def __init__(self, raw: dict[str, Any], retrieve: Retrieve | None = None) -> None:
        self._raw = raw
        if "swagger" in raw:
            self._adapter = V2(produces=raw.get("produces", []), consumes=raw.get("consumes", []))
        else:
            self._adapter = V3()
        if retrieve is not None:
            registry = Registry(retrieve=retrieve)
        else:
            registry = Registry()
        self._registry = registry.with_resource("", Resource(contents=raw, specification=Specification.OPAQUE))

    @property
    def title(self) -> str:
        return self._raw["info"]["title"]

    @property
    def version(self) -> str:
        return self._raw["info"]["version"]

    def __contains__(self, path: str) -> bool:
        paths = self._raw.get("paths", {})
        return path in paths and isinstance(paths[path], dict)

    def __iter__(self) -> Iterator[ApiOperationResult]:
        paths = self._raw.get("paths", {})
        resolver = self._registry.resolver()
        for path, item in paths.items():
            if not isinstance(item, dict):
                # There are real API schemas that have path items of incorrect types
                yield Err(InvalidSchema(f"Path item should be an object, got {type(item).__name__}: {item}", path=path))
            else:
                yield from self._iter_path_item(path, item, resolver)

    def _iter_path_item(
        self, path: str, path_item: PathItem | Reference, resolver: Resolver
    ) -> Iterator[ApiOperationResult]:
        ref = path_item.get("$ref")
        if ref is not None:
            try:
                resolved = resolver.lookup(ref)
            except Unresolvable as exc:
                yield Err(InvalidSchema(f"Failed to resolve reference: {exc.ref}", path=path))
                return
            resolver = resolved.resolver
            path_item = resolved.contents
        resolved_path_item = cast(PathItem, path_item)
        try:
            shared_parameters = list(_prepare_parameters(resolved_path_item, resolver, self._adapter))
        except Unresolvable as exc:
            yield Err(InvalidSchema(f"Failed to resolve reference: {exc.ref}", path=path))
            return
        for method, operation in resolved_path_item.items():
            if method not in HTTP_METHODS:
                continue
            yield Ok(
                ApiOperation(
                    method=method,
                    path=path,
                    definition=operation,
                    shared_parameters=shared_parameters,
                    resolver=resolver,
                    _adapter=self._adapter,
                )
            )


@dataclass
class ApiOperation:
    method: str
    path: str
    definition: Operation
    shared_parameters: list[Parameter]
    resolver: Resolver
    _adapter: SpecificationAdapter

    __slots__ = ("method", "path", "definition", "shared_parameters", "resolver", "_adapter")

    def iter_parameters(self) -> Iterator[Parameter]:
        """Iterate over all `parameters` containers applicable to this API operation."""
        seen = set()

        # Operation-level parameters take precedence - process them first
        for param in _prepare_parameters(self.definition, self.resolver, self._adapter):
            key = (param.definition["name"], param.definition["in"])
            seen.add(key)
            yield param

        # Add path-level parameters that weren't overridden
        for param in self.shared_parameters:
            key = (param.definition["name"], param.definition["in"])
            if key not in seen:
                yield param

    @property
    def parameters(self) -> ParameterContainer:
        return ParameterContainer(
            param for param in self.iter_parameters() if param.location in ("query", "path", "header", "cookie")
        )


@dataclass
class ParameterContainer:
    _iter: Iterator[Parameter]
    _cache: dict[str, Parameter]

    __slots__ = ("_iter", "_cache")

    def __init__(self, _iter: Iterator[Parameter]) -> None:
        self._iter = _iter
        self._cache = {}

    def _ensure_cache(self) -> None:
        if not self._cache:
            self._cache = {param.name: param for param in self._iter}

    def __contains__(self, name: str) -> bool:
        self._ensure_cache()
        return name in self._cache

    def __getitem__(self, name: str) -> Parameter:
        self._ensure_cache()
        return self._cache[name]

    def __iter__(self) -> Iterator[Parameter]:
        self._ensure_cache()
        yield from iter(self._cache.values())


@dataclass
class Parameter:
    definition: dict[str, Any]
    resolver: Resolver
    _adapter: SpecificationAdapter

    __slots__ = ("definition", "resolver", "_adapter")

    @property
    def name(self) -> str:
        """Parameter name."""
        return self.definition["name"]

    @property
    def location(self) -> str:
        """Where this parameter is located."""
        return self.definition["in"]


def _prepare_parameters(
    item: PathItem | Operation, resolver: Resolver, accessor: SpecificationAdapter
) -> Iterator[Parameter]:
    defined = item.get("parameters", [])
    assert isinstance(defined, list)
    for parameter in defined:
        ref = parameter.get("$ref")
        if ref is not None:
            resolved = resolver.lookup(ref)
            yield Parameter(definition=resolved.contents, resolver=resolved.resolver, _adapter=accessor)
        else:
            yield Parameter(definition=parameter, resolver=resolver, _adapter=accessor)


def _maybe_resolve(item: dict, resolver: Resolver, **kwargs: str) -> tuple[dict, Resolver]:
    ref = item.get("$ref")
    if ref is not None:
        try:
            resolved = resolver.lookup(ref)
            return resolved.contents, resolved.resolver
        except Unresolvable as exc:
            raise InvalidSchema(f"Failed to resolve reference: {exc.ref}", **kwargs) from exc

    return item, resolver
