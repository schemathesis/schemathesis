from enum import Enum

import click
import pytest
from hypothesis import assume, example, given, settings
from hypothesis import strategies as st

from schemathesis.cli import CsvChoice, CsvEnumChoice


class Options(Enum):
    first = 1
    second = 2


@given(value=st.text() | st.lists(st.text()).map(",".join))
@example("")
@settings(deadline=None)
def test_csv_enum_choice(value):
    option = CsvEnumChoice(Options)
    with pytest.raises(click.BadParameter):
        option.convert(value, None, None)


@given(options=st.lists(st.text()), value=st.text() | st.lists(st.text()).map(",".join))
@settings(deadline=None)
def test_csv_choice(options, value):
    assume(all(v not in options for v in value.split(",")))
    option = CsvChoice(options)
    with pytest.raises(click.BadParameter):
        option.convert(value, None, None)
