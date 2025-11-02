import pytest
from hypothesis import Phase, given, settings
from hypothesis import strategies as st
from requests.structures import CaseInsensitiveDict

import schemathesis
from schemathesis.core.parameters import ParameterLocation
from schemathesis.generation import GenerationMode
from schemathesis.generation.case import Case
from schemathesis.generation.meta import (
    CaseMetadata,
    ComponentInfo,
    ExamplesPhaseData,
    FuzzingPhaseData,
    GenerationInfo,
    PhaseInfo,
    TestPhase,
)


def make_negative_meta(location, parameter=None, description="Test mutation"):
    return CaseMetadata(
        generation=GenerationInfo(time=0.0, mode=GenerationMode.NEGATIVE),
        components={location: ComponentInfo(mode=GenerationMode.NEGATIVE)},
        phase=PhaseInfo(
            name=TestPhase.FUZZING,
            data=FuzzingPhaseData(
                description=description,
                parameter=parameter,
                parameter_location=location,
                location=f"/properties/{parameter}" if parameter else None,
            ),
        ),
    )


def make_positive_meta(location):
    return CaseMetadata(
        generation=GenerationInfo(time=0.0, mode=GenerationMode.POSITIVE),
        components={location: ComponentInfo(mode=GenerationMode.POSITIVE)},
        phase=PhaseInfo(
            name=TestPhase.EXAMPLES,
            data=ExamplesPhaseData(None, None, None, None),
        ),
    )


@pytest.fixture
def simple_schema():
    return {
        "openapi": "3.0.0",
        "info": {"title": "Test", "version": "1.0.0"},
        "paths": {
            "/items": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["id"],
                                    "properties": {
                                        "id": {"type": "string"},
                                        "name": {"type": "string"},
                                    },
                                    "additionalProperties": False,
                                }
                            }
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
    }


@pytest.fixture
def simple_operation(simple_schema):
    schema = schemathesis.openapi.from_dict(simple_schema)
    return schema["/items"]["POST"]


def test_direct_assignment_marks_dirty_and_revalidates(simple_operation):
    meta = make_negative_meta(ParameterLocation.BODY, "id", "Missing required property")

    case = Case(
        operation=simple_operation,
        method="POST",
        path="/items",
        body={},
        media_type="application/json",
        meta=meta,
    )

    assert case.meta.generation.mode == GenerationMode.NEGATIVE
    case.body = {"id": "test-123"}
    assert case.meta.generation.mode == GenerationMode.POSITIVE


def test_inplace_modification_detected_and_revalidated(simple_operation):
    meta = make_negative_meta(ParameterLocation.BODY, "id", "Missing required property")

    case = Case(
        operation=simple_operation,
        method="POST",
        path="/items",
        body={},
        media_type="application/json",
        meta=meta,
    )

    assert case.meta.generation.mode == GenerationMode.NEGATIVE
    case.body["id"] = "test-456"
    assert case.meta.generation.mode == GenerationMode.POSITIVE


def test_making_valid_case_invalid_detected(simple_operation):
    meta = make_positive_meta(ParameterLocation.BODY)

    case = Case(
        operation=simple_operation,
        method="POST",
        path="/items",
        body={"id": "valid"},
        media_type="application/json",
        meta=meta,
    )

    assert case.meta.generation.mode == GenerationMode.POSITIVE
    case.body["invalid_extra"] = "not allowed"
    assert case.meta.generation.mode == GenerationMode.NEGATIVE


def test_no_metadata_no_crash(simple_operation):
    case = Case(
        operation=simple_operation,
        method="POST",
        path="/items",
        body={"id": "test"},
        media_type="application/json",
        meta=None,
    )

    case.body["name"] = "test-name"
    assert case.meta is None


