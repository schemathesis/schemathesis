from contextlib import contextmanager
from typing import Generator

from hypothesis.reporting import with_reporter

IGNORED_PATTERNS = (
    "Falsifying example: ",
    "Falsifying explicit example: ",
    "You can add @seed",
    "Failed to reproduce exception. Expected:",
    "Flaky example!",
    "Traceback (most recent call last):",
    "You can reproduce this example by temporarily",
    "Unreliable test timings",
)


@contextmanager
def capture_hypothesis_output() -> Generator[list[str], None, None]:
    """Capture all output of Hypothesis into a list of strings.

    It allows us to have more granular control over Schemathesis output.

    Usage::

        @given(i=st.integers())
        def test(i):
            assert 0

        with capture_hypothesis_output() as output:
            test()  # hypothesis test
            # output == ["Falsifying example: test(i=0)"]
    """
    output = []

    def get_output(value: str) -> None:
        # Drop messages that could be confusing in the Schemathesis context
        if value.startswith(IGNORED_PATTERNS):
            return
        output.append(value)

    # the following context manager is untyped
    with with_reporter(get_output):  # type: ignore
        yield output
