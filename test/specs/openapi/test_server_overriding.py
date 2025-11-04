"""Tests for OpenAPI server overriding at different levels (Issue #603)."""

import pytest

import schemathesis


@pytest.fixture
def schema_with_server_overrides():
    """OpenAPI 3.0 schema with server overrides at all levels."""
    return {
        "openapi": "3.0.0",
        "info": {"title": "Test API", "version": "1.0.0"},
        "servers": [
            {"url": "https://api.example.com/v1"},
        ],
        "paths": {
            "/users": {
                "servers": [
                    {"url": "https://users-api.example.com/v1"},
                ],
                "get": {
                    "responses": {"200": {"description": "Success"}},
                },
                "post": {
                    "servers": [
                        {"url": "https://write-api.example.com/v1"},
                    ],
                    "responses": {"201": {"description": "Created"}},
                },
            },
            "/products": {
                "get": {
                    "responses": {"200": {"description": "Success"}},
                },
            },
        },
    }


@pytest.fixture
def schema_with_server_variables():
    """OpenAPI 3.0 schema with server variables."""
    return {
        "openapi": "3.0.0",
        "info": {"title": "Test API", "version": "1.0.0"},
        "servers": [
            {
                "url": "https://{env}.example.com/v1",
                "variables": {
                    "env": {
                        "default": "api",
                        "enum": ["api", "staging", "production"],
                    }
                },
            },
        ],
        "paths": {
            "/users": {
                "servers": [
                    {
                        "url": "https://{region}.users.example.com/{version}",
                        "variables": {
                            "region": {"default": "us-east"},
                            "version": {"default": "v2"},
                        },
                    },
                ],
                "get": {
                    "responses": {"200": {"description": "Success"}},
                },
            },
        },
    }


def test_operation_level_server_override(schema_with_server_overrides):
    """Test that operation-level servers override path and schema servers."""
    schema = schemathesis.openapi.from_dict(schema_with_server_overrides)

    # POST /users has operation-level server
    operation = schema["/users"]["POST"]
    assert operation.base_url == "https://write-api.example.com/v1"


def test_path_level_server_override(schema_with_server_overrides):
    """Test that path-level servers override schema-level servers."""
    schema = schemathesis.openapi.from_dict(schema_with_server_overrides)

    # GET /users has path-level server (no operation-level override)
    operation = schema["/users"]["GET"]
    assert operation.base_url == "https://users-api.example.com/v1"


def test_schema_level_server_fallback(schema_with_server_overrides):
    """Test that schema-level servers are used when no override exists."""
    schema = schemathesis.openapi.from_dict(schema_with_server_overrides)

    # GET /products has no path or operation-level servers
    operation = schema["/products"]["GET"]
    assert operation.base_url == "https://api.example.com/v1"


def test_server_precedence_hierarchy(schema_with_server_overrides):
    """Test the complete precedence: operation > path > schema."""
    from schemathesis.core.result import Ok

    schema = schemathesis.openapi.from_dict(schema_with_server_overrides)

    operations = list(schema.get_all_operations())
    # Check that all operations were successfully parsed
    assert all(isinstance(result, Ok) for result in operations)

    # Extract successful operations
    ops = {f"{result.ok().method} {result.ok().path}": result.ok().base_url for result in operations}

    assert ops["get /users"] == "https://users-api.example.com/v1"  # Path override
    assert ops["post /users"] == "https://write-api.example.com/v1"  # Operation override
    assert ops["get /products"] == "https://api.example.com/v1"  # Schema default


def test_server_variable_substitution_at_path_level(schema_with_server_variables):
    """Test that server variables are substituted correctly at path level."""
    schema = schemathesis.openapi.from_dict(schema_with_server_variables)

    operation = schema["/users"]["GET"]
    # Should use defaults: region=us-east, version=v2
    assert operation.base_url == "https://us-east.users.example.com/v2"


def test_server_variable_substitution_at_schema_level(schema_with_server_variables):
    """Test that server variables are substituted correctly at schema level."""
    # Add a path that doesn't override servers to test schema-level defaults
    schema_with_server_variables["paths"]["/default"] = {
        "get": {"responses": {"200": {"description": "Success"}}}
    }
    schema = schemathesis.openapi.from_dict(schema_with_server_variables)

    # Operation without server override should use schema-level server with variable substitution
    operation = schema["/default"]["GET"]
    assert operation.base_url == "https://api.example.com/v1"


