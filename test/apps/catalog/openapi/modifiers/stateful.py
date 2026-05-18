from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Literal

from flask import Flask, request

from test.apps.catalog.openapi.stateful import UserStore


@dataclass(slots=True)
class UseAfterFree:
    # Server reuses freed IDs for new resources, exposing stale GETs.
    priority: int = 0

    def apply(self, app: Flask, store: UserStore) -> None:
        store.config.use_after_free = True
        # Force names long enough that POST-then-DELETE-then-GET always leaves a stale resource.
        spec = app.config["schema"]
        spec["components"]["schemas"]["NewUser"]["properties"]["name"]["minLength"] = 10


@dataclass(slots=True)
class EnsureResourceAvailability:
    priority: int = 0

    def apply(self, app: Flask, store: UserStore) -> None:
        store.config.ensure_resource_availability = True


@dataclass(slots=True)
class NoMergeBody:
    priority: int = 0

    def apply(self, app: Flask, store: UserStore) -> None:
        store.config.merge_body = False
        spec = app.config["schema"]
        spec["paths"]["/users"]["post"]["responses"]["201"]["links"]["UpdateUser"]["x-schemathesis"] = {
            "merge_body": False
        }


@dataclass(slots=True)
class IndependentInternalError:
    priority: int = 0

    def apply(self, app: Flask, store: UserStore) -> None:
        store.config.independent_500 = True


@dataclass(slots=True)
class FailureBehindFailure:
    priority: int = 0

    def apply(self, app: Flask, store: UserStore) -> None:
        store.config.failure_behind_failure = True


@dataclass(slots=True)
class MultipleConformanceIssues:
    priority: int = 0

    def apply(self, app: Flask, store: UserStore) -> None:
        store.config.multiple_conformance_issues = True


@dataclass(slots=True)
class Unsatisfiable:
    priority: int = 0

    def apply(self, app: Flask, store: UserStore) -> None:
        spec = app.config["schema"]
        spec["components"]["schemas"]["NewUser"]["properties"]["name"]["minLength"] = 100


@dataclass(slots=True)
class CustomHeadersCheck:
    expected: dict[str, str]
    priority: int = 0

    def apply(self, app: Flask, store: UserStore) -> None:
        store.config.custom_headers = self.expected


@dataclass(slots=True)
class MultipleSourceLinks:
    priority: int = 0

    def apply(self, app: Flask, store: UserStore) -> None:
        spec = app.config["schema"]
        spec["paths"]["/users/{userId}"]["delete"]["responses"]["204"]["links"]["DeleteUserAgain"] = {
            "operationId": "deleteUser",
            "parameters": {"userId": "$request.path.userId"},
        }
        post_links = spec["paths"]["/users"]["post"]["responses"]["201"]["links"]
        delete_link = post_links["DeleteUser"]
        post_links.clear()
        post_links["DeleteUser"] = delete_link
        spec["paths"]["/users/{userId}"]["get"]["responses"]["200"]["links"].clear()


@dataclass(slots=True)
class SingleLink:
    priority: int = 0

    def apply(self, app: Flask, store: UserStore) -> None:
        spec = app.config["schema"]
        post_links = spec["paths"]["/users"]["post"]["responses"]["201"]["links"]
        delete_link = post_links["DeleteUser"]
        post_links.clear()
        post_links["DeleteUser"] = delete_link
        spec["paths"]["/users/{userId}"]["get"]["responses"]["200"]["links"].clear()
        spec["paths"]["/orders/{orderId}"]["delete"]["responses"]["200"]["links"].clear()


@dataclass(slots=True)
class BearerAuth:
    # Server enforces a Bearer token. Schema does NOT declare auth.
    token: str
    priority: int = 0

    def apply(self, app: Flask, store: UserStore) -> None:
        store.config.auth_token = self.token
        store.config.enforce_auth = True


