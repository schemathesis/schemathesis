from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, Dict, Generator, Optional

import attr
import werkzeug

from .. import Case
from ..types import Cookies
from ..utils import WSGIResponse
from .core import Response, Transport

if TYPE_CHECKING:
    from werkzeug.test import WSGIApplication


@attr.s(slots=True)
class WerkzeugTransport(Transport):
    app: "WSGIApplication" = attr.ib()
    client: werkzeug.Client = attr.ib(init=False)

    def __attrs_post_init__(self) -> None:
        self.client = werkzeug.Client(self.app, WSGIResponse)

    def send(self, case: Case, **kwargs: Any) -> Response:
        cookies = kwargs.pop("cookies", None)
        data = self.into_kwargs(case)
        data.update(**kwargs)
        with cookie_handler(self.client, cookies):
            response: WSGIResponse = self.client.open(**data)
        return self.into_response(response)

    def into_response(self, response: WSGIResponse) -> Response:
        pass

    def into_kwargs(self, case: Case) -> Dict[str, Any]:
        return {}


SERVER_NAME = "localhost"


@contextmanager
def cookie_handler(client: werkzeug.Client, cookies: Optional[Cookies]) -> Generator[None, None, None]:
    """Set cookies required for a call."""
    if not cookies:
        yield
    else:
        for key, value in cookies.items():
            client.set_cookie(SERVER_NAME, key, value)
        yield
        for key in cookies:
            client.delete_cookie(SERVER_NAME, key)
