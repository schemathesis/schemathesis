from __future__ import annotations

from schemathesis.resources import Cardinality, ResourceDescriptor, ResourceRepository, ResourceRepositoryConfig


def make_descriptor(resource_name: str = "User", pointer: str = "", cardinality: Cardinality = Cardinality.ONE):
    return ResourceDescriptor(
        resource_name=resource_name,
        operation_label="POST /users",
        status_code=201,
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