@dataclass(slots=True)
class IgnoredAuth:
    # Schema declares Bearer auth but the server does not enforce it.
    # Used to test the ignored_auth check detection.
    token: str = "ignored"
    priority: int = 0

    def apply(self, app: Flask, store: UserStore) -> None:
        store.config.auth_token = self.token
        store.config.enforce_auth = False
        spec = app.config["schema"]
        spec["components"]["securitySchemes"] = {
            "bearerAuth": {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"}
        }
        spec["security"] = [{"bearerAuth": []}]


@dataclass(slots=True)
class Slowdown:
    seconds: float
    priority: int = 0

    def apply(self, app: Flask, store: UserStore) -> None:
        store.config.slowdown = self.seconds


@dataclass(slots=True)
class MultipleIncomingLinksWithSameStatus:
    priority: int = 0

    def apply(self, app: Flask, store: UserStore) -> None:
        spec = app.config["schema"]
        spec["paths"]["/users/{userId}"]["patch"]["responses"]["200"]["links"] = {
            "GetUser": {
                "operationId": "getUser",
                "parameters": {"userId": "$request.path.userId"},
            }
        }


@dataclass(slots=True)
class CircularLinks:
    priority: int = 0

    def apply(self, app: Flask, store: UserStore) -> None:
        spec = app.config["schema"]
        spec["paths"]["/users/{userId}"]["delete"]["responses"]["204"]["links"]["CreateNewUser"] = {
            "operationId": "createUser",
            "requestBody": {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/NewUser"}}}},
        }


@dataclass(slots=True)
class DuplicateOperationLinks:
    priority: int = 0

    def apply(self, app: Flask, store: UserStore) -> None:
        store.config.duplicate_operation_links = True
        spec = app.config["schema"]
        spec["components"]["schemas"]["User"]["properties"]["manager_id"] = {"type": "integer"}
        spec["paths"]["/users"]["post"]["responses"]["201"]["links"]["GetManager"] = {
            "operationId": "getUser",
            "parameters": {"userId": "$response.body#/manager_id"},
            "description": "Get user's manager",
        }


@dataclass(slots=True)
class InvalidParameter:
    priority: int = 0

    def apply(self, app: Flask, store: UserStore) -> None:
        spec = app.config["schema"]
        for name in ("InvalidUser", "InvalidUser-2"):
            spec["paths"]["/users"]["post"]["responses"]["201"]["links"][name] = {
                "operationId": "getUser",
                "parameters": {
                    # `unknown` parameter doesn't exist in GET /users/{userId}
                    "unknown": "$response.body#/id",
                    # `wrong` parameter doesn't exist in POST /users
                    "userId": "$request.query.wrong",
                },
            }
        spec["paths"]["/users/{userId}"]["patch"]["responses"]["200"]["links"] = {
            "GetUser": {
                "operationId": "getUser",
                "parameters": {
                    "userId": "$request.path.whatever",
                    "something": "$req.[",
                },
            }
        }


@dataclass(slots=True)
class ListUsersAsRoot:
    priority: int = 0

    def apply(self, app: Flask, store: UserStore) -> None:
        spec = app.config["schema"]
        spec["paths"]["/users"]["get"]["responses"]["200"]["links"] = {
            "GetUser": {
                "operationId": "getUser",
                "parameters": {"userId": "$response.body#/users/0/id"},
            },
        }
        spec["paths"]["/users"]["post"]["responses"]["201"]["links"].clear()


@dataclass(slots=True)
class NoReliableTransitions:
    priority: int = 0

    def apply(self, app: Flask, store: UserStore) -> None:
        spec = app.config["schema"]
        del spec["paths"]["/users"]["post"]


@dataclass(slots=True)
class ReturnPlainText:
    body: Literal[False] | str | bytes
    priority: int = 0

    def apply(self, app: Flask, store: UserStore) -> None:
        store.config.return_plain_text = self.body
        spec = app.config["schema"]
        # Pin name to a fixed value so snapshots stay stable.
        spec["components"]["schemas"]["NewUser"]["properties"]["name"] = {"enum": ["fixed-name"]}


@dataclass(slots=True)
class OmitRequiredField:
    priority: int = 0

    def apply(self, app: Flask, store: UserStore) -> None:
        store.config.omit_required_field = True
        spec = app.config["schema"]
        # Pin name to a fixed value so snapshots stay stable.
        spec["components"]["schemas"]["NewUser"]["properties"]["name"] = {"enum": ["fixed-name"]}
        # Keep only DeleteUser link on POST; clear other link maps.
        post_links = spec["paths"]["/users"]["post"]["responses"]["201"]["links"]
        delete_link = post_links["DeleteUser"]
        post_links.clear()
        post_links["DeleteUser"] = delete_link
        spec["paths"]["/users/{userId}"]["get"]["responses"]["200"]["links"].clear()
        spec["paths"]["/users/{userId}"]["delete"]["responses"]["204"]["links"].clear()
        spec["paths"]["/orders/{orderId}"]["delete"]["responses"]["200"]["links"].clear()


@dataclass(slots=True)
class ReuseDeletedIds:
    priority: int = 0

    def apply(self, app: Flask, store: UserStore) -> None:
        store.config.reuse_deleted_ids = True


@dataclass(slots=True)
class RequireBearerAuth:
    """Enable bearer-token enforcement on the server. Spec components advertise the scheme."""

    valid_token: str = "real-token"
    priority: int = 0

    def apply(self, app: Flask, store: UserStore) -> None:
        store.config.auth_token = self.valid_token
        store.config.enforce_auth = True
        spec = app.config["schema"]
        spec["components"]["securitySchemes"] = {"BearerAuth": {"type": "http", "scheme": "bearer"}}


@dataclass(slots=True)
class SlowOperations:
    """Inject per-operation latency into a stateful_users app.

    Keys of `latencies` are `(HTTP method, Werkzeug URL rule)` pairs, e.g.
    `("DELETE", "/users/<int:user_id>")`. The matching handler sleeps for
    the configured number of seconds before its body runs.
    """

    latencies: dict[tuple[str, str], float]
    priority: int = 0

    def apply(self, app: Flask, store: UserStore) -> None:
        latencies = self.latencies

        @app.before_request
        def delay_request() -> None:
            rule = request.url_rule
            if rule is None:
                return
            seconds = latencies.get((request.method, rule.rule))
            if seconds:
                time.sleep(seconds)


@dataclass(slots=True)
class ParserBlamesUnrelated:
    # Correct link; server returns 400 blaming an unrelated header (X-Tenant-Id).
    priority: int = 0

    def apply(self, app: Flask, store: UserStore) -> None:
        store.config.parser_blames_unrelated = True


@dataclass(slots=True)
class WrongLinkToMissingId:
    # Link extracts `manager_id` (never resolves to a user); DELETE returns plain 404.
    priority: int = 0

    def apply(self, app: Flask, store: UserStore) -> None:
        store.config.wrong_link_to_missing_id = True
        spec = app.config["schema"]
        spec["components"]["schemas"]["User"]["properties"]["manager_id"] = {"type": "integer"}
        spec["paths"]["/users"]["post"]["responses"]["201"]["links"]["DeleteUser"]["parameters"]["userId"] = (
            "$response.body#/manager_id"
        )


@dataclass(slots=True)
class WrongLinkTypeMismatch:
    # Link feeds a string `name` into the integer userId slot; server returns 400 with an unattributable body.
    priority: int = 0

    def apply(self, app: Flask, store: UserStore) -> None:
        store.config.wrong_link_type_mismatch = True
        spec = app.config["schema"]
        # Empty `name` would yield `/users/` which Flask routes to NotFound before any handler runs,
        # turning every link execution into a 404 the calibrator early-returns on.
        spec["components"]["schemas"]["NewUser"]["properties"]["name"]["minLength"] = 1
        spec["paths"]["/users"]["post"]["responses"]["201"]["links"]["DeleteUser"]["parameters"]["userId"] = (
            "$response.body#/name"
        )


@dataclass(slots=True)
class WrongLinkParserAttributed:
    # Link feeds a string `name` into userId; server returns 422 with a DRF-style body blaming `userId`.
    priority: int = 0

    def apply(self, app: Flask, store: UserStore) -> None:
        store.config.wrong_link_parser_attributed = True
        spec = app.config["schema"]
        # Empty `name` would yield `/users/` which Flask routes to NotFound before any handler runs,
        # turning every link execution into a 404 the calibrator early-returns on.
        spec["components"]["schemas"]["NewUser"]["properties"]["name"]["minLength"] = 1
        spec["paths"]["/users"]["post"]["responses"]["201"]["links"]["DeleteUser"]["parameters"]["userId"] = (
            "$response.body#/name"
        )
