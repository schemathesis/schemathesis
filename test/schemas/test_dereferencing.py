import pytest

from .utils import get_schema


@pytest.fixture()
def petstore():
    return get_schema("petstore_v2.yaml")


@pytest.mark.parametrize(
    "ref, expected",
    (
        (
            {"$ref": "#/definitions/Category"},
            {
                "properties": {"id": {"format": "int64", "type": "integer"}, "name": {"type": "string"}},
                "type": "object",
                "xml": {"name": "Category"},
            },
        ),
        (
            {"$ref": "#/definitions/Pet"},
            {
                "properties": {
                    "category": {
                        "properties": {"id": {"format": "int64", "type": "integer"}, "name": {"type": "string"}},
                        "type": "object",
                        "xml": {"name": "Category"},
                    },
                    "id": {"format": "int64", "type": "integer"},
                    "name": {"example": "doggie", "type": "string"},
                    "photoUrls": {
                        "items": {"type": "string"},
                        "type": "array",
                        "xml": {"name": "photoUrl", "wrapped": True},
                    },
                    "status": {
                        "description": "pet status in the store",
                        "enum": ["available", "pending", "sold"],
                        "type": "string",
                    },
                    "tags": {
                        "items": {
                            "properties": {"id": {"format": "int64", "type": "integer"}, "name": {"type": "string"}},
                            "type": "object",
                            "xml": {"name": "Tag"},
                        },
                        "type": "array",
                        "xml": {"name": "tag", "wrapped": True},
                    },
                },
                "required": ["name", "photoUrls"],
                "type": "object",
                "xml": {"name": "Pet"},
            },
        ),
    ),
)
def test_resolve(petstore, ref, expected):
    assert petstore.resolve(ref) == expected
