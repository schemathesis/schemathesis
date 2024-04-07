import pytest
from hypothesis import Phase, given, settings
from pydantic import BaseModel
from starlette.responses import JSONResponse

from schemathesis import fixups, from_asgi, from_dict
from schemathesis.exceptions import CheckFailed, SchemaError
from schemathesis.experimental import OPEN_API_3_1


@pytest.mark.parametrize("with_fixup", (True, False))
def test_works_with_fastapi(fastapi_app, with_fixup):
    OPEN_API_3_1.enable()
    if with_fixup:
        fixups.fast_api.install()

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

    schema = from_asgi("/openapi.json", fastapi_app)

    @given(case=schema["/address/"]["GET"].as_strategy())
    @settings(phases=[Phase.generate], deadline=None)
    def test(case):
        with pytest.raises(CheckFailed) as exc:
            case.call_and_validate()
        assert "Unevaluated properties are not allowed ('department' was unexpected)" in str(exc.value)

    test()


def test_openapi_3_1_schema_validation():
    raw_schema = {
        "openapi": "3.1.0",
        "info": {"title": 42, "version": "0.1.0"},
        "paths": {
            "/users": {
                "get": {
                    "summary": "Root",
                    "operationId": "root_users_get",
                    "responses": {
                        "200": {"description": "Successful Response", "content": {"application/json": {"schema": {}}}}
                    },
                }
            }
        },
    }
    with pytest.raises(SchemaError):
        from_dict(raw_schema, validate_schema=True, force_schema_version="30")
