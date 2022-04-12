from enum import Enum

import click
import pytest
from hypothesis import example, given
from hypothesis import strategies as st

from schemathesis.cli import CsvEnumChoice


class Options(Enum):
    first = 1
    second = 2


@given(value=st.text() | st.lists(st.text()).map(",".join))
@example("")
def test_csv_option(value):
    option = CsvEnumChoice(Options)
    with pytest.raises(click.BadParameter):
        option.convert(value, None, None)
