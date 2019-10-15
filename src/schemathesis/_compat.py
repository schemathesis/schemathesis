from contextlib import contextmanager
from typing import Generator
from warnings import catch_warnings, simplefilter


@contextmanager
def handle_warnings() -> Generator[None, None, None]:
    try:
        from hypothesis.errors import NonInteractiveExampleWarning  # pylint: disable=import-outside-toplevel

        with catch_warnings():
            simplefilter("ignore", NonInteractiveExampleWarning)
            yield
    except ImportError:
        yield
