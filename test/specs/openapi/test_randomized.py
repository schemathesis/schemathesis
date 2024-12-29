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


@pytest.skip("Current implementation does not have enough schema validation", allow_module_level=True)
@given(openapis(version="2.0") | openapis(version="3.0"))
@settings(max_examples=25, phases=[Phase.generate], deadline=None, suppress_health_check=list(HealthCheck))
def test_random_schemas(schema):
    schema = schemathesis.openapi.from_dict(schema)
    for event in schemathesis.runner.from_schema(schema, dry_run=True).execute():
        assert not isinstance(event, events.InternalError), repr(event)
        if isinstance(event, events.AfterExecution):
            errors = [
                error
                for error in event.result.errors
                if all(f"{exc.__module__}.{exc.__name__}" not in str(error) for exc in IGNORED_EXCEPTIONS)
            ]
            if errors:
                error = errors[0]
                raise AssertionError(str(error))
