from __future__ import annotations
from dataclasses import asdict
from typing import Any, Callable, Dict, Optional, TypeVar, cast

from ..models import Response
from ..runner import events
from ..runner.serialization import SerializedCase
from ..internal.transformation import merge_recursively

S = TypeVar("S", bound=events.ExecutionEvent)
SerializeFunc = Callable[[S], Optional[Dict[str, Any]]]


def serialize_initialized(event: events.Initialized) -> dict[str, Any] | None:
    return {
        "operations_count": event.operations_count,
        "location": event.location or "",
        "base_url": event.base_url,
    }


def serialize_before_execution(event: events.BeforeExecution) -> dict[str, Any] | None:
    return {
        "correlation_id": event.correlation_id,
        "verbose_name": event.verbose_name,
        "data_generation_method": event.data_generation_method,
    }


def _serialize_case(case: SerializedCase) -> dict[str, Any]:
    return {
        "verbose_name": case.verbose_name,
        "path_template": case.path_template,
        "path_parameters": stringify_path_parameters(case.path_parameters),
        "query": prepare_query(case.query),
        "cookies": case.cookies,
        "media_type": case.media_type,
    }


def _serialize_response(response: Response) -> dict[str, Any]:
    return {
        "status_code": response.status_code,
        "headers": response.headers,
        "body": response.body,
        "encoding": response.encoding,
        "elapsed": response.elapsed,
    }


def serialize_after_execution(event: events.AfterExecution) -> dict[str, Any] | None:
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
                    "context": asdict(check.context) if check.context is not None else None,  # type: ignore
                    "history": [
                        {"case": _serialize_case(entry.case), "response": _serialize_response(entry.response)}
                        for entry in check.history
                    ],
                }
                for check in event.result.checks
            ],
            "errors": [asdict(error) for error in event.result.errors],
            "skip_reason": event.result.skip_reason,
        },
    }


def serialize_interrupted(_: events.Interrupted) -> dict[str, Any] | None:
    return None


def serialize_internal_error(event: events.InternalError) -> dict[str, Any] | None:
    return {
        "type": event.type.value,
        "subtype": event.subtype.value if event.subtype else event.subtype,
        "title": event.title,
        "message": event.message,
        "extras": event.extras,
        "exception_type": event.exception_type,
        "exception": event.exception,
        "exception_with_traceback": event.exception_with_traceback,
    }


def serialize_finished(event: events.Finished) -> dict[str, Any] | None:
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
    on_initialized: SerializeFunc | None = None,
    on_before_execution: SerializeFunc | None = None,
    on_after_execution: SerializeFunc | None = None,
    on_interrupted: SerializeFunc | None = None,
    on_internal_error: SerializeFunc | None = None,
    on_finished: SerializeFunc | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, dict[str, Any] | None]:
    """Turn an event into JSON-serializable structure."""
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
            data = merge_recursively(data, extra)
    # Externally tagged structure
    return {event.__class__.__name__: data}


def stringify_path_parameters(path_parameters: dict[str, Any] | None) -> dict[str, str]:
    """Cast all path parameter values to strings.

    Path parameter values may be of arbitrary type, but to display them properly they should be casted to strings.
    """
    return {key: str(value) for key, value in (path_parameters or {}).items()}


def prepare_query(query: dict[str, Any] | None) -> dict[str, list[str]]:
    """Convert all query values to list of strings.

    Query parameters may be generated in different shapes, including integers, strings, list of strings, etc.
    It can also be an object, if the schema contains an object, but `style` and `explode` combo is not applicable.
    """

    def to_list_of_strings(value: Any) -> list[str]:
        if isinstance(value, list):
            return list(map(str, value))
        if isinstance(value, str):
            return [value]
        return [str(value)]

    return {key: to_list_of_strings(value) for key, value in (query or {}).items()}
