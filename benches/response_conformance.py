import pytest
import requests

import schemathesis
from schemathesis.checks import CheckContext
from schemathesis.config._checks import ChecksConfig
from schemathesis.specs.openapi.checks import response_headers_conformance, response_schema_conformance

RESPONSE_CONFORMANCE_SCHEMA = {
    "openapi": "3.0.2",
    "info": {"title": "Response Conformance Test", "version": "1.0.0"},
    "paths": {
        "/simple": {
            "get": {
                "responses": {
                    "200": {
                        "description": "Simple response",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"id": {"type": "integer"}, "message": {"type": "string"}},
                                    "required": ["id", "message"],
                                }
                            }
                        },
                    }
                }
            }
        },
        "/medium": {
            "get": {
                "responses": {
                    "200": {
                        "description": "Medium complexity response",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "id": {"type": "integer"},
                                        "user": {"$ref": "#/components/schemas/User"},
                                        "timestamp": {"type": "string", "format": "date-time"},
                                    },
                                    "required": ["id", "user", "timestamp"],
                                }
                            }
                        },
                    }
                }
            }
        },
        "/complex": {
            "get": {
                "responses": {
                    "200": {
                        "description": "Complex response with multiple refs",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "id": {"type": "integer"},
                                        "user": {"$ref": "#/components/schemas/User"},
                                        "address": {"$ref": "#/components/schemas/Address"},
                                        "preferences": {"$ref": "#/components/schemas/Preferences"},
                                        "metadata": {"$ref": "#/components/schemas/Metadata"},
                                        "status": {"type": "string", "enum": ["active", "inactive", "pending"]},
                                        "created_at": {"type": "string", "format": "date-time"},
                                        "updated_at": {"type": "string", "format": "date-time"},
                                    },
                                    "required": ["id", "user", "address", "status", "created_at", "updated_at"],
                                }
                            }
                        },
                    }
                }
            }
        },
        "/simple-headers": {
            "get": {
                "responses": {
                    "200": {
                        "headers": {
                            "X-Request-ID": {
                                "required": True,
                                "schema": {
                                    "type": "string",
                                    "pattern": "^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$",
                                },
                            }
                        },
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"status": {"type": "string"}},
                                    "required": ["status"],
                                }
                            }
                        },
                    }
                }
            }
        },
        "/complex-headers": {
            "get": {
                "responses": {
                    "200": {
                        "headers": {
                            "X-Request-ID": {
                                "required": True,
                                "schema": {
                                    "type": "string",
                                    "pattern": "^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$",
                                },
                            },
                            "X-Rate-Limit-Remaining": {
                                "required": True,
                                "schema": {"type": "integer", "minimum": 0, "maximum": 10000},
                            },
                            "X-API-Version": {
                                "required": False,
                                "schema": {"type": "string", "enum": ["v1", "v2", "v3"]},
                            },
                            "X-Response-Time": {
                                "required": False,
                                "schema": {"type": "number", "minimum": 0, "multipleOf": 0.001},
                            },
                            "X-Cache-Status": {
                                "required": False,
                                "schema": {"type": "string", "enum": ["hit", "miss", "bypass"]},
                            },
                        },
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"status": {"type": "string"}},
                                    "required": ["status"],
                                }
                            }
                        },
                    }
                }
            }
        },
    },
    "components": {
        "schemas": {
            "User": {
                "type": "object",
                "properties": {"name": {"type": "string"}, "email": {"type": "string", "format": "email"}},
                "required": ["name", "email"],
            },
            "Address": {
                "type": "object",
                "properties": {
                    "street": {"type": "string"},
                    "city": {"type": "string"},
                    "country": {"type": "string", "minLength": 2, "maxLength": 2},
                },
                "required": ["street", "city", "country"],
            },
            "Preferences": {
                "type": "object",
                "properties": {
                    "theme": {"type": "string", "enum": ["light", "dark"]},
                    "notifications": {"type": "boolean"},
                    "language": {"type": "string", "pattern": "^[a-z]{2}$"},
                },
                "required": ["theme", "notifications"],
            },
            "Metadata": {
                "type": "object",
                "properties": {
                    "source": {"type": "string"},
                    "version": {"type": "string"},
                    "tags": {"type": "array", "items": {"type": "string"}, "maxItems": 10},
                },
            },
        }
    },
}

CONFIG = schemathesis.Config()
CONFORMANCE_TEST_SCHEMA = schemathesis.openapi.from_dict(RESPONSE_CONFORMANCE_SCHEMA, config=CONFIG)

SIMPLE_CASE = CONFORMANCE_TEST_SCHEMA["/simple"]["get"].Case()
SIMPLE_RESPONSE = schemathesis.Response(
    status_code=200,
    headers={"Content-Type": ["application/json"]},
    content=b'{"id": 12345, "message": "Operation successful"}',
    request=requests.Request(method="GET", url="http://127.0.0.1/simple").prepare(),
    elapsed=0.1,
    verify=False,
)

