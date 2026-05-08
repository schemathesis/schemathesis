from __future__ import annotations

from tools.corpus.metrics import DependencyMetrics, add_dependency_metrics, collect_dependency_metrics


def test_add_dependency_metrics_empty_returns_zero():
    assert add_dependency_metrics([]) == DependencyMetrics()


def test_add_dependency_metrics_sums_field_by_field():
    assert add_dependency_metrics(
        [
            DependencyMetrics(resources=1, inputs=2, outputs=3, links=4),
            DependencyMetrics(resources=5, inputs=6, outputs=7, links=8),
        ]
    ) == DependencyMetrics(resources=6, inputs=8, outputs=10, links=12)


def test_collect_dependency_metrics_counts_resources_inputs_outputs(ctx):
    raw_schema = ctx.openapi.build_schema(
        {
            "/users": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"name": {"type": "string"}},
                                    "required": ["name"],
                                }
                            }
                        },
                    },
                    "responses": {
                        "201": {
                            "description": "Created",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "id": {"type": "string"},
                                            "name": {"type": "string"},
                                        },
                                        "required": ["id", "name"],
                                    }
                                }
                            },
                        }
                    },
                },
                "get": {
                    "parameters": [{"name": "id", "in": "query", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "OK"}},
                },
            }
        }
    )

    assert collect_dependency_metrics(raw_schema) == DependencyMetrics(
        resources=1,
        inputs=1,
        outputs=1,
        links=1,
    )
