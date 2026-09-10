import json

import pytest

from schemathesis.baseline import Baseline, BaselineEntry


def write(path, entries, **rest):
    path.write_text(json.dumps({"format_version": 1, **rest, "entries": entries}))
    return path


def test_entries_sorted_on_save(tmp_path):
    path = write(
        tmp_path / "baseline.json",
        [
            {"operation": "POST /users", "check": "not_a_server_error", "failure": "ServerError", "signature": "500"},
            {"operation": "GET /users", "check": "not_a_server_error", "failure": "ServerError", "signature": "500"},
            {
                "operation": "GET /users",
                "check": "positive_data_acceptance",
                "failure": "RejectedPositiveData",
                "signature": "409",
            },
        ],
    )
    Baseline.load(path).save(path)

    assert [(entry["operation"], entry["failure"]) for entry in json.loads(path.read_text())["entries"]] == [
        ("GET /users", "ServerError"),
        ("GET /users", "RejectedPositiveData"),
        ("POST /users", "ServerError"),
    ]


def test_user_annotations_survive_round_trip(tmp_path):
    entry = {
        "operation": "GET /users/{userId}",
        "operation_id": "getUser",
        "check": "positive_data_acceptance",
        "failure": "RejectedPositiveData",
        "signature": "409",
        "reason": "Legacy conflict semantics on the v1 read path",
        "ticket": "API-4412",
        "first_seen": "2026-09-10",
        "last_seen": "2026-09-14",
        "expires": "2026-12-01",
    }
    path = write(tmp_path / "baseline.json", [entry])

    Baseline.load(path).save(path)

    stored = json.loads(path.read_text())["entries"][0]
    assert {key: stored[key] for key in entry} == entry


def test_unset_annotations_are_not_written(tmp_path):
    path = write(
        tmp_path / "baseline.json",
        [{"operation": "GET /users", "check": "not_a_server_error", "failure": "ServerError", "signature": "500"}],
    )
    Baseline.load(path).save(path)

    assert set(json.loads(path.read_text())["entries"][0]) == {"id", "operation", "check", "failure", "signature"}


def test_id_identifies_the_failure_not_its_annotations():
    common = {"operation": "GET /users", "check": "not_a_server_error", "failure": "ServerError", "signature": "500"}

    assert BaselineEntry(**common, extra={"reason": "one"}).id == BaselineEntry(**common, extra={"ticket": "API-1"}).id


def test_missing_file_loads_as_empty(tmp_path):
    assert Baseline.load(tmp_path / "absent.json").entries == []


def test_unsupported_format_version_is_rejected(tmp_path):
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps({"format_version": 99, "entries": []}))

    with pytest.raises(ValueError, match="version 99"):
        Baseline.load(path)


def test_unknown_fields_survive_round_trip(tmp_path):
    entry = {
        "operation": "GET /users",
        "check": "not_a_server_error",
        "failure": "ServerError",
        "signature": "500",
        "owner": "payments-team",
        "severity": "low",
    }
    path = write(tmp_path / "baseline.json", [entry])

    Baseline.load(path).save(path)

    stored = json.loads(path.read_text())["entries"][0]
    assert {key: stored[key] for key in entry} == entry
