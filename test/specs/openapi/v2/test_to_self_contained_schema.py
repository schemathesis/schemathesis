from __future__ import annotations

import json
from dataclasses import dataclass
from itertools import permutations
from typing import Any

import pytest
import yaml
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT4

from schemathesis.internal.copy import fast_deepcopy
from schemathesis.specs.openapi._jsonschema import (
    TransformConfig,
    to_self_contained_jsonschema,
    get_remote_schema_retriever,
)
from schemathesis.specs.openapi._jsonschema.cache import TransformCache


@dataclass
class Context:
    resolver: Any
    config: TransformConfig
    spec: dict[str, Any]

    @classmethod
    def from_spec(cls, spec: dict[str, Any]) -> Context:
        original = fast_deepcopy(spec)
        resolver = build_resolver(spec)
        config = build_config(spec)
        return cls(resolver=resolver, config=config, spec=original)

    def reset(self):
        spec = fast_deepcopy(self.spec)
        self.resolver = build_resolver(spec)
        self.config = build_config(spec)


def build_config(spec):
    return TransformConfig(
        nullable_key="x-nullable",
        remove_write_only=False,
        remove_read_only=True,
        components=spec,
        cache=TransformCache(),
    )


def build_resolver(spec):
    retrieve = get_remote_schema_retriever(DRAFT4)
    registry = Registry(retrieve=retrieve).with_resource("", Resource(contents=spec, specification=DRAFT4))
    return registry.resolver()


ITERATIONS = 2


def test_non_recursive_ref():
    ctx = Context.from_spec(
        {
            "definitions": {
                "A": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "foo": True,
                        "bar": {
                            "anyOf": [True, {"type": "integer"}],
                        },
                    },
                },
                "B": {"type": "array", "items": [True, {"type": "object"}]},
            }
        }
    )
    references = ("#/definitions/A", "#/definitions/B")
    expected_visits = {
        "#/definitions/A": {"-definitions-A"},
        "#/definitions/B": {"-definitions-B"},
    }
    for idx in range(ITERATIONS):
        for combo in permutations(references, len(references)):
            for reference in combo:
                visited = to_self_contained_jsonschema({"$ref": reference}, ctx.resolver, ctx.config)
                assert visited == expected_visits[reference]
            if idx != ITERATIONS - 1:
                ctx.reset()
    assert ctx.config.cache.moved_schemas == {
        "-definitions-A": {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "foo": True,
                "bar": {
                    "anyOf": [True, {"type": "integer"}],
                },
            },
        },
        "-definitions-B": {"type": "array", "items": [True, {"type": "object"}]},
    }
    assert not ctx.config.cache.recursive_references


def test_non_recursive_with_multiple_refs():
    ctx = Context.from_spec(
        {
            "definitions": {
                "A": {
                    "type": "object",
                    "properties": {
                        "first": {"$ref": "#/definitions/B"},
                        "second": {"$ref": "#/definitions/B"},
                    },
                },
                "B": {"type": "string"},
            }
        }
    )
    for _ in range(ITERATIONS):
        visited = to_self_contained_jsonschema({"$ref": "#/definitions/A"}, ctx.resolver, ctx.config)
        assert visited == {"-definitions-A", "-definitions-B"}
    assert ctx.config.cache.moved_schemas == {
        "-definitions-A": {
            "type": "object",
            "properties": {
                "first": {"$ref": "#/x-moved-schemas/-definitions-B"},
                "second": {"$ref": "#/x-moved-schemas/-definitions-B"},
            },
        },
        "-definitions-B": {"type": "string"},
    }
    assert not ctx.config.cache.recursive_references


@pytest.mark.parametrize(
    "reference",
    [
        "./shared.json#/definitions/A",
        "file://shared.json#/definitions/A",
        "shared.yml#/definitions/A",
    ],
)
def test_non_recursive_with_relative_file(testdir, reference):
    schema = {"definitions": {"A": {"$ref": reference}}}
    filename = reference.split("#")[0]
    if filename.startswith("file://"):
        filename = filename[7:].lstrip("./")
    filename, extension = filename.rsplit(".", 1)
    referenced = {
        "definitions": {
            "A": {
                "type": "object",
                "properties": {
                    "first": {"type": "string"},
                },
            }
        },
    }
    if filename.endswith(".yml"):
        content = yaml.dumps(referenced)
    else:
        content = json.dumps(referenced)
    testdir.makefile(extension, **{filename: content})
    ctx = Context.from_spec(schema)
    visited = to_self_contained_jsonschema({"$ref": "#/definitions/A"}, ctx.resolver, ctx.config)
    expected = f"{filename}.{extension}-definitions-A".replace("/", "-")
    assert visited == {"-definitions-A", expected}


