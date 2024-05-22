from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Generator, Literal, Mapping, TypedDict, cast

from referencing import Registry, Resource
from referencing.jsonschema import DRAFT4

from ...constants import HTTP_METHODS
from ...internal.result import Ok, Result

if TYPE_CHECKING:
    from ._jsonschema import Resolver
    from .schemas import OpenAPIOperation, OperationSchemaError

DEFAULT_BODY_MEDIA_TYPES = ["application/json"]
DEFAULT_FORM_MEDIA_TYPES = ["multipart/form-data"]

Reference = TypedDict("Reference", {"$ref": str})


class Specification(TypedDict):
    consumes: list[str]
    paths: Mapping[str, PathItem | Reference]


@dataclass
class OpenAPINonBodyParameter:
    name: str
    location: Literal["query", "header", "path"]
    required: bool
    schema: Mapping[str, Any]

    __slots__ = ("name", "location", "required", "schema")


@dataclass
class OpenAPIBodyParameter:
    required: bool
    schema: Mapping[str, Any]
    media_type: str

    __slots__ = ("required", "schema", "media_type")


class PathItem(TypedDict):
    get: Operation
    post: Operation
    put: Operation
    delete: Operation
    options: Operation
    head: Operation
    patch: Operation
    trace: Operation
    parameters: list[NonBodyParameter | BodyParameter | Reference]


class Operation(TypedDict):
    consumes: list[str]
    parameters: list[NonBodyParameter | BodyParameter | Reference]


NonBodyParameter = TypedDict(
    "NonBodyParameter",
    {
        "name": str,
        "in": Literal["query", "header", "path", "formData"],
        "required": bool,
    },
)
BodyParameter = TypedDict(
    "BodyParameter",
    {
        "name": str,
        "in": Literal["body"],
        "required": bool,
        "schema": Mapping[str, Any],
    },
)


@dataclass
class APIOperation:
    path: str
    method: str
    path_parameters: list[OpenAPINonBodyParameter]
    headers: list[OpenAPINonBodyParameter]
    query: list[OpenAPINonBodyParameter]
    body: list[OpenAPIBodyParameter]

    __slots__ = ("path", "method", "path_parameters", "headers", "query", "body")


@dataclass
class SharedParameters:
    incomplete: list[OpenAPIBodyParameter]
    complete: list[OpenAPINonBodyParameter]

    __slots__ = ("incomplete", "complete")

    def initialize_incomplete(self, media_types: list[str]) -> Generator[OpenAPIBodyParameter, None, None]:
        for media_type in media_types:
            for parameter in self.incomplete:
                yield OpenAPIBodyParameter(
                    required=parameter.required,
                    schema=parameter.schema,
                    media_type=media_type,
                )


def iter_operations(spec: Specification, uri: str) -> Generator[Result[APIOperation, OperationSchemaError], None, None]:
    """Iterate over all operations in the given OpenAPI 2.0 specification."""
    # TODO: initialize registry + resolver just here
    registry = Registry().with_resource(uri, Resource(contents=spec, specification=DRAFT4))
    root_resolver = registry.resolver()
    paths = spec["paths"]
    global_media_types = spec.get("consumes", [])
    for path, path_item_or_ref in paths.items():
        path_item: PathItem
        if "$ref" in path_item_or_ref:  # type: ignore[typeddict-item]
            resolved = root_resolver.lookup(path_item_or_ref["$ref"])  # type: ignore[typeddict-item]
            path_item_resolver = resolved.resolver
            path_item = resolved.contents
        else:
            path_item_resolver = root_resolver
            path_item = path_item_or_ref
        shared_parameters = _init_shared_parameters(path_item.get("parameters", []), path_item_resolver)
        for method, entry in path_item.items():
            if method not in HTTP_METHODS:
                continue
            operation = cast(Operation, entry)
            media_types = operation.get("consumes", global_media_types)
            local_parameters = _init_local_parameters(operation.get("parameters", []), media_types, path_item_resolver)
            path_parameters = []
            headers = []
            query = []
            body = []
            for parameter in local_parameters:
                if isinstance(parameter, OpenAPINonBodyParameter):
                    if parameter.location == "path":
                        path_parameters.append(parameter)
                    elif parameter.location == "header":
                        headers.append(parameter)
                    else:
                        query.append(parameter)
                else:
                    body.append(parameter)
            for parameter in shared_parameters.complete:
                if parameter.location == "path":
                    path_parameters.append(parameter)
                elif parameter.location == "header":
                    headers.append(parameter)
                else:
                    query.append(parameter)
            for parameter in shared_parameters.initialize_incomplete(media_types):
                body.append(parameter)
            yield Ok(
                APIOperation(
                    path=path,
                    method=method,
                    path_parameters=path_parameters,
                    headers=headers,
                    query=query,
                    body=body,
                )
            )


