import re

import graphql
import pytest

import schemathesis
from schemathesis.config import SchemathesisConfig
from schemathesis.engine import Status, events
from schemathesis.engine.run import PhaseName
from schemathesis.engine.run.dictionary_loading import DictionaryLoad
from schemathesis.generation import GenerationMode
from schemathesis.generation.hypothesis.examples import generate_many
from test.apps.catalog.graphql import bookstore
from test.apps.catalog.grpc.descriptors import load as load_grpc
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
        (
            {"from-operation": "GET /api/parents", "pointer": "/data/*/id", "query": "{ parents { id } }"},
            "`query` applies only to GraphQL operations",
            0,
        ),
    ],
    ids=["unknown-operation", "no-values", "objects-only", "error-status", "missing-path-parameter", "graphql-query"],
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


def _fuzz_with_parent_and_child_bound(ctx):
    api = ctx.openapi.serve(nested.parents_with_children())
    config = SchemathesisConfig.from_dict(
        {
            "dictionaries": {"parents": PARENTS, "children": CHILDREN_SOURCE},
            "parameters": {
                "path.parentId": {"dictionary": "parents"},
                "path.childId": {"dictionary": "children"},
            },
        }
    )
    schema = schemathesis.openapi.from_url(api.schema_url, config=config)
    EventStream(
        schema, phases=[PhaseName.FUZZING], modes=[GenerationMode.POSITIVE], max_examples=20, deterministic=True
    ).execute()
    return api


def test_bound_parent_and_child_come_from_the_same_listing(ctx):
    api = _fuzz_with_parent_and_child_bound(ctx)
    pairs = {
        match.groups()
        for request in api.requests
        if (match := re.fullmatch(r"/api/parents/([^/]+)/children/([^/]+)", request.path))
    }
    assert pairs
    assert pairs <= {("1", "c11"), ("1", "c12"), ("2", "c21"), ("2", "c22")}
    assert len({parent for parent, _ in pairs}) == 2


# The children listing binds only `parentId`, so it draws from every parent, including one without children.
def test_parent_only_binding_draws_from_every_parent(ctx):
    api = _fuzz_with_parent_and_child_bound(ctx)
    parents = {
        match.group(1)
        for request in api.requests
        if (match := re.fullmatch(r"/api/parents/([^/]+)/children", request.path))
    }
    assert parents == {"1", "2", "3"}


def test_from_operation_dictionary_needs_openapi_or_graphql(ctx):
    schema = load_grpc(
        ctx.grpc.apps.users(), dictionaries={"users": {"from-operation": "pkg.Users/GetUser", "pointer": "/id"}}
    )
    [load] = _run_setup(schema).payload.dictionaries
    assert (load.error, load.requests) == ("`from-operation` dictionaries need an OpenAPI or GraphQL schema", 0)


GRAPHQL_PARENTS = {"from-operation": "Query.parents", "query": "{ parents { id } }", "pointer": "/data/parents/*/id"}
GRAPHQL_CHILDREN = {
    "from-operation": "Query.children",
    "query": "query($parentId: ID!) { children(parentId: $parentId) { id } }",
    "pointer": "/data/children/*/id",
    "parameters": {"parentId": {"dictionary": "parents"}},
}


def _load_graphql(ctx, *, parameters=None, **dictionaries):
    api = ctx.graphql.serve(bookstore.parents_with_children())
    config = SchemathesisConfig.from_dict({"dictionaries": dictionaries, "parameters": parameters or {}})
    return schemathesis.graphql.from_url(api.schema_url, config=config)


def test_graphql_child_entries_remember_parent(ctx):
    schema = _load_graphql(ctx, parents=GRAPHQL_PARENTS, children=GRAPHQL_CHILDREN)
    loads = _run_setup(schema).payload.dictionaries
    children = schema.config.dictionaries["children"].entries
    assert (loads, [(entry.value, entry.origin) for entry in children]) == (
        [
            DictionaryLoad(dictionary="parents", operation="Query.parents", requests=1, entries=3),
            DictionaryLoad(dictionary="children", operation="Query.children", requests=3, entries=4),
        ],
        [("c11", {"parents": "1"}), ("c12", {"parents": "1"}), ("c21", {"parents": "2"}), ("c22", {"parents": "2"})],
    )