def test_non_recursive_with_nested_files(testdir):
    schema = {"definitions": {"A": {"$ref": "shared.json#/definitions/A"}}}
    shared = {
        "definitions": {
            "A": {
                "$ref": "folder/first.json#/definitions/A",
            },
        }
    }
    first = {
        "definitions": {
            "A": {
                "$ref": "second.json#/definitions/A",
            },
        }
    }
    second = {"definitions": {"A": {"type": "string"}}}
    testdir.makefile("json", shared=json.dumps(shared))
    folder = testdir.mkdir("folder")
    (folder / "first.json").write_text(json.dumps(first), "utf8")
    (folder / "second.json").write_text(json.dumps(second), "utf8")
    ctx = Context.from_spec(schema)
    visited = to_self_contained_jsonschema({"$ref": "#/definitions/A"}, ctx.resolver, ctx.config)
    assert visited == {
        "-definitions-A",
        "folder-first.json-definitions-A",
        "second.json-definitions-A",
        "shared.json-definitions-A",
    }


def test_schema_transformation():
    ctx = Context.from_spec(
        {
            "definitions": {
                "A": {
                    "type": "object",
                    "properties": {
                        "first": {"type": "file"},
                        "second": {"type": "string", "x-nullable": True},
                        "third": {
                            "type": "integer",
                            "readOnly": True,
                        },
                        "nested": {
                            "allOf": [{"type": "file"}, {"type": "null"}],
                        },
                    },
                },
            }
        }
    )
    for _ in range(ITERATIONS):
        visited = to_self_contained_jsonschema({"$ref": "#/definitions/A"}, ctx.resolver, ctx.config)
        assert visited == {"-definitions-A"}
    assert ctx.config.cache.moved_schemas == {
        "-definitions-A": {
            "not": {"required": ["third"]},
            "properties": {
                "first": {"type": "string", "format": "binary"},
                "nested": {"allOf": [{"type": "string", "format": "binary"}, {"type": "null"}]},
                "second": {"anyOf": [{"type": "string"}, {"type": "null"}]},
            },
            "type": "object",
        }
    }
    assert not ctx.config.cache.recursive_references


def test_recursive_1_hop():
    ctx = Context.from_spec(
        {
            "definitions": {
                "A": {"type": "object", "properties": {"id": {"type": "string"}, "ref": {"$ref": "#/definitions/B"}}},
                "B": {"type": "object", "properties": {"name": {"type": "string"}, "ref": {"$ref": "#/definitions/A"}}},
            }
        }
    )
    references = ("#/definitions/A", "#/definitions/B")
    for idx in range(ITERATIONS):
        for combo in permutations(references, len(references)):
            for reference in combo:
                visited = to_self_contained_jsonschema({"$ref": reference}, ctx.resolver, ctx.config)
                assert visited == {"-definitions-A", "-definitions-B"}
            if idx != ITERATIONS - 1:
                ctx.reset()
    assert ctx.config.cache.moved_schemas == {
        "-definitions-A": {
            "type": "object",
            "properties": {"id": {"type": "string"}, "ref": {"$ref": "#/x-moved-schemas/-definitions-B"}},
        },
        "-definitions-B": {
            "type": "object",
            "properties": {"name": {"type": "string"}, "ref": {"$ref": "#/x-moved-schemas/-definitions-A"}},
        },
    }
    assert ctx.config.cache.recursive_references == {
        "#/x-moved-schemas/-definitions-A",
        "#/x-moved-schemas/-definitions-B",
    }


