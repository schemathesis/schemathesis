from __future__ import annotations

import sys
import json
from typing import Union, TYPE_CHECKING, NoReturn
from .._compat import JSONMixin
from werkzeug.wrappers import Response as BaseResponse

if TYPE_CHECKING:
    from httpx import Response as httpxResponse
    from requests import Response as requestsResponse
    from requests import PreparedRequest


class WSGIResponse(BaseResponse, JSONMixin):
    # We store "requests" request to build a reproduction code
    request: PreparedRequest

    def on_json_loading_failed(self, e: json.JSONDecodeError) -> NoReturn:
        # We don't need a werkzeug-specific exception when JSON parsing error happens
        raise e


def get_reason(status_code: int) -> str:
    if sys.version_info < (3, 9) and status_code == 418:
        # Python 3.8 does not have 418 status in the `HTTPStatus` enum
        return "I'm a Teapot"

    import http.client

    return http.client.responses.get(status_code, "Unknown")


GenericResponse = Union["httpxResponse", "requestsResponse", WSGIResponse]
