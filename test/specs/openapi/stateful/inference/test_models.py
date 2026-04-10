from __future__ import annotations

from schemathesis.core.jsonschema.resolver import make_root_resolver
from schemathesis.specs.openapi.stateful.dependencies.models import extract_nested_fk_fields


def test_extract_nested_fk_fields_with_direct_resolver():
    root_schema = {
        "components": {
            "schemas": {
                "Shipping": {
                    "type": "object",
                    "properties": {
                        "location": {"$ref": "#/components/schemas/Location"},
                    },
                },
                "Location": {
                    "type": "object",
                    "properties": {
                        "warehouse_id": {"type": "string"},
                        "address": {"type": "string"},
                    },
                },
            }
        }
    }
    schema = {
        "type": "object",
        "properties": {
            "shipping": {"$ref": "#/components/schemas/Shipping"},
        },
    }

    nested_fk_fields = extract_nested_fk_fields(schema, make_root_resolver(root_schema))

    assert len(nested_fk_fields) == 1
    field = nested_fk_fields[0]
    assert field.pointer == "/shipping/location/warehouse_id"
    assert field.field_name == "warehouse_id"
    assert field.target_resource == "Warehouse"
    assert field.target_field == "id"
    assert field.is_array is False
