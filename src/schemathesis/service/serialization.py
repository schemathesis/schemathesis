from __future__ import annotations

from dataclasses import asdict
from typing import Any, Callable, Dict, Optional, TypeVar, cast

from ..internal.transformation import merge_recursively
from ..runner import events
from ..runner.serialization import _serialize_check
from ..stateful import events as stateful_events

S = TypeVar("S", bound=events.ExecutionEvent)
SerializeFunc = Callable[[S], Optional[Dict[str, Any]]]


def serialize_initialized(event: events.Initialized) -> dict[str, Any] | None:
    return {
        "operations_count": event.operations_count,
        "location": event.location or "",
        "base_url": event.base_url,
    }


def serialize_before_probing(_: events.BeforeProbing) -> None:
    return None


def serialize_after_probing(event: events.AfterProbing) -> dict[str, Any] | None:
    probes = event.probes or []
    return {"probes": [probe.serialize() for probe in probes]}


def serialize_before_analysis(_: events.BeforeAnalysis) -> None:
    return None


def serialize_after_analysis(event: events.AfterAnalysis) -> dict[str, Any] | None:
    return event._serialize()


def serialize_before_execution(event: events.BeforeExecution) -> dict[str, Any] | None:
    return {
        "correlation_id": event.correlation_id,
        "verbose_name": event.verbose_name,
        "data_generation_method": event.data_generation_method,
    }


def serialize_after_execution(event: events.AfterExecution) -> dict[str, Any] | None:
    return {
        "correlation_id": event.correlation_id,
        "verbose_name": event.verbose_name,
        "status": event.status,
        "elapsed_time": event.elapsed_time,
        "data_generation_method": event.data_generation_method,
        "result": {
            "checks": [_serialize_check(check) for check in event.result.checks],
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


def serialize_stateful_event(event: events.StatefulEvent) -> dict[str, Any] | None:
    return _serialize_stateful_event(event.data)


def _serialize_stateful_event(event: stateful_events.StatefulEvent) -> dict[str, Any] | None:
    return {"data": {event.__class__.__name__: event.asdict()}}


def serialize_after_stateful_execution(event: events.AfterStatefulExecution) -> dict[str, Any] | None:
    return {
        "status": event.status,
        "data_generation_method": event.data_generation_method,
        "result": asdict(event.result),
        "elapsed_time": event.elapsed_time,
    }


SERIALIZER_MAP = {
    events.Initialized: serialize_initialized,
    events.BeforeProbing: serialize_before_probing,
    events.AfterProbing: serialize_after_probing,
    events.BeforeAnalysis: serialize_before_analysis,
    events.AfterAnalysis: serialize_after_analysis,
    events.BeforeExecution: serialize_before_execution,
    events.AfterExecution: serialize_after_execution,
    events.Interrupted: serialize_interrupted,
    events.InternalError: serialize_internal_error,
    events.StatefulEvent: serialize_stateful_event,
    events.AfterStatefulExecution: serialize_after_stateful_execution,
    events.Finished: serialize_finished,
}


def serialize_event(
    event: events.ExecutionEvent,
    *,
    on_initialized: SerializeFunc | None = None,
    on_before_probing: SerializeFunc | None = None,
    on_after_probing: SerializeFunc | None = None,
    on_before_analysis: SerializeFunc | None = None,
    on_after_analysis: SerializeFunc | None = None,
    on_before_execution: SerializeFunc | None = None,
    on_after_execution: SerializeFunc | None = None,
    on_interrupted: SerializeFunc | None = None,
    on_internal_error: SerializeFunc | None = None,
    on_stateful_event: SerializeFunc | None = None,
    on_after_stateful_execution: SerializeFunc | None = None,
    on_finished: SerializeFunc | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, dict[str, Any] | None]:
    """Turn an event into JSON-serializable structure."""
    # Use the explicitly provided serializer for this event and fallback to default one if it is not provided
    serializer = {
        events.Initialized: on_initialized,
        events.BeforeProbing: on_before_probing,
        events.AfterProbing: on_after_probing,
        events.BeforeAnalysis: on_before_analysis,
        events.AfterAnalysis: on_after_analysis,
        events.BeforeExecution: on_before_execution,
        events.AfterExecution: on_after_execution,
        events.Interrupted: on_interrupted,
        events.InternalError: on_internal_error,
        events.StatefulEvent: on_stateful_event,
        events.AfterStatefulExecution: on_after_stateful_execution,
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
