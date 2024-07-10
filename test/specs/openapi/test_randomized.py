import sys

import pytest
from hypothesis import Phase, given, settings

import schemathesis
import schemathesis.runner
from schemathesis.runner import events

if sys.version_info < (3, 10):
    pytest.skip("Required Python 3.10+", allow_module_level=True)

from hypothesis_openapi import openapis


@given(openapis(version="2.0") | openapis(version="3.0"))
@settings(max_examples=25, phases=[Phase.generate], deadline=None)
def test_random_schemas(schema):
    schema = schemathesis.from_dict(schema, validate_schema=True)
    # Disable schema validation to allow more flexible behavior at runtime
    schema.validate_schema = False
    for event in schemathesis.runner.from_schema(schema, dry_run=True).execute():
        assert not isinstance(event, events.InternalError), repr(event)
        if isinstance(event, events.AfterExecution):
            if event.result.errors:
                error = event.result.errors[0]
                raise AssertionError(error.exception_with_traceback)
