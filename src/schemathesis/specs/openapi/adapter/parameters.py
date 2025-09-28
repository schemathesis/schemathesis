from __future__ import annotations

from itertools import chain
from typing import TYPE_CHECKING, Any, Iterator, Mapping, Sequence, TypedDict, cast

from schemathesis.core.compat import RefResolver
from schemathesis.core.errors import InvalidSchema
from schemathesis.core.jsonschema import BundleError, Bundler
from schemathesis.core.validation import check_header_name
from schemathesis.specs.openapi.adapter.references import maybe_resolve

if TYPE_CHECKING:
    from schemathesis.specs.openapi.parameters import OpenAPIParameter


PathItem = Mapping[str, Any]
Operation = TypedDict("Operation", {"responses": dict})


def _bundle_parameter(parameter: Mapping, resolver: RefResolver, bundler: Bundler) -> dict:
    _, definition = maybe_resolve(parameter, resolver, "")
    schema = definition.get("schema")
    if schema is not None:
        # Copy the definition and bundle the schema to make it self-contained
        definition = {k: v for k, v in definition.items() if k != "schema"}
        try:
            definition["schema"] = bundler.bundle(schema, resolver, inline_recursive=True)
        except BundleError as exc:
            location = parameter.get("in", "")
            name = parameter.get("name", "<UNKNOWN>")
            raise InvalidSchema.from_bundle_error(exc, location, name) from exc
    return cast(dict, definition)


OPENAPI_20_DEFAULT_BODY_MEDIA_TYPE = "application/json"
OPENAPI_20_DEFAULT_FORM_MEDIA_TYPE = "multipart/form-data"


def iter_parameters_v2(
    definition: Mapping[str, Any],
    shared_parameters: Sequence[Mapping[str, Any]],
    default_media_types: list[str],
    resolver: RefResolver,
) -> Iterator[OpenAPIParameter]:
    from schemathesis.specs.openapi.parameters import OpenAPI20Body, OpenAPI20CompositeBody, OpenAPI20Parameter

    media_types = definition.get("consumes", default_media_types)
    # For `in=body` parameters, we imply `application/json` as the default media type because it is the most common.
    body_media_types = media_types or (OPENAPI_20_DEFAULT_BODY_MEDIA_TYPE,)
    # If an API operation has parameters with `in=formData`, Schemathesis should know how to serialize it.
    # We can't be 100% sure what media type is expected by the server and chose `multipart/form-data` as
    # the default because it is broader since it allows us to upload files.
    form_data_media_types = media_types or (OPENAPI_20_DEFAULT_FORM_MEDIA_TYPE,)

    form_parameters = []
    bundler = Bundler()
    for parameter in chain(definition.get("parameters", []), shared_parameters):
        parameter = _bundle_parameter(parameter, resolver, bundler)
        if parameter["in"] in ("header", "cookie"):
            check_header_name(parameter["name"])

        if parameter["in"] == "formData":
            # We need to gather form parameters first before creating a composite parameter for them
            form_parameters.append(parameter)
        elif parameter["in"] == "body":
            # Take the original definition & extract the resource_name from there
            resource_name = None
            for param in chain(definition.get("parameters", []), shared_parameters):
                _, param = maybe_resolve(param, resolver, "")
                if param.get("in") == "body":
                    if "$ref" in param["schema"]:
                        resource_name = _get_resource_name(param["schema"]["$ref"])
            for media_type in body_media_types:
                yield OpenAPI20Body(definition=parameter, media_type=media_type, resource_name=resource_name)
        else:
            yield OpenAPI20Parameter(definition=parameter)

    if form_parameters:
        for media_type in form_data_media_types:
            # Individual `formData` parameters are joined into a single "composite" one.
            yield OpenAPI20CompositeBody.from_parameters(*form_parameters, media_type=media_type)


def iter_parameters_v3(
    definition: Mapping[str, Any],
    shared_parameters: Sequence[Mapping[str, Any]],
    default_media_types: list[str],
    resolver: RefResolver,
) -> Iterator[OpenAPIParameter]:
    from schemathesis.specs.openapi.parameters import OpenAPI30Body, OpenAPI30Parameter

    # Open API 3.0 has the `requestBody` keyword, which may contain multiple different payload variants.
    operation = definition

    bundler = Bundler()
    for parameter in chain(definition.get("parameters", []), shared_parameters):
        parameter = _bundle_parameter(parameter, resolver, bundler)
        if parameter["in"] in ("header", "cookie"):
            check_header_name(parameter["name"])

        yield OpenAPI30Parameter(definition=parameter)

    request_body_or_ref = operation.get("requestBody")
    if request_body_or_ref is not None:
        scope, request_body_or_ref = maybe_resolve(request_body_or_ref, resolver, "")
        # It could be an object inside `requestBodies`, which could be a reference itself
        _, request_body = maybe_resolve(request_body_or_ref, resolver, scope)

        required = request_body.get("required", False)
        for media_type, content in request_body["content"].items():
            resource_name = None
            schema = content.get("schema")
            if isinstance(schema, dict):
                content = dict(content)
                if "$ref" in schema:
                    resource_name = _get_resource_name(schema["$ref"])
                try:
                    to_bundle = cast(dict[str, Any], schema)
                    bundled = bundler.bundle(to_bundle, resolver, inline_recursive=True)
                    content["schema"] = bundled
                except BundleError as exc:
                    raise InvalidSchema.from_bundle_error(exc, "body") from exc
            yield OpenAPI30Body(content, media_type=media_type, required=required, resource_name=resource_name)


def _get_resource_name(reference: str) -> str:
    return reference.rsplit("/", maxsplit=1)[1]


def build_path_parameter_v2(kwargs: Mapping[str, Any]) -> OpenAPIParameter:
    from schemathesis.specs.openapi.parameters import OpenAPI20Parameter

    return OpenAPI20Parameter({"in": "path", "required": True, "type": "string", "minLength": 1, **kwargs})


def build_path_parameter_v3(kwargs: Mapping[str, Any]) -> OpenAPIParameter:
    from schemathesis.specs.openapi.parameters import OpenAPI30Parameter

    return OpenAPI30Parameter({"in": "path", "required": True, "schema": {"type": "string", "minLength": 1}, **kwargs})
