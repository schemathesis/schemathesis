from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from schemathesis.cli.commands.run.context import ExecutionContext
    from schemathesis.engine import events


class EventHandler:
    def __init__(self, *args: Any, **params: Any) -> None: ...

    def handle_event(self, ctx: ExecutionContext, event: events.EngineEvent) -> None:
        raise NotImplementedError

    def start(self, ctx: ExecutionContext) -> None: ...

    def shutdown(self, ctx: ExecutionContext) -> None: ...
