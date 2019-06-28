import pytest

from ..utils import string

PARAMETRIZE_STRINGS = pytest.mark.parametrize(
    "parameter",
    (
        string(name="key1"),
        string(name="key2", maxLength=5),
        string(name="key3", minLength=5),
        string(name="key4", pattern="ab{2}"),
        string(name="key5", minLength=3, maxLength=6, pattern="ab{2}"),
        string(name="key6", format="date"),
        string(name="key7", format="date-time"),
    ),
)
