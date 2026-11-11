from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from schemathesis.core.marks import Mark

if TYPE_CHECKING:
    import hypothesis

    from schemathesis.generation.stateful.state_machine import APIStateMachine
    from schemathesis.schemas import BaseSchema

__all__ = [
    "APIStateMachine",
]


def __getattr__(name: str) -> type[APIStateMachine]:
    if name == "APIStateMachine":
        from schemathesis.generation.stateful.state_machine import APIStateMachine

        return APIStateMachine
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


STATEFUL_TESTS_LABEL = "Stateful tests"

StatefulSchemaMark: Mark[BaseSchema] = Mark(attr_name="stateful_schema")
StatefulCallbackMark: Mark[Callable | None] = Mark(attr_name="stateful_callback")


def run_state_machine_as_test(
    state_machine_factory: type[APIStateMachine], *, settings: hypothesis.settings | None = None
) -> None:
    """Run a state machine as a test.

    It automatically adds the `_min_steps` argument if ``Hypothesis`` is recent enough.
    """
    from hypothesis.stateful import run_state_machine_as_test as _run_state_machine_as_test

    from schemathesis.engine.errors import clear_hypothesis_notes
    from schemathesis.generation.hypothesis.reporting import ignore_hypothesis_output

    __tracebackhide__ = True

    try:
        with ignore_hypothesis_output():
            return _run_state_machine_as_test(state_machine_factory, settings=settings, _min_steps=2)
    except Exception as exc:
        clear_hypothesis_notes(exc)
        raise
