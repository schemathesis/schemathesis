"""Test edge cases for server overriding."""

import pytest
import schemathesis


def test_server_with_missing_variable_default():
    """Test server with variable that has no default - should raise InvalidSchema."""
    from schemathesis.core.errors import InvalidSchema

    schema_dict = {
        "openapi": "3.0.0",
        "info": {"title": "Test API", "version": "1.0.0"},
        "paths": {
            "/users": {
                "servers": [
                    {
                        "url": "https://{env}.example.com",
                        "variables": {
                            "env": {
                                # Missing 'default' key - invalid per OpenAPI spec
                                "enum": ["staging", "production"],
                            }
                        },
                    }
                ],
                "get": {"responses": {"200": {"description": "Success"}}},
            },
        },
    }

    schema = schemathesis.openapi.from_dict(schema_dict)

    # This should raise InvalidSchema with a clear error message
    with pytest.raises(InvalidSchema, match="Server variable 'default' is missing required 'default' field"):
        operation = schema["/users"]["GET"]


def test_server_url_with_no_variables_defined():
    """Test server URL that has placeholders but no variables section.

    This is technically invalid per OpenAPI spec, but we don't substitute variables
    if the 'variables' field is missing, so the URL keeps its placeholders.
    """
    schema_dict = {
        "openapi": "3.0.0",
        "info": {"title": "Test API", "version": "1.0.0"},
        "paths": {
            "/users": {
                "servers": [
                    {
                        "url": "https://{env}.example.com",
                        # No 'variables' key - invalid but we don't crash
                    }
                ],
                "get": {"responses": {"200": {"description": "Success"}}},
            },
        },
    }

    schema = schemathesis.openapi.from_dict(schema_dict)
    operation = schema["/users"]["GET"]

    # URL keeps placeholders as-is (invalid but doesn't crash)
    assert operation.base_url == "https://{env}.example.com"


def test_server_url_without_variables():
    """Test server URL with no placeholders."""
    schema_dict = {
        "openapi": "3.0.0",
        "info": {"title": "Test API", "version": "1.0.0"},
        "servers": [{"url": "https://api.example.com/v1"}],
        "paths": {
            "/users": {
                "get": {"responses": {"200": {"description": "Success"}}},
            },
        },
    }

    schema = schemathesis.openapi.from_dict(schema_dict)
    operation = schema["/users"]["GET"]

    assert operation.base_url == "https://api.example.com/v1"


def test_operation_with_ref_in_servers():
    """Test that $ref in servers is not supported (per OpenAPI spec)."""
    # OpenAPI spec doesn't support $ref in servers array
    # Just document this limitation
    pass


def test_link_server_override_in_stateful():
    """Test that link-specific server is properly stored and accessed."""
    from schemathesis.specs.openapi.stateful.links import OpenApiLink

    schema_dict = {
        "openapi": "3.0.0",
        "info": {"title": "Test API", "version": "1.0.0"},
        "servers": [{"url": "https://api.example.com"}],
        "paths": {
            "/users": {
                "post": {
                    "operationId": "createUser",
                    "responses": {
                        "201": {
                            "description": "Created",
                            "links": {
                                "GetUser": {
                                    "operationId": "getUser",
                                    "server": {"url": "https://read-api.example.com"},
                                }
                            },
                        }
                    },
                },
            },
            "/users/{id}": {
                "get": {
                    "operationId": "getUser",
                    "parameters": [{"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "Success"}},
                },
            },
        },
    }

    schema = schemathesis.openapi.from_dict(schema_dict)
    create_op = schema["/users"]["POST"]
    get_op = schema["/users/{id}"]["GET"]

    # Create a link directly
    link_definition = {"operationId": "getUser", "server": {"url": "https://read-api.example.com"}}

    link = OpenApiLink(name="GetUser", status_code="201", definition=link_definition, source=create_op)

    # The link should have a server defined
    assert link.server is not None
    assert link.server["url"] == "https://read-api.example.com"

    # The link's target base URL should use the link server
    target_base_url = link.get_target_base_url()
    assert target_base_url == "https://read-api.example.com"

    # Without a link server, it should use the target operation's base URL
    # (This needs proper integration testing with stateful test execution)
    link_no_server = OpenApiLink(name="GetUser2", status_code="201", definition={"operationId": "getUser"}, source=create_op)
    assert link_no_server.server is None


def test_relative_server_urls():
    """Test relative server URLs (allowed in OpenAPI)."""
    schema_dict = {
        "openapi": "3.0.0",
        "info": {"title": "Test API", "version": "1.0.0"},
        "servers": [{"url": "/api/v1"}],  # Relative URL
        "paths": {
            "/users": {
                "get": {"responses": {"200": {"description": "Success"}}},
            },
        },
    }

    schema = schemathesis.openapi.from_dict(schema_dict)
    operation = schema["/users"]["GET"]

    # Relative URLs should be preserved
    assert operation.base_url == "/api/v1"


def test_multiple_path_items_with_different_servers():
    """Test that different paths can have different servers."""
    schema_dict = {
        "openapi": "3.0.0",
        "info": {"title": "Test API", "version": "1.0.0"},
        "servers": [{"url": "https://api.example.com"}],
        "paths": {
            "/users": {
                "servers": [{"url": "https://users.example.com"}],
                "get": {"responses": {"200": {"description": "Success"}}},
            },
            "/products": {
                "servers": [{"url": "https://products.example.com"}],
                "get": {"responses": {"200": {"description": "Success"}}},
            },
        },
    }

    schema = schemathesis.openapi.from_dict(schema_dict)

    users_op = schema["/users"]["GET"]
    products_op = schema["/products"]["GET"]

    assert users_op.base_url == "https://users.example.com"
    assert products_op.base_url == "https://products.example.com"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
