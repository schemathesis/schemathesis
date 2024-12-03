from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from schemathesis.core.errors import format_exception

if TYPE_CHECKING:
    from . import ci


class Event:
    """Signalling events coming from the Schemathesis.io worker.

    The purpose is to communicate with the thread that writes to stdout.
    """

    @property
    def status(self) -> str:
        return self.__class__.__name__.upper()


@dataclass
class Metadata(Event):
    """Meta-information about the report."""

    size: int
    ci_environment: ci.Environment | None


@dataclass
class Completed(Event):
    """Report uploaded successfully."""

    message: str
    next_url: str


@dataclass
class Error(Event):
    """Internal error inside the Schemathesis.io handler."""

    exception: Exception

    def get_message(self, with_traceback: bool = False) -> str:
        return format_exception(self.exception, with_traceback=with_traceback)


@dataclass
class Failed(Event):
    """A client-side error which should be displayed to the user."""

    detail: str


@dataclass
class Timeout(Event):
    """The handler did not finish its work in time.

    This event is not created in the handler itself, but rather in the main thread code to uniform the processing.
    """
