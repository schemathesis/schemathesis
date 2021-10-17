import attr

from ..utils import format_exception


class Event:
    """Signalling events coming from the Schemathesis.io worker.

    The purpose is to communicate with the thread that writes to stdout.
    """

    @property
    def name(self) -> str:
        return self.__class__.__name__.upper()


@attr.s(slots=True)
class Completed(Event):
    """The handler finished successfully."""

    short_url: str = attr.ib()


@attr.s(slots=True)
class Error(Event):
    """Internal error inside the Schemathesis.io handler."""

    exception: Exception = attr.ib()

    def get_message(self, include_traceback: bool = False) -> str:
        return format_exception(self.exception, include_traceback=include_traceback)


@attr.s(slots=True)
class Timeout(Event):
    """The handler did not finish its work in time.

    This event is not created in the handler itself, but rather in the main thread code to uniform the processing.
    """