MEDIUM_CASE = CONFORMANCE_TEST_SCHEMA["/medium"]["get"].Case()
MEDIUM_RESPONSE = schemathesis.Response(
    status_code=200,
    headers={"Content-Type": ["application/json"]},
    content=b"""{"id": 12345, "user": {"name": "John Doe", "email": "john@example.com"}, "timestamp": "2023-12-01T10:30:00Z"}""",
    request=requests.Request(method="GET", url="http://127.0.0.1/medium").prepare(),
    elapsed=0.1,
    verify=False,
)

COMPLEX_CASE = CONFORMANCE_TEST_SCHEMA["/complex"]["get"].Case()
COMPLEX_RESPONSE = schemathesis.Response(
    status_code=200,
    headers={"Content-Type": ["application/json"]},
    content=b"""{"id": 12345, "user": {"name": "John Doe", "email": "john@example.com"}, "address": {"street": "123 Main St", "city": "San Francisco", "country": "US"}, "preferences": {"theme": "dark", "notifications": true, "language": "en"}, "metadata": {"source": "api", "version": "1.2.3", "tags": ["important", "user-data"]}, "status": "active", "created_at": "2023-01-15T09:00:00Z", "updated_at": "2023-12-01T10:30:00Z"}""",
    request=requests.Request(method="GET", url="http://127.0.0.1/complex").prepare(),
    elapsed=0.1,
    verify=False,
)
SIMPLE_HEADERS_CASE = CONFORMANCE_TEST_SCHEMA["/simple-headers"]["get"].Case()
SIMPLE_HEADERS_RESPONSE = schemathesis.Response(
    status_code=200,
    headers={"Content-Type": ["application/json"], "X-Request-ID": ["a1b2c3d4-e5f6-7890-abcd-ef1234567890"]},
    content=b'{"status": "success"}',
    request=requests.Request(method="GET", url="http://127.0.0.1/simple-headers").prepare(),
    elapsed=0.1,
    verify=False,
)

COMPLEX_HEADERS_CASE = CONFORMANCE_TEST_SCHEMA["/complex-headers"]["get"].Case()
COMPLEX_HEADERS_RESPONSE = schemathesis.Response(
    status_code=200,
    headers={
        "Content-Type": ["application/json"],
        "X-Request-ID": ["a1b2c3d4-e5f6-7890-abcd-ef1234567890"],
        "X-Rate-Limit-Remaining": ["9500"],
        "X-API-Version": ["v2"],
        "X-Response-Time": ["0.125"],
        "X-Cache-Status": ["hit"],
    },
    content=b'{"status": "success"}',
    request=requests.Request(method="GET", url="http://127.0.0.1/complex-headers").prepare(),
    elapsed=0.1,
    verify=False,
)

CTX = CheckContext(override=None, auth=None, headers=None, config=ChecksConfig(), transport_kwargs=None)

assert response_schema_conformance(CTX, SIMPLE_RESPONSE, SIMPLE_CASE) is None
assert response_schema_conformance(CTX, MEDIUM_RESPONSE, MEDIUM_CASE) is None
assert response_schema_conformance(CTX, COMPLEX_RESPONSE, COMPLEX_CASE) is None
assert response_headers_conformance(CTX, SIMPLE_HEADERS_RESPONSE, SIMPLE_HEADERS_CASE) is None
assert response_headers_conformance(CTX, COMPLEX_HEADERS_RESPONSE, COMPLEX_HEADERS_CASE) is None


@pytest.mark.benchmark(group="simple")
def test_response_conformance_simple(benchmark):
    benchmark(response_schema_conformance, CTX, SIMPLE_RESPONSE, SIMPLE_CASE)


@pytest.mark.benchmark(group="medium")
def test_response_conformance_medium(benchmark):
    benchmark(response_schema_conformance, CTX, MEDIUM_RESPONSE, MEDIUM_CASE)


@pytest.mark.benchmark(group="complex")
def test_response_conformance_complex(benchmark):
    benchmark(response_schema_conformance, CTX, COMPLEX_RESPONSE, COMPLEX_CASE)


@pytest.mark.benchmark(group="simple-headers")
def test_response_headers_conformance_simple(benchmark):
    benchmark(response_headers_conformance, CTX, SIMPLE_HEADERS_RESPONSE, SIMPLE_HEADERS_CASE)


@pytest.mark.benchmark(group="complex-headers")
def test_response_headers_conformance_complex(benchmark):
    benchmark(response_headers_conformance, CTX, COMPLEX_HEADERS_RESPONSE, COMPLEX_HEADERS_CASE)