def test_recursive_1_hop_in_array():
    ctx = Context.from_spec(
        {
            "definitions": {
                "A": {
                    "allOf": [
                        {"type": "string"},
                        {"$ref": "#/definitions/B"},
                    ]
                },
                "B": {"type": "object", "properties": {"name": {"type": "string"}, "ref": {"$ref": "#/definitions/A"}}},
            }
        }
    )
    references = ("#/definitions/A", "#/definitions/B")
    for idx in range(ITERATIONS):
        for combo in permutations(references, len(references)):
            for reference in combo:
                visited = to_self_contained_jsonschema({"$ref": reference}, ctx.resolver, ctx.config)
                assert visited == {"-definitions-A", "-definitions-B"}
            if idx != ITERATIONS - 1:
                ctx.reset()
    assert ctx.config.cache.moved_schemas == {
        "-definitions-A": {
            "allOf": [
                {"type": "string"},
                {"$ref": "#/x-moved-schemas/-definitions-B"},
            ]
        },
        "-definitions-B": {
            "type": "object",
            "properties": {"name": {"type": "string"}, "ref": {"$ref": "#/x-moved-schemas/-definitions-A"}},
        },
    }
    assert ctx.config.cache.recursive_references == {
        "#/x-moved-schemas/-definitions-A",
        "#/x-moved-schemas/-definitions-B",
    }


def test_recursive_2_hops():
    ctx = Context.from_spec(
        {
            "definitions": {
                "A": {"type": "object", "properties": {"id": {"type": "string"}, "ref": {"$ref": "#/definitions/B"}}},
                "B": {"type": "object", "properties": {"name": {"type": "string"}, "ref": {"$ref": "#/definitions/C"}}},
                "C": {
                    "type": "object",
                    "properties": {"email": {"type": "string"}, "ref": {"$ref": "#/definitions/A"}},
                },
            }
        }
    )
    references = ("#/definitions/A", "#/definitions/B", "#/definitions/C")
    for idx in range(ITERATIONS):
        for combo in permutations(references, len(references)):
            for reference in combo:
                visited = to_self_contained_jsonschema({"$ref": reference}, ctx.resolver, ctx.config)
                assert visited == {"-definitions-A", "-definitions-B", "-definitions-C"}
            if idx != ITERATIONS - 1:
                ctx.reset()
    assert ctx.config.cache.moved_schemas == {
        "-definitions-A": {
            "type": "object",
            "properties": {"id": {"type": "string"}, "ref": {"$ref": "#/x-moved-schemas/-definitions-B"}},
        },
        "-definitions-B": {
            "type": "object",
            "properties": {"name": {"type": "string"}, "ref": {"$ref": "#/x-moved-schemas/-definitions-C"}},
        },
        "-definitions-C": {
            "properties": {"email": {"type": "string"}, "ref": {"$ref": "#/x-moved-schemas/-definitions-A"}},
            "type": "object",
        },
    }
    assert ctx.config.cache.recursive_references == {
        "#/x-moved-schemas/-definitions-A",
        "#/x-moved-schemas/-definitions-B",
        "#/x-moved-schemas/-definitions-C",
    }


def test_recursive_with_nested():
    ctx = Context.from_spec(
        {
            "definitions": {
                "Patch": {"$ref": "#/definitions/Shared"},
                "Put": {"$ref": "#/definitions/Shared"},
                "Shared": {"$ref": "#/definitions/RecursiveRoot"},
                "RecursiveRoot": {"$ref": "#/definitions/RecursiveA"},
                "RecursiveA": {"$ref": "#/definitions/RecursiveB"},
                "RecursiveB": {"$ref": "#/definitions/RecursiveA"},
            }
        }
    )
    references = (
        "#/definitions/Patch",
        "#/definitions/Put",
        "#/definitions/Shared",
        "#/definitions/RecursiveRoot",
        "#/definitions/RecursiveA",
        "#/definitions/RecursiveB",
    )
    expected_visits = {
        "#/definitions/Patch": {
            "-definitions-Patch",
            "-definitions-Shared",
            "-definitions-RecursiveRoot",
            "-definitions-RecursiveA",
            "-definitions-RecursiveB",
        },
        "#/definitions/Put": {
            "-definitions-Put",
            "-definitions-Shared",
            "-definitions-RecursiveRoot",
            "-definitions-RecursiveA",
            "-definitions-RecursiveB",
        },
        "#/definitions/Shared": {
            "-definitions-Shared",
            "-definitions-RecursiveRoot",
            "-definitions-RecursiveA",
            "-definitions-RecursiveB",
        },
        "#/definitions/RecursiveRoot": {
            "-definitions-RecursiveRoot",
            "-definitions-RecursiveA",
            "-definitions-RecursiveB",
        },
        "#/definitions/RecursiveA": {
            "-definitions-RecursiveA",
            "-definitions-RecursiveB",
        },
        "#/definitions/RecursiveB": {
            "-definitions-RecursiveB",
            "-definitions-RecursiveA",
        },
    }
    for idx in range(ITERATIONS):
        for combo in permutations(references, len(references)):
            for reference in combo:
                visited = to_self_contained_jsonschema({"$ref": reference}, ctx.resolver, ctx.config)
                assert visited == expected_visits[reference], reference
            if idx != ITERATIONS - 1:
                ctx.reset()
    assert ctx.config.cache.moved_schemas == {
        "-definitions-Patch": {"$ref": "#/x-moved-schemas/-definitions-Shared"},
        "-definitions-Put": {"$ref": "#/x-moved-schemas/-definitions-Shared"},
        "-definitions-RecursiveA": {"$ref": "#/x-moved-schemas/-definitions-RecursiveB"},
        "-definitions-RecursiveB": {"$ref": "#/x-moved-schemas/-definitions-RecursiveA"},
        "-definitions-RecursiveRoot": {"$ref": "#/x-moved-schemas/-definitions-RecursiveA"},
        "-definitions-Shared": {"$ref": "#/x-moved-schemas/-definitions-RecursiveRoot"},
    }
    assert ctx.config.cache.recursive_references == {
        "#/x-moved-schemas/-definitions-RecursiveA",
        "#/x-moved-schemas/-definitions-RecursiveB",
    }


