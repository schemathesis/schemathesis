from __future__ import annotations

import enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import hypothesis

    from .state_machine import APIStateMachine


@enum.unique
class Stateful(enum.Enum):
    none = 1
    links = 2


def run_state_machine_as_test(
    state_machine_factory: type[APIStateMachine], *, settings: hypothesis.settings | None = None
) -> None:
    """Run a state machine as a test.

    It automatically adds the `_min_steps` argument if ``Hypothesis`` is recent enough.
    """
    from hypothesis.stateful import run_state_machine_as_test as _run_state_machine_as_test

    return _run_state_machine_as_test(state_machine_factory, settings=settings, _min_steps=2)
