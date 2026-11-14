import re

import pytest

import schemathesis
from schemathesis.config import SchemathesisConfig
from schemathesis.engine import Status, events
from schemathesis.engine.run import PhaseName
from schemathesis.engine.run.dictionary_loading import DictionaryLoad
from schemathesis.generation import GenerationMode
from test.apps.catalog.openapi import nested
from test.utils import EventStream

PARENTS = {"from-operation": "GET /api/parents", "pointer": "/data/*/id", "parameters": {"query.limit": 100}}
CHILDREN = "GET /api/parents/{parentId}/children"


def _config(source):
    return SchemathesisConfig.from_dict(
        {"dictionaries": {"parents": source}, "parameters": {"path.parentId": {"dictionary": "parents"}}}
    )


def _load(api, source):
    return schemathesis.openapi.from_url(api.schema_url, config=_config(source))


def _setup(stream):
    return stream.find(events.PhaseFinished, phase=lambda phase: phase.name is PhaseName.SETUP)


# The API returns integer IDs, while the path parameter declares `type: string`.
def test_fetched_values_reach_bound_parameter(ctx):
    api = ctx.openapi.serve(nested.parents_with_children())
    EventStream(
        _load(api, PARENTS), phases=[PhaseName.FUZZING], modes=[GenerationMode.POSITIVE], max_examples=10
    ).execute()
    parents = {
        match.group(1)
        for request in api.requests
        if (match := re.fullmatch(r"/api/parents/([^/]+)/children", request.path))
    }
    assert parents
    assert parents <= {"1", "2", "3"}


def test_source_request_uses_literal_parameters_and_run_headers(ctx):
    api = ctx.openapi.serve(nested.parents_with_children())
    EventStream(_load(api, PARENTS), phases=[PhaseName.FUZZING], max_examples=1, headers={"X-Run": "1"}).execute()
    request = api.calls_to("/api/parents")[0]
    assert (request.query["limit"], request.headers["X-Run"]) == ("100", "1")


# Operation filters decide what to test, not where dictionary values come from.
def test_source_operation_outside_filters_still_loads(ctx):
    api = ctx.openapi.serve(nested.parents_with_children())
    schema = _load(api, PARENTS).include(path_regex="children$")
    stream = EventStream(schema, phases=[PhaseName.FUZZING], max_examples=1).execute()
    assert _setup(stream).payload.dictionaries == [
        DictionaryLoad(dictionary="parents", operation="GET /api/parents", requests=1, entries=3)
    ]


@pytest.mark.parametrize(
    ("source", "error", "sent"),
    [
        (
            {"from-operation": "GET /api/nope", "pointer": "/data/*/id"},
            "`GET /api/nope` is not an operation in the schema",
            0,
        ),
        (
            {**PARENTS, "pointer": "/items/*/id"},
            "no values at `/items/*/id` in the `GET /api/parents` response",
            1,
        ),
        (
            {**PARENTS, "pointer": "/data/*"},
            "no values at `/data/*` in the `GET /api/parents` response",
            1,
        ),
        (
            {"from-operation": CHILDREN, "pointer": "/data/*/id", "parameters": {"path.parentId": "9"}},
            f"`{CHILDREN}` returned 404",
            1,
        ),
        (
            {"from-operation": CHILDREN, "pointer": "/data/*/id"},
            f"`{CHILDREN}` needs path parameter `parentId` - set it under `parameters`",
            0,
        ),
    ],
    ids=["unknown-operation", "no-values", "objects-only", "error-status", "missing-path-parameter"],
)
def test_failed_load_is_reported(ctx, source, error, sent):
    api = ctx.openapi.serve(nested.parents_with_children())
    schema = _load(api, source)
    stream = EventStream(schema, phases=[PhaseName.FUZZING], max_examples=1).execute()
    finished = _setup(stream)
    [load] = finished.payload.dictionaries
    reported = stream.find(events.NonFatalError, label="Dictionary `parents`")
    assert (
        finished.status,
        load.error,
        load.requests,
        schema.config.dictionaries["parents"].entries,
        str(reported.value),
    ) == (Status.ERROR, error, sent, (), f"Dictionary `parents`: {error}")


def test_dropped_connection_is_reported(app_runner):
    schema = schemathesis.openapi.from_dict(nested.parents_with_children().spec, config=_config(PARENTS))
    schema.config.update(base_url=f"http://127.0.0.1:{app_runner.run_resetting_server()}")
    stream = EventStream(schema, phases=[PhaseName.FUZZING], max_examples=1).execute()
    [load] = _setup(stream).payload.dictionaries
    assert (load.error.startswith("`GET /api/parents` request failed: "), load.requests) == (True, 1)


# A proxy or login page can answer with HTML instead of the API's JSON.
def test_non_json_response_is_reported(app_runner):
    api = nested.parents_with_children()

    @api.server.before_request
    def serve_html():
        return "<html></html>", 200

    schema = schemathesis.openapi.from_dict(api.spec, config=_config(PARENTS))
    schema.config.update(base_url=f"http://127.0.0.1:{app_runner.run_flask_app(api.server)}")
    stream = EventStream(schema, phases=[PhaseName.FUZZING], max_examples=1).execute()
    [load] = _setup(stream).payload.dictionaries
    assert (load.error, load.requests) == ("`GET /api/parents` returned a response that is not JSON", 1)


def test_from_operation_dictionary_needs_openapi(ctx):
    config = SchemathesisConfig.from_dict({"dictionaries": {"books": {"from-operation": "Query.books", "pointer": ""}}})
    schema = schemathesis.graphql.from_url(ctx.graphql.apps.books().schema_url, config=config)
    stream = EventStream(schema, phases=[PhaseName.FUZZING], max_examples=1).execute()
    [load] = _setup(stream).payload.dictionaries
    assert (load.error, load.requests) == ("`from-operation` dictionaries need an OpenAPI schema", 0)
