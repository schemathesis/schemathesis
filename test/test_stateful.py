import pytest
from flask import Flask, jsonify, request

import schemathesis
from schemathesis.core.errors import InvalidStateMachine
from schemathesis.core.result import Ok

pytestmark = [pytest.mark.openapi_version("3.0")]


@pytest.fixture
def customer_order_schema():
    return {
        "/customers": {
            "post": {
                "operationId": "createCustomer",
                "requestBody": {
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {"name": {"type": "string"}},
                                "required": ["name"],
                            }
                        }
                    },
                    "required": True,
                },
                "responses": {
                    "201": {
                        "description": "Customer created",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"id": {"type": "string"}},
                                    "required": ["id"],
                                }
                            }
                        },
                    }
                },
            }
        },
        "/orders": {
            "post": {
                "operationId": "createOrder",
                "requestBody": {
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "customer_id": {"type": "string"},
                                    "total": {"type": "number"},
                                },
                                "required": ["customer_id"],
                            }
                        }
                    },
                    "required": True,
                },
                "responses": {
                    "201": {
                        "description": "Order created",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "id": {"type": "string"},
                                        "customer_id": {"type": "string"},
                                        "total": {"type": "number"},
                                    },
                                    "required": ["id", "customer_id", "total"],
                                }
                            }
                        },
                    }
                },
            }
        },
    }


def test_missing_operation(ctx):
    schema = ctx.openapi.build_schema(
        {
            "/users/": {
                "post": {
                    "responses": {
                        "201": {
                            "description": "OK",
                            "links": {
                                "GetUserByUserId": {
                                    "operationId": "unknown",
                                    "parameters": {"path.user_id": "$response.body#/id"},
                                },
                            },
                        }
                    },
                }
            },
            "/users/{user_id}": {
                "get": {"operationId": "getUser", "responses": {"200": {"description": "OK"}}},
            },
        }
    )

    schema = schemathesis.openapi.from_dict(schema)

    with pytest.raises(InvalidStateMachine) as exc:
        schema.as_state_machine()
    assert "Operation 'unknown' not found" in str(exc.value)


def count_links(schema):
    total = 0
    for result in schema.get_all_operations():
        if isinstance(result, Ok):
            operation = result.ok()
            for _, response in operation.responses.items():
                total += sum(1 for _ in response.iter_links())
    return total


@pytest.mark.parametrize("enable_inference", [True, False])
def test_inference_respects_config(ctx, customer_order_schema, enable_inference):
    schema = schemathesis.openapi.from_dict(ctx.openapi.build_schema(customer_order_schema))

    if not enable_inference:
        schema.config.phases.stateful.inference.algorithms = []

    links_before = count_links(schema)
    schema.as_state_machine()
    links_after = count_links(schema)

    if enable_inference:
        assert links_after > links_before
    else:
        assert links_after == 0


def test_pytest_stateful_discovers_bug_with_dependency_inference(testdir, app_runner, customer_order_schema):
    app = Flask(__name__)
    customers = {}
    next_customer_id = [1]
    next_order_id = [1]

    @app.route("/customers", methods=["POST"])
    def create_customer():
        data = request.get_json() or {}
        customer_id = str(next_customer_id[0])
        next_customer_id[0] += 1
        customers[customer_id] = {"id": customer_id, "name": data.get("name", "Unknown")}
        return jsonify({"id": customer_id}), 201

    @app.route("/orders", methods=["POST"])
    def create_order():
        data = request.get_json() or {}
        customer_id = data.get("customer_id")
        order_id = str(next_order_id[0])
        next_order_id[0] += 1

        if customer_id in customers:
            return (
                jsonify(
                    {
                        "id": order_id,
                        "customer_id": customer_id,
                        "total": str(data.get("total", 0)),
                    }
                ),
                201,
            )

        return jsonify({"detail": "Customer does not exist"}), 404

    port = app_runner.run_flask_app(app)

    testdir.make_test(
        f"""
schema.config.generation.modes = [GenerationMode.POSITIVE]

class APIWorkflow(schema.as_state_machine()):
    def get_call_kwargs(self, case):
        return {{"base_url": "http://127.0.0.1:{port}"}}

TestAPI = APIWorkflow.TestCase
""",
        schema_name="simple_openapi.yaml",
        paths=customer_order_schema,
    )

    result = testdir.runpytest("-v")
    stdout = result.stdout.str()

    assert "Schema contains no link definitions" not in stdout
    result.assert_outcomes(failed=1)
    assert "is not of type" in stdout