def _init_shared_parameters(
    parameters: list[NonBodyParameter | BodyParameter | Reference], path_item_resolver: Resolver
) -> SharedParameters:
    complete: list[OpenAPINonBodyParameter] = []
    incomplete: list[OpenAPIBodyParameter] = []
    form_parameters = []
    for parameter_or_ref in parameters:
        parameter: NonBodyParameter | BodyParameter
        if "$ref" in parameter_or_ref:  # type: ignore[typeddict-item]
            resolved = path_item_resolver.lookup(parameter_or_ref["$ref"])  # type: ignore[typeddict-item]
            parameter = resolved.contents
            parameter_resolver = resolved.resolver
        else:
            parameter = parameter_or_ref
            parameter_resolver = path_item_resolver
        required = parameter.get("required", False)
        if parameter["in"] == "formData":
            schema = _extract_non_body_parameter_schema(parameter)
            # TODO: Convert to jsonschema
            form_parameters.append((parameter["name"], required, schema))
        elif parameter["in"] == "body":
            schema = parameter["schema"]
            # TODO: Convert to jsonschema + filter out
            incomplete.append(
                OpenAPIBodyParameter(
                    required=required,
                    schema=schema,
                    media_type="",
                )
            )
        else:
            schema = _extract_non_body_parameter_schema(parameter)
            # TODO: Convert to jsonschema
            complete.append(
                OpenAPINonBodyParameter(
                    name=parameter["name"],
                    location=parameter["in"],
                    required=required,
                    schema=schema,
                )
            )
    if form_parameters:
        schema = _parameters_to_json_schema(form_parameters)
        incomplete.append(
            OpenAPIBodyParameter(
                required=True,
                schema=schema,
                media_type="",
            )
        )
    return SharedParameters(incomplete=incomplete, complete=complete)


def _init_local_parameters(
    parameters: list[NonBodyParameter | BodyParameter | Reference], media_types: list[str], path_item_resolver: Resolver
) -> list[OpenAPINonBodyParameter | OpenAPIBodyParameter]:
    initialized: list[OpenAPINonBodyParameter | OpenAPIBodyParameter] = []
    body_media_types = media_types or DEFAULT_BODY_MEDIA_TYPES
    form_data_media_types = media_types or DEFAULT_FORM_MEDIA_TYPES
    form_parameters = []
    for parameter_or_ref in parameters:
        parameter: NonBodyParameter | BodyParameter
        if "$ref" in parameter_or_ref:  # type: ignore[typeddict-item]
            resolved = path_item_resolver.lookup(parameter_or_ref["$ref"])  # type: ignore[typeddict-item]
            parameter = resolved.contents
            parameter_resolver = resolved.resolver
        else:
            parameter = parameter_or_ref
            parameter_resolver = path_item_resolver
        required = parameter.get("required", False)
        if parameter["in"] == "formData":
            schema = _extract_non_body_parameter_schema(parameter)
            # TODO: Convert to jsonschema
            form_parameters.append((parameter["name"], required, schema))
        elif parameter["in"] == "body":
            schema = parameter["schema"]
            # TODO: Convert to jsonschema + filter out
            for media_type in body_media_types:
                initialized.append(
                    OpenAPIBodyParameter(
                        required=required,
                        schema=schema,
                        media_type=media_type,
                    )
                )
        else:
            schema = _extract_non_body_parameter_schema(parameter)
            # TODO: Convert to jsonschema
            initialized.append(
                OpenAPINonBodyParameter(
                    name=parameter["name"],
                    location=parameter["in"],
                    required=required,
                    schema=schema,
                )
            )
    if form_parameters:
        schema = _parameters_to_json_schema(form_parameters)
        for media_type in form_data_media_types:
            initialized.append(
                OpenAPIBodyParameter(
                    required=True,
                    schema=schema,
                    media_type=media_type,
                )
            )
    return initialized


def _parameters_to_json_schema(parameters: list[tuple[str, bool, Mapping[str, Any]]]) -> Mapping[str, Any]:
    properties = {}
    required = []
    for name, is_required, schema in parameters:
        properties[name] = schema
        # If parameter names are duplicated, we need to avoid duplicate entries in `required` anyway
        if is_required and name not in required:
            required.append(name)
    return {"properties": properties, "additionalProperties": False, "type": "object", "required": required}


def _extract_non_body_parameter_schema(parameter: NonBodyParameter) -> Mapping[str, Any]:
    return {
        key: value
        for key, value in parameter.items()
        # Allow only supported keywords or vendor extensions
        if key
        in {
            "type",  # only as a string
            "format",
            "items",
            "maximum",
            "exclusiveMaximum",
            "minimum",
            "exclusiveMinimum",
            "maxLength",
            "minLength",
            "pattern",
            "maxItems",
            "minItems",
            "uniqueItems",
            "enum",
            "multipleOf",
        }
        or key.startswith("x-")
        or key == "x-nullable"
    }
