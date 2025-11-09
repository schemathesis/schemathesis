from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

from schemathesis.core.parameters import ParameterLocation
from schemathesis.resources import Cardinality, ResourceDescriptor, ResourceRepository, ResourceRepositoryConfig
from schemathesis.specs.openapi.resource_provider import OpenApiResourceProvider, ParameterRequirement


def make_descriptor(
    resource_name: str = "User",
    pointer: str = "",
    cardinality: Cardinality = Cardinality.ONE,
    status_code: str = "201",
):
    return ResourceDescriptor(
        resource_name=resource_name,
        operation_label="POST /users",
        status_code=status_code,
        pointer=pointer,
        cardinality=cardinality,
        fields=("id",),
    )


def test_store_single_resource():
    repo = ResourceRepository([make_descriptor()])
    payload = {"id": "123", "name": "Jane"}

    repo.ingest_response(operation_label="POST /users", status_code=201, payload=payload)

    resources = list(repo.iter_instances("User"))
    assert len(resources) == 1
    assert resources[0].data["id"] == "123"


def test_capacity_limit_evicts_oldest():
    descriptor = make_descriptor()
    repo = ResourceRepository([descriptor], config=ResourceRepositoryConfig(per_type_capacity=1))

    repo.ingest_response(operation_label="POST /users", status_code=201, payload={"id": "1"})
    repo.ingest_response(operation_label="POST /users", status_code=201, payload={"id": "2"})

    resources = list(repo.iter_instances("User"))
    assert len(resources) == 1
    assert resources[0].data["id"] == "2"


def test_ignore_non_matching_status_code():
    repo = ResourceRepository([make_descriptor()])
    repo.ingest_response(operation_label="POST /users", status_code=200, payload={"id": "1"})
    assert list(repo.iter_instances("User")) == []


def test_many_cardinality_extracts_each_item():
    descriptor = make_descriptor(pointer="/items", cardinality=Cardinality.MANY)
    repo = ResourceRepository([descriptor])

    payload = {"items": [{"id": "a"}, {"id": "b"}]}
    repo.ingest_response(operation_label="POST /users", status_code=201, payload=payload)

    resources = list(repo.iter_instances("User"))
    assert {instance.data["id"] for instance in resources} == {"a", "b"}


def test_resource_provider_augments_schema() -> None:
    repo = ResourceRepository([make_descriptor()])
    repo.ingest_response(operation_label="POST /users", status_code=201, payload={"id": "1"})
    repo.ingest_response(operation_label="POST /users", status_code=201, payload={"id": "2"})

    requirements = {
        ("GET /users/{id}", ParameterLocation.PATH, "user_id"): ParameterRequirement("User", "id"),
    }
    provider = OpenApiResourceProvider(repository=repo, requirements=requirements)

    schema = {
        "type": "object",
        "properties": {"user_id": {"type": "string"}},
        "required": ["user_id"],
    }
    operation = SimpleNamespace(label="GET /users/{id}")

    augmented = provider.augment(operation=operation, location=ParameterLocation.PATH, schema=schema)

    assert augmented is not schema
    options = augmented["properties"]["user_id"]["anyOf"]
    assert options[1]["enum"] == ["2", "1"]


def test_wildcard_status_code_matching() -> None:
    descriptor = replace(make_descriptor(), status_code="2XX")
    repo = ResourceRepository([descriptor])

    repo.ingest_response(operation_label="POST /users", status_code=201, payload={"id": "1"})
    repo.ingest_response(operation_label="POST /users", status_code=404, payload={"id": "2"})

    resources = list(repo.iter_instances("User"))
    assert len(resources) == 1
    assert resources[0].data["id"] == "1"
