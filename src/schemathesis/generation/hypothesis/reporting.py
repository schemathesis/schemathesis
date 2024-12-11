from contextlib import contextmanager
from typing import Generator

from hypothesis.reporting import with_reporter


def ignore(_: str) -> None:
    pass


@contextmanager
def ignore_hypothesis_output() -> Generator:
    with with_reporter(ignore):  # type: ignore
        yield
