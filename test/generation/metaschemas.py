"""JSON Schema metaschemas, vendored so tests need no `jsonschema` install.

Only the top-level document of each draft: jsonschema-rs resolves the vocabulary `$ref`s
(`meta/core` and friends) from its own embedded copies.
"""

from typing import Any

DRAFT_04: dict[str, Any] = {
    "$schema": "http://json-schema.org/draft-04/schema#",
    "default": {},
    "definitions": {
        "positiveInteger": {"minimum": 0, "type": "integer"},
        "positiveIntegerDefault0": {"allOf": [{"$ref": "#/definitions/positiveInteger"}, {"default": 0}]},
        "schemaArray": {"items": {"$ref": "#"}, "minItems": 1, "type": "array"},
        "simpleTypes": {"enum": ["array", "boolean", "integer", "null", "number", "object", "string"]},
        "stringArray": {"items": {"type": "string"}, "minItems": 1, "type": "array", "uniqueItems": True},
    },
    "dependencies": {"exclusiveMaximum": ["maximum"], "exclusiveMinimum": ["minimum"]},
    "description": "Core schema meta-schema",
    "id": "http://json-schema.org/draft-04/schema#",
    "properties": {
        "$schema": {"type": "string"},
        "additionalItems": {"anyOf": [{"type": "boolean"}, {"$ref": "#"}], "default": {}},
        "additionalProperties": {"anyOf": [{"type": "boolean"}, {"$ref": "#"}], "default": {}},
        "allOf": {"$ref": "#/definitions/schemaArray"},
        "anyOf": {"$ref": "#/definitions/schemaArray"},
        "default": {},
        "definitions": {"additionalProperties": {"$ref": "#"}, "default": {}, "type": "object"},
        "dependencies": {
            "additionalProperties": {"anyOf": [{"$ref": "#"}, {"$ref": "#/definitions/stringArray"}]},
            "type": "object",
        },
        "description": {"type": "string"},
        "enum": {"minItems": 1, "type": "array", "uniqueItems": True},
        "exclusiveMaximum": {"default": False, "type": "boolean"},
        "exclusiveMinimum": {"default": False, "type": "boolean"},
        "format": {"type": "string"},
        "id": {"type": "string"},
        "items": {"anyOf": [{"$ref": "#"}, {"$ref": "#/definitions/schemaArray"}], "default": {}},
        "maxItems": {"$ref": "#/definitions/positiveInteger"},
        "maxLength": {"$ref": "#/definitions/positiveInteger"},
        "maxProperties": {"$ref": "#/definitions/positiveInteger"},
        "maximum": {"type": "number"},
        "minItems": {"$ref": "#/definitions/positiveIntegerDefault0"},
        "minLength": {"$ref": "#/definitions/positiveIntegerDefault0"},
        "minProperties": {"$ref": "#/definitions/positiveIntegerDefault0"},
        "minimum": {"type": "number"},
        "multipleOf": {"exclusiveMinimum": True, "minimum": 0, "type": "number"},
        "not": {"$ref": "#"},
        "oneOf": {"$ref": "#/definitions/schemaArray"},
        "pattern": {"format": "regex", "type": "string"},
        "patternProperties": {"additionalProperties": {"$ref": "#"}, "default": {}, "type": "object"},
        "properties": {"additionalProperties": {"$ref": "#"}, "default": {}, "type": "object"},
        "required": {"$ref": "#/definitions/stringArray"},
        "title": {"type": "string"},
        "type": {
            "anyOf": [
                {"$ref": "#/definitions/simpleTypes"},
                {"items": {"$ref": "#/definitions/simpleTypes"}, "minItems": 1, "type": "array", "uniqueItems": True},
            ]
        },
        "uniqueItems": {"default": False, "type": "boolean"},
    },
    "type": "object",
}

