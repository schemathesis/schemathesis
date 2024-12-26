from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Dict, Optional, TypeVar

from ..runner import events

if TYPE_CHECKING:
    from ..stateful import events as stateful_events

S = TypeVar("S", bound=events.EngineEvent)
SerializeFunc = Callable[[S], Optional[Dict[str, Any]]]


def serialize_initialized(event: events.Initialized) -> dict[str, Any] | None:
    return event.asdict()


def serialize_phase_started(event: events.PhaseStarted) -> dict[str, str]:
    return event.asdict()


def serialize_phase_finished(event: events.PhaseFinished) -> dict[str, str]:
    return event.asdict()


def serialize_before_execution(event: events.BeforeExecution) -> dict[str, Any] | None:
    return event.asdict()


def serialize_after_execution(event: events.AfterExecution) -> dict[str, Any] | None:
    return {
        "correlation_id": event.correlation_id,
        "status": event.status,
        "elapsed_time": event.elapsed_time,
        "result": {
            "checks": [check.asdict() for check in event.result.checks],
            "errors": [error.asdict() for error in event.result.errors],
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
    return {"running_time": event.running_time}


def serialize_stateful_event(event: events.StatefulEvent) -> dict[str, Any] | None:
    return _serialize_stateful_event(event.data)


def _serialize_stateful_event(event: stateful_events.StatefulEvent) -> dict[str, Any] | None:
    return {"data": {event.__class__.__name__: event.asdict()}}


def serialize_after_stateful_execution(event: events.AfterStatefulExecution) -> dict[str, Any] | None:
    return {
        "status": event.status,
        "result": event.result.asdict(),
        "elapsed_time": event.elapsed_time,
    }


SERIALIZER_MAP: dict[type[events.EngineEvent], SerializeFunc] = {
    events.Initialized: serialize_initialized,
    events.PhaseStarted: serialize_phase_started,
    events.PhaseFinished: serialize_phase_started,
    events.BeforeExecution: serialize_before_execution,
    events.AfterExecution: serialize_after_execution,
    events.Interrupted: serialize_interrupted,
    events.InternalError: serialize_internal_error,
    events.StatefulEvent: serialize_stateful_event,
    events.AfterStatefulExecution: serialize_after_stateful_execution,
    events.Finished: serialize_finished,
}


def serialize_event(
    event: events.EngineEvent,
) -> dict[str, dict[str, Any] | None]:
    """Turn an event into JSON-serializable structure."""
    serializer = SERIALIZER_MAP[event.__class__]
    # Externally tagged structure
    return {event.__class__.__name__: serializer(event)}