def test_nested_dict_modification_detected():
    raw_schema = {
        "openapi": "3.0.0",
        "info": {"title": "Test", "version": "1.0.0"},
        "paths": {
            "/items": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["config"],
                                    "properties": {
                                        "config": {
                                            "type": "object",
                                            "properties": {
                                                "enabled": {"type": "boolean"},
                                            },
                                        },
                                    },
                                    "additionalProperties": False,
                                }
                            }
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
    }
    schema = schemathesis.openapi.from_dict(raw_schema)
    operation = schema["/items"]["POST"]

    meta = make_positive_meta(ParameterLocation.BODY)

    case = Case(
        operation=operation,
        method="POST",
        path="/items",
        body={"config": {"enabled": True}},
        media_type="application/json",
        meta=meta,
    )

    case.body["extra"] = "not allowed"
    assert case.meta.generation.mode == GenerationMode.NEGATIVE


def test_hook_adds_required_field_metadata_updates():
    # See GH-3073
    raw_schema = {
        "openapi": "3.0.0",
        "info": {"title": "Test", "version": "1.0.0"},
        "paths": {
            "/locations": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["name", "address_id"],
                                    "properties": {
                                        "name": {
                                            "type": "string",
                                            "pattern": "^[A-Za-z]{4,254}$",
                                            "minLength": 4,
                                        },
                                        "address_id": {
                                            "type": "string",
                                            "format": "uuid",
                                        },
                                        "purpose": {"type": "string", "default": ""},
                                        "costs": {"type": "number", "default": 0},
                                        "revenue": {"type": "number", "default": 0},
                                        "debt": {"type": "number", "default": 0},
                                    },
                                    "additionalProperties": False,
                                }
                            }
                        },
                    },
                    "responses": {"201": {"description": "Created"}},
                }
            }
        },
    }

    schema = schemathesis.openapi.from_dict(raw_schema)
    operation = schema["/locations"]["POST"]

    meta = make_negative_meta(ParameterLocation.BODY, "address_id", "Required property removed")

    case = Case(
        operation=operation,
        method="POST",
        path="/locations",
        body={"name": "ABCD", "costs": 0, "debt": 0, "revenue": 0, "purpose": ""},
        media_type="application/json",
        meta=meta,
    )

    assert case.meta.generation.mode == GenerationMode.NEGATIVE
    case.body["address_id"] = "9d0b8c88-a4aa-42b6-9a12-d0e4919c924f"

    updated_meta = case.meta
    assert updated_meta.generation.mode == GenerationMode.POSITIVE
    assert updated_meta.components[ParameterLocation.BODY].mode == GenerationMode.POSITIVE


@pytest.mark.hypothesis_nested
def test_before_call_hook_with_negative_cases():
    raw_schema = {
        "openapi": "3.0.0",
        "info": {"title": "Test", "version": "1.0.0"},
        "paths": {
            "/items": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["id"],
                                    "properties": {
                                        "id": {"type": "string"},
                                    },
                                    "additionalProperties": False,
                                }
                            }
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
    }

    schema = schemathesis.openapi.from_dict(raw_schema)
    operation = schema["/items"]["POST"]

    @schema.hook
    def before_generate_body(context, strategy):
        return st.just({})

    strategy = operation.as_strategy(generation_mode=GenerationMode.NEGATIVE)

    @given(case=strategy)
    @settings(max_examples=3, phases=[Phase.generate], deadline=None)
    def test_case(case):
        if case.meta and isinstance(case.body, dict):
            # Initially negative (missing required id)
            initial_mode = case.meta.generation.mode

            # Simulate hook adding the missing field
            case.body["id"] = "hook-added-id"

            # After modification, metadata should reflect POSITIVE
            assert case.meta.generation.mode == GenerationMode.POSITIVE
            # Verify transition happened
            if initial_mode == GenerationMode.NEGATIVE:
                assert case.meta.components[ParameterLocation.BODY].mode == GenerationMode.POSITIVE

    test_case()


