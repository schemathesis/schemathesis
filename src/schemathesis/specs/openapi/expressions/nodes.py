"""Expression nodes description and evaluation logic."""
from dataclasses import dataclass
from enum import Enum, unique
from typing import Any, Dict, Optional, Union

from requests.structures import CaseInsensitiveDict

from ....utils import WSGIResponse
from .. import references
from .context import ExpressionContext


@dataclass
class Node:
    """Generic expression node."""

    def evaluate(self, context: ExpressionContext) -> str:
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

    def evaluate(self, context: ExpressionContext) -> str:
        """String tokens are passed as they are.

        ``foo{$request.path.id}``

        "foo" is String token there.
        """
        return self.value


@dataclass
class URL(Node):
    """A node for `$url` expression."""

    def evaluate(self, context: ExpressionContext) -> str:
        return context.case.get_full_url()


@dataclass
class Method(Node):
    """A node for `$method` expression."""

    def evaluate(self, context: ExpressionContext) -> str:
        return context.case.operation.method.upper()


@dataclass
class StatusCode(Node):
    """A node for `$statusCode` expression."""

    def evaluate(self, context: ExpressionContext) -> str:
        return str(context.response.status_code)


@dataclass
class NonBodyRequest(Node):
    """A node for `$request` expressions where location is not `body`."""

    location: str
    parameter: str

    def evaluate(self, context: ExpressionContext) -> str:
        container: Union[Dict, CaseInsensitiveDict] = {
            "query": context.case.query,
            "path": context.case.path_parameters,
            "header": context.case.headers,
        }[self.location] or {}
        if self.location == "header":
            container = CaseInsensitiveDict(container)
        return container[self.parameter]


@dataclass
class BodyRequest(Node):
    """A node for `$request` expressions where location is `body`."""

    pointer: Optional[str] = None

    def evaluate(self, context: ExpressionContext) -> Any:
        document = context.case.body
        if self.pointer is None:
            return document
        return references.resolve_pointer(document, self.pointer[1:])


@dataclass
class HeaderResponse(Node):
    """A node for `$response.header` expressions."""

    parameter: str

    def evaluate(self, context: ExpressionContext) -> str:
        return context.response.headers[self.parameter]


@dataclass
class BodyResponse(Node):
    """A node for `$response.body` expressions."""

    pointer: Optional[str] = None

    def evaluate(self, context: ExpressionContext) -> Any:
        if isinstance(context.response, WSGIResponse):
            document = context.response.json
        else:
            document = context.response.json()
        if self.pointer is None:
            # We need the parsed document - data will be serialized before sending to the application
            return document
        return references.resolve_pointer(document, self.pointer[1:])
