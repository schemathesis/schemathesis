from __future__ import annotations

import pytest


@pytest.fixture
def open_api_3_schema_with_yaml_payload(ctx):
    return ctx.openapi.load_schema(
        {
            "/yaml": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "text/yaml": {
                                "schema": {"type": "array", "items": {"enum": [42]}, "minItems": 1, "maxItems": 1}
                            }
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                },
            },
        }
    )


@pytest.fixture
def openapi_3_schema_with_xml(ctx):
    id_schema = {"type": "integer", "enum": [42]}

    def operation(schema: dict):
        return {
            "post": {
                "requestBody": {"content": {"application/xml": {"schema": schema}}, "required": True},
                "responses": {"200": {"description": "OK"}},
            }
        }

    def make_object(id_extra=None, **kwargs):
        return {
            "type": "object",
            "properties": {"id": {**id_schema, **(id_extra or {})}},
            "required": ["id"],
            "additionalProperties": False,
            **kwargs,
        }

    def make_array(items, **kwargs):
        return {"type": "array", "items": items, "minItems": 2, "maxItems": 2, **kwargs}

    no_xml_object = make_object()
    renamed_property_xml_object = make_object(id_extra={"xml": {"name": "renamed-id"}})
    property_as_attribute = make_object(id_extra={"xml": {"attribute": True}})

    simple_array = make_array(items=id_schema)
    wrapped_array = make_array(items=id_schema, xml={"wrapped": True})
    array_with_renaming = make_array(
        items={**id_schema, "xml": {"name": "item"}}, xml={"wrapped": True, "name": "items-array"}
    )
    object_in_array = make_array(
        items=make_object(id_extra={"xml": {"name": "item-id"}}, xml={"name": "item"}),
        xml={"wrapped": True, "name": "items"},
    )
    array_in_object = {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {**id_schema, "xml": {"name": "id"}},
                "minItems": 2,
                "maxItems": 2,
                "xml": {"wrapped": True, "name": "items-array"},
            },
        },
        "required": ["items"],
        "additionalProperties": False,
        "xml": {"name": "items-object"},
    }

    prefixed_object = make_object(xml={"prefix": "smp"})
    prefixed_array = make_array(items=id_schema, xml={"prefix": "smp", "namespace": "http://example.com/schema"})
    prefixed_attribute = make_object(
        id_extra={"xml": {"attribute": True, "prefix": "smp", "namespace": "http://example.com/schema"}}
    )
    namespaced_object = make_object(xml={"namespace": "http://example.com/schema"})
    namespaced_array = make_array(items=id_schema, xml={"namespace": "http://example.com/schema"})
    namespaced_wrapped_array = make_array(
        items=id_schema, xml={"namespace": "http://example.com/schema", "wrapped": True}
    )
    namespaced_prefixed_object = make_object(xml={"namespace": "http://example.com/schema", "prefix": "smp"})
    namespaced_prefixed_array = make_array(
        items=id_schema, xml={"namespace": "http://example.com/schema", "prefix": "smp"}
    )
    namespaced_prefixed_wrapped_array = make_array(
        items=id_schema, xml={"namespace": "http://example.com/schema", "prefix": "smp", "wrapped": True}
    )

    return ctx.openapi.load_schema(
        {
            "/root-name": operation(make_object()),
            "/auto-name": operation({"$ref": "#/components/schemas/AutoName"}),
            "/explicit-name": operation({"$ref": "#/components/schemas/ExplicitName"}),
            "/renamed-property": operation({"$ref": "#/components/schemas/RenamedProperty"}),
            "/property-attribute": operation({"$ref": "#/components/schemas/PropertyAsAttribute"}),
            "/simple-array": operation({"$ref": "#/components/schemas/SimpleArray"}),
            "/wrapped-array": operation({"$ref": "#/components/schemas/WrappedArray"}),
            "/array-with-renaming": operation({"$ref": "#/components/schemas/ArrayWithRenaming"}),
            "/object-in-array": operation({"$ref": "#/components/schemas/ObjectInArray"}),
            "/array-in-object": operation({"$ref": "#/components/schemas/ArrayInObject"}),
            "/prefixed-object": operation({"$ref": "#/components/schemas/PrefixedObject"}),
            "/prefixed-array": operation({"$ref": "#/components/schemas/PrefixedArray"}),
            "/prefixed-attribute": operation({"$ref": "#/components/schemas/PrefixedAttribute"}),
            "/namespaced-object": operation({"$ref": "#/components/schemas/NamespacedObject"}),
            "/namespaced-array": operation({"$ref": "#/components/schemas/NamespacedArray"}),
            "/namespaced-wrapped-array": operation({"$ref": "#/components/schemas/NamespacedWrappedArray"}),
            "/namespaced-prefixed-object": operation({"$ref": "#/components/schemas/NamespacedPrefixedObject"}),
            "/namespaced-prefixed-array": operation({"$ref": "#/components/schemas/NamespacedPrefixedArray"}),
            "/namespaced-prefixed-wrapped-array": operation(
                {"$ref": "#/components/schemas/NamespacedPrefixedWrappedArray"}
            ),
        },
        components={
            "schemas": {
                "AutoName": no_xml_object,
                "ExplicitName": {**no_xml_object, "xml": {"name": "CustomName"}},
                "RenamedProperty": renamed_property_xml_object,
                "PropertyAsAttribute": property_as_attribute,
                "SimpleArray": simple_array,
                "WrappedArray": wrapped_array,
                "ArrayWithRenaming": array_with_renaming,
                "ObjectInArray": object_in_array,
                "ArrayInObject": array_in_object,
                "PrefixedObject": prefixed_object,
                "PrefixedArray": prefixed_array,
                "PrefixedAttribute": prefixed_attribute,
                "NamespacedObject": namespaced_object,
                "NamespacedArray": namespaced_array,
                "NamespacedWrappedArray": namespaced_wrapped_array,
                "NamespacedPrefixedObject": namespaced_prefixed_object,
                "NamespacedPrefixedArray": namespaced_prefixed_array,
                "NamespacedPrefixedWrappedArray": namespaced_prefixed_wrapped_array,
            }
        },
    )