@pytest.mark.parametrize(
    ("source", "error", "sent"),
    [
        (
            {"from-operation": "Query.parents", "pointer": "/data/parents/*/id"},
            "`Query.parents` needs `query` - the GraphQL query to send",
            0,
        ),
        (
            {"from-operation": "Query.children", "query": '{ children(parentId: "9") { id } }', "pointer": "/data"},
            "`Query.children` returned errors: Unknown parent `9`",
            1,
        ),
    ],
    ids=["no-query", "errors"],
)
def test_failed_graphql_load_is_reported(ctx, source, error, sent):
    [load] = _run_setup(_load_graphql(ctx, parents=source)).payload.dictionaries
    assert (load.error, load.requests) == (error, sent)


GRAPHQL_PAIRS = {("1", "c11"), ("1", "c12"), ("2", "c21"), ("2", "c22")}


def _graphql_child_arguments(ctx, child_binding):
    schema = _load_graphql(
        ctx,
        parents=GRAPHQL_PARENTS,
        children=GRAPHQL_CHILDREN,
        parameters={"Query.child.parentId": {"dictionary": "parents"}, "Query.child.childId": child_binding},
    )
    _run_setup(schema)
    pairs = []
    for case in generate_many(schema["Query"]["child"].as_strategy(), 30):
        [operation] = graphql.parse(case.body).definitions
        [field] = operation.selection_set.selections
        arguments = {argument.name.value: argument.value.value for argument in field.arguments}
        pairs.append((arguments["parentId"], arguments["childId"]))
    return pairs


def test_graphql_bound_parent_and_child_come_from_the_same_listing(ctx):
    pairs = set(_graphql_child_arguments(ctx, {"dictionary": "children"}))
    assert pairs <= GRAPHQL_PAIRS
    assert len({parent for parent, _ in pairs}) == 2


def test_graphql_child_binding_probability_decides_how_often_the_pair_is_drawn(ctx):
    pairs = _graphql_child_arguments(ctx, {"dictionary": "children", "probability": 0.5})
    assert any(pair in GRAPHQL_PAIRS for pair in pairs)
    assert any(child not in {"c11", "c12", "c21", "c22"} for _, child in pairs)


def _child_path_parameters(ctx, *, child_binding, parent_ids=None):
    app = nested.parents_with_children()
    if parent_ids is not None:
        [parent, _] = app.spec["paths"]["/api/parents/{parentId}/children/{childId}"]["get"]["parameters"]
        parent["schema"] = {"type": "string", "enum": parent_ids}
    api = ctx.openapi.serve(app)
    config = SchemathesisConfig.from_dict(
        {
            "dictionaries": {"parents": PARENTS, "children": CHILDREN_SOURCE},
            "parameters": {"path.parentId": {"dictionary": "parents"}, "path.childId": child_binding},
        }
    )
    schema = schemathesis.openapi.from_url(api.schema_url, config=config)
    _run_setup(schema)
    strategy = schema["/api/parents/{parentId}/children/{childId}"]["GET"].as_strategy()
    return {(case.path_parameters["parentId"], case.path_parameters["childId"]) for case in generate_many(strategy, 30)}


@pytest.mark.parametrize(
    ("parent_ids", "expected"),
    [
        (["1", "3"], {("1", "c11"), ("1", "c12")}),
        # Parent `3` has no children, so no pair is left and each parameter draws on its own.
        (["3"], {("3", "c11"), ("3", "c12"), ("3", "c21"), ("3", "c22")}),
    ],
    ids=["some-parents", "childless-parent"],
)
def test_children_of_parents_the_schema_rejects_are_not_paired(ctx, parent_ids, expected):
    assert _child_path_parameters(ctx, child_binding={"dictionary": "children"}, parent_ids=parent_ids) == expected


def test_rarely_drawn_child_binding_leaves_parent_to_draw_on_its_own(ctx):
    pairs = _child_path_parameters(ctx, child_binding={"dictionary": "children", "probability": 0.1})
    assert {parent for parent, _ in pairs} == {"1", "2", "3"}
