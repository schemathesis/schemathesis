import sys

import hypothesis.errors
import pytest
from hypothesis import HealthCheck, Phase, given, settings

import schemathesis
import schemathesis.runner
from schemathesis.runner import events

if sys.version_info < (3, 10):
    pytest.skip("Required Python 3.10+", allow_module_level=True)

from hypothesis_openapi import openapis

IGNORED_EXCEPTIONS = [hypothesis.errors.Unsatisfiable]


@given(openapis(version="2.0") | openapis(version="3.0"))
@settings(max_examples=25, phases=[Phase.generate], deadline=None, suppress_health_check=list(HealthCheck))
def test_random_schemas(schema):
    schema = schemathesis.from_dict(schema, validate_schema=True)
    # Disable schema validation to allow more flexible behavior at runtime
    schema.validate_schema = False
    for event in schemathesis.runner.from_schema(schema, dry_run=True).execute():
        assert not isinstance(event, events.InternalError), repr(event)
        if isinstance(event, events.AfterExecution):
            errors = [
                error
                for error in event.result.errors
                if all(
                    f"{exc.__module__}.{exc.__name__}" not in error.exception_with_traceback
                    for exc in IGNORED_EXCEPTIONS
                )
            ]
            if errors:
                error = errors[0]
                raise AssertionError(error.exception_with_traceback)
