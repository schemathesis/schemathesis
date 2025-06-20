from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterator, Protocol, TypedDict, cast

from referencing import Registry, Resource, Specification
from referencing._core import Resolver
from referencing.exceptions import Unresolvable
from referencing.typing import Retrieve

from schemathesis.core import HTTP_METHODS
from schemathesis.core.errors import InvalidSchema
from schemathesis.core.result import Err, Ok
from schemathesis.specs._access import ApiOperationResult

PathItem = dict
Reference = TypedDict("Reference", {"$ref": str})
Operation = TypedDict("Operation", {"responses": dict})


class OperationParameter(Protocol):
    @property
    def definition(self) -> dict[str, Any]:
        raise NotImplementedError

    @property
    def location(self) -> str:
        raise NotImplementedError

    @property
    def resolver(self) -> Resolver:
        raise NotImplementedError


class SpecAccessor(Protocol):
    def extract_body(self, operation: ApiOperation) -> Iterator[Body]:
        raise NotImplementedError

    def extract_content_types(self, operation: ApiOperation, response: Response) -> list[str]:
        raise NotImplementedError

    def extract_response_schema(self, response: Response) -> dict[str, Any] | None:
        raise NotImplementedError

    def extract_response_examples(self, response: Response) -> Iterator[Example]:
        raise NotImplementedError


OPENAPI_20_DEFAULT_BODY_MEDIA_TYPE = "application/json"
OPENAPI_20_DEFAULT_FORM_MEDIA_TYPE = "multipart/form-data"


@dataclass
class V2:
    produces: list[str]
    consumes: list[str]

    __slots__ = ("produces", "consumes")

    def extract_body(self, operation: ApiOperation) -> Iterator[Body]:
        consumes = operation.definition.get("consumes", [])
        assert isinstance(consumes, list)
        # For `in=body` parameters, we imply `application/json` as the default media type because it is the most common.
        body_media_types: list[str] = consumes or self.consumes or [OPENAPI_20_DEFAULT_BODY_MEDIA_TYPE]
        form_parameters = []
        for param in operation._iter_parameters():
            if param.location == "body":
                for media_type in body_media_types:
                    yield Body(media_type=media_type, definition=param.definition, resolver=param.resolver)
            if param.location == "formData":
                form_parameters.append(param)
        if form_parameters:
            # If an API operation has parameters with `in=formData`, Schemathesis should know how to serialize it.
            # We can't be 100% sure what media type is expected by the server and chose `multipart/form-data` as
            # the default because it is broader since it allows us to upload files.
            form_data_media_types = consumes or self.consumes or [OPENAPI_20_DEFAULT_FORM_MEDIA_TYPE]
            resolver = form_parameters[0].resolver
            for media_type in form_data_media_types:
                # Individual `formData` parameters are joined into a single "composite" one.
                yield Body(
                    media_type=media_type, definition=[param.definition for param in form_parameters], resolver=resolver
                )

    def extract_response_schema(self, response: Response) -> dict[str, Any] | None:
        return response.definition.get("schema")

    def extract_content_types(self, operation: ApiOperation, response: Response) -> list[str]:
        produces = operation.definition.get("produces", [])
        assert isinstance(produces, list)
        return produces or self.produces

    def extract_response_examples(self, response: Response) -> Iterator:
        # In Swagger 2.0, examples are directly in the response under "examples"
        examples = response.definition.get("examples", {})
        for name, value in examples.items():
            yield Example(name=name, value=value)


