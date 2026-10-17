from __future__ import annotations

from typing import TYPE_CHECKING, Any

from schemathesis.cli.commands import Group, run, schemathesis
from schemathesis.cli.events import LoadingFinished, LoadingStarted
from schemathesis.cli.ext.groups import GROUPS, OptionGroup
from schemathesis.cli.ext.handlers import handler

if TYPE_CHECKING:
    from schemathesis.cli.commands.run.context import ExecutionContext
    from schemathesis.cli.commands.run.handlers import EventHandler

__all__ = [
    "schemathesis",
    "run",
    "EventHandler",
    "ExecutionContext",
    "LoadingStarted",
    "LoadingFinished",
    "add_group",
    "handler",
]


def add_group(name: str, *, index: int | None = None) -> Group:
    """Add a custom options group to `st run` and `st fuzz`."""
    if index is not None:
        GROUPS[name] = OptionGroup(name=name, order=index)
    else:
        GROUPS[name] = OptionGroup(name=name)
    return Group(name)


# Only needed by extensions, `st --help` should not pay for the run machinery.
def __getattr__(name: str) -> Any:
    if name == "ExecutionContext":
        from schemathesis.cli.commands.run.context import ExecutionContext

        return ExecutionContext
    if name == "EventHandler":
        from schemathesis.cli.commands.run.handlers import EventHandler

        return EventHandler
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
