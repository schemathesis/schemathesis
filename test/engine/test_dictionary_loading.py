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
CHILDREN_SOURCE = {
    "from-operation": CHILDREN,
    "pointer": "/data/*/id",
    "parameters": {"path.parentId": {"dictionary": "parents"}},
}
TOYS_SOURCE = {
    "from-operation": "GET /api/parents/{parentId}/children/{childId}/toys",
    "pointer": "/data/*/id",
    "parameters": {"path.parentId": {"dictionary": "parents"}, "path.childId": {"dictionary": "children"}},
}


def _config(source, **dictionaries):
    return SchemathesisConfig.from_dict(
        {
            "dictionaries": {"parents": source, **dictionaries},
            "parameters": {"path.parentId": {"dictionary": "parents"}},
        }
    )


def _load(api, source, **dictionaries):
    return schemathesis.openapi.from_url(api.schema_url, config=_config(source, **dictionaries))


# The schema has no examples, so only setup sends requests.
def _run_setup(schema):
    return _setup(EventStream(schema, phases=[PhaseName.EXAMPLES]).execute())


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


# Parent `3` has no children; the listing still loads from the other parents.
def test_child_entries_remember_their_parent(ctx):
    api = ctx.openapi.serve(nested.parents_with_children())
    schema = _load(api, PARENTS, children=CHILDREN_SOURCE)
    finished = _run_setup(schema)
    entries = schema.config.dictionaries["children"].entries
    assert ([(entry.value, entry.origin) for entry in entries], finished.payload.dictionaries[1]) == (
        [("c11", {"parents": 1}), ("c12", {"parents": 1}), ("c21", {"parents": 2}), ("c22", {"parents": 2})],
        DictionaryLoad(dictionary="children", operation=CHILDREN, requests=3, entries=4),
    )


def test_grandchild_entries_remember_every_ancestor(ctx):
    api = ctx.openapi.serve(nested.parents_with_children())
    schema = _load(api, PARENTS, children=CHILDREN_SOURCE, toys=TOYS_SOURCE)
    _run_setup(schema)
    entries = schema.config.dictionaries["toys"].entries
    assert (
        [(entry.value, entry.origin) for entry in entries],
        sorted(request.path for request in api.requests if request.path.endswith("/toys")),
    ) == (
        [
            ("c11-toy", {"children": "c11", "parents": 1}),
            ("c12-toy", {"children": "c12", "parents": 1}),
            ("c21-toy", {"children": "c21", "parents": 2}),
            ("c22-toy", {"children": "c22", "parents": 2}),
        ],
        [
            "/api/parents/1/children/c11/toys",
            "/api/parents/1/children/c12/toys",
            "/api/parents/2/children/c21/toys",
            "/api/parents/2/children/c22/toys",
        ],
    )


@pytest.mark.parametrize(
    ("parents", "sent", "entries", "failed", "capped"),
    [
        # A repeated parent fetches the same children again; parent `9` does not exist.
        ([1, 1, 9], 3, 2, 1, False),
        (list(range(1, 102)), 100, 4, 97, True),
    ],
    ids=["repeated-and-unknown-parent", "capped"],
)
def test_partial_load_is_counted(ctx, parents, sent, entries, failed, capped):
    api = ctx.openapi.serve(nested.parents_with_children())
    finished = _run_setup(_load(api, {"values": parents}, children=CHILDREN_SOURCE))
    assert finished.payload.dictionaries == [
        DictionaryLoad(
            dictionary="children", operation=CHILDREN, requests=sent, entries=entries, failed=failed, capped=capped
        )
    ]


def test_empty_dependency_fails_the_dependent(ctx):
    api = ctx.openapi.serve(nested.parents_with_children())
    finished = _run_setup(_load(api, {**PARENTS, "pointer": "/items/*/id"}, children=CHILDREN_SOURCE))
    load = finished.payload.dictionaries[1]
    assert (load.error, load.requests) == (
        "source parameter `path.parentId` has no values: dictionary `parents` is empty",
        0,
    )


def test_from_operation_dictionary_needs_openapi(ctx):
    config = SchemathesisConfig.from_dict({"dictionaries": {"books": {"from-operation": "Query.books", "pointer": ""}}})
    schema = schemathesis.graphql.from_url(ctx.graphql.apps.books().schema_url, config=config)
    stream = EventStream(schema, phases=[PhaseName.FUZZING], max_examples=1).execute()
    [load] = _setup(stream).payload.dictionaries
    assert (load.error, load.requests) == ("`from-operation` dictionaries need an OpenAPI schema", 0)
