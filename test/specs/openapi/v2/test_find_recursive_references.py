from schemathesis.specs.openapi._jsonschema import find_recursive_references


DEFINITIONS = {
    "A": {"type": "object", "properties": {"id": {"type": "string"}, "ref": {"$ref": "B"}}},
    "B": {"type": "object", "properties": {"name": {"type": "string"}, "ref": {"$ref": "A"}}},
    "C": {
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "ref": {
                "anyOf": [
                    {"$ref": "D"},
                    {"$ref": "E"},
                    True,
                ]
            },
        },
    },
    "D": {"type": "object", "properties": {"name": {"type": "string"}, "ref": {"$ref": "C"}}},
    "E": {"type": "object", "properties": {"email": {"type": "string"}, "ref": {"$ref": "C"}}},
    "F": {"type": "object", "properties": {"email": {"type": "string"}, "ref": {"$ref": "G"}}},
    "G": {"type": "object"},
}


def test_find_recursive_references_simple():
    recursive_refs = find_recursive_references("A", DEFINITIONS, {}, 0)
    assert recursive_refs == {"A", "B"}


def test_find_recursive_references_anyof():
    recursive_refs = find_recursive_references("C", DEFINITIONS, {}, 0)
    assert recursive_refs == {"C", "D", "E"}


def test_find_recursive_references_with_cache():
    cache = {"A": {"A", "B"}, "D": {"C", "D"}}
    recursive_refs = find_recursive_references("C", DEFINITIONS, cache, 0)
    assert recursive_refs == {"C", "D", "E"}


def test_find_recursive_references_with_cache_for_non_recursive():
    cache = {"G": set()}
    recursive_refs = find_recursive_references("F", DEFINITIONS, cache, 0)
    assert recursive_refs == set()


def test_find_recursive_references_with_partial_cache():
    cache = {"A": {"A", "B"}}
    recursive_refs = find_recursive_references("C", DEFINITIONS, cache, 0)
    assert recursive_refs == {"C", "D", "E"}


def test_find_recursive_references_single_item_in_storage():
    recursive_refs = find_recursive_references("A", {"A": {}}, {}, 0)
    assert recursive_refs == set()
