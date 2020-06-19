import pytest
import requests

from schemathesis import Case
from schemathesis.models import Endpoint
from schemathesis.schemas import BaseSchema
from schemathesis.specs.openapi.checks import content_type_conformance, response_schema_conformance


@pytest.mark.parametrize("check", (content_type_conformance, response_schema_conformance))
def test_wrong_schema_type(check):
    schema = BaseSchema({})
    # These checks should not be valid for some generic schema
    case = Case(endpoint=Endpoint("", "", None, schema=schema))
    with pytest.raises(TypeError):
        check(requests.Response(), case)
