from typing import Union

import attr
import requests

from .utils import WSGIResponse


class InvalidSchema(Exception):
    """Schema associated with an endpoint contains an error."""


@attr.s
class HTTPError(Exception):
    response: Union[requests.Response, WSGIResponse] = attr.ib()
    url: str = attr.ib()