class V3:
    __slots__ = ()

    def extract_body(self, operation: ApiOperation) -> Iterator[Body]:
        body = operation.definition.get("requestBody")
        if body is None:
            return
        resolver = operation.resolver
        assert isinstance(body, dict)
        ref = body.get("$ref")
        if ref is not None:
            resolved = resolver.lookup(ref)
            body = resolved.contents
            resolver = resolved.resolver
        try:
            content = body["content"]
        except KeyError:
            # It is rare, but happens in real schemas
            raise InvalidSchema("Missing required key `content`") from None
        for media_type, body in content.items():
            ref = body.get("$ref")
            if ref is not None:
                resolved = resolver.lookup(ref)
                body = resolved.contents
                resolver = resolved.resolver
            yield Body(media_type=media_type, definition=body, resolver=resolver)

    def extract_response_schema(self, response: Response) -> dict[str, Any] | None:
        options = iter(response.definition.get("content", {}).values())
        option = next(options, None)
        if isinstance(option, dict):
            return option.get("schema")
        return None

    def extract_content_types(self, operation: ApiOperation, response: Response) -> list[str]:
        return list(response.definition.get("content", {}).keys())

    def extract_response_examples(self, response: Response) -> Iterator[Example]:
        # In OpenAPI 3.0, examples are in content -> media type -> examples/example
        content = response.definition.get("content", {})
        for media_type, definition in content.items():
            # Try to get a more descriptive example name from the `$ref` value
            schema_ref = definition.get("schema", {}).get("$ref")
            if schema_ref:
                name = schema_ref.split("/")[-1]
            else:
                name = f"{response.status_code}/{media_type}"

            for examples_field, example_field in (
                ("examples", "example"),
                ("x-examples", "x-example"),
            ):
                examples = definition.get(examples_field, {})
                for example in examples.values():
                    if "value" in example:
                        yield Example(name=name, value=example["value"])
                if example_field in definition:
                    yield Example(name=name, value=definition[example_field])


@dataclass
class ApiOperation:
    method: str
    path: str
    definition: Operation
    shared_parameters: list[Parameter]
    resolver: Resolver
    _accessor: SpecAccessor

    __slots__ = ("method", "path", "definition", "shared_parameters", "resolver", "_accessor")

    @property
    def label(self) -> str:
        return f"{self.method.upper()} {self.path}"

    def iter_parameters(self) -> Iterator[OperationParameter]:
        return (param for param in self._iter_parameters() if param.location in ("query", "path", "header", "cookie"))

    def _iter_parameters(self) -> Iterator[OperationParameter]:
        """Iterate over all `parameters` containers applicable to this API operation."""
        seen = set()

        # Operation-level parameters take precedence - process them first
        for param in _prepare_parameters(self.definition, self.resolver):
            key = (param.definition["name"], param.definition["in"])
            seen.add(key)
            yield param

        # Add path-level parameters that weren't overridden
        for param in self.shared_parameters:
            key = (param.definition["name"], param.definition["in"])
            if key not in seen:
                yield param

    @property
    def query(self) -> Iterator[OperationParameter]:
        return (param for param in self._iter_parameters() if param.location == "query")

    @property
    def path_parameters(self) -> Iterator[OperationParameter]:
        return (param for param in self._iter_parameters() if param.location == "path")

    @property
    def headers(self) -> Iterator[OperationParameter]:
        return (param for param in self._iter_parameters() if param.location == "header")

    @property
    def cookies(self) -> Iterator[OperationParameter]:
        return (param for param in self._iter_parameters() if param.location == "cookie")

    @property
    def body(self) -> Iterator[Body]:
        yield from self._accessor.extract_body(self)

    @property
    def responses(self) -> dict[str, Response]:
        responses = {}
        for key, response in self.definition.get("responses", {}).items():
            response, resolver = _maybe_resolve(response, self.resolver)
            responses[str(key)] = Response(
                status_code=str(key), definition=response, resolver=resolver, _accessor=self._accessor
            )
        return responses

    def get_response_definition(self, status_code: int) -> Response | None:
        responses = self.responses
        return responses.get(str(status_code)) or responses.get("default")

    @property
    def security(self) -> list | None:
        security = self.definition.get("security")
        assert isinstance(security, list)
        return security

    def get_content_types(self, status_code: int) -> list[str]:
        response = self.get_response_definition(status_code)
        if not response:
            return []
        return self._accessor.extract_content_types(self, response)


@dataclass
class Response:
    status_code: str
    definition: dict[str, Any]
    resolver: Resolver
    _accessor: SpecAccessor

    __slots__ = ("status_code", "definition", "resolver", "_accessor")

    @property
    def schema(self) -> dict[str, Any] | None:
        return self._accessor.extract_response_schema(self)

    @property
    def headers(self) -> dict[str, Any] | None:
        return self.definition.get("headers")

    @property
    def examples(self) -> Iterator[Example]:
        """Iterate over all examples defined in this response."""
        return self._accessor.extract_response_examples(self)


@dataclass
class Example:
    name: str
    value: Any

    __slots__ = ("name", "value")


@dataclass
class Parameter:
    definition: dict[str, Any]
    resolver: Resolver

    __slots__ = ("definition", "resolver")

    @property
    def location(self) -> str:
        """Where this parameter is located."""
        return self.definition["in"]


