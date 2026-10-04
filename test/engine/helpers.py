import re

import hypothesis
from hypothesis import strategies as st

from schemathesis.core.jsonschema import make_validator_for
from schemathesis.engine.run import PhaseName
from test.utils import EventStream


def examples_only(schema) -> EventStream:
    return EventStream(schema, phases=[PhaseName.EXAMPLES]).execute()


def raise_unsatisfiable():
    @hypothesis.given(st.integers().filter(lambda x: False))
    @hypothesis.settings(max_examples=1, suppress_health_check=list(hypothesis.HealthCheck))
    def t(_):
        pass

    t()


def raise_invalid_argument():
    hypothesis.settings(max_examples=-1)


def raise_regex_validation_error():
    make_validator_for({"type": "string", "pattern": "[unclosed"})


def raise_non_regex_validation_error():
    make_validator_for({"type": 12345})


def raise_yaml_pattern_as_float_typeerror():
    re.compile(12345)


def raise_keyboard_interrupt():
    raise KeyboardInterrupt
