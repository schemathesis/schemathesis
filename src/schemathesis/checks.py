from typing import TYPE_CHECKING, Callable, Tuple, Union

import jsonschema
import requests

from .utils import WSGIResponse, are_content_types_equal

if TYPE_CHECKING:
    from .models import Case

GenericResponse = Union[requests.Response, WSGIResponse]  # pragma: no mutate


def not_a_server_error(response: GenericResponse, case: "Case") -> None:
    """A check to verify that the response is not a server-side error."""
    if response.status_code >= 500:
        raise AssertionError(f"Received a response with 5xx status code: {response.status_code}")


def status_code_conformance(response: GenericResponse, case: "Case") -> None:
    responses = case.endpoint.definition.get("responses", {})
    # "default" can be used as the default response object for all HTTP codes that are not covered individually
    if "default" in responses:
        return
    allowed_response_statuses = list(map(str, responses))
    if str(response.status_code) not in allowed_response_statuses:
        message = (
            f"Received a response with a status code, which is not defined in the schema: "
            f"{response.status_code}\n\nDeclared status codes: {', '.join(allowed_response_statuses)}"
        )
        raise AssertionError(message)


def content_type_conformance(response: GenericResponse, case: "Case") -> None:
    global_produces = case.endpoint.schema.raw_schema.get("produces", None)
    if global_produces:
        produces = global_produces
    else:
        produces = case.endpoint.definition.get("produces", None)
    if not produces:
        return
    content_type = response.headers["Content-Type"]
    for option in produces:
        if are_content_types_equal(option, content_type):
            return
    raise AssertionError(
        f"Received a response with '{content_type}' Content-Type, "
        f"but it is not declared in the schema.\n\n"
        f"Defined content types: {', '.join(produces)}"
    )


def response_schema_conformance(response: GenericResponse, case: "Case") -> None:
    if not response.headers["Content-Type"].startswith("application/json"):
        return
    # the keys should be strings
    responses = {str(key): value for key, value in case.endpoint.definition.get("responses", {}).items()}
    status_code = str(response.status_code)
    if status_code in responses:
        definition = responses[status_code]
    elif "default" in responses:
        definition = responses["default"]
    else:
        # No response defined for the received response status code
        return
    schema = definition.get("schema")
    if not schema:
        return
    if isinstance(response, requests.Response):
        data = response.json()
    else:
        data = response.json
    try:
        jsonschema.validate(data, schema)
    except jsonschema.ValidationError as exc:
        raise AssertionError(f"The received response does not conform to the defined schema!\n\nDetails: \n\n{exc}")


DEFAULT_CHECKS = (not_a_server_error,)
OPTIONAL_CHECKS = (status_code_conformance, content_type_conformance, response_schema_conformance)
ALL_CHECKS: Tuple[
    Callable[[Union[requests.Response, WSGIResponse], "Case"], None], ...
] = DEFAULT_CHECKS + OPTIONAL_CHECKS
