from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class Example:
    """Smallest schema and response that trigger a code; tests execute it."""

    # One-operation OpenAPI document, rendered as YAML.
    schema: dict[str, Any]
    status_code: int
    headers: dict[str, str]
    body: str


@dataclass(frozen=True, slots=True)
class Explanation:
    """Hand-written part of an `st explain` page; the renderer generates the rest."""

    # Checks that raise this code; drive "Turn it off" and "Also disables".
    checks: tuple[str, ...]
    summary: str
    why: str
    how_found: str
    fix: str
    example: Example | None = None
    # Request/response sequence for codes one response can't show; not executed.
    scenario: str = ""
    false_positives: str = ""
    # Checks in `checks` that raise this code only for GraphQL schemas.
    graphql_only: tuple[str, ...] = ()
    # Checks whose `expected-statuses` decides whether this code fires.
    tuning: tuple[str, ...] = ()
    # Replaces the generated `st replay` text where a replay can't give a verdict.
    verify: str = ""


EXPLANATIONS: dict[str, Explanation] = {
    "ST101": Explanation(
        checks=("not_a_server_error",),
        summary="""
            The API answered a request with a 5xx status, where a success or a client error belongs.
        """,
        why="""
            A 5xx means the server failed on the request: an uncaught exception, a missing null check, a
            dependency that went down. The client gets no reason it can act on. Anyone who can send that
            request can trigger the failure again, and the crash pages the on-call engineer.
        """,
        example=Example(
            schema={
                "openapi": "3.0.3",
                "paths": {"/orders": {"post": {"responses": {"201": {"description": "Created"}}}}},
            },
            status_code=500,
            headers={"Content-Type": "application/json"},
            body='{"error": "NullPointerException"}',
        ),
        how_found="""
            Schemathesis checks the status of every response in every phase. A status outside
            `expected-statuses` fails, including a 5xx the schema documents. The default list is `2xx`, `3xx`
            and `4xx`. A 5xx on a `Content-Type` probe reports `Server error on unexpected Content-Type`
            (ST102) instead.

            In the failure output, `[500] Internal Server Error:` shows the status and the body, which may
            name the exception or a trace ID. `Reproduce with:` gives the curl command for the same request.
        """,
        fix="""
            Decide whether the 5xx is a bug or a designed response.

            - A bug: find the exception in your server logs and handle that input. Return a 4xx when the
              client sent something the API can't process.
            - By design, such as `503` when an integration is not configured: allow it for that operation, as
              shown below. An allowed status hides real crashes with the same code there.
        """,
        false_positives="""
            - An operation returns a 5xx by design. In schemathesis.toml, add an `[[operations]]` table with
              `include-name = "GET /integrations/slack"` and `checks.not_a_server_error.expected-statuses =
              ["2xx", "3xx", "4xx", "503"]`.
        """,
        tuning=("not_a_server_error",),
    ),
    "ST102": Explanation(
        checks=("not_a_server_error",),
        summary="""
            The API returned a 5xx for a bad `Content-Type` and handled the same request with a declared type.
        """,
        why="""
            Clients send a wrong `Content-Type` by mistake: a form post to a JSON endpoint, a multipart
            request without a boundary. The API crashes where a `415` or `400` would tell the client what to
            change. Anyone can send that header, trigger the crash and page the on-call engineer.
        """,
        scenario="""
            POST /orders
            Content-Type: text/plain
            response: 500 Internal Server Error

            POST /orders  (same request, control)
            Content-Type: application/json
            response: 201 Created
        """,
        how_found="""
            In the coverage phase, Schemathesis sends `Content-Type: multipart/form-data` without a boundary
            to each operation. Operations with a request body get a second probe with a media type they don't
            declare: `text/plain`, or `application/xml` when the operation accepts `text/plain`.

            When a probe gets a 5xx, Schemathesis resends it with the operation's own media type. The failure
            fires when that control request gets no server error. If it fails as well, the header is not the
            cause and this probe reports nothing; the operation's ordinary requests report the crash as ST101.
            Ordinary requests to the operation report the crash as `Server error` (ST101).

            The message names the header, the status the API returned and the status it should return:
            `415 Unsupported Media Type` for an undeclared type, `400 Bad Request` or `415` for a missing
            boundary.
        """,
        fix="""
            The schema is fine. The API crashes on a header it should reject.

            - Undeclared media type: reject it with `415 Unsupported Media Type` before parsing the body.
            - `multipart/form-data` without a boundary: reject it with `400 Bad Request` or `415` before the
              multipart parser runs.
        """,
        tuning=("not_a_server_error",),
    ),
    "ST103": Explanation(
        checks=("status_code_conformance",),
        summary="""
            The API returned a status code that the operation's documented responses don't list.
        """,
        why="""
            Clients generated from the schema handle the statuses it lists. An unlisted status lands in a
            generic error branch or raises in the client. The API consumer reading your docs finds no
            description of it.

            A `429` missing from the schema leaves clients without a retry plan. A `403` missing from it
            leaves them guessing whether the token or the request is wrong.
        """,
        example=Example(
            schema={
                "openapi": "3.0.3",
                "paths": {
                    "/users/me": {
                        "get": {"responses": {"200": {"description": "OK"}, "404": {"description": "Not Found"}}}
                    }
                },
            },
            status_code=403,
            headers={"Content-Type": "application/json"},
            body='{"detail": "Forbidden"}',
        ),
        how_found="""
            Schemathesis compares each response status with the keys under the operation's `responses`. Ranges
            such as `2XX` count, and a `default` response accepts every status.

            The message shows both sides: `Received: 403` is what the API sent, and `Documented: 200, 404` is
            what the schema lists.
        """,
        fix="""
            Decide which side is wrong.

            - The status is part of the API's behavior: add it to the operation's `responses` with a
              description. Add a schema when it has a body, so regenerated clients can handle it.
            - A `400` or `422` for invalid input: Schemathesis sends invalid data on purpose, and the API
              rejects it with a status the schema omits. Document that error response and its body.
            - The API should not return it: fix the handler. A 5xx here triggers a `Server error` failure as
              well.
            - A `401` or `403` from a run without credentials: pass them with `--header` and rerun. Document
              `401` anyway when the operation requires auth.

            Adding a `default` response silences every undocumented status for the operation. Clients then
            lose the list of errors they need to handle.
        """,
    ),
    "ST104": Explanation(
        checks=("content_type_conformance", "response_schema_conformance"),
        summary="""
            The API sent a response without a `Content-Type` header, where the schema documents a media type.
        """,
        why="""
            Clients choose a parser from `Content-Type`. Without the header, an HTTP library may hand a JSON
            body to your consumer as text or bytes. A generated client then fails to deserialize it. A client
            that checks the header before parsing rejects the response.
        """,
        example=Example(
            schema={
                "openapi": "3.0.3",
                "paths": {
                    "/users": {
                        "get": {
                            "responses": {
                                "200": {
                                    "description": "OK",
                                    "content": {"application/json": {"schema": {"type": "object"}}},
                                }
                            }
                        }
                    }
                },
            },
            status_code=200,
            headers={},
            body='{"id": 1}',
        ),
        how_found="""
            Schemathesis looks up the media types the schema documents for the response status. When that list
            is not empty and the response has no `Content-Type` header, the check fails. The message starts
            with `The following media types are documented in the schema:` and lists them.

            When the documented media type has a schema, response schema validation reports the same failure,
            since it picks the body schema by `Content-Type`. It then validates the body against the
            documented media type.
        """,
        fix="""
            Decide whether the response has a body. The failure output prints it under the status line, or
            `<EMPTY>` when there is none.

            - It has a body: set `Content-Type` in the API. Responses written as raw bytes or strings need the
              header set by hand.
            - It has no body, for example an empty `200`: remove `content` from that response in the schema.
              Under a JSON media type, an empty body also fails as `JSON deserialization error` (ST109).
              Removing `content` clears both failures.
        """,
    ),
    "ST105": Explanation(
        checks=("content_type_conformance",),
        summary="""
            The API sent a `Content-Type` that matches none of the media types the schema documents.
        """,
        why="""
            A generated client parses the body by the documented media type. An HTML page sent with
            `text/html` where the schema promises JSON breaks that parser. The API consumer sees a parse error
            and never sees what the API meant to say.
        """,
        example=Example(
            schema={
                "openapi": "3.0.3",
                "paths": {
                    "/users": {
                        "get": {
                            "responses": {
                                "200": {
                                    "description": "OK",
                                    "content": {"application/json": {"schema": {"type": "object"}}},
                                }
                            }
                        }
                    }
                },
            },
            status_code=200,
            headers={"Content-Type": "text/html; charset=utf-8"},
            body="<html><body>Welcome</body></html>",
        ),
        how_found="""
            Schemathesis compares the type and subtype of the response `Content-Type` with each media type the
            schema documents for the status. Parameters such as `charset` don't count, and wildcards in the
            schema, such as `application/*`, match.

            The message shows `Received:` with the header value and `Documented:` with the media types from
            the schema.
        """,
        fix="""
            Decide which side is wrong.

            - The API is wrong: return the documented media type. Error handlers and middleware write
              responses outside the route's serializer, so check those first.
            - The schema is wrong: add the media type under the response's `content`, with its schema. Each
              extra media type is one more format every client has to handle.
        """,
    ),
    "ST106": Explanation(
        checks=("content_type_conformance",),
        summary="""
            The response's `Content-Type`, or a media type in the schema, is not a valid `type/subtype` value.
        """,
        why="""
            A client can't choose a parser for a media type it can't parse. HTTP libraries fall back to raw
            bytes or raise. A malformed value in the schema reaches every client built from it.
        """,
        example=Example(
            schema={
                "openapi": "3.0.3",
                "paths": {
                    "/users": {
                        "get": {
                            "responses": {
                                "200": {
                                    "description": "OK",
                                    "content": {"application/json": {"schema": {"type": "object"}}},
                                }
                            }
                        }
                    }
                },
            },
            status_code=200,
            headers={"Content-Type": "application-json"},
            body='{"id": 1}',
        ),
        how_found="""
            Schemathesis walks the media types the schema documents for the status, in order. It parses each
            one and the response header, and stops at the first match. The first line of the message says
            which side failed to parse: `Media type for Response is incorrect` or `Media type for Schema is
            incorrect`. `Received:` shows the malformed value.

            A malformed response header also fails response schema validation, as `Content deserialization
            error` (ST110). Fixing the header clears both failures.
        """,
        fix="""
            The first line of the message tells you which side to fix.

            - `for Response`: fix the header the API sends. Look for a missing `/`, such as `application-json`
              or `json`.
            - `for Schema`: fix the media type key under the response's `content` in the schema.
        """,
    ),
    "ST107": Explanation(
        checks=("response_headers_conformance",),
        summary="""
            The response lacks a header that the schema marks as required for its status.
        """,
        why="""
            Clients read documented headers to decide what to do next: `Location` after a create,
            `X-RateLimit-Remaining` before the next call, `ETag` for caching. A client built from the schema
            expects the header. It fails, or falls back to a default that is wrong for your API.
        """,
        example=Example(
            schema={
                "openapi": "3.0.3",
                "paths": {
                    "/items": {
                        "get": {
                            "responses": {
                                "200": {
                                    "description": "OK",
                                    "headers": {
                                        "X-RateLimit-Remaining": {"required": True, "schema": {"type": "integer"}}
                                    },
                                }
                            }
                        }
                    }
                },
            },
            status_code=200,
            headers={"Content-Type": "application/json"},
            body="[]",
        ),
        how_found="""
            Schemathesis finds the response definition for the status, which can be an exact code, a range
            such as `2XX`, or `default`. It checks each header that definition declares. A header with
            `required: true` that the response lacks fails the check. The failure output lists every missing
            header under `The following required headers are missing from the response:`.
        """,
        fix="""
            Decide which side is wrong.

            - The API is wrong: send the header on every response the definition covers, including error
              responses under `default` or a range such as `4XX`.
            - The schema is wrong: drop `required: true`, or move the header to the statuses that send it.
              Clients then have to handle its absence.
        """,
        false_positives="""
            - A gateway or proxy adds the header, and the run targets the app behind it. Point `st run` at the
              gateway URL, the one your clients call.
        """,
    ),
    "ST108": Explanation(
        checks=("response_schema_conformance", "response_headers_conformance"),
        summary="""
            A response body or header holds a value that its documented schema rejects.
        """,
        why="""
            Generated clients and typed SDKs trust the schema. A `null` where the schema promises a string
            becomes a crash in the consumer's code, far from your API, with no hint of the cause. Every
            consumer who wrote code against the documented shape carries that bug.
        """,
        example=Example(
            schema={
                "openapi": "3.0.3",
                "paths": {
                    "/users/me": {
                        "get": {
                            "responses": {
                                "200": {
                                    "description": "OK",
                                    "content": {
                                        "application/json": {
                                            "schema": {"type": "object", "properties": {"email": {"type": "string"}}}
                                        }
                                    },
                                }
                            }
                        }
                    }
                },
            },
            status_code=200,
            headers={"Content-Type": "application/json"},
            body='{"email": null}',
        ),
        how_found="""
            Schemathesis validates the body against the schema for the response status and media type, and
            each documented header against its schema. The title names the part: `Response violates schema`,
            `Response header does not conform to the schema`, or an `SSE ...` title for Server-Sent Events.

            In the failure output for a body:

            - The first line names the violation, such as `null is not of type "string"`.
            - `Validated against the response schema for status code 200.` names the response definition. A
              `default` or range match names that key instead.
            - `Schema at /properties/email:` gives the location in the schema and the subschema that failed.
            - `Value:` shows what the API returned.
        """,
        fix="""
            Decide which side is wrong.

            - The API is wrong: fix the serializer or the data. A `null` points to a field your storage allows
              to be empty.
            - The schema is wrong: change it to match, for example `nullable: true` in OpenAPI 3.0. Widening
              the schema weakens what clients can rely on.

            Too many `format` failures in bodies: set `validate-formats = false` under
            `[checks.response_schema_conformance]`. Body format violations then go unreported, so turn it back
            on once you fix the structural failures. The setting leaves header formats checked.
        """,
    ),
    "ST109": Explanation(
        checks=("response_schema_conformance", "not_a_server_error"),
        summary="""
            The API sent a body that is not valid JSON, under a JSON `Content-Type` or with none.
        """,
        why="""
            Every JSON client fails on this body before your data reaches it. The consumer gets a parser error
            and can't tell a truncated response from a server bug.
        """,
        example=Example(
            schema={
                "openapi": "3.0.3",
                "paths": {
                    "/users/me": {
                        "get": {
                            "responses": {
                                "200": {
                                    "description": "OK",
                                    "content": {"application/json": {"schema": {"type": "object"}}},
                                }
                            }
                        }
                    }
                },
            },
            status_code=200,
            headers={"Content-Type": "application/json"},
            body='{"id": 42, "email": "a@b.c"',
        ),
        how_found="""
            When the schema documents a body schema for the status and media type, Schemathesis parses the
            body. It parses a response without `Content-Type` as the documented media type, which also fails
            ST104. A JSON parse error fails the check. A lone Unicode surrogate escape such as `\\uDCF3` fails
            too, when the schema validates that string.

            The message starts with `Response must be valid JSON with 'Content-Type: application/json'
            header:` and quotes the parser error with its position, such as `Expecting ',' delimiter: line 1
            column 28 (char 27)`. A lone surrogate failure starts with `Response contains invalid JSON (lone
            Unicode surrogate characters are not valid per RFC 8259):` instead.

            For GraphQL schemas, a non-JSON body fails here when its status passes `not_a_server_error`: 2xx,
            3xx or 4xx by default.
        """,
        fix="""
            Decide which side is wrong.

            - The API is wrong: look for truncated output or text printed before the body. Fix the handler.
              For GraphQL, this is the only side.
            - Empty body (`Expecting value: line 1 column 1 (char 0)`): send a JSON value, or remove `content`
              from that response in the schema.
            - No `Content-Type`: send the header with the media type the schema documents.
            - Not JSON by design: send the real `Content-Type` and document that media type.
        """,
        graphql_only=("not_a_server_error",),
    ),
    "ST110": Explanation(
        checks=("response_schema_conformance",),
        summary="""
            The API sent a body that the parser for its `Content-Type` can't read.
        """,
        why="""
            A client that trusts the `Content-Type` fails on the body before it reaches any data. The consumer
            gets a parser error with no hint of what the API meant to send.
        """,
        example=Example(
            schema={
                "openapi": "3.0.3",
                "paths": {
                    "/config": {
                        "get": {
                            "responses": {
                                "200": {
                                    "description": "OK",
                                    "content": {"application/yaml": {"schema": {"type": "object"}}},
                                }
                            }
                        }
                    }
                },
            },
            status_code=200,
            headers={"Content-Type": "application/yaml"},
            body="name: [unclosed",
        ),
        how_found="""
            When the schema documents a body schema for the status, Schemathesis parses the body by its
            `Content-Type`. JSON syntax errors go to ST109. These fail here:

            - Errors from the YAML parser.
            - Bytes that don't decode in the `charset` that `Content-Type` names, for JSON and YAML bodies. An
              `application/` media type without `charset` decodes as UTF-8. The message then quotes `codec
              can't decode byte`.
            - Errors from a deserializer you registered with `@schemathesis.deserializer`.

            Schemathesis skips media types without a deserializer. The message starts with `Failed to
            deserialize response content:` and quotes the parser's error, with line and column when the parser
            gives them.
        """,
        fix="""
            Decide which side is wrong.

            - The API is wrong: fix the serializer so the body is valid for the media type it declares.
            - The encoding is wrong: encode the body as UTF-8, or name the encoding it uses in the `charset`
              parameter of `Content-Type`.
            - The header is wrong: send the `Content-Type` that matches the body, and document that media type
              in the schema.
        """,
        false_positives="""
            - A deserializer you registered raises on valid content. Schemathesis reports any exception from
              it as this failure. Run your deserializer on the response body from the failure output, and fix
              it if it rejects valid content.
        """,
    ),
    "ST111": Explanation(
        checks=("response_schema_conformance",),
        summary="""
            The response's discriminator property holds a value that the schema maps to no known variant.
        """,
        why="""
            Generated clients pick the type to build from the discriminator value. An unknown value makes the
            client fail, or fall back to a base type that drops fields. Each client hits this as soon as the
            API ships a variant that the schema lacks.
        """,
        example=Example(
            schema={
                "openapi": "3.0.3",
                "paths": {
                    "/pets": {
                        "get": {
                            "responses": {
                                "200": {
                                    "description": "OK",
                                    "content": {
                                        "application/json": {
                                            "schema": {"type": "array", "items": {"$ref": "#/components/schemas/Pet"}}
                                        }
                                    },
                                }
                            }
                        }
                    }
                },
                "components": {
                    "schemas": {
                        "Pet": {
                            "anyOf": [{"$ref": "#/components/schemas/Cat"}, {"$ref": "#/components/schemas/Dog"}],
                            "discriminator": {
                                "propertyName": "kind",
                                "mapping": {"cat": "#/components/schemas/Cat", "dog": "#/components/schemas/Dog"},
                            },
                        },
                        "Cat": {"type": "object"},
                        "Dog": {"type": "object"},
                    }
                },
            },
            status_code=200,
            headers={"Content-Type": "application/json"},
            body='[{"kind": "cat"}, {"kind": "parrot"}]',
        ),
        how_found="""
            Schemathesis follows the response schema into referenced components, array items and nested
            properties. Wherever a schema on that path declares a `discriminator`, it reads that `propertyName`
            from the matching part of the body. It does not look inside `allOf`, `oneOf` or `anyOf` branches, or
            `additionalProperties`. The known values are the `mapping` keys and the names of schemas referenced
            under `oneOf` or `anyOf`. A string outside that set fails.

            The message starts with `Response contains discriminator property 'kind' with value 'parrot',` and
            lists the known values: `'Cat', 'Dog', 'cat', 'dog'`. It does not say where in the body the value
            sits; search the body for the value. It reports one value per response, so a replay after your fix
            can report the next one.
        """,
        fix="""
            Decide which side is wrong.

            - A new variant: add its schema under `oneOf` or `anyOf` and a `mapping` entry. Clients regenerate
              to use it.
            - A wrong value, such as a renamed enum member: fix the value the API writes, in every operation
              that returns this type.
        """,
    ),
    "ST112": Explanation(
        checks=("max_response_time",),
        summary="""
            The API took longer to respond than the response time limit you set.
        """,
        why="""
            A slow response holds a client connection and a server worker for its whole duration. Callers with
            their own timeouts give up and retry, adding load to a server that is already slow.
        """,
        scenario="""
            st run openapi.yaml --url http://127.0.0.1:8000 --max-response-time 1.5

            GET /reports?year=2026
            response: 200 OK after 2.5 s
        """,
        how_found="""
            The check runs once you set a limit in seconds, with `--max-response-time` or `max_response_time`
            under `[checks]` in schemathesis.toml. Schemathesis times each response from sending the request
            to receiving its headers, and compares that time with the limit. A slow body download after the
            headers does not count. The message shows both values in milliseconds: `Actual: 2500.00ms` and
            `Limit: 1500.00ms`.

            The request timeout, `--request-timeout`, aborts a request that runs too long. This check lets the
            response finish and then compares its time.
        """,
        fix="""
            Decide whether the API or the limit is wrong.

            - The API is slow: profile the request from `Reproduce with:` and fix the slow path, such as a
              missing index or an unbounded query.
            - One operation is slow by design, such as a report export: give it its own limit. Add an
              `[[operations]]` entry with `include-path = "/reports"` and `checks.max_response_time = 5.0`.
              The other operations keep the tighter limit.
            - The limit is too tight for the whole API: raise it. A higher limit lets slower regressions
              through unnoticed.
        """,
        false_positives="""
            - The server or the machine running Schemathesis is under load. Response time changes from run to
              run. Rerun against a quiet environment before you treat one slow response as a bug.
        """,
        verify="""
            Schemathesis stores no replay case for this failure, because response time changes from run to
            run. Run the `Reproduce with:` command a few times with `-o /dev/null -s -w
            '%{time_starttransfer}\\n'` added. It prints the seconds until the first response byte, the time
            the check measures. Compare them with the limit, then rerun `st run` with the same limit.
        """,
    ),
    "ST201": Explanation(
        checks=("negative_data_rejection", "not_a_server_error"),
        summary="""
            The API accepted a request that breaks the schema, where it should have answered with an error
            status.
        """,
        why="""
            The schema promises limits the API does not enforce. An attacker sends a value past those limits,
            such as a page size of a million, and the server loads every row. A write past the limits stores
            a record that clients built from the schema fail to parse.
        """,
        scenario="""
            request:
              GET /items?limit=1000          (schema: limit is an integer, maximum 100)
            response:
              HTTP/1.1 200 OK
              Content-Type: application/json

              []
        """,
        how_found="""
            Schemathesis generates negative test cases: requests that break a schema constraint, such as a value
            above `maximum`, a wrong type or a missing required property. They run in `--mode negative` and
            `--mode all`, the default. The API must answer with a status from `expected-statuses`. The default
            list includes `5xx`, because `not_a_server_error` reports server errors as ST101.

            In the failure output for OpenAPI, `Invalid component:` names the parameter, its location and the
            broken constraint. `Expected:` lists the rejection statuses, and the `[200] OK:` line below the
            message shows what the API returned. For GraphQL, the message describes the invalid input.

            Schemathesis skips values that turn valid on the wire, such as `"5"` for an integer query
            parameter, and undeclared query, header or cookie parameters. For GraphQL, the API must return an
            `errors` entry for an invalid query.
        """,
        fix="""
            Decide which side is wrong:
            - The API is wrong: the schema states the rule you mean. Validate the component named in `Invalid
              component:` and return 400 or 422.
            - The schema is wrong: the API accepts the value on purpose. Relax the constraint in the schema.
              Clients and validators generated from it lose that guarantee.
            - GraphQL: the API returned `data` and no `errors` for the input the message describes. Reject
              that input with an `errors` entry.
        """,
        false_positives="""
            - The API rejects the request with a status outside the list, such as 410 or 413. Copy the list
              under Tuning, add that status and set it in schemathesis.toml.
        """,
        graphql_only=("not_a_server_error",),
        tuning=("negative_data_rejection",),
    ),
    "ST202": Explanation(
        checks=("positive_data_acceptance",),
        summary="""
            The API rejected a request that the schema describes as valid.
        """,
        why="""
            A generated client builds its requests from the schema. When the API rejects them, the client
            fails on input its users had no reason to doubt. The integrator then debugs a contract the API
            does not honor, and learns each hidden rule from a failed request.
        """,
        scenario="""
            request:
              POST /users
              {"name": "a"}                  (schema: name is a string, no minLength)
            response:
              HTTP/1.1 422 Unprocessable Entity
              Content-Type: application/json

              {"detail": "name must be at least 3 characters"}
        """,
        how_found="""
            Schemathesis generates positive test cases that satisfy the schema. They run in `--mode positive`
            and `--mode all`, the default. The API must answer with a status from `expected-statuses`, which
            by default also tolerates auth, missing-resource, conflict, rate-limit and server-error statuses.

            In the failure output, `Expected:` lists those statuses and the status line shows what the API
            returned, often `[400]` or `[422]`. The response body may name the field the API refused. When the
            body carries properties the schema does not declare and the rest of it is valid, a `Hint:` line
            names them.

            Schemathesis ignores 400 and 422 from an operation that issues credentials, since generated
            credentials do not exist. It finds that operation by the `tokenUrl` in the schema or a configured
            dynamic auth path.
        """,
        fix="""
            Decide which side is wrong:
            - The API is wrong: it rejects a value the contract allows. Make the API accept it.
            - The schema is wrong: the API enforces a rule the schema omits, such as `pattern`, `minLength` or
              `enum`. Add it to the schema. Clients regenerated from the stricter schema reject values they
              sent before.
            - The `Hint:` line names extra properties: declare `additionalProperties: false` if the API
              refuses extras, or make the API ignore unknown fields.
        """,
        false_positives="""
            - The API answers 400 or 422 to a value that refers to data it lacks, such as an unknown
              `category_id`. A cross-field rule JSON Schema cannot express does the same. Allow that status
              for the affected operation only. Add an `[[operations]]` table with `include-name = "POST
              /orders"`. In it, set `checks.positive_data_acceptance.expected-statuses` to the list under
              Tuning plus that status. Schemathesis then stops reporting real rejections with that status on
              that operation.
        """,
        tuning=("positive_data_acceptance",),
    ),
    "ST203": Explanation(
        checks=("missing_required_header",),
        summary="""
            The API did not reject a request that lacks a header the schema marks as required.
        """,
        why="""
            A required header carries part of the contract: an API key, a tenant ID, an idempotency key. When
            the API runs without it, the server falls back to a value the client never chose. A missing
            `Authorization` that still gets 200 means an attacker needs no credentials at all.
        """,
        scenario="""
            request:
              GET /reports                   (schema: header X-API-Key, required)
              (no X-API-Key header)
            response:
              HTTP/1.1 200 OK
              Content-Type: application/json

              [{"id": 1}]
        """,
        how_found="""
            In the coverage phase, Schemathesis sends a request that omits one required header and keeps the
            rest valid. The API must answer with a status from `expected-statuses`. For `Authorization`, the
            only accepted status is 401, and `expected-statuses` does not apply.

            The message names the header, the status and the expected list: `Got 200 when missing required
            'X-API-Key' header, expected 400, 401, 403, 406, 415, 422`. A 404 does not count when the path
            holds generated parameters, since the resource may not exist.
        """,
        fix="""
            Decide which side is wrong:
            - The API is wrong: reject the request when the header is missing, with 400, or 401 for
              credentials.
            - The schema is wrong: the header is optional in practice. Set `required: false`. Clients may stop
              sending it, so the API must keep handling its absence.
            - The API answers 403 to a missing `Authorization`: return 401. A 403 means the credentials
              arrived and lack permission.
        """,
        false_positives="""
            The API rejects the request with a status outside the list, such as 428 for a missing `If-Match`.
            Copy the list under Tuning, add that status and set it in schemathesis.toml.
        """,
        tuning=("missing_required_header",),
    ),
    "ST204": Explanation(
        checks=("unsupported_method",),
        summary="""
            The API answered an undeclared method with a status other than 405, or with a 405 that has no
            `Allow` header.
        """,
        why="""
            A method that returns 2xx without documentation is an endpoint nobody reviewed. A stray `DELETE`
            or `PUT` handler can change data with no contract behind it, and an attacker finds it by trying
            methods. A 405 without `Allow` breaks RFC 9110 and leaves clients guessing which methods work.
        """,
        scenario="""
            request:
              PATCH /items                   (schema: /items declares GET and POST)
            response:
              HTTP/1.1 200 OK
              Content-Type: application/json

              {"updated": 0}
        """,
        how_found="""
            In the coverage phase, with `--mode negative` or `--mode all` (the default), Schemathesis sends each
            method from `phases.coverage.unexpected-methods` that the path does not declare. The default list is
            `get`, `put`, `post`, `delete`, `options`, `patch`, `trace` and `query`. The API must answer `405
            Method Not Allowed` with an `Allow` header. The message reports either the wrong status, as in
            `Unsupported method PATCH returned 200, expected 405 Method Not Allowed`, or a 405 that lacks
            `Allow`.

            Schemathesis does not report 404 when the path holds generated parameters, 401 or 403 on
            operations that require auth, or 429. `OPTIONS` responses go to ST205 instead.

            After the first failure, Schemathesis skips the remaining cases for that operation, so later
            methods, `OPTIONS` among them, can go untested. Add `--continue-on-failure` to `st run` to test
            every method in one run.
        """,
        fix="""
            Decide which side is wrong:
            - The API is wrong: answer undeclared methods with 405 and list the supported methods in `Allow`.
            - The schema is wrong: the method is a real feature. Document it in the schema.
            - You don't want these requests sent: set `phases.coverage.unexpected-methods` to the methods you
              care about, or `[]`. Turning the check off still sends them. Dropping `options` from the list
              also stops ST205 checks on paths that don't declare `OPTIONS`.
        """,
        false_positives="""
            - The API answers 501 to a method it does not recognize, such as `QUERY` or `TRACE`. RFC 9110
              allows 501 for an unknown method, and Schemathesis still reports it. Remove that method from
              `phases.coverage.unexpected-methods`.
        """,
    ),
    "ST205": Explanation(
        checks=("allow_header_conformance",),
        summary="""
            The response to `OPTIONS` lists different methods in `Allow` than the schema declares for this path.
        """,
        why="""
            Clients and gateways read `Allow` to learn what a resource supports. A documented method missing
            from `Allow` makes them skip an operation that works. An extra method advertises an endpoint the
            schema never documented, which may expose functionality nobody meant to publish.
        """,
        scenario="""
            request:
              OPTIONS /items                 (schema: /items declares GET and POST)
            response:
              HTTP/1.1 200 OK
              Allow: GET, HEAD, OPTIONS
        """,
        how_found="""
            With `--mode negative` or `--mode all` (the default), the coverage phase sends `OPTIONS` to each
            path that does not declare it. `phases.coverage.unexpected-methods` includes `options` by default.
            Schemathesis checks every `OPTIONS` response that carries `Allow`, declared operations included.
            It compares the listed methods with the methods the schema declares for that path. `HEAD` and
            `OPTIONS` don't count on either side.

            The message quotes the header and lists each difference. For the example, it quotes `Allow: GET,
            HEAD, OPTIONS` and adds `Missing documented methods: POST`. An extra method appears as
            `Undocumented methods advertised: DELETE`.
        """,
        fix="""
            Decide which side is wrong:
            - The API is wrong: build `Allow` from the routes the path registers, so it matches what the API
              serves.
            - The schema is wrong: document a method that works but is missing from the schema. Remove a
              documented method the API no longer serves.
            - You don't want `OPTIONS` probes sent: remove `options` from
              `phases.coverage.unexpected-methods`. Schemathesis still checks `OPTIONS` operations the schema
              declares.
        """,
    ),
    "ST301": Explanation(
        checks=("use_after_free",),
        summary="""
            The API served a resource that a successful `DELETE` had removed earlier in the same scenario.
        """,
        why="""
            A user who deletes an account or a document expects it gone. When the API still serves it, deleted
            personal data leaks to anyone with the ID, which can breach privacy law. Clients that cached the
            ID keep acting on a record that no longer exists.
        """,
        scenario="""
            1  POST    /items     201  {"id": 7}
            2  DELETE  /items/7   204
            3  GET     /items/7   200  {"id": 7, "name": "x"}     expected 404
        """,
        how_found="""
            In the stateful phase, Schemathesis chains operations through links: it creates a resource,
            deletes it, then reads it or a path under it. A 2xx or 3xx from `GET`, `HEAD`, `OPTIONS`, `QUERY`
            or `PATCH` is a failure, since none of them can recreate the resource. A successful creation
            between the delete and the read clears the case.

            The failure message names the `DELETE` call that removed the resource. `Reproduce with:` lists
            one curl command per step, and `st replay <case id>` prints the steps as a table.
        """,
        fix="""
            Decide which side is wrong:
            - The API is wrong: remove the record, or mark it deleted and filter it out on every read path,
              caches included.
            - Neither: deleted records stay readable by design, such as a soft delete with an archive view.
              Answer the deleted path with 404 or 410 and serve the archive under another path. Schemathesis
              reports only 2xx and 3xx reads. To keep the design, add an `[[operations]]` entry for the read
              with `checks.use_after_free.enabled = false`. That operation then stops reporting real leaks.
        """,
        false_positives="""
            - A shared cache served the read. The message then ends with `The response came from a cache ...
              and may be stale`, triggered by `Age` above 0 or an `X-Cache` hit. Invalidate the cache entry on
              `DELETE`, or run Schemathesis against the origin server to confirm.
            - The `DELETE` answered `202 Accepted` and removes the record later. RFC 9110 allows that, and
              Schemathesis counts any 2xx as a completed delete. Read the resource again after the job
              finishes: a 404 then shows a timing gap, not a leak.
        """,
    ),
    "ST302": Explanation(
        checks=("ensure_resource_availability",),
        summary="""
            The API returned 404 for a resource that a successful `POST` created earlier in the scenario.
        """,
        why="""
            A client creates a resource, follows the returned ID and gets 404. It retries the POST and creates
            a duplicate, or tells a user their data was lost when it was saved.
        """,
        scenario="""
            1  POST  /users        201  {"id": "u_42"}
            2  GET   /users/u_42   404                           expected the created user
        """,
        how_found="""
            In the stateful phase, Schemathesis follows a link from a successful `POST` to an operation under
            the created path. The link fills every parameter. A 404 there is a failure. A `POST` to a collection
            counts as a creation with any 2xx or 3xx status. A `POST` to the resource's own path counts only
            with 201 or a `Location` header.

            Schemathesis skips the case when a parameter was generated instead of taken from the link, or when
            an earlier step deleted the resource. The `Created with` and `Not available with` lines in the
            message name both operations.
        """,
        fix="""
            Decide which side is wrong:
            - The API is wrong: make the read see the write. Check that both operations use the same ID
              format, and read from the primary store after a write.
            - The link is wrong: it passes the wrong response field, such as `id` where the path expects a
              `slug`. Fix the link in the schema. When the schema declares no link between the two operations,
              Schemathesis inferred it: declare an explicit one.
        """,
        false_positives="""
            - A shared cache served the read. The message then ends with `The response came from a cache ... and
              may be stale`, triggered by `Age` above 0 or an `X-Cache` hit. Bypass or invalidate the cache for
              new resources, or run Schemathesis against the origin server to confirm.
            - The `POST` answered `202 Accepted` and creates the resource later. For a `POST` to a collection,
              Schemathesis counts any 2xx or 3xx as a completed creation. Read the resource again after the job
              finishes: a 200 then shows a timing gap, not a lost write.
        """,
    ),
    "ST401": Explanation(
        checks=("ignored_auth",),
        summary="""
            The API did not reject a request with missing or invalid credentials, though the schema requires
            authentication for this operation.
        """,
        why="""
            An attacker can call this operation without an account, or with a made-up token. They read or
            change data meant for signed-in users. When the API answers another error instead of `401` or
            `403`, clients cannot tell a bad token from a broken request. Their token refresh logic does not
            run.
        """,
        scenario="""
            The schema requires a bearer token on GET /orders:

              security:
                - bearerAuth: []

            You run with a valid token. Schemathesis repeats the successful request without it:

              GET /orders   with your token       -> 200 OK  []
              GET /orders   without credentials   -> 200 OK  []

              - ST401 API accepts requests without authentication

                  Expected 401 or 403, got `200 OK` for `GET /orders`
        """,
        how_found="""
            Schemathesis checks operations whose security requirement uses an API key or an HTTP scheme, such
            as Basic or Bearer, after a `2xx` response. When the request carried credentials you configured,
            Schemathesis resends it without credentials, and with invalid credentials for each security
            scheme. A probe passes on `401`, `403` or a redirect. Once an operation passes, Schemathesis stops
            probing it for the rest of the run. When the request carried no credentials, or credentials
            Schemathesis generated, the `2xx` itself fails.

            The title names the scenario. "Accepts" means the probe got a `2xx`; "Unexpected response" means
            another status, such as `404` or `500`. The message shows that status, and adds "invalid
            credentials provided" or "generated auth likely invalid" for the invalid-credentials cases. When a
            probe fails, the request and response in the failure output are the probe's.
        """,
        fix="""
            Decide whether the operation must be private.

            - Private: the API is wrong. Check credentials before the handler runs, and answer missing or
              invalid ones with `401` or `403`.
            - Public: the schema is wrong. Set `security: []` on the operation, which overrides a top-level
              requirement, or add an empty `{}` entry to mark credentials optional. Clients then stop treating
              the operation as protected.
        """,
        false_positives="""
            - The probes trip a rate limiter or a login lockout and get `429`. Raise the limit in the test
              environment, or turn the check off for that run.
            - The API answers anonymous callers with `404` to hide the resource. Schemathesis accepts `401`,
              `403` and redirects as enforcement and reports other statuses. Add an `[[operations]]` entry
              with `include-name = "GET /orders"` and `checks.ignored_auth.enabled = false`. That operation
              then also stops reporting a `2xx` sent without credentials.
        """,
    ),
    "ST402": Explanation(
        checks=("object_level_authorization",),
        summary="""
            The API returned an object that one user owns to another user who should not see it.
        """,
        why="""
            Any signed-in user can read other users' orders, invoices or profiles by changing an id in the
            URL. OWASP lists this flaw first in its API Security Top 10 as API1, Broken Object Level
            Authorization.
        """,
        scenario="""
            With `peers = ["alice", "bob"]` under `[auth.wfc]`:

              POST /orders     as alice   -> 201  {"id": 1, "item": "book"}
              GET  /orders/1   as alice   -> 200  {"id": 1, "item": "book"}
              GET  /orders/1   as bob     -> 200  {"id": 1, "item": "book"}
              GET  /orders/1   no auth    -> 401

              - ST402 Object-level authorization bypass

                  `bob` received the `Order` that `alice` owns

                  Owner: GET /orders/1 as alice -> 200
                  Peer:  GET /orders/1 as bob -> 200 (equivalent body)
        """,
        how_found="""
            The check runs when `[auth.wfc]` lists at least two `peers`, and skips operations the schema
            declares public. A peer owns an id when its own write returned or set it. It also owns an id that
            a list returned to it and not to the others. When a peer's `GET` with an owned id succeeds,
            Schemathesis repeats it as each other peer.

            The check fails when another peer gets a `2xx` with the same object. Both bodies hold the id at
            the same place, and every field both hold has equal values. At least one such field besides the
            id must match. Fields count when the response schema declares them, or when it declares no fields.
            Before it reports, Schemathesis sends the read without credentials and requests the lists the
            peer can see. A match there marks the object as public or shared, and the check passes.

            The first message line names both users and the resource. `Owner:` and `Peer:` show the two
            requests and their statuses.
        """,
        fix="""
            Decide whether the object belongs to its owner alone.

            - Yes: the API is wrong. Check that the caller owns the object before returning it, and answer
              `403` or `404` otherwise.
            - No, it is shared by design: see False positives.
        """,
        false_positives="""
            - The object is shared by design, such as a team resource, and no list shows it to the other peer.
              Add an `[[operations]]` entry with `include-path` for that operation, such as `include-path =
              "/orders/{id}"`, and `checks.object_level_authorization.enabled = false`.
            - Two peers belong to the same team or account. List as `peers` the users whose objects must stay
              private from each other.
        """,
    ),
    "ST501": Explanation(
        checks=("not_a_server_error",),
        summary="""
            The GraphQL API reported a failed resolver in the `errors` array of its response.
        """,
        why="""
            The client loses the fields that resolver should have returned. The error message can expose
            internal details to whoever sent the query. The response often carries status `200`, so monitors
            that alert on `5xx` miss the crash.
        """,
        scenario="""
            query { book(id: 7) { title } }

            200 OK
            {"data": {"book": null},
             "errors": [{"message": "division by zero", "path": ["book"]}]}

              - ST501 GraphQL server error

                  division by zero
        """,
        how_found="""
            Schemathesis reads the body when the status is in `expected-statuses` under
            `[checks.not_a_server_error]`: `2xx`, `3xx` or `4xx` by default. A `5xx` status reports as ST101
            instead. A non-empty `errors` array counts as a server failure when an error carries
            `INTERNAL_SERVER_ERROR` or `DataFetchingException` in its `extensions`. Without such a code, it
            counts when no error carries a client-error code, and `data` is non-null or the first error has a
            `path`.

            The message lists the error messages, numbered when there are several. The response body shows
            each error's `path`: the field whose resolver failed.
        """,
        fix="""
            Find the resolver at the `path` and decide what the error means.

            - A crash: the API is wrong. Fix the resolver for the arguments in the query.
            - An expected outcome, such as a missing record: return it in `data`, as a null value or a result
              type. Clients then handle it as data, without parsing error messages.
        """,
        false_positives="""
            - A resolver rejects missing or invalid credentials with a code such as `UNAUTHENTICATED` or
              `FORBIDDEN`. That error carries a `path`, so it counts as a server failure. Pass credentials
              with `--header` or `--auth`.
        """,
    ),
    "ST502": Explanation(
        checks=("not_a_server_error",),
        summary="""
            The GraphQL API returned null for a field that its own schema declares non-nullable.
        """,
        why="""
            Clients generated from the schema treat the field as always present. The null spreads to the
            nearest nullable parent and erases its sibling fields, or crashes a typed client that does not
            check for null.
        """,
        scenario="""
            type Query { authorName: String! }

            query { authorName }

            200 OK
            {"data": null,
             "errors": [{"message": "Cannot return null for non-nullable field Query.authorName.",
                         "path": ["authorName"]}]}

              - ST502 GraphQL schema violation

                  Cannot return null for non-nullable field Query.authorName.
        """,
        how_found="""
            Schemathesis reads the body when the status counts as a non-error, as for ST501. Every error in
            `errors` must report a non-nullable field that resolved to null. Schemathesis knows the messages
            of graphql-js, graphql-core, graphql-java and Spring for GraphQL, and the graphql-java
            classification `NullValueInNonNullableField`. A response with other errors besides these reports
            as ST501, so those errors stay visible.

            The message names the field, `Query.authorName` above. The `path` in the response body shows where
            the field sits in the query.
        """,
        fix="""
            Decide whether the field can be absent.

            - It cannot: the API is wrong. Fix the resolver, or the data it reads, so it always returns a
              value.
            - It can: the schema is wrong. Make the field nullable. Clients must then handle null, and
              generated client types change.
        """,
    ),
    "ST503": Explanation(
        checks=("not_a_server_error",),
        summary="""
            The GraphQL API rejected a query that its published schema accepts as valid.
        """,
        why="""
            The server enforces rules its schema does not publish. Clients built from that schema pass type
            checks, then fail at runtime with an error their authors could not see in the schema.
        """,
        scenario="""
            input Range { from: Int!, to: Int! }
            type Query { events(range: Range!): [Event] }

            query { events(range: {from: 5, to: 1}) { id } }

            200 OK
            {"errors": [{"message": "`from` must not exceed `to`",
                         "extensions": {"code": "BAD_USER_INPUT"}}]}

              - ST503 GraphQL client error

                  `from` must not exceed `to`
        """,
        how_found="""
            Schemathesis sends a query the schema accepts and reads the body when the status counts as a
            non-error. It treats the response as a rejection when an error carries a client-error code and no
            error carries a server-error code. Client-error codes include `BAD_USER_INPUT`,
            `GRAPHQL_VALIDATION_FAILED` and graphql-java's `ValidationError`. Without a recognized code, the
            response counts as a rejection when its `data` is missing or null and the first error has no
            `path`.
            Queries Schemathesis makes invalid on purpose expect this rejection and do not fail.

            The message is the first error's message. The response body shows every error with its
            `extensions`.
        """,
        fix="""
            Decide whether the server's rule is intended.

            - It is not: the API is wrong. Relax the validation that rejected the query.
            - It is, and GraphQL can express it: the schema is wrong. Express the rule as an enum or a custom
              scalar. Changing an argument type breaks existing clients.
            - It is, and GraphQL cannot express it, such as `from` not exceeding `to`: document the rule in
              the argument's description. Then add an `[[operations]]` entry with
              `include-name = "Query.events"` and `checks.not_a_server_error.enabled = false`. That also hides
              ST101, ST109, ST201 and ST501-ST504 for that field, including `5xx` crashes.
        """,
        false_positives="""
            - The server rejects the query for missing or invalid credentials. Pass them with `--header` or
              `--auth`.
            - A strategy you registered with `schemathesis.graphql.scalar` produces values the server rejects.
              Narrow the strategy to the values the scalar accepts.
            - The server rejects queries that are not persisted and answers `PERSISTED_QUERY_NOT_FOUND`. Allow
              ad hoc queries in the test environment.
        """,
    ),
    "ST504": Explanation(
        checks=("not_a_server_error",),
        summary="""
            The GraphQL API returned a JSON value other than an object, such as a list, a string or null.
        """,
        why="""
            Every GraphQL client expects an object with `data` or `errors`. A list or a bare string breaks the
            client's response parser before application code sees the result.
        """,
        scenario="""
            query { books { title } }

            200 OK
            Content-Type: application/json

            ["Dune", "Emma"]

              - ST504 Unexpected GraphQL Response

                  GraphQL response is not a JSON object
        """,
        how_found="""
            Schemathesis parses the body as JSON when the status is in `expected-statuses` for
            `not_a_server_error`: 2xx, 3xx and 4xx by default. It does so for positive and negative test
            cases. A body that is not JSON reports as ST109. JSON that is not an object reports as this code.

            The message is the same for every case. The response body below it shows the value the server
            sent.
        """,
        fix="""
            The API is wrong: the schema has no say over the response envelope. Return an object such as
            `{"data": ...}`. Check handlers that write the body themselves, such as error handlers or
            middleware.
        """,
    ),
    "ST901": Explanation(
        checks=(),
        summary="""
            The API broke a rule that a custom check, registered with `@schemathesis.check`, enforces.
        """,
        why="""
            The check guards a rule the schema cannot express, such as an audit header on every write. When
            the API breaks it, whatever depends on that rule, such as an audit log, receives wrong or missing
            data.
        """,
        scenario="""
            @schemathesis.check
            def check_audit_trail(ctx, response, case):
                if case.method == "POST" and "x-audit-id" not in response.headers:
                    raise AssertionError("Data modification missing audit trail")

              - ST901 Custom check failed: `check_audit_trail`

                  Data modification missing audit trail
        """,
        how_found="""
            Schemathesis runs every enabled check against each response. A check that raises
            `AssertionError` produces a failure titled "Custom check failed". The title carries the check's
            name in backticks: the function name, or the class name for a class-based check. The message
            below the title is the assertion message, empty when the assertion has none. Response header
            names reach the check in lowercase.

            A class-based check that fails in `after_run` shows under "Run checks", with no case ID. An
            unexpected exception there reads "Check `<name>` raised an unexpected error". A check that raises
            its own `Failure` subclass shows the title that subclass sets.

            To find the logic, search for `def <name>` or `class <name>` under `@schemathesis.check`. The CLI
            loads checks from the module or file named in `SCHEMATHESIS_HOOKS` or in the `hooks` key of
            schemathesis.toml. pytest loads them from `conftest.py`.
        """,
        fix="""
            Decide whether the rule holds for the request in the failure.

            - It holds: the API is wrong. Fix the API for that request.
            - It does not: the check is wrong. Narrow it, for example skip operations or methods it does not
              cover. Until then, `[checks.<name>]` with `enabled = false` in schemathesis.toml turns it off.
            - The check raised an unexpected error: fix the bug in the check.
        """,
        verify="""
            For a function check, run `st replay <case id>` with the same `SCHEMATHESIS_HOOKS` or `hooks`
            setting as the run. Without it, replay reports "check not available to re-run". Replay cannot
            rerun class-based checks or `after_run` failures, which have no case ID. For those, rerun `st run`
            and confirm the failure is gone.
        """,
    ),
}
