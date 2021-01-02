# pylint: disable=too-many-lines
# These schemas are copied from https://github.com/OAI/OpenAPI-Specification/tree/master/schemas
import jsonschema

SWAGGER_20 = {
    "title": "A JSON Schema for Swagger 2.0 API.",
    "id": "http://swagger.io/v2/schema.json#",
    "$schema": "http://json-schema.org/draft-04/schema#",
    "type": "object",
    "required": ["swagger", "info", "paths"],
    "additionalProperties": False,
    "patternProperties": {"^x-": {"$ref": "#/definitions/vendorExtension"}},
    "properties": {
        "swagger": {"type": "string", "enum": ["2.0"], "description": "The Swagger version of this document."},
        "info": {"$ref": "#/definitions/info"},
        "host": {
            "type": "string",
            "pattern": "^[^{}/ :\\\\]+(?::\\d+)?$",
            "description": "The host (name or ip) of the API. Example: 'swagger.io'",
        },
        "basePath": {"type": "string", "pattern": "^/", "description": "The base path to the API. Example: '/api'."},
        "schemes": {"$ref": "#/definitions/schemesList"},
        "consumes": {
            "description": "A list of MIME types accepted by the API.",
            "allOf": [{"$ref": "#/definitions/mediaTypeList"}],
        },
        "produces": {
            "description": "A list of MIME types the API can produce.",
            "allOf": [{"$ref": "#/definitions/mediaTypeList"}],
        },
        "paths": {"$ref": "#/definitions/paths"},
        "definitions": {"$ref": "#/definitions/definitions"},
        "parameters": {"$ref": "#/definitions/parameterDefinitions"},
        "responses": {"$ref": "#/definitions/responseDefinitions"},
        "security": {"$ref": "#/definitions/security"},
        "securityDefinitions": {"$ref": "#/definitions/securityDefinitions"},
        "tags": {"type": "array", "items": {"$ref": "#/definitions/tag"}, "uniqueItems": True},
        "externalDocs": {"$ref": "#/definitions/externalDocs"},
    },
    "definitions": {
        "info": {
            "type": "object",
            "description": "General information about the API.",
            "required": ["version", "title"],
            "additionalProperties": False,
            "patternProperties": {"^x-": {"$ref": "#/definitions/vendorExtension"}},
            "properties": {
                "title": {"type": "string", "description": "A unique and precise title of the API."},
                "version": {"type": "string", "description": "A semantic version number of the API."},
                "description": {
                    "type": "string",
                    "description": "A longer description of the API. Should be different from the title.  GitHub Flavored Markdown is allowed.",
                },
                "termsOfService": {"type": "string", "description": "The terms of service for the API."},
                "contact": {"$ref": "#/definitions/contact"},
                "license": {"$ref": "#/definitions/license"},
            },
        },
        "contact": {
            "type": "object",
            "description": "Contact information for the owners of the API.",
            "additionalProperties": False,
            "properties": {
                "name": {"type": "string", "description": "The identifying name of the contact person/organization."},
                "url": {
                    "type": "string",
                    "description": "The URL pointing to the contact information.",
                    "format": "uri",
                },
                "email": {
                    "type": "string",
                    "description": "The email address of the contact person/organization.",
                    "format": "email",
                },
            },
            "patternProperties": {"^x-": {"$ref": "#/definitions/vendorExtension"}},
        },
        "license": {
            "type": "object",
            "required": ["name"],
            "additionalProperties": False,
            "properties": {
                "name": {
                    "type": "string",
                    "description": "The name of the license type. It's encouraged to use an OSI compatible license.",
                },
                "url": {"type": "string", "description": "The URL pointing to the license.", "format": "uri"},
            },
            "patternProperties": {"^x-": {"$ref": "#/definitions/vendorExtension"}},
        },
        "paths": {
            "type": "object",
            "description": "Relative paths to the individual endpoints. They must be relative to the 'basePath'.",
            "patternProperties": {
                "^x-": {"$ref": "#/definitions/vendorExtension"},
                "^/": {"$ref": "#/definitions/pathItem"},
            },
            "additionalProperties": False,
        },
        "definitions": {
            "type": "object",
            "additionalProperties": {"$ref": "#/definitions/schema"},
            "description": "One or more JSON objects describing the schemas being consumed and produced by the API.",
        },
        "parameterDefinitions": {
            "type": "object",
            "additionalProperties": {"$ref": "#/definitions/parameter"},
            "description": "One or more JSON representations for parameters",
        },
        "responseDefinitions": {
            "type": "object",
            "additionalProperties": {"$ref": "#/definitions/response"},
            "description": "One or more JSON representations for responses",
        },
        "externalDocs": {
            "type": "object",
            "additionalProperties": False,
            "description": "information about external documentation",
            "required": ["url"],
            "properties": {"description": {"type": "string"}, "url": {"type": "string", "format": "uri"}},
            "patternProperties": {"^x-": {"$ref": "#/definitions/vendorExtension"}},
        },
        "examples": {"type": "object", "additionalProperties": True},
        "mimeType": {"type": "string", "description": "The MIME type of the HTTP message."},
        "operation": {
            "type": "object",
            "required": ["responses"],
            "additionalProperties": False,
            "patternProperties": {"^x-": {"$ref": "#/definitions/vendorExtension"}},
            "properties": {
                "tags": {"type": "array", "items": {"type": "string"}, "uniqueItems": True},
                "summary": {"type": "string", "description": "A brief summary of the operation."},
                "description": {
                    "type": "string",
                    "description": "A longer description of the operation, GitHub Flavored Markdown is allowed.",
                },
                "externalDocs": {"$ref": "#/definitions/externalDocs"},
                "operationId": {"type": "string", "description": "A unique identifier of the operation."},
                "produces": {
                    "description": "A list of MIME types the API can produce.",
                    "allOf": [{"$ref": "#/definitions/mediaTypeList"}],
                },
                "consumes": {
                    "description": "A list of MIME types the API can consume.",
                    "allOf": [{"$ref": "#/definitions/mediaTypeList"}],
                },
                "parameters": {"$ref": "#/definitions/parametersList"},
                "responses": {"$ref": "#/definitions/responses"},
                "schemes": {"$ref": "#/definitions/schemesList"},
                "deprecated": {"type": "boolean", "default": False},
                "security": {"$ref": "#/definitions/security"},
            },
        },
        "pathItem": {
            "type": "object",
            "additionalProperties": False,
            "patternProperties": {"^x-": {"$ref": "#/definitions/vendorExtension"}},
            "properties": {
                "$ref": {"type": "string"},
                "get": {"$ref": "#/definitions/operation"},
                "put": {"$ref": "#/definitions/operation"},
                "post": {"$ref": "#/definitions/operation"},
                "delete": {"$ref": "#/definitions/operation"},
                "options": {"$ref": "#/definitions/operation"},
                "head": {"$ref": "#/definitions/operation"},
                "patch": {"$ref": "#/definitions/operation"},
                "parameters": {"$ref": "#/definitions/parametersList"},
            },
        },
        "responses": {
            "type": "object",
            "description": "Response objects names can either be any valid HTTP status code or 'default'.",
            "minProperties": 1,
            "additionalProperties": False,
            "patternProperties": {
                "^([0-9]{3})$|^(default)$": {"$ref": "#/definitions/responseValue"},
                "^x-": {"$ref": "#/definitions/vendorExtension"},
            },
            "not": {
                "type": "object",
                "additionalProperties": False,
                "patternProperties": {"^x-": {"$ref": "#/definitions/vendorExtension"}},
            },
        },
        "responseValue": {"oneOf": [{"$ref": "#/definitions/response"}, {"$ref": "#/definitions/jsonReference"}]},
        "response": {
            "type": "object",
            "required": ["description"],
            "properties": {
                "description": {"type": "string"},
                "schema": {"oneOf": [{"$ref": "#/definitions/schema"}, {"$ref": "#/definitions/fileSchema"}]},
                "headers": {"$ref": "#/definitions/headers"},
                "examples": {"$ref": "#/definitions/examples"},
            },
            "additionalProperties": False,
            "patternProperties": {"^x-": {"$ref": "#/definitions/vendorExtension"}},
        },
        "headers": {"type": "object", "additionalProperties": {"$ref": "#/definitions/header"}},
        "header": {
            "type": "object",
            "additionalProperties": False,
            "required": ["type"],
            "properties": {
                "type": {"type": "string", "enum": ["string", "number", "integer", "boolean", "array"]},
                "format": {"type": "string"},
                "items": {"$ref": "#/definitions/primitivesItems"},
                "collectionFormat": {"$ref": "#/definitions/collectionFormat"},
                "default": {"$ref": "#/definitions/default"},
                "maximum": {"$ref": "#/definitions/maximum"},
                "exclusiveMaximum": {"$ref": "#/definitions/exclusiveMaximum"},
                "minimum": {"$ref": "#/definitions/minimum"},
                "exclusiveMinimum": {"$ref": "#/definitions/exclusiveMinimum"},
                "maxLength": {"$ref": "#/definitions/maxLength"},
                "minLength": {"$ref": "#/definitions/minLength"},
                "pattern": {"$ref": "#/definitions/pattern"},
                "maxItems": {"$ref": "#/definitions/maxItems"},
                "minItems": {"$ref": "#/definitions/minItems"},
                "uniqueItems": {"$ref": "#/definitions/uniqueItems"},
                "enum": {"$ref": "#/definitions/enum"},
                "multipleOf": {"$ref": "#/definitions/multipleOf"},
                "description": {"type": "string"},
            },
            "patternProperties": {"^x-": {"$ref": "#/definitions/vendorExtension"}},
        },
        "vendorExtension": {
            "description": "Any property starting with x- is valid.",
            "additionalProperties": True,
            "additionalItems": True,
        },
        "bodyParameter": {
            "type": "object",
            "required": ["name", "in", "schema"],
            "patternProperties": {"^x-": {"$ref": "#/definitions/vendorExtension"}},
            "properties": {
                "description": {
                    "type": "string",
                    "description": "A brief description of the parameter. This could contain examples of use.  GitHub Flavored Markdown is allowed.",
                },
                "name": {"type": "string", "description": "The name of the parameter."},
                "in": {"type": "string", "description": "Determines the location of the parameter.", "enum": ["body"]},
                "required": {
                    "type": "boolean",
                    "description": "Determines whether or not this parameter is required or optional.",
                    "default": False,
                },
                "schema": {"$ref": "#/definitions/schema"},
            },
            "additionalProperties": False,
        },
        "headerParameterSubSchema": {
            "additionalProperties": False,
            "patternProperties": {"^x-": {"$ref": "#/definitions/vendorExtension"}},
            "properties": {
                "required": {
                    "type": "boolean",
                    "description": "Determines whether or not this parameter is required or optional.",
                    "default": False,
                },
                "in": {
                    "type": "string",
                    "description": "Determines the location of the parameter.",
                    "enum": ["header"],
                },
                "description": {
                    "type": "string",
                    "description": "A brief description of the parameter. This could contain examples of use.  GitHub Flavored Markdown is allowed.",
                },
                "name": {"type": "string", "description": "The name of the parameter."},
                "type": {"type": "string", "enum": ["string", "number", "boolean", "integer", "array"]},
                "format": {"type": "string"},
                "items": {"$ref": "#/definitions/primitivesItems"},
                "collectionFormat": {"$ref": "#/definitions/collectionFormat"},
                "default": {"$ref": "#/definitions/default"},
                "maximum": {"$ref": "#/definitions/maximum"},
                "exclusiveMaximum": {"$ref": "#/definitions/exclusiveMaximum"},
                "minimum": {"$ref": "#/definitions/minimum"},
                "exclusiveMinimum": {"$ref": "#/definitions/exclusiveMinimum"},
                "maxLength": {"$ref": "#/definitions/maxLength"},
                "minLength": {"$ref": "#/definitions/minLength"},
                "pattern": {"$ref": "#/definitions/pattern"},
                "maxItems": {"$ref": "#/definitions/maxItems"},
                "minItems": {"$ref": "#/definitions/minItems"},
                "uniqueItems": {"$ref": "#/definitions/uniqueItems"},
                "enum": {"$ref": "#/definitions/enum"},
                "multipleOf": {"$ref": "#/definitions/multipleOf"},
            },
        },
        "queryParameterSubSchema": {
            "additionalProperties": False,
            "patternProperties": {"^x-": {"$ref": "#/definitions/vendorExtension"}},
            "properties": {
                "required": {
                    "type": "boolean",
                    "description": "Determines whether or not this parameter is required or optional.",
                    "default": False,
                },
                "in": {"type": "string", "description": "Determines the location of the parameter.", "enum": ["query"]},
                "description": {
                    "type": "string",
                    "description": "A brief description of the parameter. This could contain examples of use.  GitHub Flavored Markdown is allowed.",
                },
                "name": {"type": "string", "description": "The name of the parameter."},
                "allowEmptyValue": {
                    "type": "boolean",
                    "default": False,
                    "description": "allows sending a parameter by name only or with an empty value.",
                },
                "type": {"type": "string", "enum": ["string", "number", "boolean", "integer", "array"]},
                "format": {"type": "string"},
                "items": {"$ref": "#/definitions/primitivesItems"},
                "collectionFormat": {"$ref": "#/definitions/collectionFormatWithMulti"},
                "default": {"$ref": "#/definitions/default"},
                "maximum": {"$ref": "#/definitions/maximum"},
                "exclusiveMaximum": {"$ref": "#/definitions/exclusiveMaximum"},
                "minimum": {"$ref": "#/definitions/minimum"},
                "exclusiveMinimum": {"$ref": "#/definitions/exclusiveMinimum"},
                "maxLength": {"$ref": "#/definitions/maxLength"},
                "minLength": {"$ref": "#/definitions/minLength"},
                "pattern": {"$ref": "#/definitions/pattern"},
                "maxItems": {"$ref": "#/definitions/maxItems"},
                "minItems": {"$ref": "#/definitions/minItems"},
                "uniqueItems": {"$ref": "#/definitions/uniqueItems"},
                "enum": {"$ref": "#/definitions/enum"},
                "multipleOf": {"$ref": "#/definitions/multipleOf"},
            },
        },
        "formDataParameterSubSchema": {
            "additionalProperties": False,
            "patternProperties": {"^x-": {"$ref": "#/definitions/vendorExtension"}},
            "properties": {
                "required": {
                    "type": "boolean",
                    "description": "Determines whether or not this parameter is required or optional.",
                    "default": False,
                },
                "in": {
                    "type": "string",
                    "description": "Determines the location of the parameter.",
                    "enum": ["formData"],
                },
                "description": {
                    "type": "string",
                    "description": "A brief description of the parameter. This could contain examples of use.  GitHub Flavored Markdown is allowed.",
                },
                "name": {"type": "string", "description": "The name of the parameter."},
                "allowEmptyValue": {
                    "type": "boolean",
                    "default": False,
                    "description": "allows sending a parameter by name only or with an empty value.",
                },
                "type": {"type": "string", "enum": ["string", "number", "boolean", "integer", "array", "file"]},
                "format": {"type": "string"},
                "items": {"$ref": "#/definitions/primitivesItems"},
                "collectionFormat": {"$ref": "#/definitions/collectionFormatWithMulti"},
                "default": {"$ref": "#/definitions/default"},
                "maximum": {"$ref": "#/definitions/maximum"},
                "exclusiveMaximum": {"$ref": "#/definitions/exclusiveMaximum"},
                "minimum": {"$ref": "#/definitions/minimum"},
                "exclusiveMinimum": {"$ref": "#/definitions/exclusiveMinimum"},
                "maxLength": {"$ref": "#/definitions/maxLength"},
                "minLength": {"$ref": "#/definitions/minLength"},
                "pattern": {"$ref": "#/definitions/pattern"},
                "maxItems": {"$ref": "#/definitions/maxItems"},
                "minItems": {"$ref": "#/definitions/minItems"},
                "uniqueItems": {"$ref": "#/definitions/uniqueItems"},
                "enum": {"$ref": "#/definitions/enum"},
                "multipleOf": {"$ref": "#/definitions/multipleOf"},
            },
        },
        "pathParameterSubSchema": {
            "additionalProperties": False,
            "patternProperties": {"^x-": {"$ref": "#/definitions/vendorExtension"}},
            "required": ["required"],
            "properties": {
                "required": {
                    "type": "boolean",
                    "enum": [True],
                    "description": "Determines whether or not this parameter is required or optional.",
                },
                "in": {"type": "string", "description": "Determines the location of the parameter.", "enum": ["path"]},
                "description": {
                    "type": "string",
                    "description": "A brief description of the parameter. This could contain examples of use.  GitHub Flavored Markdown is allowed.",
                },
                "name": {"type": "string", "description": "The name of the parameter."},
                "type": {"type": "string", "enum": ["string", "number", "boolean", "integer", "array"]},
                "format": {"type": "string"},
                "items": {"$ref": "#/definitions/primitivesItems"},
                "collectionFormat": {"$ref": "#/definitions/collectionFormat"},
                "default": {"$ref": "#/definitions/default"},
                "maximum": {"$ref": "#/definitions/maximum"},
                "exclusiveMaximum": {"$ref": "#/definitions/exclusiveMaximum"},
                "minimum": {"$ref": "#/definitions/minimum"},
                "exclusiveMinimum": {"$ref": "#/definitions/exclusiveMinimum"},
                "maxLength": {"$ref": "#/definitions/maxLength"},
                "minLength": {"$ref": "#/definitions/minLength"},
                "pattern": {"$ref": "#/definitions/pattern"},
                "maxItems": {"$ref": "#/definitions/maxItems"},
                "minItems": {"$ref": "#/definitions/minItems"},
                "uniqueItems": {"$ref": "#/definitions/uniqueItems"},
                "enum": {"$ref": "#/definitions/enum"},
                "multipleOf": {"$ref": "#/definitions/multipleOf"},
            },
        },
        "nonBodyParameter": {
            "type": "object",
            "required": ["name", "in", "type"],
            "oneOf": [
                {"$ref": "#/definitions/headerParameterSubSchema"},
                {"$ref": "#/definitions/formDataParameterSubSchema"},
                {"$ref": "#/definitions/queryParameterSubSchema"},
                {"$ref": "#/definitions/pathParameterSubSchema"},
            ],
        },
        "parameter": {"oneOf": [{"$ref": "#/definitions/bodyParameter"}, {"$ref": "#/definitions/nonBodyParameter"}]},
        "schema": {
            "type": "object",
            "description": "A deterministic version of a JSON Schema object.",
            "patternProperties": {"^x-": {"$ref": "#/definitions/vendorExtension"}},
            "properties": {
                "$ref": {"type": "string"},
                "format": {"type": "string"},
                "title": {"$ref": "http://json-schema.org/draft-04/schema#/properties/title"},
                "description": {"$ref": "http://json-schema.org/draft-04/schema#/properties/description"},
                "default": {"$ref": "http://json-schema.org/draft-04/schema#/properties/default"},
                "multipleOf": {"$ref": "http://json-schema.org/draft-04/schema#/properties/multipleOf"},
                "maximum": {"$ref": "http://json-schema.org/draft-04/schema#/properties/maximum"},
                "exclusiveMaximum": {"$ref": "http://json-schema.org/draft-04/schema#/properties/exclusiveMaximum"},
                "minimum": {"$ref": "http://json-schema.org/draft-04/schema#/properties/minimum"},
                "exclusiveMinimum": {"$ref": "http://json-schema.org/draft-04/schema#/properties/exclusiveMinimum"},
                "maxLength": {"$ref": "http://json-schema.org/draft-04/schema#/definitions/positiveInteger"},
                "minLength": {"$ref": "http://json-schema.org/draft-04/schema#/definitions/positiveIntegerDefault0"},
                "pattern": {"$ref": "http://json-schema.org/draft-04/schema#/properties/pattern"},
                "maxItems": {"$ref": "http://json-schema.org/draft-04/schema#/definitions/positiveInteger"},
                "minItems": {"$ref": "http://json-schema.org/draft-04/schema#/definitions/positiveIntegerDefault0"},
                "uniqueItems": {"$ref": "http://json-schema.org/draft-04/schema#/properties/uniqueItems"},
                "maxProperties": {"$ref": "http://json-schema.org/draft-04/schema#/definitions/positiveInteger"},
                "minProperties": {
                    "$ref": "http://json-schema.org/draft-04/schema#/definitions/positiveIntegerDefault0"
                },
                "required": {"$ref": "http://json-schema.org/draft-04/schema#/definitions/stringArray"},
                "enum": {"$ref": "http://json-schema.org/draft-04/schema#/properties/enum"},
                "additionalProperties": {
                    "anyOf": [{"$ref": "#/definitions/schema"}, {"type": "boolean"}],
                    "default": {},
                },
                "type": {"$ref": "http://json-schema.org/draft-04/schema#/properties/type"},
                "items": {
                    "anyOf": [
                        {"$ref": "#/definitions/schema"},
                        {"type": "array", "minItems": 1, "items": {"$ref": "#/definitions/schema"}},
                    ],
                    "default": {},
                },
                "allOf": {"type": "array", "minItems": 1, "items": {"$ref": "#/definitions/schema"}},
                "properties": {
                    "type": "object",
                    "additionalProperties": {"$ref": "#/definitions/schema"},
                    "default": {},
                },
                "discriminator": {"type": "string"},
                "readOnly": {"type": "boolean", "default": False},
                "xml": {"$ref": "#/definitions/xml"},
                "externalDocs": {"$ref": "#/definitions/externalDocs"},
                "example": {},
            },
            "additionalProperties": False,
        },
        "fileSchema": {
            "type": "object",
            "description": "A deterministic version of a JSON Schema object.",
            "patternProperties": {"^x-": {"$ref": "#/definitions/vendorExtension"}},
            "required": ["type"],
            "properties": {
                "format": {"type": "string"},
                "title": {"$ref": "http://json-schema.org/draft-04/schema#/properties/title"},
                "description": {"$ref": "http://json-schema.org/draft-04/schema#/properties/description"},
                "default": {"$ref": "http://json-schema.org/draft-04/schema#/properties/default"},
                "required": {"$ref": "http://json-schema.org/draft-04/schema#/definitions/stringArray"},
                "type": {"type": "string", "enum": ["file"]},
                "readOnly": {"type": "boolean", "default": False},
                "externalDocs": {"$ref": "#/definitions/externalDocs"},
                "example": {},
            },
            "additionalProperties": False,
        },
        "primitivesItems": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "type": {"type": "string", "enum": ["string", "number", "integer", "boolean", "array"]},
                "format": {"type": "string"},
                "items": {"$ref": "#/definitions/primitivesItems"},
                "collectionFormat": {"$ref": "#/definitions/collectionFormat"},
                "default": {"$ref": "#/definitions/default"},
                "maximum": {"$ref": "#/definitions/maximum"},
                "exclusiveMaximum": {"$ref": "#/definitions/exclusiveMaximum"},
                "minimum": {"$ref": "#/definitions/minimum"},
                "exclusiveMinimum": {"$ref": "#/definitions/exclusiveMinimum"},
                "maxLength": {"$ref": "#/definitions/maxLength"},
                "minLength": {"$ref": "#/definitions/minLength"},
                "pattern": {"$ref": "#/definitions/pattern"},
                "maxItems": {"$ref": "#/definitions/maxItems"},
                "minItems": {"$ref": "#/definitions/minItems"},
                "uniqueItems": {"$ref": "#/definitions/uniqueItems"},
                "enum": {"$ref": "#/definitions/enum"},
                "multipleOf": {"$ref": "#/definitions/multipleOf"},
            },
            "patternProperties": {"^x-": {"$ref": "#/definitions/vendorExtension"}},
        },
        "security": {"type": "array", "items": {"$ref": "#/definitions/securityRequirement"}, "uniqueItems": True},
        "securityRequirement": {
            "type": "object",
            "additionalProperties": {"type": "array", "items": {"type": "string"}, "uniqueItems": True},
        },
        "xml": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "name": {"type": "string"},
                "namespace": {"type": "string"},
                "prefix": {"type": "string"},
                "attribute": {"type": "boolean", "default": False},
                "wrapped": {"type": "boolean", "default": False},
            },
            "patternProperties": {"^x-": {"$ref": "#/definitions/vendorExtension"}},
        },
        "tag": {
            "type": "object",
            "additionalProperties": False,
            "required": ["name"],
            "properties": {
                "name": {"type": "string"},
                "description": {"type": "string"},
                "externalDocs": {"$ref": "#/definitions/externalDocs"},
            },
            "patternProperties": {"^x-": {"$ref": "#/definitions/vendorExtension"}},
        },
        "securityDefinitions": {
            "type": "object",
            "additionalProperties": {
                "oneOf": [
                    {"$ref": "#/definitions/basicAuthenticationSecurity"},
                    {"$ref": "#/definitions/apiKeySecurity"},
                    {"$ref": "#/definitions/oauth2ImplicitSecurity"},
                    {"$ref": "#/definitions/oauth2PasswordSecurity"},
                    {"$ref": "#/definitions/oauth2ApplicationSecurity"},
                    {"$ref": "#/definitions/oauth2AccessCodeSecurity"},
                ]
            },
        },
        "basicAuthenticationSecurity": {
            "type": "object",
            "additionalProperties": False,
            "required": ["type"],
            "properties": {"type": {"type": "string", "enum": ["basic"]}, "description": {"type": "string"}},
            "patternProperties": {"^x-": {"$ref": "#/definitions/vendorExtension"}},
        },
        "apiKeySecurity": {
            "type": "object",
            "additionalProperties": False,
            "required": ["type", "name", "in"],
            "properties": {
                "type": {"type": "string", "enum": ["apiKey"]},
                "name": {"type": "string"},
                "in": {"type": "string", "enum": ["header", "query"]},
                "description": {"type": "string"},
            },
            "patternProperties": {"^x-": {"$ref": "#/definitions/vendorExtension"}},
        },
        "oauth2ImplicitSecurity": {
            "type": "object",
            "additionalProperties": False,
            "required": ["type", "flow", "authorizationUrl"],
            "properties": {
                "type": {"type": "string", "enum": ["oauth2"]},
                "flow": {"type": "string", "enum": ["implicit"]},
                "scopes": {"$ref": "#/definitions/oauth2Scopes"},
                "authorizationUrl": {"type": "string", "format": "uri"},
                "description": {"type": "string"},
            },
            "patternProperties": {"^x-": {"$ref": "#/definitions/vendorExtension"}},
        },
        "oauth2PasswordSecurity": {
            "type": "object",
            "additionalProperties": False,
            "required": ["type", "flow", "tokenUrl"],
            "properties": {
                "type": {"type": "string", "enum": ["oauth2"]},
                "flow": {"type": "string", "enum": ["password"]},
                "scopes": {"$ref": "#/definitions/oauth2Scopes"},
                "tokenUrl": {"type": "string", "format": "uri"},
                "description": {"type": "string"},
            },
            "patternProperties": {"^x-": {"$ref": "#/definitions/vendorExtension"}},
        },
        "oauth2ApplicationSecurity": {
            "type": "object",
            "additionalProperties": False,
            "required": ["type", "flow", "tokenUrl"],
            "properties": {
                "type": {"type": "string", "enum": ["oauth2"]},
                "flow": {"type": "string", "enum": ["application"]},
                "scopes": {"$ref": "#/definitions/oauth2Scopes"},
                "tokenUrl": {"type": "string", "format": "uri"},
                "description": {"type": "string"},
            },
            "patternProperties": {"^x-": {"$ref": "#/definitions/vendorExtension"}},
        },
        "oauth2AccessCodeSecurity": {
            "type": "object",
            "additionalProperties": False,
            "required": ["type", "flow", "authorizationUrl", "tokenUrl"],
            "properties": {
                "type": {"type": "string", "enum": ["oauth2"]},
                "flow": {"type": "string", "enum": ["accessCode"]},
                "scopes": {"$ref": "#/definitions/oauth2Scopes"},
                "authorizationUrl": {"type": "string", "format": "uri"},
                "tokenUrl": {"type": "string", "format": "uri"},
                "description": {"type": "string"},
            },
            "patternProperties": {"^x-": {"$ref": "#/definitions/vendorExtension"}},
        },
        "oauth2Scopes": {"type": "object", "additionalProperties": {"type": "string"}},
        "mediaTypeList": {"type": "array", "items": {"$ref": "#/definitions/mimeType"}, "uniqueItems": True},
        "parametersList": {
            "type": "array",
            "description": "The parameters needed to send a valid API call.",
            "additionalItems": False,
            "items": {"oneOf": [{"$ref": "#/definitions/parameter"}, {"$ref": "#/definitions/jsonReference"}]},
            "uniqueItems": True,
        },
        "schemesList": {
            "type": "array",
            "description": "The transfer protocol of the API.",
            "items": {"type": "string", "enum": ["http", "https", "ws", "wss"]},
            "uniqueItems": True,
        },
        "collectionFormat": {"type": "string", "enum": ["csv", "ssv", "tsv", "pipes"], "default": "csv"},
        "collectionFormatWithMulti": {
            "type": "string",
            "enum": ["csv", "ssv", "tsv", "pipes", "multi"],
            "default": "csv",
        },
        "title": {"$ref": "http://json-schema.org/draft-04/schema#/properties/title"},
        "description": {"$ref": "http://json-schema.org/draft-04/schema#/properties/description"},
        "default": {"$ref": "http://json-schema.org/draft-04/schema#/properties/default"},
        "multipleOf": {"$ref": "http://json-schema.org/draft-04/schema#/properties/multipleOf"},
        "maximum": {"$ref": "http://json-schema.org/draft-04/schema#/properties/maximum"},
        "exclusiveMaximum": {"$ref": "http://json-schema.org/draft-04/schema#/properties/exclusiveMaximum"},
        "minimum": {"$ref": "http://json-schema.org/draft-04/schema#/properties/minimum"},
        "exclusiveMinimum": {"$ref": "http://json-schema.org/draft-04/schema#/properties/exclusiveMinimum"},
        "maxLength": {"$ref": "http://json-schema.org/draft-04/schema#/definitions/positiveInteger"},
        "minLength": {"$ref": "http://json-schema.org/draft-04/schema#/definitions/positiveIntegerDefault0"},
        "pattern": {"$ref": "http://json-schema.org/draft-04/schema#/properties/pattern"},
        "maxItems": {"$ref": "http://json-schema.org/draft-04/schema#/definitions/positiveInteger"},
        "minItems": {"$ref": "http://json-schema.org/draft-04/schema#/definitions/positiveIntegerDefault0"},
        "uniqueItems": {"$ref": "http://json-schema.org/draft-04/schema#/properties/uniqueItems"},
        "enum": {"$ref": "http://json-schema.org/draft-04/schema#/properties/enum"},
        "jsonReference": {
            "type": "object",
            "required": ["$ref"],
            "additionalProperties": False,
            "properties": {"$ref": {"type": "string"}},
        },
    },
}
SWAGGER_20_VALIDATOR = jsonschema.validators.validator_for(SWAGGER_20)(SWAGGER_20)
OPENAPI_30 = {
    "id": "https://spec.openapis.org/oas/3.0/schema/2019-04-02",
    "$schema": "http://json-schema.org/draft-04/schema#",
    "description": "Validation schema for OpenAPI Specification 3.0.X.",
    "type": "object",
    "required": ["openapi", "info", "paths"],
    "properties": {
        "openapi": {"type": "string", "pattern": "^3\\.0\\.\\d(-.+)?$"},
        "info": {"$ref": "#/definitions/Info"},
        "externalDocs": {"$ref": "#/definitions/ExternalDocumentation"},
        "servers": {"type": "array", "items": {"$ref": "#/definitions/Server"}},
        "security": {"type": "array", "items": {"$ref": "#/definitions/SecurityRequirement"}},
        "tags": {"type": "array", "items": {"$ref": "#/definitions/Tag"}, "uniqueItems": True},
        "paths": {"$ref": "#/definitions/Paths"},
        "components": {"$ref": "#/definitions/Components"},
    },
    "patternProperties": {"^x-": {}},
    "additionalProperties": False,
    "definitions": {
        "Reference": {
            "type": "object",
            "required": ["$ref"],
            "patternProperties": {"^\\$ref$": {"type": "string", "format": "uri-reference"}},
        },
        "Info": {
            "type": "object",
            "required": ["title", "version"],
            "properties": {
                "title": {"type": "string"},
                "description": {"type": "string"},
                "termsOfService": {"type": "string", "format": "uri-reference"},
                "contact": {"$ref": "#/definitions/Contact"},
                "license": {"$ref": "#/definitions/License"},
                "version": {"type": "string"},
            },
            "patternProperties": {"^x-": {}},
            "additionalProperties": False,
        },
        "Contact": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "url": {"type": "string", "format": "uri-reference"},
                "email": {"type": "string", "format": "email"},
            },
            "patternProperties": {"^x-": {}},
            "additionalProperties": False,
        },
        "License": {
            "type": "object",
            "required": ["name"],
            "properties": {"name": {"type": "string"}, "url": {"type": "string", "format": "uri-reference"}},
            "patternProperties": {"^x-": {}},
            "additionalProperties": False,
        },
        "Server": {
            "type": "object",
            "required": ["url"],
            "properties": {
                "url": {"type": "string"},
                "description": {"type": "string"},
                "variables": {"type": "object", "additionalProperties": {"$ref": "#/definitions/ServerVariable"}},
            },
            "patternProperties": {"^x-": {}},
            "additionalProperties": False,
        },
        "ServerVariable": {
            "type": "object",
            "required": ["default"],
            "properties": {
                "enum": {"type": "array", "items": {"type": "string"}},
                "default": {"type": "string"},
                "description": {"type": "string"},
            },
            "patternProperties": {"^x-": {}},
            "additionalProperties": False,
        },
        "Components": {
            "type": "object",
            "properties": {
                "schemas": {
                    "type": "object",
                    "patternProperties": {
                        "^[a-zA-Z0-9\\.\\-_]+$": {
                            "oneOf": [{"$ref": "#/definitions/Schema"}, {"$ref": "#/definitions/Reference"}]
                        }
                    },
                },
                "responses": {
                    "type": "object",
                    "patternProperties": {
                        "^[a-zA-Z0-9\\.\\-_]+$": {
                            "oneOf": [{"$ref": "#/definitions/Reference"}, {"$ref": "#/definitions/Response"}]
                        }
                    },
                },
                "parameters": {
                    "type": "object",
                    "patternProperties": {
                        "^[a-zA-Z0-9\\.\\-_]+$": {
                            "oneOf": [{"$ref": "#/definitions/Reference"}, {"$ref": "#/definitions/Parameter"}]
                        }
                    },
                },
                "examples": {
                    "type": "object",
                    "patternProperties": {
                        "^[a-zA-Z0-9\\.\\-_]+$": {
                            "oneOf": [{"$ref": "#/definitions/Reference"}, {"$ref": "#/definitions/Example"}]
                        }
                    },
                },
                "requestBodies": {
                    "type": "object",
                    "patternProperties": {
                        "^[a-zA-Z0-9\\.\\-_]+$": {
                            "oneOf": [{"$ref": "#/definitions/Reference"}, {"$ref": "#/definitions/RequestBody"}]
                        }
                    },
                },
                "headers": {
                    "type": "object",
                    "patternProperties": {
                        "^[a-zA-Z0-9\\.\\-_]+$": {
                            "oneOf": [{"$ref": "#/definitions/Reference"}, {"$ref": "#/definitions/Header"}]
                        }
                    },
                },
                "securitySchemes": {
                    "type": "object",
                    "patternProperties": {
                        "^[a-zA-Z0-9\\.\\-_]+$": {
                            "oneOf": [{"$ref": "#/definitions/Reference"}, {"$ref": "#/definitions/SecurityScheme"}]
                        }
                    },
                },
                "links": {
                    "type": "object",
                    "patternProperties": {
                        "^[a-zA-Z0-9\\.\\-_]+$": {
                            "oneOf": [{"$ref": "#/definitions/Reference"}, {"$ref": "#/definitions/Link"}]
                        }
                    },
                },
                "callbacks": {
                    "type": "object",
                    "patternProperties": {
                        "^[a-zA-Z0-9\\.\\-_]+$": {
                            "oneOf": [{"$ref": "#/definitions/Reference"}, {"$ref": "#/definitions/Callback"}]
                        }
                    },
                },
            },
            "patternProperties": {"^x-": {}},
            "additionalProperties": False,
        },
        "Schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "multipleOf": {"type": "number", "minimum": 0, "exclusiveMinimum": True},
                "maximum": {"type": "number"},
                "exclusiveMaximum": {"type": "boolean", "default": False},
                "minimum": {"type": "number"},
                "exclusiveMinimum": {"type": "boolean", "default": False},
                "maxLength": {"type": "integer", "minimum": 0},
                "minLength": {"type": "integer", "minimum": 0, "default": 0},
                "pattern": {"type": "string", "format": "regex"},
                "maxItems": {"type": "integer", "minimum": 0},
                "minItems": {"type": "integer", "minimum": 0, "default": 0},
                "uniqueItems": {"type": "boolean", "default": False},
                "maxProperties": {"type": "integer", "minimum": 0},
                "minProperties": {"type": "integer", "minimum": 0, "default": 0},
                "required": {"type": "array", "items": {"type": "string"}, "minItems": 1, "uniqueItems": True},
                "enum": {"type": "array", "items": {}, "minItems": 1, "uniqueItems": False},
                "type": {"type": "string", "enum": ["array", "boolean", "integer", "number", "object", "string"]},
                "not": {"oneOf": [{"$ref": "#/definitions/Schema"}, {"$ref": "#/definitions/Reference"}]},
                "allOf": {
                    "type": "array",
                    "items": {"oneOf": [{"$ref": "#/definitions/Schema"}, {"$ref": "#/definitions/Reference"}]},
                },
                "oneOf": {
                    "type": "array",
                    "items": {"oneOf": [{"$ref": "#/definitions/Schema"}, {"$ref": "#/definitions/Reference"}]},
                },
                "anyOf": {
                    "type": "array",
                    "items": {"oneOf": [{"$ref": "#/definitions/Schema"}, {"$ref": "#/definitions/Reference"}]},
                },
                "items": {"oneOf": [{"$ref": "#/definitions/Schema"}, {"$ref": "#/definitions/Reference"}]},
                "properties": {
                    "type": "object",
                    "additionalProperties": {
                        "oneOf": [{"$ref": "#/definitions/Schema"}, {"$ref": "#/definitions/Reference"}]
                    },
                },
                "additionalProperties": {
                    "oneOf": [
                        {"$ref": "#/definitions/Schema"},
                        {"$ref": "#/definitions/Reference"},
                        {"type": "boolean"},
                    ],
                    "default": True,
                },
                "description": {"type": "string"},
                "format": {"type": "string"},
                "default": {},
                "nullable": {"type": "boolean", "default": False},
                "discriminator": {"$ref": "#/definitions/Discriminator"},
                "readOnly": {"type": "boolean", "default": False},
                "writeOnly": {"type": "boolean", "default": False},
                "example": {},
                "externalDocs": {"$ref": "#/definitions/ExternalDocumentation"},
                "deprecated": {"type": "boolean", "default": False},
                "xml": {"$ref": "#/definitions/XML"},
            },
            "patternProperties": {"^x-": {}},
            "additionalProperties": False,
        },
        "Discriminator": {
            "type": "object",
            "required": ["propertyName"],
            "properties": {
                "propertyName": {"type": "string"},
                "mapping": {"type": "object", "additionalProperties": {"type": "string"}},
            },
        },
        "XML": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "namespace": {"type": "string", "format": "uri"},
                "prefix": {"type": "string"},
                "attribute": {"type": "boolean", "default": False},
                "wrapped": {"type": "boolean", "default": False},
            },
            "patternProperties": {"^x-": {}},
            "additionalProperties": False,
        },
        "Response": {
            "type": "object",
            "required": ["description"],
            "properties": {
                "description": {"type": "string"},
                "headers": {
                    "type": "object",
                    "additionalProperties": {
                        "oneOf": [{"$ref": "#/definitions/Header"}, {"$ref": "#/definitions/Reference"}]
                    },
                },
                "content": {"type": "object", "additionalProperties": {"$ref": "#/definitions/MediaType"}},
                "links": {
                    "type": "object",
                    "additionalProperties": {
                        "oneOf": [{"$ref": "#/definitions/Link"}, {"$ref": "#/definitions/Reference"}]
                    },
                },
            },
            "patternProperties": {"^x-": {}},
            "additionalProperties": False,
        },
        "MediaType": {
            "type": "object",
            "properties": {
                "schema": {"oneOf": [{"$ref": "#/definitions/Schema"}, {"$ref": "#/definitions/Reference"}]},
                "example": {},
                "examples": {
                    "type": "object",
                    "additionalProperties": {
                        "oneOf": [{"$ref": "#/definitions/Example"}, {"$ref": "#/definitions/Reference"}]
                    },
                },
                "encoding": {"type": "object", "additionalProperties": {"$ref": "#/definitions/Encoding"}},
            },
            "patternProperties": {"^x-": {}},
            "additionalProperties": False,
            "allOf": [{"$ref": "#/definitions/ExampleXORExamples"}],
        },
        "Example": {
            "type": "object",
            "properties": {
                "summary": {"type": "string"},
                "description": {"type": "string"},
                "value": {},
                "externalValue": {"type": "string", "format": "uri-reference"},
            },
            "patternProperties": {"^x-": {}},
            "additionalProperties": False,
        },
        "Header": {
            "type": "object",
            "properties": {
                "description": {"type": "string"},
                "required": {"type": "boolean", "default": False},
                "deprecated": {"type": "boolean", "default": False},
                "allowEmptyValue": {"type": "boolean", "default": False},
                "style": {"type": "string", "enum": ["simple"], "default": "simple"},
                "explode": {"type": "boolean"},
                "allowReserved": {"type": "boolean", "default": False},
                "schema": {"oneOf": [{"$ref": "#/definitions/Schema"}, {"$ref": "#/definitions/Reference"}]},
                "content": {
                    "type": "object",
                    "additionalProperties": {"$ref": "#/definitions/MediaType"},
                    "minProperties": 1,
                    "maxProperties": 1,
                },
                "example": {},
                "examples": {
                    "type": "object",
                    "additionalProperties": {
                        "oneOf": [{"$ref": "#/definitions/Example"}, {"$ref": "#/definitions/Reference"}]
                    },
                },
            },
            "patternProperties": {"^x-": {}},
            "additionalProperties": False,
            "allOf": [{"$ref": "#/definitions/ExampleXORExamples"}, {"$ref": "#/definitions/SchemaXORContent"}],
        },
        "Paths": {
            "type": "object",
            "patternProperties": {"^\\/": {"$ref": "#/definitions/PathItem"}, "^x-": {}},
            "additionalProperties": False,
        },
        "PathItem": {
            "type": "object",
            "properties": {
                "$ref": {"type": "string"},
                "summary": {"type": "string"},
                "description": {"type": "string"},
                "servers": {"type": "array", "items": {"$ref": "#/definitions/Server"}},
                "parameters": {
                    "type": "array",
                    "items": {"oneOf": [{"$ref": "#/definitions/Parameter"}, {"$ref": "#/definitions/Reference"}]},
                    "uniqueItems": True,
                },
            },
            "patternProperties": {
                "^(get|put|post|delete|options|head|patch|trace)$": {"$ref": "#/definitions/Operation"},
                "^x-": {},
            },
            "additionalProperties": False,
        },
        "Operation": {
            "type": "object",
            "required": ["responses"],
            "properties": {
                "tags": {"type": "array", "items": {"type": "string"}},
                "summary": {"type": "string"},
                "description": {"type": "string"},
                "externalDocs": {"$ref": "#/definitions/ExternalDocumentation"},
                "operationId": {"type": "string"},
                "parameters": {
                    "type": "array",
                    "items": {"oneOf": [{"$ref": "#/definitions/Parameter"}, {"$ref": "#/definitions/Reference"}]},
                    "uniqueItems": True,
                },
                "requestBody": {"oneOf": [{"$ref": "#/definitions/RequestBody"}, {"$ref": "#/definitions/Reference"}]},
                "responses": {"$ref": "#/definitions/Responses"},
                "callbacks": {
                    "type": "object",
                    "additionalProperties": {
                        "oneOf": [{"$ref": "#/definitions/Callback"}, {"$ref": "#/definitions/Reference"}]
                    },
                },
                "deprecated": {"type": "boolean", "default": False},
                "security": {"type": "array", "items": {"$ref": "#/definitions/SecurityRequirement"}},
                "servers": {"type": "array", "items": {"$ref": "#/definitions/Server"}},
            },
            "patternProperties": {"^x-": {}},
            "additionalProperties": False,
        },
        "Responses": {
            "type": "object",
            "properties": {
                "default": {"oneOf": [{"$ref": "#/definitions/Response"}, {"$ref": "#/definitions/Reference"}]}
            },
            "patternProperties": {
                "^[1-5](?:\\d{2}|XX)$": {
                    "oneOf": [{"$ref": "#/definitions/Response"}, {"$ref": "#/definitions/Reference"}]
                },
                "^x-": {},
            },
            "minProperties": 1,
            "additionalProperties": False,
        },
        "SecurityRequirement": {
            "type": "object",
            "additionalProperties": {"type": "array", "items": {"type": "string"}},
        },
        "Tag": {
            "type": "object",
            "required": ["name"],
            "properties": {
                "name": {"type": "string"},
                "description": {"type": "string"},
                "externalDocs": {"$ref": "#/definitions/ExternalDocumentation"},
            },
            "patternProperties": {"^x-": {}},
            "additionalProperties": False,
        },
        "ExternalDocumentation": {
            "type": "object",
            "required": ["url"],
            "properties": {"description": {"type": "string"}, "url": {"type": "string", "format": "uri-reference"}},
            "patternProperties": {"^x-": {}},
            "additionalProperties": False,
        },
        "ExampleXORExamples": {
            "description": "Example and examples are mutually exclusive",
            "not": {"required": ["example", "examples"]},
        },
        "SchemaXORContent": {
            "description": "Schema and content are mutually exclusive, at least one is required",
            "not": {"required": ["schema", "content"]},
            "oneOf": [
                {"required": ["schema"]},
                {
                    "required": ["content"],
                    "description": "Some properties are not allowed if content is present",
                    "allOf": [
                        {"not": {"required": ["style"]}},
                        {"not": {"required": ["explode"]}},
                        {"not": {"required": ["allowReserved"]}},
                        {"not": {"required": ["example"]}},
                        {"not": {"required": ["examples"]}},
                    ],
                },
            ],
        },
        "Parameter": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "in": {"type": "string"},
                "description": {"type": "string"},
                "required": {"type": "boolean", "default": False},
                "deprecated": {"type": "boolean", "default": False},
                "allowEmptyValue": {"type": "boolean", "default": False},
                "style": {"type": "string"},
                "explode": {"type": "boolean"},
                "allowReserved": {"type": "boolean", "default": False},
                "schema": {"oneOf": [{"$ref": "#/definitions/Schema"}, {"$ref": "#/definitions/Reference"}]},
                "content": {
                    "type": "object",
                    "additionalProperties": {"$ref": "#/definitions/MediaType"},
                    "minProperties": 1,
                    "maxProperties": 1,
                },
                "example": {},
                "examples": {
                    "type": "object",
                    "additionalProperties": {
                        "oneOf": [{"$ref": "#/definitions/Example"}, {"$ref": "#/definitions/Reference"}]
                    },
                },
            },
            "patternProperties": {"^x-": {}},
            "additionalProperties": False,
            "required": ["name", "in"],
            "allOf": [
                {"$ref": "#/definitions/ExampleXORExamples"},
                {"$ref": "#/definitions/SchemaXORContent"},
                {"$ref": "#/definitions/ParameterLocation"},
            ],
        },
        "ParameterLocation": {
            "description": "Parameter location",
            "oneOf": [
                {
                    "description": "Parameter in path",
                    "required": ["required"],
                    "properties": {
                        "in": {"enum": ["path"]},
                        "style": {"enum": ["matrix", "label", "simple"], "default": "simple"},
                        "required": {"enum": [True]},
                    },
                },
                {
                    "description": "Parameter in query",
                    "properties": {
                        "in": {"enum": ["query"]},
                        "style": {"enum": ["form", "spaceDelimited", "pipeDelimited", "deepObject"], "default": "form"},
                    },
                },
                {
                    "description": "Parameter in header",
                    "properties": {"in": {"enum": ["header"]}, "style": {"enum": ["simple"], "default": "simple"}},
                },
                {
                    "description": "Parameter in cookie",
                    "properties": {"in": {"enum": ["cookie"]}, "style": {"enum": ["form"], "default": "form"}},
                },
            ],
        },
        "RequestBody": {
            "type": "object",
            "required": ["content"],
            "properties": {
                "description": {"type": "string"},
                "content": {"type": "object", "additionalProperties": {"$ref": "#/definitions/MediaType"}},
                "required": {"type": "boolean", "default": False},
            },
            "patternProperties": {"^x-": {}},
            "additionalProperties": False,
        },
        "SecurityScheme": {
            "oneOf": [
                {"$ref": "#/definitions/APIKeySecurityScheme"},
                {"$ref": "#/definitions/HTTPSecurityScheme"},
                {"$ref": "#/definitions/OAuth2SecurityScheme"},
                {"$ref": "#/definitions/OpenIdConnectSecurityScheme"},
            ]
        },
        "APIKeySecurityScheme": {
            "type": "object",
            "required": ["type", "name", "in"],
            "properties": {
                "type": {"type": "string", "enum": ["apiKey"]},
                "name": {"type": "string"},
                "in": {"type": "string", "enum": ["header", "query", "cookie"]},
                "description": {"type": "string"},
            },
            "patternProperties": {"^x-": {}},
            "additionalProperties": False,
        },
        "HTTPSecurityScheme": {
            "type": "object",
            "required": ["scheme", "type"],
            "properties": {
                "scheme": {"type": "string"},
                "bearerFormat": {"type": "string"},
                "description": {"type": "string"},
                "type": {"type": "string", "enum": ["http"]},
            },
            "patternProperties": {"^x-": {}},
            "additionalProperties": False,
            "oneOf": [
                {"description": "Bearer", "properties": {"scheme": {"enum": ["bearer"]}}},
                {
                    "description": "Non Bearer",
                    "not": {"required": ["bearerFormat"]},
                    "properties": {"scheme": {"not": {"enum": ["bearer"]}}},
                },
            ],
        },
        "OAuth2SecurityScheme": {
            "type": "object",
            "required": ["type", "flows"],
            "properties": {
                "type": {"type": "string", "enum": ["oauth2"]},
                "flows": {"$ref": "#/definitions/OAuthFlows"},
                "description": {"type": "string"},
            },
            "patternProperties": {"^x-": {}},
            "additionalProperties": False,
        },
        "OpenIdConnectSecurityScheme": {
            "type": "object",
            "required": ["type", "openIdConnectUrl"],
            "properties": {
                "type": {"type": "string", "enum": ["openIdConnect"]},
                "openIdConnectUrl": {"type": "string", "format": "uri-reference"},
                "description": {"type": "string"},
            },
            "patternProperties": {"^x-": {}},
            "additionalProperties": False,
        },
        "OAuthFlows": {
            "type": "object",
            "properties": {
                "implicit": {"$ref": "#/definitions/ImplicitOAuthFlow"},
                "password": {"$ref": "#/definitions/PasswordOAuthFlow"},
                "clientCredentials": {"$ref": "#/definitions/ClientCredentialsFlow"},
                "authorizationCode": {"$ref": "#/definitions/AuthorizationCodeOAuthFlow"},
            },
            "patternProperties": {"^x-": {}},
            "additionalProperties": False,
        },
        "ImplicitOAuthFlow": {
            "type": "object",
            "required": ["authorizationUrl", "scopes"],
            "properties": {
                "authorizationUrl": {"type": "string", "format": "uri-reference"},
                "refreshUrl": {"type": "string", "format": "uri-reference"},
                "scopes": {"type": "object", "additionalProperties": {"type": "string"}},
            },
            "patternProperties": {"^x-": {}},
            "additionalProperties": False,
        },
        "PasswordOAuthFlow": {
            "type": "object",
            "required": ["tokenUrl"],
            "properties": {
                "tokenUrl": {"type": "string", "format": "uri-reference"},
                "refreshUrl": {"type": "string", "format": "uri-reference"},
                "scopes": {"type": "object", "additionalProperties": {"type": "string"}},
            },
            "patternProperties": {"^x-": {}},
            "additionalProperties": False,
        },
        "ClientCredentialsFlow": {
            "type": "object",
            "required": ["tokenUrl"],
            "properties": {
                "tokenUrl": {"type": "string", "format": "uri-reference"},
                "refreshUrl": {"type": "string", "format": "uri-reference"},
                "scopes": {"type": "object", "additionalProperties": {"type": "string"}},
            },
            "patternProperties": {"^x-": {}},
            "additionalProperties": False,
        },
        "AuthorizationCodeOAuthFlow": {
            "type": "object",
            "required": ["authorizationUrl", "tokenUrl"],
            "properties": {
                "authorizationUrl": {"type": "string", "format": "uri-reference"},
                "tokenUrl": {"type": "string", "format": "uri-reference"},
                "refreshUrl": {"type": "string", "format": "uri-reference"},
                "scopes": {"type": "object", "additionalProperties": {"type": "string"}},
            },
            "patternProperties": {"^x-": {}},
            "additionalProperties": False,
        },
        "Link": {
            "type": "object",
            "properties": {
                "operationId": {"type": "string"},
                "operationRef": {"type": "string", "format": "uri-reference"},
                "parameters": {"type": "object", "additionalProperties": {}},
                "requestBody": {},
                "description": {"type": "string"},
                "server": {"$ref": "#/definitions/Server"},
            },
            "patternProperties": {"^x-": {}},
            "additionalProperties": False,
            "not": {
                "description": "Operation Id and Operation Ref are mutually exclusive",
                "required": ["operationId", "operationRef"],
            },
        },
        "Callback": {
            "type": "object",
            "additionalProperties": {"$ref": "#/definitions/PathItem"},
            "patternProperties": {"^x-": {}},
        },
        "Encoding": {
            "type": "object",
            "properties": {
                "contentType": {"type": "string"},
                "headers": {"type": "object", "additionalProperties": {"$ref": "#/definitions/Header"}},
                "style": {"type": "string", "enum": ["form", "spaceDelimited", "pipeDelimited", "deepObject"]},
                "explode": {"type": "boolean"},
                "allowReserved": {"type": "boolean", "default": False},
            },
            "additionalProperties": False,
        },
    },
}
OPENAPI_30_VALIDATOR = jsonschema.validators.validator_for(OPENAPI_30)(OPENAPI_30)
