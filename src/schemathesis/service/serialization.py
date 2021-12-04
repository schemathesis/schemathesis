from typing import Any, Callable, Dict, List, Optional, TypeVar

import attr

from ..runner import events
from ..runner.serialization import SerializedCase, deduplicate_checks

S = TypeVar("S", bound=events.ExecutionEvent)
SerializeFunc = Callable[[S], Optional[Dict[str, Any]]]


def serialize_initialized(event: events.Initialized) -> Optional[Dict[str, Any]]:
    return {
        "schema": event.schema,
        "operations_count": event.operations_count,
        "location": event.location or "",
        "base_url": event.base_url,
    }


def serialize_before_execution(event: events.BeforeExecution) -> Optional[Dict[str, Any]]:
    return {
        "correlation_id": event.correlation_id,
        "verbose_name": event.verbose_name,
        "data_generation_method": event.data_generation_method,
    }


def _serialize_case(case: SerializedCase) -> Dict[str, Any]:
    return {
        "verbose_name": case.verbose_name,
        "path_template": case.path_template,
        "path_parameters": stringify_path_parameters(case.path_parameters),
        "query": prepare_query(case.query),
        "cookies": case.cookies,
        "media_type": case.media_type,
    }


def serialize_after_execution(event: events.AfterExecution) -> Optional[Dict[str, Any]]:
    return {
        "correlation_id": event.correlation_id,
        "verbose_name": event.verbose_name,
        "data_generation_method": event.data_generation_method,
        "result": {
            "checks": [
                {
                    "name": check.name,
                    "value": check.value,
                    "request": {
                        "method": check.request.method,
                        "uri": check.request.uri,
                        "body": check.request.body,
                        "headers": check.request.headers,
                    },
                    "response": {
                        "status_code": check.response.status_code,
                        "headers": check.response.headers,
                        "body": check.response.body,
                        "encoding": check.response.encoding,
                        "elapsed": check.response.elapsed,
                    }
                    if check.response is not None
                    else None,
                    "example": _serialize_case(check.example),
                    "message": check.message,
                    "context": attr.asdict(check.context) if check.context is not None else None,
                }
                for check in deduplicate_checks(event.result.checks)
            ],
            "errors": [
                {
                    "exception": error.exception,
                    "exception_with_traceback": error.exception_with_traceback,
                    "example": None if error.example is None else _serialize_case(error.example),
                }
                for error in event.result.errors
            ],
        },
    }


def serialize_interrupted(_: events.Interrupted) -> Optional[Dict[str, Any]]:
    return None


def serialize_internal_error(event: events.InternalError) -> Optional[Dict[str, Any]]:
    return {
        "message": event.message,
        "exception_type": event.exception_type,
        "exception_with_traceback": event.exception_with_traceback,
    }


def serialize_finished(event: events.Finished) -> Optional[Dict[str, Any]]:
    return {
        "generic_errors": [
            {
                "exception": error.exception,
                "exception_with_traceback": error.exception_with_traceback,
                "title": error.title,
            }
            for error in event.generic_errors
        ]
    }


def serialize_event(
    event: events.ExecutionEvent,
    on_initialized: SerializeFunc = serialize_initialized,
    on_before_execution: SerializeFunc = serialize_before_execution,
    on_after_execution: SerializeFunc = serialize_after_execution,
    on_interrupted: SerializeFunc = serialize_interrupted,
    on_internal_error: SerializeFunc = serialize_internal_error,
    on_finished: SerializeFunc = serialize_finished,
) -> Dict[str, Optional[Dict[str, Any]]]:
    """Turn an event into JSON-serializable structure."""
    # Due to https://github.com/python-attrs/attrs/issues/864 it is easier to implement filtration manually
    serializer = {
        events.Initialized: on_initialized,
        events.BeforeExecution: on_before_execution,
        events.AfterExecution: on_after_execution,
        events.Interrupted: on_interrupted,
        events.InternalError: on_internal_error,
        events.Finished: on_finished,
    }[event.__class__]
    # Externally tagged structure
    return {event.__class__.__name__: serializer(event)}


def stringify_path_parameters(path_parameters: Optional[Dict[str, Any]]) -> Dict[str, str]:
    """Cast all path parameter values to strings.

    Path parameter values may be of arbitrary type, but to display them properly they should be casted to strings.
    """
    return {key: str(value) for key, value in (path_parameters or {}).items()}


def prepare_query(query: Optional[Dict[str, Any]]) -> Dict[str, List[str]]:
    """Convert all query values to list of strings.

    Query parameters may be generated in different shapes, including integers, strings, list of strings, etc.
    It can also be an object, if the schema contains an object, but `style` and `explode` combo is not applicable.
    """

    def to_list_of_strings(value: Any) -> List[str]:
        if isinstance(value, list):
            return list(map(str, value))
        if isinstance(value, str):
            return [value]
        return [str(value)]

    return {key: to_list_of_strings(value) for key, value in (query or {}).items()}
