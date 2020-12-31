from schemathesis.specs.openapi.references import InliningResolver
from schemathesis.specs.openapi.security import OpenAPISecurityProcessor


def test_ref_resolving():
    http_schema = {"basic_auth": {"type": "http", "scheme": "basic"}}
    schema = {
        "openapi": "3.0.0",
        "info": {"title": "Blank API", "version": "1.0"},
        "servers": [{"url": "http://localhost/api"}],
        "paths": {"foo": {"get": {"responses": {"200": {"description": "OK"}}}}},
        "components": {"securitySchemes": {"$ref": "#/components/HTTPSchema"}, "HTTPSchema": http_schema},
    }
    resolver = InliningResolver("", schema)
    assert OpenAPISecurityProcessor().get_security_definitions(schema, resolver) == http_schema
