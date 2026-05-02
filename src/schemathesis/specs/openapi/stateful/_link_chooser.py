from __future__ import annotations

from typing import TYPE_CHECKING, Any

from schemathesis.core.result import Ok
from schemathesis.core.transforms import Unresolvable
from schemathesis.core.transport import status_code_matches
from schemathesis.generation.stateful.state_machine import StepOutput
from schemathesis.specs.openapi.stateful.links import OpenApiLink

if TYPE_CHECKING:
    from schemathesis.core.transport import Response
    from schemathesis.generation.case import Case
    from schemathesis.schemas import APIOperation


def collect_link_candidates(
    *,
    operation: APIOperation,
    case: Case,
    response: Response,
    operations_by_label: dict[str, APIOperation],
    excluded_labels: set[str],
) -> list[tuple[APIOperation, dict[str, Any]]]:
    """Return resolvable (target, overrides) candidates for Links from this response."""
    candidates: list[tuple[APIOperation, dict[str, Any]]] = []
    output = StepOutput(response=response, case=case)
    for status_code, response_def in operation.responses.items():
        if not status_code_matches(status_code, response.status_code):
            continue
        for name, link_def in response_def.iter_links():
            try:
                link = OpenApiLink(name, status_code, link_def, operation)
            except Exception:
                continue
            target_label = link.target.label
            if target_label not in operations_by_label or target_label in excluded_labels:
                continue
            transition = link.extract(output)
            overrides: dict[str, Any] = {}
            failed = False
            for container_name, container_params in transition.parameters.items():
                container_overrides: dict[str, Any] = {}
                for param_name, param in container_params.items():
                    resolved = _resolve(param.value)
                    if resolved is _FAILED:
                        failed = True
                        break
                    container_overrides[param_name] = resolved
                if failed:
                    break
                if container_overrides:
                    overrides[container_name] = container_overrides
            if failed:
                continue
            if transition.request_body is not None:
                resolved_body = _resolve(transition.request_body.value)
                if resolved_body is _FAILED:
                    continue
                overrides["body"] = resolved_body
            candidates.append((operations_by_label[target_label], overrides))
    return candidates


_FAILED: Any = object()


def _resolve(value: Any) -> Any:
    """Unwrap a `Result` Ok value, returning `_FAILED` if Err or `Unresolvable`."""
    if not isinstance(value, Ok):
        return _FAILED
    inner = value.ok()
    if isinstance(inner, Unresolvable):
        return _FAILED
    return inner
