# pylint: disable=too-many-instance-attributes
from typing import Any, Dict

import attr

from .types import Body, Cookies, FormData, Headers, PathParameters, Query


@attr.s(slots=True)
class Case:
    """A single test case parameters."""

    path: str = attr.ib()
    method: str = attr.ib()
    path_parameters: PathParameters = attr.ib()
    headers: Headers = attr.ib()
    cookies: Cookies = attr.ib()
    query: Query = attr.ib()
    body: Body = attr.ib()
    form_data: FormData = attr.ib()

    @property
    def formatted_path(self) -> str:
        # pylint: disable=not-a-mapping
        return self.path.format(**self.path_parameters)


def empty_object() -> Dict[str, Any]:
    return {"properties": {}, "additionalProperties": False, "type": "object", "required": []}


@attr.s(slots=True)
class Endpoint:
    """A container that could be used for test cases generation."""

    path: str = attr.ib()
    method: str = attr.ib()
    path_parameters: PathParameters = attr.ib(factory=empty_object)
    headers: Headers = attr.ib(factory=empty_object)
    cookies: Cookies = attr.ib(factory=empty_object)
    query: Query = attr.ib(factory=empty_object)
    body: Body = attr.ib(factory=empty_object)
    form_data: FormData = attr.ib(factory=empty_object)
