from typing import Any, Dict, List, Optional

import attr

from ..runner import events
from ..runner.serialization import SerializedCase, SerializedTestResult, deduplicate_failures


def serialize_event(event: events.ExecutionEvent) -> Dict[str, Any]:
    """Turn an event into JSON-serializable structure."""
    if isinstance(event, events.AfterExecution):
        # It may contain a lot of data, most of which is not needed on the Schemathesis.io side.
        return event.asdict(
            filter=attr.filters.exclude(
                attr.fields(SerializedTestResult).interactions, attr.fields(SerializedTestResult).logs
            ),
            value_serializer=after_execution_serializer,
        )
    return event.asdict()


def after_execution_serializer(instance: Any, attribute: attr.Attribute, value: Any) -> Any:
    if isinstance(instance, SerializedTestResult) and attribute is not None and attribute.name == "checks":
        # Checks data takes the most, but we are interested only in deduplicated failures
        return deduplicate_failures(value)
    if isinstance(instance, SerializedCase) and attribute is not None and attribute.name == "path_parameters":
        return stringify_path_parameters(value)
    if isinstance(instance, SerializedCase) and attribute is not None and attribute.name == "query":
        return prepare_query(value)
    return value


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
