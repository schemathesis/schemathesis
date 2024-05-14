from hypothesis import given
from hypothesis import strategies as st, settings, Phase

import pytest
import schemathesis
import schemathesis.runner
import sys
from schemathesis.runner import events

if sys.version_info < (3, 10):
    pytest.skip("Required Python 3.10+", allow_module_level=True)

from .factory import asdict
from .v2 import Swagger


@given(st.from_type(Swagger).map(asdict))
@settings(max_examples=25, phases=[Phase.generate], deadline=None)
def test_swagger(schema):
    schema = schemathesis.from_dict(schema, validate_schema=True)
    schema.validate_schema = False
    for event in schemathesis.runner.from_schema(schema, dry_run=True).execute():
        assert not isinstance(event, events.InternalError), repr(event)
        if isinstance(event, events.AfterExecution):
            if event.result.errors:
                error = event.result.errors[0]
                raise AssertionError(error.exception_with_traceback)
