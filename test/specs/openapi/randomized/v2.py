# ruff: noqa: F722, F821
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from .types import CombinedDict, Missing, Pattern, UniqueList

RESPONSE_SUCCESS = {"description": "OK"}
PATH_ITEM_SAMPLE = {"get": {"responses": {"default": RESPONSE_SUCCESS}}}
PARAMETER_SAMPLE = {"name": "sample", "in": "query", "type": "string"}
ResponseId = Pattern["^([0-9]{3})$|^(default)$"]


@dataclass
class PathItemReference:
    def map_value(self, value):
        value["$ref"] = "#/x-paths/Entry"
        return value


@dataclass
class ParameterReference:
    def map_value(self, value):
        value["$ref"] = "#/parameters/SampleParameter"
        return value


@dataclass
class ResponseReference:
    def map_value(self, value):
        value["$ref"] = "#/responses/Success"
        return value


MediaType = (
    Literal["application/json"]
    | Literal["application/xml"]
    | Literal["text/plain"]
    | Literal["text/yaml"]
    | Literal["application/x-www-form-urlencoded"]
    | Literal["multipart/form-data"]
    | Literal["application/octet-stream"]
)


@dataclass
class Swagger:
    swagger: Literal["2.0"]
    info: Info
    host: Pattern["^[^{}/ :\\\\]+(?::\\d+)?$"] | Missing
    basePath: Pattern["^/"] | Missing
    paths: dict[Pattern["^/"], PathItem | PathItemReference]
    consumes: MediaTypeList | Missing
    produces: MediaTypeList | Missing
    parameters: CombinedDict[str, Parameter, {"SampleParameter": PARAMETER_SAMPLE}]
    responses: CombinedDict[ResponseId, Response, {"Success": RESPONSE_SUCCESS}]
    definitions: Definitions

    def map_value(self, value):
        value["x-paths"] = {"Entry": PATH_ITEM_SAMPLE}
        return value


@dataclass
class Definitions:
    pass


@dataclass
class Info:
    version: Literal["1.0.0"]
    title: Literal["Example API"]


@dataclass
class PathItem:
    get: Operation | Missing
    put: Operation | Missing
    post: Operation | Missing
    delete: Operation | Missing
    options: Operation | Missing
    head: Operation | Missing
    patch: Operation | Missing
    parameters: ParameterList | Missing

    def map_value(self, value):
        if "parameters" in value:
            value["parameters"] = deduplicate_parameters(value["parameters"])
        return value


@dataclass
class Operation:
    operationId: str | Missing
    parameters: ParameterList | Missing
    tags: UniqueList[str] | Missing
    consumes: MediaTypeList | Missing
    produces: MediaTypeList | Missing
    responses: dict[ResponseId, Response]

    def map_value(self, value):
        if not value["responses"]:
            value["responses"] = {"default": RESPONSE_SUCCESS}
        if "parameters" in value:
            value["parameters"] = deduplicate_parameters(value["parameters"])
        return value


def deduplicate_parameters(parameters):
    parameter_names = set()
    deduplicated = []
    has_reference = False
    for parameter in parameters:
        if "$ref" in parameter:
            if has_reference:
                continue
            has_reference = True
            deduplicated.append(parameter)
        else:
            name = parameter["name"]
            if name in parameter_names:
                continue
            parameter_names.add(name)
            deduplicated.append(parameter)
    return deduplicated


@dataclass
class Response:
    description: Literal["Ok"]


@dataclass
class BodyParameter:
    name: str
    schema: dict = field(default_factory=dict)

    def map_value(self, value):
        value["in"] = "body"
        return value

    def __hash__(self):
        return hash(self.name)


@dataclass
class QueryParameter:
    name: str
    type: Literal["string", "number", "boolean", "integer", "array"]

    def map_value(self, value):
        value["in"] = "query"
        return value

    def __hash__(self):
        return hash(self.name)


@dataclass
class HeaderParameter:
    name: str
    type: Literal["string", "number", "boolean", "integer", "array"]

    def map_value(self, value):
        value["in"] = "header"
        return value

    def __hash__(self):
        return hash(self.name)


@dataclass
class PathParameter:
    name: str
    type: Literal["string", "number", "boolean", "integer", "array"]

    def map_value(self, value):
        value["in"] = "path"
        value["required"] = True
        return value

    def __hash__(self):
        return hash(self.name)


@dataclass
class FormDataParameter:
    name: str
    type: Literal["string", "number", "boolean", "integer", "array", "file"]

    def map_value(self, value):
        value["in"] = "formData"
        return value

    def __hash__(self):
        return hash(self.name)


Parameter = BodyParameter | QueryParameter | HeaderParameter | PathParameter | FormDataParameter
ParameterList = list[Parameter | ParameterReference]
MediaTypeList = UniqueList[MediaType]