def test_empty_servers_array_uses_schema_default():
    """Test that empty servers array falls back to schema-level servers."""
    schema_dict = {
        "openapi": "3.0.0",
        "info": {"title": "Test API", "version": "1.0.0"},
        "servers": [{"url": "https://api.example.com"}],
        "paths": {
            "/users": {
                "servers": [],  # Empty array
                "get": {"responses": {"200": {"description": "Success"}}},
            },
        },
    }
    schema = schemathesis.openapi.from_dict(schema_dict)
    operation = schema["/users"]["GET"]

    # Should fall back to schema-level server
    assert operation.base_url == "https://api.example.com"


def test_config_base_url_overrides_servers():
    """Test that config.base_url still takes precedence (backward compatibility)."""
    schema_dict = {
        "openapi": "3.0.0",
        "info": {"title": "Test API", "version": "1.0.0"},
        "servers": [{"url": "https://api.example.com"}],
        "paths": {
            "/users": {
                "servers": [{"url": "https://override.example.com"}],
                "get": {"responses": {"200": {"description": "Success"}}},
            },
        },
    }
    # When base_url is provided via config before accessing operations, it should override everything
    schema = schemathesis.openapi.from_dict(schema_dict)
    schema.config.update(base_url="https://config-override.example.com")
    # Access operation AFTER setting config
    operation = schema["/users"]["GET"]

    # Config base_url takes precedence over server definitions
    assert operation.base_url == "https://config-override.example.com"


def test_openapi_31_server_overriding():
    """Test that server overriding works with OpenAPI 3.1."""
    schema_dict = {
        "openapi": "3.1.0",
        "info": {"title": "Test API", "version": "1.0.0"},
        "servers": [{"url": "https://api.example.com"}],
        "paths": {
            "/users": {
                "get": {
                    "servers": [{"url": "https://users.example.com"}],
                    "responses": {"200": {"description": "Success"}},
                },
            },
        },
    }
    schema = schemathesis.openapi.from_dict(schema_dict)
    operation = schema["/users"]["GET"]

    assert operation.base_url == "https://users.example.com"


def test_multiple_servers_uses_first():
    """Test that when multiple servers are defined, the first one is used."""
    schema_dict = {
        "openapi": "3.0.0",
        "info": {"title": "Test API", "version": "1.0.0"},
        "servers": [
            {"url": "https://primary.example.com"},
            {"url": "https://secondary.example.com"},
        ],
        "paths": {
            "/users": {
                "get": {
                    "servers": [
                        {"url": "https://first.example.com"},
                        {"url": "https://second.example.com"},
                    ],
                    "responses": {"200": {"description": "Success"}},
                },
            },
        },
    }
    schema = schemathesis.openapi.from_dict(schema_dict)
    operation = schema["/users"]["GET"]

    # Should use the first operation-level server
    assert operation.base_url == "https://first.example.com"


def test_no_servers_defined():
    """Test behavior when no servers are defined at any level."""
    schema_dict = {
        "openapi": "3.0.0",
        "info": {"title": "Test API", "version": "1.0.0"},
        "paths": {
            "/users": {
                "get": {"responses": {"200": {"description": "Success"}}},
            },
        },
    }
    schema = schemathesis.openapi.from_dict(schema_dict)
    operation = schema["/users"]["GET"]

    # Should use default "/"
    assert operation.base_url == "/"


def test_location_based_url_precedence():
    """Test that schema.location takes precedence over schema servers.

    When a schema is loaded from a URL (e.g., from_url), the host/port from that
    URL should be used instead of schema-level servers. This is critical for test
    fixtures that serve schemas with production server URLs but expect requests
    to go to the test server.
    """
    import tempfile
    import yaml

    schema_dict = {
        "openapi": "3.0.0",
        "info": {"title": "Test API", "version": "1.0.0"},
        "servers": [{"url": "https://production.example.com/api"}],
        "paths": {
            "/users": {
                "get": {"responses": {"200": {"description": "Success"}}},
            },
        },
    }

    # Write schema to a temporary file and load it
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        yaml.dump(schema_dict, f)
        temp_path = f.name

    try:
        # Load from path - sets schema.location
        schema = schemathesis.openapi.from_path(temp_path)
        # Manually set location to simulate from_url behavior
        schema.location = "http://localhost:8080/schema.yaml"

        operation = schema["/users"]["GET"]

        # Should use location's host/port + schema server's path
        # Location: http://localhost:8080 + schema path: /api = http://localhost:8080/api
        assert operation.base_url == "http://localhost:8080/api"
    finally:
        import os
        os.unlink(temp_path)