def test_recursive_with_leaf():
    ctx = Context.from_spec(
        {
            "definitions": {
                "Patch": {"$ref": "#/definitions/Shared"},
                "Put": {"$ref": "#/definitions/Shared"},
                "Shared": {
                    "properties": {
                        "left": {
                            "$ref": "#/definitions/Leaf",
                        },
                        "right": {
                            "$ref": "#/definitions/Put",
                        },
                    },
                },
                "Leaf": {},
            }
        }
    )
    references = ("#/definitions/Patch", "#/definitions/Put", "#/definitions/Shared", "#/definitions/Leaf")
    expected_visits = {
        # `Patch` -> `Shared`
        # `Shared` -> `Leaf`, `Put`
        # `Put` -> `Shared`
        "#/definitions/Patch": {"-definitions-Patch", "-definitions-Shared", "-definitions-Leaf", "-definitions-Put"},
        # `Shared` -> `Leaf`, `Put`
        # `Put` -> `Shared`
        "#/definitions/Shared": {"-definitions-Shared", "-definitions-Leaf", "-definitions-Put"},
        # `Put` -> `Shared`
        # `Shared` -> `Leaf`, `Put`
        "#/definitions/Put": {"-definitions-Put", "-definitions-Shared", "-definitions-Leaf"},
        # `Leaf` has no children
        "#/definitions/Leaf": {"-definitions-Leaf"},
    }
    for idx in range(ITERATIONS):
        for combo in permutations(references, len(references)):
            for reference in combo:
                visited = to_self_contained_jsonschema({"$ref": reference}, ctx.resolver, ctx.config)
                assert visited == expected_visits[reference]
            if idx != ITERATIONS - 1:
                ctx.reset()
    visited = to_self_contained_jsonschema({"$ref": "#/definitions/Patch"}, ctx.resolver, ctx.config)
    assert ctx.config.cache.recursive_references == {
        "#/x-moved-schemas/-definitions-Shared",
        "#/x-moved-schemas/-definitions-Put",
    }
    ctx.reset()

    visited = to_self_contained_jsonschema({"$ref": "#/definitions/Put"}, ctx.resolver, ctx.config)
    assert visited == {"-definitions-Put", "-definitions-Shared", "-definitions-Leaf"}
    assert ctx.config.cache.recursive_references == {
        "#/x-moved-schemas/-definitions-Shared",
        "#/x-moved-schemas/-definitions-Put",
    }
    ctx.reset()

    visited = to_self_contained_jsonschema({"$ref": "#/definitions/Shared"}, ctx.resolver, ctx.config)
    assert visited == {"-definitions-Put", "-definitions-Shared", "-definitions-Leaf"}
    assert ctx.config.cache.recursive_references == {
        "#/x-moved-schemas/-definitions-Put",
        "#/x-moved-schemas/-definitions-Shared",
    }
    ctx.reset()

    visited = to_self_contained_jsonschema({"$ref": "#/definitions/Leaf"}, ctx.resolver, ctx.config)
    assert visited == {"-definitions-Leaf"}
    assert not ctx.config.cache.recursive_references
