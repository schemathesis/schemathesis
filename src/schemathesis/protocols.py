"""Protocols that describe how concrete implementations should behave."""
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Sequence, Tuple, TypeVar

import requests
from hypothesis.strategies import SearchStrategy
from typing_extensions import Protocol

from .types import Body, Cookies, FormData, Headers, PathParameters, Query
from .utils import GenericResponse, WSGIResponse

if TYPE_CHECKING:
    from .hooks import HookDispatcher
    from .stateful import Feedback, Stateful, StatefulTest


class CaseProtocol(Protocol):
    endpoint: "EndpointProtocol"
    path_parameters: Optional[PathParameters]
    headers: Optional[Headers]
    cookies: Optional[Cookies]
    query: Optional[Query]
    body: Optional[Body]
    form_data: Optional[FormData]

    @property
    def path(self) -> str:
        ...

    @property
    def full_path(self) -> str:
        ...

    @property
    def method(self) -> str:
        ...

    @property
    def base_url(self) -> Optional[str]:
        ...

    @property
    def app(self) -> Any:
        ...

    @property
    def formatted_path(self) -> str:
        ...

    def partial_deepcopy(self) -> "CaseProtocol":
        ...

    def as_requests_kwargs(self, base_url: Optional[str] = None) -> Dict[str, Any]:
        ...

    def as_text_lines(self) -> List[str]:
        ...

    def get_code_to_reproduce(self, headers: Optional[Dict[str, Any]] = None) -> str:
        ...

    def get_full_base_url(self) -> Optional[str]:
        ...

    def call(
        self,
        base_url: Optional[str] = None,
        session: Optional[requests.Session] = None,
        headers: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> requests.Response:
        ...

    def call_wsgi(self, app: Any = None, headers: Optional[Dict[str, str]] = None, **kwargs: Any) -> WSGIResponse:
        ...

    def call_asgi(
        self,
        app: Any = None,
        base_url: Optional[str] = "http://testserver",
        headers: Optional[Dict[str, str]] = None,
        **kwargs: Any,
    ) -> requests.Response:
        ...

    def get_full_url(self) -> str:
        ...


C = TypeVar("C", bound=CaseProtocol)


class EndpointProtocol(Protocol[C]):
    path: str
    method: str
    definition: Any
    schema: Any
    app: Any
    base_url: Optional[str]
    path_parameters: Optional[PathParameters]
    headers: Optional[Headers]
    cookies: Optional[Cookies]
    query: Optional[Query]
    body: Optional[Body]
    form_data: Optional[FormData]

    def __init__(  # pylint: disable=too-many-arguments
        self,
        path: str,
        method: str,
        definition: Any,
        schema: Any,
        app: Any,
        base_url: Optional[str],
        path_parameters: Optional[PathParameters],
        headers: Optional[Headers],
        cookies: Optional[Cookies],
        query: Optional[Query],
        body: Optional[Body],
        form_data: Optional[FormData],
    ) -> None:
        ...

    def as_strategy(
        self, hooks: Optional["HookDispatcher"] = None, feedback: Optional["Feedback"] = None
    ) -> SearchStrategy[C]:
        ...

    def get_hypothesis_conversions(self, location: str) -> Optional[Callable]:
        ...

    def get_stateful_tests(self, response: GenericResponse, stateful: Optional["Stateful"]) -> Sequence["StatefulTest"]:
        ...

    def get_strategies_from_examples(self) -> List[SearchStrategy[C]]:
        ...

    def get_request_payload_content_types(self) -> List[str]:
        ...

    def prepare_multipart(self, form_data: Optional[FormData]) -> Tuple[Optional[List], Optional[Dict[str, Any]]]:
        ...

    def partial_deepcopy(self) -> "EndpointProtocol":
        ...

    @property
    def full_path(self) -> str:
        ...