DRAFT_06: dict[str, Any] = {
    "$id": "http://json-schema.org/draft-06/schema#",
    "$schema": "http://json-schema.org/draft-06/schema#",
    "default": {},
    "definitions": {
        "nonNegativeInteger": {"minimum": 0, "type": "integer"},
        "nonNegativeIntegerDefault0": {"allOf": [{"$ref": "#/definitions/nonNegativeInteger"}, {"default": 0}]},
        "schemaArray": {"items": {"$ref": "#"}, "minItems": 1, "type": "array"},
        "simpleTypes": {"enum": ["array", "boolean", "integer", "null", "number", "object", "string"]},
        "stringArray": {"default": [], "items": {"type": "string"}, "type": "array", "uniqueItems": True},
    },
    "properties": {
        "$id": {"format": "uri-reference", "type": "string"},
        "$ref": {"format": "uri-reference", "type": "string"},
        "$schema": {"format": "uri", "type": "string"},
        "additionalItems": {"$ref": "#"},
        "additionalProperties": {"$ref": "#"},
        "allOf": {"$ref": "#/definitions/schemaArray"},
        "anyOf": {"$ref": "#/definitions/schemaArray"},
        "const": {},
        "contains": {"$ref": "#"},
        "default": {},
        "definitions": {"additionalProperties": {"$ref": "#"}, "default": {}, "type": "object"},
        "dependencies": {
            "additionalProperties": {"anyOf": [{"$ref": "#"}, {"$ref": "#/definitions/stringArray"}]},
            "type": "object",
        },
        "description": {"type": "string"},
        "enum": {"type": "array"},
        "examples": {"items": {}, "type": "array"},
        "exclusiveMaximum": {"type": "number"},
        "exclusiveMinimum": {"type": "number"},
        "format": {"type": "string"},
        "items": {"anyOf": [{"$ref": "#"}, {"$ref": "#/definitions/schemaArray"}], "default": {}},
        "maxItems": {"$ref": "#/definitions/nonNegativeInteger"},
        "maxLength": {"$ref": "#/definitions/nonNegativeInteger"},
        "maxProperties": {"$ref": "#/definitions/nonNegativeInteger"},
        "maximum": {"type": "number"},
        "minItems": {"$ref": "#/definitions/nonNegativeIntegerDefault0"},
        "minLength": {"$ref": "#/definitions/nonNegativeIntegerDefault0"},
        "minProperties": {"$ref": "#/definitions/nonNegativeIntegerDefault0"},
        "minimum": {"type": "number"},
        "multipleOf": {"exclusiveMinimum": 0, "type": "number"},
        "not": {"$ref": "#"},
        "oneOf": {"$ref": "#/definitions/schemaArray"},
        "pattern": {"format": "regex", "type": "string"},
        "patternProperties": {
            "additionalProperties": {"$ref": "#"},
            "default": {},
            "propertyNames": {"format": "regex"},
            "type": "object",
        },
        "properties": {"additionalProperties": {"$ref": "#"}, "default": {}, "type": "object"},
        "propertyNames": {"$ref": "#"},
        "required": {"$ref": "#/definitions/stringArray"},
        "title": {"type": "string"},
        "type": {
            "anyOf": [
                {"$ref": "#/definitions/simpleTypes"},
                {"items": {"$ref": "#/definitions/simpleTypes"}, "minItems": 1, "type": "array", "uniqueItems": True},
            ]
        },
        "uniqueItems": {"default": False, "type": "boolean"},
    },
    "title": "Core schema meta-schema",
    "type": ["object", "boolean"],
}

DRAFT_07: dict[str, Any] = {
    "$id": "http://json-schema.org/draft-07/schema#",
    "$schema": "http://json-schema.org/draft-07/schema#",
    "default": True,
    "definitions": {
        "nonNegativeInteger": {"minimum": 0, "type": "integer"},
        "nonNegativeIntegerDefault0": {"allOf": [{"$ref": "#/definitions/nonNegativeInteger"}, {"default": 0}]},
        "schemaArray": {"items": {"$ref": "#"}, "minItems": 1, "type": "array"},
        "simpleTypes": {"enum": ["array", "boolean", "integer", "null", "number", "object", "string"]},
        "stringArray": {"default": [], "items": {"type": "string"}, "type": "array", "uniqueItems": True},
    },
    "properties": {
        "$comment": {"type": "string"},
        "$id": {"format": "uri-reference", "type": "string"},
        "$ref": {"format": "uri-reference", "type": "string"},
        "$schema": {"format": "uri", "type": "string"},
        "additionalItems": {"$ref": "#"},
        "additionalProperties": {"$ref": "#"},
        "allOf": {"$ref": "#/definitions/schemaArray"},
        "anyOf": {"$ref": "#/definitions/schemaArray"},
        "const": True,
        "contains": {"$ref": "#"},
        "contentEncoding": {"type": "string"},
        "contentMediaType": {"type": "string"},
        "default": True,
        "definitions": {"additionalProperties": {"$ref": "#"}, "default": {}, "type": "object"},
        "dependencies": {
            "additionalProperties": {"anyOf": [{"$ref": "#"}, {"$ref": "#/definitions/stringArray"}]},
            "type": "object",
        },
        "description": {"type": "string"},
        "else": {"$ref": "#"},
        "enum": {"items": True, "type": "array"},
        "examples": {"items": True, "type": "array"},
        "exclusiveMaximum": {"type": "number"},
        "exclusiveMinimum": {"type": "number"},
        "format": {"type": "string"},
        "if": {"$ref": "#"},
        "items": {"anyOf": [{"$ref": "#"}, {"$ref": "#/definitions/schemaArray"}], "default": True},
        "maxItems": {"$ref": "#/definitions/nonNegativeInteger"},
        "maxLength": {"$ref": "#/definitions/nonNegativeInteger"},
        "maxProperties": {"$ref": "#/definitions/nonNegativeInteger"},
        "maximum": {"type": "number"},
        "minItems": {"$ref": "#/definitions/nonNegativeIntegerDefault0"},
        "minLength": {"$ref": "#/definitions/nonNegativeIntegerDefault0"},
        "minProperties": {"$ref": "#/definitions/nonNegativeIntegerDefault0"},
        "minimum": {"type": "number"},
        "multipleOf": {"exclusiveMinimum": 0, "type": "number"},
        "not": {"$ref": "#"},
        "oneOf": {"$ref": "#/definitions/schemaArray"},
        "pattern": {"format": "regex", "type": "string"},
        "patternProperties": {
            "additionalProperties": {"$ref": "#"},
            "default": {},
            "propertyNames": {"format": "regex"},
            "type": "object",
        },
        "properties": {"additionalProperties": {"$ref": "#"}, "default": {}, "type": "object"},
        "propertyNames": {"$ref": "#"},
        "readOnly": {"default": False, "type": "boolean"},
        "required": {"$ref": "#/definitions/stringArray"},
        "then": {"$ref": "#"},
        "title": {"type": "string"},
        "type": {
            "anyOf": [
                {"$ref": "#/definitions/simpleTypes"},
                {"items": {"$ref": "#/definitions/simpleTypes"}, "minItems": 1, "type": "array", "uniqueItems": True},
            ]
        },
        "uniqueItems": {"default": False, "type": "boolean"},
    },
    "title": "Core schema meta-schema",
    "type": ["object", "boolean"],
}

