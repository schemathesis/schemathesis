from typing import Any, Callable, Dict, List, Optional, TypeVar, cast

import attr

from ..models import Response
from ..runner import events
from ..runner.serialization import SerializedCase
from ..utils import merge

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


def _serialize_response(response: Response) -> Dict[str, Any]:
    return {
        "status_code": response.status_code,
        "headers": response.headers,
        "body": response.body,
        "encoding": response.encoding,
        "elapsed": response.elapsed,
    }


def serialize_after_execution(event: events.AfterExecution) -> Optional[Dict[str, Any]]:
    return {
        "correlation_id": event.correlation_id,
        "verbose_name": event.verbose_name,
        "status": event.status,
        "elapsed_time": event.elapsed_time,
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
                    "response": _serialize_response(check.response) if check.response is not None else None,
                    "example": _serialize_case(check.example),
                    "message": check.message,
                    "context": attr.asdict(check.context) if check.context is not None else None,
                    "history": [
                        {"case": _serialize_case(entry.case), "response": _serialize_response(entry.response)}
                        for entry in check.history
                    ],
                }
                for check in event.result.checks
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
        ],
        "running_time": event.running_time,
    }


SERIALIZER_MAP = {
    events.Initialized: serialize_initialized,
    events.BeforeExecution: serialize_before_execution,
    events.AfterExecution: serialize_after_execution,
    events.Interrupted: serialize_interrupted,
    events.InternalError: serialize_internal_error,
    events.Finished: serialize_finished,
}


def serialize_event(
    event: events.ExecutionEvent,
    *,
    on_initialized: Optional[SerializeFunc] = None,
    on_before_execution: Optional[SerializeFunc] = None,
    on_after_execution: Optional[SerializeFunc] = None,
    on_interrupted: Optional[SerializeFunc] = None,
    on_internal_error: Optional[SerializeFunc] = None,
    on_finished: Optional[SerializeFunc] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Optional[Dict[str, Any]]]:
    """Turn an event into JSON-serializable structure."""
    # Due to https://github.com/python-attrs/attrs/issues/864 it is easier to implement filtration manually
    # Use the explicitly provided serializer for this event and fallback to default one if it is not provided
    serializer = {
        events.Initialized: on_initialized,
        events.BeforeExecution: on_before_execution,
        events.AfterExecution: on_after_execution,
        events.Interrupted: on_interrupted,
        events.InternalError: on_internal_error,
        events.Finished: on_finished,
    }.get(event.__class__)
    if serializer is None:
        serializer = cast(SerializeFunc, SERIALIZER_MAP[event.__class__])
    data = serializer(event)
    if extra is not None:
        # If `extra` is present, then merge it with the serialized data. If serialized data is empty, then replace it
        # with `extra` value
        if data is None:
            data = extra
        else:
            data = merge(data, extra)
    # Externally tagged structure
    return {event.__class__.__name__: data}


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
