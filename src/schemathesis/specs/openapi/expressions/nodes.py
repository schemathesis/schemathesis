"""Expression nodes description and evaluation logic."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum, unique
from typing import TYPE_CHECKING, Any, cast

from requests.structures import CaseInsensitiveDict

from schemathesis.core.deserialization import DeserializationContext, deserialize_response
from schemathesis.core.transforms import UNRESOLVABLE, Unresolvable, resolve_pointer
from schemathesis.core.transport import Response, response_content_type
from schemathesis.generation.stateful.state_machine import StepOutput
from schemathesis.transport.requests import REQUESTS_TRANSPORT

if TYPE_CHECKING:
    from .extractors import RegexExtractor


def link_media_type(response: Response) -> str:
    """The media type links read a response body as; one without `Content-Type` is read as JSON."""
    content_type = response_content_type(response)
    return "application/json" if content_type is None else content_type


@dataclass
class Node:
    """Generic expression node."""

    def evaluate(self, output: StepOutput) -> str | Unresolvable:
        raise NotImplementedError


@unique
class NodeType(StrEnum):
    URL = "$url"
    METHOD = "$method"
    STATUS_CODE = "$statusCode"
    REQUEST = "$request"
    RESPONSE = "$response"


@dataclass(slots=True)
class String(Node):
    """A simple string that is not evaluated somehow specifically."""

    value: str

    def evaluate(self, output: StepOutput) -> str | Unresolvable:
        """String tokens are passed as they are.

        ``foo{$request.path.id}``

        "foo" is String token there.
        """
        return self.value


@dataclass(slots=True)
class URL(Node):
    """A node for `$url` expression."""

    def evaluate(self, output: StepOutput) -> str | Unresolvable:
        import requests

        base_url = output.case.operation.base_url or "http://127.0.0.1"
        kwargs = REQUESTS_TRANSPORT.serialize_case(output.case, base_url=base_url)
        prepared = requests.Request(**kwargs).prepare()
        return cast(str, prepared.url)


@dataclass(slots=True)
class Method(Node):
    """A node for `$method` expression."""

    def evaluate(self, output: StepOutput) -> str | Unresolvable:
        return output.case.operation.method.upper()


@dataclass(slots=True)
class StatusCode(Node):
    """A node for `$statusCode` expression."""

    def evaluate(self, output: StepOutput) -> str | Unresolvable:
        return str(output.response.status_code)


@dataclass(slots=True)
class NonBodyRequest(Node):
    """A node for `$request` expressions where location is not `body`."""

    location: str
    parameter: str
    extractor: RegexExtractor | None

    def __init__(self, location: str, parameter: str, extractor: RegexExtractor | None = None) -> None:
        self.location = location
        self.parameter = parameter
        self.extractor = extractor

    def evaluate(self, output: StepOutput) -> str | Unresolvable:
        container = {
            "query": output.case.query,
            "path": output.case.path_parameters,
            "header": output.case.headers,
        }[self.location] or {}
        if self.location == "header":
            container = CaseInsensitiveDict(container)
        value = container.get(self.parameter)
        if value is None:
            return UNRESOLVABLE
        if self.extractor is not None:
            return self.extractor.extract(value) or UNRESOLVABLE
        return value


@dataclass(slots=True)
class BodyRequest(Node):
    """A node for `$request` expressions where location is `body`."""

    pointer: str | None

    def __init__(self, pointer: str | None = None) -> None:
        self.pointer = pointer

    def evaluate(self, output: StepOutput) -> Any | Unresolvable:
        document = output.case.body
        if self.pointer is None:
            return document
        return resolve_pointer(document, self.pointer[1:])


@dataclass(slots=True)
class HeaderResponse(Node):
    """A node for `$response.header` expressions."""

    parameter: str
    extractor: RegexExtractor | None

    def __init__(self, parameter: str, extractor: RegexExtractor | None = None) -> None:
        self.parameter = parameter
        self.extractor = extractor

    def evaluate(self, output: StepOutput) -> str | Unresolvable:
        value = output.response.headers.get(self.parameter.lower())
        if value is None:
            return UNRESOLVABLE
        if self.extractor is not None:
            return self.extractor.extract(value[0]) or UNRESOLVABLE
        return value[0]


@dataclass(slots=True)
class BodyResponse(Node):
    """A node for `$response.body` expressions."""

    pointer: str | None
    extractor: RegexExtractor | None

    def __init__(self, pointer: str | None = None, extractor: RegexExtractor | None = None) -> None:
        self.pointer = pointer
        self.extractor = extractor

    def evaluate(self, output: StepOutput) -> Any:
        response = output.response
        content_type = link_media_type(response)

        context = DeserializationContext(operation=output.case.operation, case=output.case)
        document = deserialize_response(response, content_type, context=context)

        if self.pointer is None:
            # We need the parsed document - data will be serialized before sending to the application
            return document
        value = resolve_pointer(document, self.pointer[1:])
        if self.extractor is not None:
            if not isinstance(value, str):
                return UNRESOLVABLE
            return self.extractor.extract(value) or UNRESOLVABLE
        return value
