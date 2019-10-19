# pylint: disable=too-many-instance-attributes
from typing import TYPE_CHECKING, Any, Dict, Optional

import attr
from hypothesis.searchstrategy import SearchStrategy

from .types import Body, Cookies, FormData, Headers, PathParameters, Query

if TYPE_CHECKING:
    import requests  # Typechecking-only import to speedup import of schemathesis


@attr.s(slots=True)
class Case:
    """A single test case parameters."""

    path: str = attr.ib()
    method: str = attr.ib()
    base_url: Optional[str] = attr.ib(default=None)
    path_parameters: PathParameters = attr.ib(factory=dict)
    headers: Headers = attr.ib(factory=dict)
    cookies: Cookies = attr.ib(factory=dict)
    query: Query = attr.ib(factory=dict)
    body: Body = attr.ib(factory=dict)
    form_data: FormData = attr.ib(factory=dict)

    @property
    def formatted_path(self) -> str:
        # pylint: disable=not-a-mapping
        return self.path.format(**self.path_parameters)

    def _get_base_url(self, base_url: Optional[str]) -> str:
        if base_url is None:
            if self.base_url is not None:
                base_url = self.base_url
            else:
                raise ValueError(
                    "Base URL is required as `base_url` argument in `call` or should be specified "
                    "in the schema constructor as a part of Schema URL."
                )
        return base_url

    def as_requests_kwargs(self, base_url: Optional[str] = None) -> Dict[str, Any]:
        """Convert the case into a dictionary acceptable by requests."""
        base_url = self._get_base_url(base_url)
        return {
            "method": self.method,
            "url": f"{base_url}{self.formatted_path}",
            "headers": self.headers,
            "params": self.query,
            "json": self.body,
        }

    def call(self, base_url: Optional[str] = None, **kwargs: Any) -> "requests.Response":
        """Convert the case into a dictionary acceptable by requests."""
        # Local import to speedup import of schemathesis
        import requests  # pylint: disable=import-outside-toplevel

        base_url = self._get_base_url(base_url)
        data = self.as_requests_kwargs(base_url)
        return requests.request(**data, **kwargs)


def empty_object() -> Dict[str, Any]:
    return {"properties": {}, "additionalProperties": False, "type": "object", "required": []}


@attr.s(slots=True)
class Endpoint:
    """A container that could be used for test cases generation."""

    path: str = attr.ib()
    method: str = attr.ib()
    base_url: Optional[str] = attr.ib(default=None)
    path_parameters: PathParameters = attr.ib(factory=empty_object)
    headers: Headers = attr.ib(factory=empty_object)
    cookies: Cookies = attr.ib(factory=empty_object)
    query: Query = attr.ib(factory=empty_object)
    body: Body = attr.ib(factory=empty_object)
    form_data: FormData = attr.ib(factory=empty_object)

    def as_strategy(self) -> SearchStrategy:
        from ._hypothesis import get_case_strategy  # pylint: disable=import-outside-toplevel

        return get_case_strategy(self)
