from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, NoReturn, Union

from werkzeug.wrappers import Response as BaseResponse

from .._compat import JSONMixin

if TYPE_CHECKING:
    from datetime import timedelta

    from httpx import Response as httpxResponse
    from requests import PreparedRequest
    from requests import Response as requestsResponse


class WSGIResponse(BaseResponse, JSONMixin):
    # We store "requests" request to build a reproduction code
    request: PreparedRequest
    elapsed: timedelta

    def on_json_loading_failed(self, e: json.JSONDecodeError) -> NoReturn:
        # We don't need a werkzeug-specific exception when JSON parsing error happens
        raise e


def get_payload(response: GenericResponse) -> str:
    from httpx import Response as httpxResponse
    from requests import Response as requestsResponse

    if isinstance(response, (httpxResponse, requestsResponse)):
        return response.text
    return response.get_data(as_text=True)


def get_json(response: GenericResponse) -> Any:
    from httpx import Response as httpxResponse
    from requests import Response as requestsResponse

    if isinstance(response, (httpxResponse, requestsResponse)):
        return json.loads(response.text)
    return response.json


def get_reason(status_code: int) -> str:
    import http.client

    return http.client.responses.get(status_code, "Unknown")


GenericResponse = Union["httpxResponse", "requestsResponse", WSGIResponse]
