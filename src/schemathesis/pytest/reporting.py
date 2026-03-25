from __future__ import annotations

import time
from typing import TYPE_CHECKING

from schemathesis.engine.recorder import ScenarioRecorder

if TYPE_CHECKING:
    import requests

    from schemathesis.checks import CheckResult
    from schemathesis.core.transport import Response
    from schemathesis.generation.case import Case
    from schemathesis.hooks import HookContext
    from schemathesis.schemas import BaseSchema


class PytestReportDispatcher:
    """Accumulates ScenarioRecorders for a schema, one per API operation label.

    Registers after_call and after_validate on schema.hooks (not globally).
    Does not own writers - the plugin owns writers and calls pop_recorder() at teardown.
    This separation keeps the dispatcher unchanged when xdist support is added later.
    """

    def __init__(self, schema: BaseSchema) -> None:
        self._schema = schema
        self._recorders: dict[str, ScenarioRecorder] = {}
        self._start_times: dict[str, float] = {}
        # Store bound methods so unregister() removes the same objects.
        # Python bound methods are not cached - accessing self._on_after_call twice
        # gives two different objects, breaking identity comparison in hooks.unregister().
        self._after_call_hook = self._on_after_call
        self._after_validate_hook = self._on_after_validate
        self._after_network_error_hook = self._on_after_network_error
        schema.hooks.register_hook_with_name(self._after_call_hook, "after_call")
        schema.hooks.register_hook_with_name(self._after_validate_hook, "after_validate")
        schema.hooks.register_hook_with_name(self._after_network_error_hook, "after_network_error")

    def _on_after_call(self, context: HookContext, case: Case, response: Response) -> None:
        label = case.operation.label
        self._start_times.setdefault(label, time.monotonic())
        recorder = self._recorders.setdefault(label, ScenarioRecorder(label=label))
        recorder.record_case(parent_id=None, case=case, transition=None, is_transition_applied=False)
        recorder.record_response(case_id=case.id, response=response)

    def _on_after_network_error(self, context: HookContext, case: Case, request: requests.PreparedRequest) -> None:
        label = case.operation.label
        self._start_times.setdefault(label, time.monotonic())
        recorder = self._recorders.setdefault(label, ScenarioRecorder(label=label))
        recorder.record_case(parent_id=None, case=case, transition=None, is_transition_applied=False)
        recorder.record_request(case_id=case.id, request=request)

    def _on_after_validate(
        self, context: HookContext, case: Case, response: Response, check_results: list[CheckResult]
    ) -> None:
        recorder = self._recorders.get(case.operation.label)
        assert recorder is not None, "after_validate fired without a prior after_call for the same operation"
        for result in check_results:
            if result.failure is not None:
                code_sample = case.as_curl_command(
                    headers=dict(response.request.headers),
                    verify=getattr(response, "verify", True),
                )
                recorder.record_check_failure(
                    name=result.name,
                    case_id=case.id,
                    code_sample=code_sample,
                    failure=result.failure,
                )
            else:
                recorder.record_check_success(name=result.name, case_id=case.id)

    def pop_recorder(self, label: str) -> tuple[ScenarioRecorder, float] | None:
        """Remove and return the recorder and elapsed seconds for this label.

        Removing the recorder lets the GC reclaim the Case objects it holds.
        Called by the plugin at test teardown after writing to all report writers.
        """
        recorder = self._recorders.pop(label, None)
        if recorder is None:
            return None
        elapsed = time.monotonic() - self._start_times.pop(label, time.monotonic())
        return recorder, elapsed

    def unregister(self) -> None:
        """Remove all hooks from schema.hooks. Called at pytest_sessionfinish."""
        self._schema.hooks.unregister(self._after_call_hook)
        self._schema.hooks.unregister(self._after_validate_hook)
        self._schema.hooks.unregister(self._after_network_error_hook)