@pytest.mark.hypothesis_nested
def test_map_case_hook_with_metadata():
    raw_schema = {
        "openapi": "3.0.0",
        "info": {"title": "Test", "version": "1.0.0"},
        "paths": {
            "/users/{user_id}": {
                "get": {
                    "parameters": [
                        {
                            "name": "user_id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string", "pattern": "^[0-9]+$"},
                        }
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
    }

    schema = schemathesis.openapi.from_dict(raw_schema)
    operation = schema["/users/{user_id}"]["GET"]

    @schema.hook
    def map_case(ctx, case):
        if case.path_parameters.get("user_id"):
            # Replace invalid value with valid one
            case.path_parameters["user_id"] = "999"
        return case

    strategy = operation.as_strategy(generation_mode=GenerationMode.NEGATIVE)

    @given(case=strategy)
    @settings(max_examples=3, phases=[Phase.generate], deadline=None)
    def test_case(case):
        # Verify path parameter is valid after hook fix
        assert case.meta.components[ParameterLocation.PATH].mode == GenerationMode.POSITIVE

    test_case()


def test_query_parameter_modification_revalidates():
    raw_schema = {
        "openapi": "3.0.0",
        "info": {"title": "Test", "version": "1.0.0"},
        "paths": {
            "/users": {
                "get": {
                    "parameters": [
                        {
                            "name": "include_deleted",
                            "in": "query",
                            "schema": {"type": "boolean"},
                        }
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
    }

    schema = schemathesis.openapi.from_dict(raw_schema)
    operation = schema["/users"]["GET"]

    meta = make_negative_meta(ParameterLocation.QUERY, "include_deleted", "Invalid type")

    case = Case(
        operation=operation,
        method="GET",
        path="/users",
        query={"include_deleted": "not-a-boolean"},
        meta=meta,
    )

    assert case.meta.generation.mode == GenerationMode.NEGATIVE
    case.query["include_deleted"] = True
    assert case.meta.generation.mode == GenerationMode.POSITIVE
    assert case.meta.components[ParameterLocation.QUERY].mode == GenerationMode.POSITIVE


def test_header_case_insensitive_dict_hash():
    raw_schema = {
        "openapi": "3.0.0",
        "info": {"title": "Test", "version": "1.0.0"},
        "paths": {
            "/users": {
                "get": {
                    "parameters": [
                        {
                            "name": "X-API-Version",
                            "in": "header",
                            "required": True,
                            "schema": {"type": "string"},
                        }
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
    }

    schema = schemathesis.openapi.from_dict(raw_schema)
    operation = schema["/users"]["GET"]

    meta = make_positive_meta(ParameterLocation.HEADER)

    case = Case(
        operation=operation,
        method="GET",
        path="/users",
        headers=CaseInsensitiveDict({"X-API-Version": "v1"}),
        meta=meta,
    )

    assert case.meta.generation.mode == GenerationMode.POSITIVE
    # Modify headers
    case.headers["X-Custom"] = "value"
    # Just verify that modification is detected (metadata access doesn't crash)
    _ = case.meta.generation.mode


def test_path_parameter_modification_revalidates():
    raw_schema = {
        "openapi": "3.0.0",
        "info": {"title": "Test", "version": "1.0.0"},
        "paths": {
            "/users/{user_id}": {
                "get": {
                    "parameters": [
                        {
                            "name": "user_id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string", "pattern": "^[0-9]+$"},
                        }
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
    }

    schema = schemathesis.openapi.from_dict(raw_schema)
    operation = schema["/users/{user_id}"]["GET"]

    meta = make_negative_meta(ParameterLocation.PATH, "user_id", "Invalid pattern")

    case = Case(
        operation=operation,
        method="GET",
        path="/users/{user_id}",
        path_parameters={"user_id": "not-a-number"},
        meta=meta,
    )

    assert case.meta.generation.mode == GenerationMode.NEGATIVE
    case.path_parameters["user_id"] = "12345"
    assert case.meta.generation.mode == GenerationMode.POSITIVE


def test_multiple_components_modified_all_revalidate():
    raw_schema = {
        "openapi": "3.0.0",
        "info": {"title": "Test", "version": "1.0.0"},
        "paths": {
            "/users/{user_id}": {
                "post": {
                    "parameters": [
                        {
                            "name": "user_id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string", "pattern": "^[0-9]+$"},
                        }
                    ],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["email"],
                                    "properties": {
                                        "email": {"type": "string", "format": "email"},
                                        "name": {"type": "string", "minLength": 2},
                                    },
                                    "additionalProperties": False,
                                }
                            }
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
    }

    schema = schemathesis.openapi.from_dict(raw_schema)
    operation = schema["/users/{user_id}"]["POST"]

    meta = CaseMetadata(
        generation=GenerationInfo(time=0.0, mode=GenerationMode.NEGATIVE),
        components={
            ParameterLocation.PATH: ComponentInfo(mode=GenerationMode.NEGATIVE),
            ParameterLocation.BODY: ComponentInfo(mode=GenerationMode.NEGATIVE),
        },
        phase=PhaseInfo(
            name=TestPhase.FUZZING,
            data=FuzzingPhaseData("Multiple invalid fields", None, None, None),
        ),
    )

    case = Case(
        operation=operation,
        method="POST",
        path="/users/{user_id}",
        path_parameters={"user_id": "abc"},
        body={"extra": "field"},
        media_type="application/json",
        meta=meta,
    )

    assert case.meta.generation.mode == GenerationMode.NEGATIVE

    # Fix path but not body - should still be NEGATIVE
    case.path_parameters["user_id"] = "123"
    assert case.meta.generation.mode == GenerationMode.NEGATIVE

    # Fix body as well - should become POSITIVE
    case.body = {"email": "test@example.com", "name": "John"}
    assert case.meta.generation.mode == GenerationMode.POSITIVE


def test_chained_modifications_all_tracked(simple_operation):
    meta = make_positive_meta(ParameterLocation.BODY)

    case = Case(
        operation=simple_operation,
        method="POST",
        path="/items",
        body={"id": "test@example.com"},
        media_type="application/json",
        meta=meta,
    )

    assert case.meta.generation.mode == GenerationMode.POSITIVE

    # Add invalid field
    case.body["invalid"] = "field"
    assert case.meta.generation.mode == GenerationMode.NEGATIVE

    # Remove it
    del case.body["invalid"]
    assert case.meta.generation.mode == GenerationMode.POSITIVE

    # Add valid optional field
    case.body["name"] = "Alice"
    assert case.meta.generation.mode == GenerationMode.POSITIVE

    # Add extra property (additionalProperties: false)
    case.body["extra_field"] = "invalid"
    assert case.meta.generation.mode == GenerationMode.NEGATIVE


@pytest.mark.hypothesis_nested
def test_graphql_schema_ignores_modifications():
    graphql_schema = """
type User {
  id: ID!
  name: String!
}

type Query {
  user(id: ID!): User
}
"""

    schema = schemathesis.graphql.from_file(graphql_schema)
    operation = schema["Query"]["user"]

    strategy = operation.as_strategy()

    @given(case=strategy)
    @settings(max_examples=5, phases=[Phase.generate], deadline=None)
    def test_case(case):
        assert case.meta is not None
        assert case.meta.generation.mode == GenerationMode.POSITIVE

        # Modify body to something potentially invalid
        case.body = '{"query": "invalid syntax {{{"}'
        # GraphQL schemas don't validate, so mode should remain POSITIVE
        assert case.meta.generation.mode == GenerationMode.POSITIVE

    test_case()


def test_case_without_metadata_no_crash():
    raw_schema = {
        "openapi": "3.0.0",
        "info": {"title": "Test", "version": "1.0.0"},
        "paths": {
            "/items": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["id"],
                                    "properties": {
                                        "id": {"type": "string"},
                                        "name": {"type": "string"},
                                    },
                                }
                            }
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
    }

    schema = schemathesis.openapi.from_dict(raw_schema)
    operation = schema["/items"]["POST"]

    # Create case without metadata (as users would do)
    case = operation.Case(
        body={"id": "test"},
        media_type="application/json",
    )

    assert case.meta is None
    case.body["name"] = "Test Item"
    assert case.meta is None
    case.body = {"id": "updated"}
    assert case.meta is None
    case.query = {"filter": "active"}
    assert case.meta is None
    case.headers["X-Custom"] = "value"
    assert case.meta is None
