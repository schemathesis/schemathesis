import pytest

import schemathesis
from schemathesis.specs.openapi.declared_values import collect


def _schema(response_schema):
    return schemathesis.openapi.from_dict(
        {
            "openapi": "3.0.0",
            "info": {"title": "Test", "version": "1.0"},
            "paths": {
                "/orders": {
                    "get": {
                        "responses": {
                            "200": {
                                "description": "OK",
                                "content": {"application/json": {"schema": response_schema}},
                            }
                        }
                    }
                }
            },
        }
    )


def _declared(response_schema):
    schema = _schema(response_schema)
    return collect(schema["/orders"]["GET"])


def test_a_top_level_enum_is_collected():
    declared = _declared({"type": "object", "properties": {"status": {"enum": ["active", "archived"]}}})

    assert declared == {"status": frozenset({"active", "archived"})}


def test_one_level_of_nesting_is_collected():
    declared = _declared(
        {
            "type": "object",
            "properties": {"data": {"type": "object", "properties": {"state": {"enum": ["new", "done"]}}}},
        }
    )

    assert declared == {"data.state": frozenset({"new", "done"})}


def test_deeper_nesting_is_out_of_reach():
    # The alphabet produces no label for `a.b.c`, so a value declared there could never be matched.
    declared = _declared(
        {
            "type": "object",
            "properties": {
                "a": {"type": "object", "properties": {"b": {"type": "object", "properties": {"c": {"enum": ["x"]}}}}}
            },
        }
    )

    assert declared == {}


def test_arrays_are_out_of_reach():
    declared = _declared({"type": "array", "items": {"type": "object", "properties": {"state": {"enum": ["x"]}}}})

    assert declared == {}


@pytest.mark.parametrize("keyword", ["allOf", "anyOf", "oneOf"])
def test_a_field_described_by_several_branches_declares_all_of_them(keyword):
    declared = _declared(
        {
            "type": "object",
            "properties": {"status": {keyword: [{"enum": ["active"]}, {"enum": ["archived"]}]}},
        }
    )

    assert declared == {"status": frozenset({"active", "archived"})}


def test_a_body_described_by_several_branches_is_walked_through():
    declared = _declared(
        {
            "allOf": [
                {"type": "object", "properties": {"status": {"enum": ["active"]}}},
                {"type": "object", "properties": {"kind": {"enum": ["retail"]}}},
            ]
        }
    )

    assert declared == {"status": frozenset({"active"}), "kind": frozenset({"retail"})}


def test_values_are_named_the_way_the_alphabet_names_them():
    declared = _declared({"type": "object", "properties": {"flag": {"enum": [True, False, None, 3]}}})

    assert declared == {"flag": frozenset({"true", "false", "null", "3"})}


def test_values_too_long_to_become_a_label_are_left_out():
    # The alphabet ignores values over its length limit, so a declared one could never be matched.
    declared = _declared({"type": "object", "properties": {"note": {"enum": ["short", "x" * 100]}}})

    assert declared == {"note": frozenset({"short"})}


def test_an_operation_that_declares_nothing():
    assert _declared({"type": "object", "properties": {"name": {"type": "string"}}}) == {}


def test_a_self_referential_schema_terminates():
    schema = schemathesis.openapi.from_dict(
        {
            "openapi": "3.0.0",
            "info": {"title": "Test", "version": "1.0"},
            "paths": {
                "/orders": {
                    "get": {
                        "responses": {
                            "200": {
                                "description": "OK",
                                "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Node"}}},
                            }
                        }
                    }
                }
            },
            "components": {
                "schemas": {
                    "Node": {
                        "type": "object",
                        "properties": {
                            "kind": {"enum": ["leaf", "branch"]},
                            "child": {"$ref": "#/components/schemas/Node"},
                        },
                    }
                }
            },
        }
    )

    # `child.kind` is one level in, so the alphabet labels it too; anything deeper is out of reach.
    assert collect(schema["/orders"]["GET"]) == {
        "kind": frozenset({"leaf", "branch"}),
        "child.kind": frozenset({"leaf", "branch"}),
    }