@dataclass
class Body:
    media_type: str
    definition: dict[str, Any] | list[dict[str, Any]]
    resolver: Resolver

    __slots__ = ("media_type", "definition", "resolver")

    @property
    def location(self) -> str:
        return "body"


@dataclass
class OpenApi:
    _raw: dict[str, Any]
    _registry: Registry
    _accessor: SpecAccessor

    __slots__ = ("_raw", "_registry", "_accessor")

    def __init__(self, raw: dict[str, Any], retrieve: Retrieve | None = None) -> None:
        self._raw = raw
        if "swagger" in raw:
            self._accessor = V2(produces=raw.get("produces", []), consumes=raw.get("consumes", []))
        else:
            self._accessor = V3()
        if retrieve is not None:
            registry = Registry(retrieve=retrieve)
        else:
            registry = Registry()
        self._registry = registry.with_resource("", Resource(contents=raw, specification=Specification.OPAQUE))

    def __getitem__(self, path: str) -> PathOperations:
        paths = self._raw.get("paths", {})
        if path not in paths:
            raise KeyError(f"Path '{path}' not found")

        path_item = paths[path]
        if not isinstance(path_item, dict):
            raise InvalidSchema(
                f"Path item should be an object, got {type(path_item).__name__}: {path_item}", path=path
            )

        return PathOperations(path, path_item, self._registry.resolver(), self._accessor)

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
            shared_parameters = list(_prepare_parameters(resolved_path_item, resolver))
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
                    _accessor=self._accessor,
                )
            )


@dataclass
class PathOperations:
    """Provides method-level access to operations for a specific path."""

    _path: str
    _path_item: PathItem
    _resolver: Resolver
    _accessor: SpecAccessor

    __slots__ = ("_path", "_path_item", "_resolver", "_accessor")

    def _get_shared_parameters(self, path_item: PathItem, resolver: Resolver) -> list[Parameter]:
        try:
            return list(_prepare_parameters(path_item, resolver))
        except Unresolvable as exc:
            raise InvalidSchema(f"Failed to resolve reference: {exc.ref}", path=self._path) from exc

    def __getitem__(self, method: str) -> ApiOperation:
        method = method.upper()
        if method.lower() not in HTTP_METHODS:
            raise KeyError(f"Invalid HTTP method '{method}'")

        path_item, resolver = _maybe_resolve(self._path_item, self._resolver, path=self._path)

        if method.lower() not in path_item:
            raise KeyError(f"Method '{method}' not found for path '{self._path}'")

        operation = path_item[method.lower()]
        shared_parameters = self._get_shared_parameters(path_item, resolver)

        return ApiOperation(
            method=method,
            path=self._path,
            definition=operation,
            shared_parameters=shared_parameters,
            resolver=resolver,
            _accessor=self._accessor,
        )

    def __contains__(self, method: str) -> bool:
        method = method.upper()
        if method.lower() not in HTTP_METHODS:
            return False

        resolved_path_item, _ = _maybe_resolve(self._path_item, self._resolver, path=self._path)
        return method.lower() in resolved_path_item

    def __iter__(self) -> Iterator[str]:
        """Iterate over available HTTP methods."""
        resolved_path_item, _ = _maybe_resolve(self._path_item, self._resolver, path=self._path)
        return (method.upper() for method in resolved_path_item.keys() if method in HTTP_METHODS)


def _prepare_parameters(item: PathItem | Operation, resolver: Resolver) -> Iterator[Parameter]:
    defined = item.get("parameters", [])
    assert isinstance(defined, list)
    for parameter in defined:
        ref = parameter.get("$ref")
        if ref is not None:
            resolved = resolver.lookup(ref)
            yield Parameter(definition=resolved.contents, resolver=resolved.resolver)
        else:
            yield Parameter(definition=parameter, resolver=resolver)


def _maybe_resolve(item: dict, resolver: Resolver, **kwargs: str) -> tuple[dict, Resolver]:
    ref = item.get("$ref")
    if ref is not None:
        try:
            resolved = resolver.lookup(ref)
            return resolved.contents, resolved.resolver
        except Unresolvable as exc:
            raise InvalidSchema(f"Failed to resolve reference: {exc.ref}", **kwargs) from exc

    return item, resolver
