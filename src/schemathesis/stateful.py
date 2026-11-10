from __future__ import annotations

from typing import TYPE_CHECKING

import schemathesis.generation.stateful

if TYPE_CHECKING:
    from schemathesis.generation.stateful.state_machine import APIStateMachine

__all__ = [
    "APIStateMachine",
]


def __getattr__(name: str) -> type[APIStateMachine]:
    if name == "APIStateMachine":
        return schemathesis.generation.stateful.APIStateMachine
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
