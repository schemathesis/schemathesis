"""Expression nodes description and evaluation logic."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, unique
from typing import TYPE_CHECKING, Any, cast

from requests.structures import CaseInsensitiveDict

from schemathesis.core.transforms import UNRESOLVABLE, Unresolvable, resolve_pointer
from schemathesis.generation.stateful.state_machine import StepOutput
from schemathesis.transport.requests import REQUESTS_TRANSPORT

if TYPE_CHECKING:
    from .extractors import Extractor


@dataclass
class Node:
    """Generic expression node."""

    def evaluate(self, output: StepOutput) -> str | Unresolvable:
        raise NotImplementedError


@unique
class NodeType(Enum):
    URL = "$url"
    METHOD = "$method"
    STATUS_CODE = "$statusCode"
    REQUEST = "$request"
    RESPONSE = "$response"


@dataclass
class String(Node):
    """A simple string that is not evaluated somehow specifically."""

    value: str

    def evaluate(self, output: StepOutput) -> str | Unresolvable:
        """String tokens are passed as they are.

        ``foo{$request.path.id}``

        "foo" is String token there.
        """
        return self.value


@dataclass
class URL(Node):
    """A node for `$url` expression."""

    def evaluate(self, output: StepOutput) -> str | Unresolvable:
        import requests

        base_url = output.case.operation.base_url or "http://127.0.0.1"
        kwargs = REQUESTS_TRANSPORT.serialize_case(output.case, base_url=base_url)
        prepared = requests.Request(**kwargs).prepare()
        return cast(str, prepared.url)


@dataclass
class Method(Node):
    """A node for `$method` expression."""

    def evaluate(self, output: StepOutput) -> str | Unresolvable:
        return output.case.operation.method.upper()


@dataclass
class StatusCode(Node):
    """A node for `$statusCode` expression."""

    def evaluate(self, output: StepOutput) -> str | Unresolvable:
        return str(output.response.status_code)


@dataclass
class NonBodyRequest(Node):
    """A node for `$request` expressions where location is not `body`."""

    location: str
    parameter: str
    extractor: Extractor | None = None

    def evaluate(self, output: StepOutput) -> str | Unresolvable:
        container: dict | CaseInsensitiveDict = {
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


@dataclass
class BodyRequest(Node):
    """A node for `$request` expressions where location is `body`."""

    pointer: str | None = None

    def evaluate(self, output: StepOutput) -> Any | Unresolvable:
        document = output.case.body
        if self.pointer is None:
            return document
        return resolve_pointer(document, self.pointer[1:])


@dataclass
class HeaderResponse(Node):
    """A node for `$response.header` expressions."""

    parameter: str
    extractor: Extractor | None = None

    def evaluate(self, output: StepOutput) -> str | Unresolvable:
        value = output.response.headers.get(self.parameter.lower())
        if value is None:
            return UNRESOLVABLE
        if self.extractor is not None:
            return self.extractor.extract(value[0]) or UNRESOLVABLE
        return value[0]


@dataclass
class BodyResponse(Node):
    """A node for `$response.body` expressions."""

    pointer: str | None = None

    def evaluate(self, output: StepOutput) -> Any:
        document = output.response.json()
        if self.pointer is None:
            # We need the parsed document - data will be serialized before sending to the application
            return document
        return resolve_pointer(document, self.pointer[1:])