DRAFT_2019_09: dict[str, Any] = {
    "$id": "https://json-schema.org/draft/2019-09/schema",
    "$recursiveAnchor": True,
    "$schema": "https://json-schema.org/draft/2019-09/schema",
    "$vocabulary": {
        "https://json-schema.org/draft/2019-09/vocab/applicator": True,
        "https://json-schema.org/draft/2019-09/vocab/content": True,
        "https://json-schema.org/draft/2019-09/vocab/core": True,
        "https://json-schema.org/draft/2019-09/vocab/format": False,
        "https://json-schema.org/draft/2019-09/vocab/meta-data": True,
        "https://json-schema.org/draft/2019-09/vocab/validation": True,
    },
    "allOf": [
        {"$ref": "meta/core"},
        {"$ref": "meta/applicator"},
        {"$ref": "meta/validation"},
        {"$ref": "meta/meta-data"},
        {"$ref": "meta/format"},
        {"$ref": "meta/content"},
    ],
    "properties": {
        "definitions": {
            "$comment": "While no longer an official keyword as it is replaced by $defs, "
            "this keyword is retained in the meta-schema to prevent "
            "incompatible extensions as it remains in common use.",
            "additionalProperties": {"$recursiveRef": "#"},
            "default": {},
            "type": "object",
        },
        "dependencies": {
            "$comment": '"dependencies" is no longer a keyword, but schema authors '
            "should avoid redefining it to facilitate a smooth transition "
            'to "dependentSchemas" and "dependentRequired"',
            "additionalProperties": {"anyOf": [{"$recursiveRef": "#"}, {"$ref": "meta/validation#/$defs/stringArray"}]},
            "type": "object",
        },
    },
    "title": "Core and Validation specifications meta-schema",
    "type": ["object", "boolean"],
}

DRAFT_2020_12: dict[str, Any] = {
    "$comment": "This meta-schema also defines keywords that have appeared in previous drafts in order to "
    "prevent incompatible extensions as they remain in common use.",
    "$dynamicAnchor": "meta",
    "$id": "https://json-schema.org/draft/2020-12/schema",
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$vocabulary": {
        "https://json-schema.org/draft/2020-12/vocab/applicator": True,
        "https://json-schema.org/draft/2020-12/vocab/content": True,
        "https://json-schema.org/draft/2020-12/vocab/core": True,
        "https://json-schema.org/draft/2020-12/vocab/format-annotation": True,
        "https://json-schema.org/draft/2020-12/vocab/meta-data": True,
        "https://json-schema.org/draft/2020-12/vocab/unevaluated": True,
        "https://json-schema.org/draft/2020-12/vocab/validation": True,
    },
    "allOf": [
        {"$ref": "meta/core"},
        {"$ref": "meta/applicator"},
        {"$ref": "meta/unevaluated"},
        {"$ref": "meta/validation"},
        {"$ref": "meta/meta-data"},
        {"$ref": "meta/format-annotation"},
        {"$ref": "meta/content"},
    ],
    "properties": {
        "$recursiveAnchor": {
            "$comment": '"$recursiveAnchor" has been replaced by "$dynamicAnchor".',
            "$ref": "meta/core#/$defs/anchorString",
            "deprecated": True,
        },
        "$recursiveRef": {
            "$comment": '"$recursiveRef" has been replaced by "$dynamicRef".',
            "$ref": "meta/core#/$defs/uriReferenceString",
            "deprecated": True,
        },
        "definitions": {
            "$comment": '"definitions" has been replaced by "$defs".',
            "additionalProperties": {"$dynamicRef": "#meta"},
            "default": {},
            "deprecated": True,
            "type": "object",
        },
        "dependencies": {
            "$comment": '"dependencies" has been split and replaced by '
            '"dependentSchemas" and "dependentRequired" in order to serve '
            "their differing semantics.",
            "additionalProperties": {
                "anyOf": [{"$dynamicRef": "#meta"}, {"$ref": "meta/validation#/$defs/stringArray"}]
            },
            "default": {},
            "deprecated": True,
            "type": "object",
        },
    },
    "title": "Core and Validation specifications meta-schema",
    "type": ["object", "boolean"],
}
