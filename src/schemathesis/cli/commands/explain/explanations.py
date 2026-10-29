from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Explanation:
    """Hand-written part of an `st explain` page; the renderer generates the rest."""

    # One line for `--list`, and the first line of the page.
    summary: str
    # What a reader would not guess: when the code fires, the fix that isn't obvious.
    notes: str = ""
    # Request sequence that one response can't show, printed before the notes.
    scenario: str = ""
    # Config snippet the notes lead into, printed after them.
    block: str = ""
    # Check whose `expected-statuses` decides whether this code fires, and a status to show allowing.
    tuning: tuple[str, str] | None = None


EXPLANATIONS: dict[str, Explanation] = {
    "ST101": Explanation(
        summary="5xx status for a request the API should have handled or rejected with a 4xx.",
        notes="""
            A 5xx fails even when the schema documents it. A 5xx by design, such as `503` when an integration is
            off, can be allowed for the operation:
        """,
        tuning=("not_a_server_error", "503"),
    ),
    "ST102": Explanation(
        summary=(
            "5xx for a request sent with an undeclared `Content-Type`, while the same request with a declared one "
            "succeeds."
        ),
        notes="""
            Schemathesis sends media types the operation doesn't declare, and multipart without a boundary.
            Reject them with `415` or `400` before the body parser runs.
        """,
    ),
    "ST103": Explanation(
        summary="Status code missing from the operation's documented responses.",
        notes="""
            `401` or `403` usually means the run had no credentials: pass them with `--header` or `--auth`.
            Don't add a `default` response to silence it: it accepts every status and hides real mismatches.
        """,
    ),
    "ST104": Explanation(
        summary="Response without a `Content-Type` header, though the schema documents a media type for its status.",
        notes="For a response with no body, such as `204`, remove `content` from it in the schema.",
    ),
    "ST105": Explanation(
        summary="`Content-Type` that matches none of the documented media types.",
        notes="Error handlers often send `application/problem+json`; document it on the error responses.",
    ),
    "ST106": Explanation(
        summary="`Content-Type` or a schema media type that is not a valid `type/subtype`.",
        notes="The message says which side is malformed. Usually a missing `/`, as in `application-json`.",
    ),
    "ST107": Explanation(
        summary="Required response header missing for this status.",
        notes="A required header under `default` or a range such as `4XX` applies to error responses too.",
    ),
    "ST108": Explanation(
        summary="Response body or header value that its documented schema rejects.",
        notes="If `format` alone causes most failures, skip format validation while you fix the structural ones:",
        block="""
            [checks.response_schema_conformance]
            validate-formats = false
        """,
    ),
    "ST109": Explanation(
        summary="Body that is not valid JSON, under a JSON `Content-Type` or with no `Content-Type`.",
        notes="""
            An empty body fails too (`Expecting value: line 1 column 1`): send JSON, or remove `content` from
            that response in the schema.
        """,
    ),
    "ST110": Explanation(
        summary="Body that the parser for its `Content-Type` cannot read, other than invalid JSON (ST109).",
        notes="Errors raised by your `@schemathesis.deserializer` functions are reported here too.",
    ),
    "ST111": Explanation(
        summary="Discriminator value that matches no schema in the `discriminator` mapping.",
        notes="One unknown value is reported per response, so rerunning after a fix can show the next one.",
    ),
    "ST112": Explanation(
        summary="Response slower than the configured response time limit.",
        notes="""
            Measured until the response headers arrive. `--checks all` turns it on with a 10s limit. Timings
            vary, so no replay case is stored: rerun `st run`. A slow-by-design operation can get its own
            limit, in seconds:
        """,
        block="""
            [[operations]]
            include-name = "GET /reports"
            checks.max_response_time = 30.0
        """,
    ),
    "ST113": Explanation(
        summary="GraphQL `errors` entry reporting a field that failed on the server.",
        notes="""
            An `errors` entry that isn't a query validation error counts as a resolver failure. Return an
            expected outcome, such as a missing record, in `data`.
        """,
    ),
    "ST114": Explanation(
        summary="Null for a field the GraphQL schema declares non-nullable.",
    ),
    "ST115": Explanation(
        summary="GraphQL response that is not a JSON object, such as a list, a string or null.",
    ),
    "ST116": Explanation(
        summary="GraphQL `data` that does not match the query, such as a missing field or a value of the wrong type.",
        notes="""
            Checked only for successful responses without `errors`, whose `data` must be an object or `null`. Every
            requested field must be present under its alias, with no unrequested fields, a value of its declared type,
            and no `null` for a non-nullable field.
        """,
    ),
    "ST117": Explanation(
        summary="gRPC response or status details that do not decode as the declared message.",
        notes="""
            A successful response must decode as the method's output message, and `grpc-status-details-bin` on an
            error must decode as `google.rpc.Status`. Regenerate the descriptor set if the server's protos changed.
        """,
    ),
    "ST201": Explanation(
        summary="Schema-violating request accepted.",
        notes="""
            `Invalid component:` in the failure names the broken constraint. For GraphQL, an invalid query must
            get an `errors` entry, whatever the status. A rejection with a status outside the list, such as `410`
            or `413`, can be allowed for the operation:
        """,
        tuning=("negative_data_rejection", "410"),
    ),
    "ST202": Explanation(
        summary="Schema-valid request rejected.",
        notes="""
            A `Hint:` line names undeclared properties the body carried. A rejection the schema can't express,
            such as an unknown id or a cross-field rule, can be allowed for the operation:
        """,
        tuning=("positive_data_acceptance", "422"),
    ),
    "ST203": Explanation(
        summary="Request without a required header parameter accepted.",
        notes="""
            A missing `Authorization` must get `401`; the list below covers other headers. To make a header from
            `securitySchemes` optional, add `{}` to the operation's `security`. Another rejection status, such
            as `428` for `If-Match`, can be allowed for the operation:
        """,
        tuning=("missing_required_header", "428"),
    ),
    "ST204": Explanation(
        summary="Undeclared method answered with a status other than 405, or with 405 and no `Allow` header.",
        notes="""
            To stop probing methods the API answers differently on purpose, such as `501` for `QUERY`, narrow
            the list of methods Schemathesis sends:
        """,
        block="""
            [phases.coverage]
            unexpected-methods = ["get", "put", "post", "delete", "options", "patch"]
        """,
    ),
    "ST205": Explanation(
        summary="`Allow` in the `OPTIONS` response lists different methods than the schema declares for the path.",
        notes="""
            CORS middleware or a gateway that sends one `Allow` for every path causes this: fix it there, or
            remove `options` from `phases.coverage.unexpected-methods`.
        """,
    ),
    "ST206": Explanation(
        summary="Valid GraphQL query rejected by the server.",
        notes="""
            A `schemathesis.graphql.scalar` strategy producing values the server rejects shows up here too. A
            rule GraphQL can't express, such as `from` not exceeding `to`, can be skipped for that field:
        """,
        block="""
            [[operations]]
            include-name = "Query.events"
            checks.positive_data_acceptance.enabled = false
        """,
    ),
    "ST301": Explanation(
        summary="Resource still served after a successful `DELETE` removed it.",
        scenario="""
            POST    /items     201  {"id": 7}
            DELETE  /items/7   204
            GET     /items/7   200  {"id": 7}
        """,
        notes="A soft delete that keeps records readable should answer the deleted path with `404` or `410`.",
    ),
    "ST302": Explanation(
        summary="404 for a resource that an earlier successful `POST` created.",
        scenario="""
            POST  /users        201  {"id": "u_42"}
            GET   /users/u_42   404
        """,
        notes="""
            Return the id in the format the path accepts, and read from the primary store right after a write.
            If the link passes the wrong field, such as `id` where the path expects a `slug`, declare the right
            link in the schema.
        """,
    ),
    "ST401": Explanation(
        summary="Request with missing or invalid credentials accepted on an operation that requires authentication.",
        notes="""
            After a `2xx`, Schemathesis resends the request without credentials and with invalid ones. A public
            operation needs `security: []` in the schema.
        """,
    ),
    "ST402": Explanation(
        summary="One user's object returned to another user who should not see it.",
        scenario="""
            POST /orders     as alice   201  {"id": 1, "item": "book"}
            GET  /orders/1   as alice   200  {"id": 1, "item": "book"}
            GET  /orders/1   as bob     200  {"id": 1, "item": "book"}
        """,
        notes="""
            Check that the caller owns the object, and answer `403` or `404` otherwise. An object shared by
            design, such as a team resource, can be skipped for that operation:
        """,
        block="""
            [[operations]]
            include-name = "GET /orders/{id}"
            checks.object_level_authorization.enabled = false
        """,
    ),
    "ST901": Explanation(
        summary="Response that breaks a rule a custom `@schemathesis.check` enforces.",
        notes="The failure title names the check.",
    ),
}
