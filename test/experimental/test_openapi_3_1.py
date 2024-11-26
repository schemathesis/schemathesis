import pytest
from hypothesis import Phase, given, settings
from pydantic import BaseModel
from starlette.responses import JSONResponse

import schemathesis
from schemathesis.core.failures import FailureGroup


def test_works_with_fastapi(fastapi_app):
    json_schema = {
        "type": "object",
        "properties": {
            "street_address": {"type": "string"},
            "city": {"type": "string"},
            "state": {"type": "string"},
            "type": {"enum": ["residential", "business"]},
        },
        "required": ["street_address", "city", "state", "type"],
        "if": {"type": "object", "properties": {"type": {"const": "business"}}, "required": ["type"]},
        "then": {"properties": {"department": {"type": "string"}}},
        "unevaluatedProperties": False,
    }

    class Address(BaseModel):
        street_address: str
        city: str
        state: str
        type: str
        department: str

        @classmethod
        def __get_pydantic_json_schema__(cls, core_schema, handler):
            return json_schema

    @fastapi_app.get("/address/")
    def address() -> Address:
        return JSONResponse(
            # `then` schema properties only count as "evaluated" properties if the "type" of address is "business"
            # Hence this response should not validate against Draft 2020-12
            content={
                "street_address": "1600 Pennsylvania Avenue NW",
                "city": "Washington",
                "state": "DC",
                "type": "residential",
                "department": "HR",
            }
        )

    schema = schemathesis.openapi.from_asgi("/openapi.json", fastapi_app)

    @given(case=schema["/address/"]["GET"].as_strategy())
    @settings(phases=[Phase.generate], deadline=None)
    def test(case):
        with pytest.raises(FailureGroup) as exc:
            case.call_and_validate()
        assert "Unevaluated properties are not allowed ('department' was unexpected)" in str(exc.value.exceptions[0])

    test()
