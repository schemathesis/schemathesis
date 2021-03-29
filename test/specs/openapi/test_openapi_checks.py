import pytest
import requests

from schemathesis import Case
from schemathesis.models import APIOperation
from schemathesis.schemas import BaseSchema
from schemathesis.specs.openapi.checks import (
    content_type_conformance,
    response_headers_conformance,
    response_schema_conformance,
    status_code_conformance,
)


@pytest.mark.parametrize(
    "check",
    (content_type_conformance, response_headers_conformance, response_schema_conformance, status_code_conformance),
)
def test_wrong_schema_type(check):
    schema = BaseSchema({})
    # These checks should not be valid for some generic schema
    case = Case(operation=APIOperation("", "", None, verbose_name="", schema=schema))
    with pytest.raises(TypeError):
        check(requests.Response(), case)
