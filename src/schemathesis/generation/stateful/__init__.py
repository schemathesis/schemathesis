from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import hypothesis

    from schemathesis.generation.stateful.state_machine import APIStateMachine

__all__ = [
    "APIStateMachine",
]


def __getattr__(name: str) -> type[APIStateMachine]:
    if name == "APIStateMachine":
        from schemathesis.generation.stateful.state_machine import APIStateMachine

        return APIStateMachine
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


STATEFUL_TESTS_LABEL = "Stateful tests"


def run_state_machine_as_test(
    state_machine_factory: type[APIStateMachine], *, settings: hypothesis.settings | None = None
) -> None:
    """Run a state machine as a test.

    It automatically adds the `_min_steps` argument if ``Hypothesis`` is recent enough.
    """
    from hypothesis.stateful import run_state_machine_as_test as _run_state_machine_as_test

    __tracebackhide__ = True

    return _run_state_machine_as_test(state_machine_factory, settings=settings, _min_steps=2)
