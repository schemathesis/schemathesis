"""Expression nodes description and evaluation logic."""
import json
from enum import Enum, unique
from typing import Any, Dict, Optional, Union

import attr
from requests.structures import CaseInsensitiveDict

from ....utils import WSGIResponse
from . import pointers
from .context import ExpressionContext
from .errors import RuntimeExpressionError


@attr.s(slots=True)  # pragma: no mutate
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


@attr.s(slots=True)  # pragma: no mutate
class String(Node):
    """A simple string that is not evaluated somehow specifically."""

    value: str = attr.ib()  # pragma: no mutate

    def evaluate(self, context: ExpressionContext) -> str:
        """String tokens are passed as they are.

        ``foo{$request.path.id}``

        "foo" is String token there.
        """
        return self.value


@attr.s(slots=True)  # pragma: no mutate
class URL(Node):
    """A node for `$url` expression."""

    def evaluate(self, context: ExpressionContext) -> str:
        return context.case.get_full_url()


@attr.s(slots=True)  # pragma: no mutate
class Method(Node):
    """A node for `$method` expression."""

    def evaluate(self, context: ExpressionContext) -> str:
        return context.case.endpoint.method


@attr.s(slots=True)  # pragma: no mutate
class StatusCode(Node):
    """A node for `$statusCode` expression."""

    def evaluate(self, context: ExpressionContext) -> str:
        return str(context.response.status_code)


@attr.s(slots=True)  # pragma: no mutate
class NonBodyRequest(Node):
    """A node for `$request` expressions where location is not `body`."""

    location: str = attr.ib()  # pragma: no mutate
    parameter: str = attr.ib()  # pragma: no mutate

    def evaluate(self, context: ExpressionContext) -> str:
        container: Union[Dict, CaseInsensitiveDict] = {
            "query": context.case.query,
            "path": context.case.path_parameters,
            "header": context.case.headers,
        }[self.location] or {}
        if self.location == "header":
            container = CaseInsensitiveDict(container)
        return str(container[self.parameter])


@attr.s(slots=True)  # pragma: no mutate
class BodyRequest(Node):
    """A node for `$request` expressions where location is `body`."""

    pointer: Optional[str] = attr.ib(default=None)  # pragma: no mutate

    def evaluate(self, context: ExpressionContext) -> Any:
        if self.pointer is None:
            try:
                return json.dumps(context.case.body)
            except TypeError as exc:
                raise RuntimeExpressionError("The request body is not JSON-serializable") from exc
        document = context.case.body
        return pointers.resolve(document, self.pointer[1:])


@attr.s(slots=True)  # pragma: no mutate
class HeaderResponse(Node):
    """A node for `$response.header` expressions."""

    parameter: str = attr.ib()  # pragma: no mutate

    def evaluate(self, context: ExpressionContext) -> str:
        return context.response.headers[self.parameter]


@attr.s(slots=True)  # pragma: no mutate
class BodyResponse(Node):
    """A node for `$response.body` expressions."""

    pointer: Optional[str] = attr.ib(default=None)  # pragma: no mutate

    def evaluate(self, context: ExpressionContext) -> Any:
        if self.pointer is None:
            return context.response.text
        if isinstance(context.response, WSGIResponse):
            document = context.response.json
        else:
            document = context.response.json()
        return pointers.resolve(document, self.pointer[1:])
