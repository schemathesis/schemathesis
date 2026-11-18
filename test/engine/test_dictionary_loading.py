import re

import graphql
import pytest

import schemathesis
from schemathesis.config import SchemathesisConfig
from schemathesis.core.parameters import ParameterLocation
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
        # Only GraphQL reports failures as `errors`; this response has a warning there.
        (
            {**PARENTS, "parameters": {"query.limit": 0}},
            "no values at `/data/*/id` in the `GET /api/parents` response",
            1,
        ),
    ],
    ids=[
        "unknown-operation",
        "no-values",
        "objects-only",
        "error-status",
        "missing-path-parameter",
        "graphql-query",
        "rest-errors-member",
    ],
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
@pytest.mark.parametrize(
    ("status", "error"),
    [(200, "`GET /api/parents` returned a response that is not JSON"), (502, "`GET /api/parents` returned 502")],
    ids=["success", "error-status"],
)
def test_non_json_response_is_reported(app_runner, status, error):
    api = nested.parents_with_children()

    @api.server.before_request
    def serve_html():
        return "<html></html>", status

    schema = schemathesis.openapi.from_dict(api.spec, config=_config(PARENTS))
    schema.config.update(base_url=f"http://127.0.0.1:{app_runner.run_flask_app(api.server)}")
    stream = EventStream(schema, phases=[PhaseName.FUZZING], max_examples=1).execute()
    [load] = _setup(stream).payload.dictionaries
    assert (load.error, load.requests) == (error, 1)


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


PAIRS = {("1", "c11"), ("1", "c12"), ("2", "c21"), ("2", "c22")}


def _generate(ctx, config, path, method, generation_mode=GenerationMode.POSITIVE):
    api = ctx.openapi.serve(nested.parents_with_children())
    schema = schemathesis.openapi.from_url(api.schema_url, config=SchemathesisConfig.from_dict(config))
    _run_setup(schema)
    return schema, generate_many(schema[path][method].as_strategy(generation_mode=generation_mode), 30)


def _read_cases(ctx, path, method, read, child_binding, generation_mode=GenerationMode.POSITIVE, parameters=None):
    parents = {"dictionary": "parents"}
    config = {
        "dictionaries": {"parents": PARENTS, "children": CHILDREN_SOURCE},
        "parameters": {
            "path.parentId": parents,
            "query.parentId": parents,
            "body.parentId": parents,
            "path.childId": child_binding,
            "body.childId": child_binding,
            "body.wardId": child_binding,
            "body.childIds[*]": child_binding,
            **(parameters or {}),
        },
    }
    _, cases = _generate(ctx, config, path, method, generation_mode)
    return [read(case) for case in cases]


def _query_parent_and_path_child(case):
    return case.query["parentId"], case.path_parameters["childId"]


def _path_parent_and_child(case):
    return case.path_parameters["parentId"], case.path_parameters["childId"]


NOTES = "/api/parents/{parentId}/children/{childId}/notes"


@pytest.mark.parametrize(
    ("path", "method", "read_pair"),
    [
        ("/api/children/{childId}", "GET", _query_parent_and_path_child),
        (
            "/api/parents/{parentId}/adoptions",
            "POST",
            lambda case: (case.path_parameters["parentId"], case.body["childId"]),
        ),
        # The API lists integer parent IDs, while this body field declares `type: string`. `wardId` is bound to
        # children too, but no child fits its `type: integer`.
        ("/api/transfers", "POST", lambda case: (case.body["parentId"], case.body["childId"])),
        # The body holds either `childId` or `wardId`, both bound to children, and the one generated takes the pair.
        (
            "/api/parents/{parentId}/guardians",
            "POST",
            lambda case: (case.path_parameters["parentId"], case.body.get("wardId", case.body.get("childId"))),
        ),
    ],
    ids=["query-and-path", "path-and-body", "body", "either-body-field"],
)
def test_parent_and_child_in_any_location_come_from_the_same_listing(ctx, path, method, read_pair):
    pairs = set(_read_cases(ctx, path, method, read_pair, {"dictionary": "children"}))
    assert pairs <= PAIRS
    assert len({parent for parent, _ in pairs}) == 2


# Only parent `1` fits `parentId`, and only the first child of each parent fits `childId`.
def test_pair_is_drawn_among_values_that_fit_every_parameter(ctx):
    pairs = set(_read_cases(ctx, NOTES, "POST", _path_parent_and_child, {"dictionary": "children"}))
    assert pairs == {("1", "c11")}


def test_negative_case_pairs_values_in_locations_it_keeps_valid(ctx):
    cases = _read_cases(
        ctx,
        NOTES,
        "POST",
        lambda case: (case.meta.components[ParameterLocation.PATH].mode, _path_parent_and_child(case)),
        {"dictionary": "children"},
        generation_mode=GenerationMode.NEGATIVE,
    )
    assert {pair for mode, pair in cases if mode.is_positive} == {("1", "c11")}


# The path takes the paired child first, so `text`, also bound to children, draws its own one.
def test_second_place_bound_to_a_paired_dictionary_draws_on_its_own(ctx):
    cases = _read_cases(
        ctx,
        NOTES,
        "POST",
        lambda case: (_path_parent_and_child(case), case.body["text"]),
        {"dictionary": "children"},
        parameters={"body.text": {"dictionary": "children"}},
    )
    assert {pair for pair, _ in cases} == {("1", "c11")}
    assert any(text != "c11" for _, text in cases)


def test_type_wide_binding_draws_on_its_own(ctx):
    config = {
        "dictionaries": {"parents": PARENTS, "children": CHILDREN_SOURCE},
        "parameters": {"query.parentId": {"dictionary": "parents"}},
        "generation": {"dictionaries": {"string": {"dictionary": "children", "probability": 1.0}}},
    }
    _, cases = _generate(ctx, config, "/api/children/{childId}", "GET")
    assert any(_query_parent_and_path_child(case) not in PAIRS for case in cases)


# Every parent lists all parents, so each value is held by several entries, each remembering another parent.
def test_paired_draw_records_the_entry_it_was_drawn_from(ctx):
    listed = {
        "from-operation": "GET /api/parents",
        "pointer": "/data/*/id",
        "parameters": {"query.limit": {"dictionary": "parents"}},
    }
    config = {
        "dictionaries": {"parents": PARENTS, "listed": listed},
        "parameters": {"query.parentId": {"dictionary": "parents"}, "path.childId": {"dictionary": "listed"}},
    }
    schema, cases = _generate(ctx, config, "/api/children/{childId}", "GET")
    entries = schema.config.dictionaries["listed"].entries
    for case in cases:
        [draw] = [draw for draw in case.meta.dictionary_draws if draw.dictionary == "listed"]
        assert str(entries[draw.entry_index].origin["parents"]) == case.query["parentId"]


# A pair fills one field, so the items of a wildcard binding keep drawing children on their own.
def test_wildcard_body_binding_draws_each_item_on_its_own(ctx):
    children = _read_cases(
        ctx,
        "/api/parents/{parentId}/adoptions/batch",
        "POST",
        lambda case: case.body["childIds"],
        {"dictionary": "children"},
    )
    assert any(len(set(items)) == 2 for items in children)


def test_child_binding_probability_decides_how_often_a_split_pair_is_drawn(ctx):
    pairs = _read_cases(
        ctx,
        "/api/children/{childId}",
        "GET",
        _query_parent_and_path_child,
        {"dictionary": "children", "probability": 0.5},
    )
    assert any(pair in PAIRS for pair in pairs)
    assert any(pair not in PAIRS for pair in pairs)


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
GRAPHQL_LOCKERS = {
    "from-operation": "Query.lockers",
    "query": "query($parentId: ID!) { lockers(parentId: $parentId) { id } }",
    "pointer": "/data/lockers/*/id",
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
        # The server answers a query that fails validation with status 400.
        (
            {"from-operation": "Query.parents", "query": "{ parents { name } }", "pointer": "/data"},
            "`Query.parents` returned 400: Cannot query field 'name' on type 'Parent'.",
            1,
        ),
    ],
    ids=["no-query", "errors", "error-status"],
)
def test_failed_graphql_load_is_reported(ctx, source, error, sent):
    [load] = _run_setup(_load_graphql(ctx, parents=source)).payload.dictionaries
    assert (load.error, load.requests) == (error, sent)


GRAPHQL_PAIRS = {("1", "c11"), ("1", "c12"), ("2", "c21"), ("2", "c22")}


def _graphql_child_arguments(ctx, child_binding, field_name="child", child_argument="childId"):
    schema = _load_graphql(
        ctx,
        parents=GRAPHQL_PARENTS,
        children=GRAPHQL_CHILDREN,
        lockers=GRAPHQL_LOCKERS,
        parameters={
            f"Query.{field_name}.parentId": {"dictionary": "parents"},
            f"Query.{field_name}.{child_argument}": child_binding,
        },
    )
    _run_setup(schema)
    pairs = []
    for case in generate_many(schema["Query"][field_name].as_strategy(), 30):
        [operation] = graphql.parse(case.body).definitions
        [field] = operation.selection_set.selections
        arguments = {argument.name.value: argument.value.value for argument in field.arguments}
        pairs.append((arguments["parentId"], arguments[child_argument]))
    return pairs


def test_graphql_bound_parent_and_child_come_from_the_same_listing(ctx):
    pairs = set(_graphql_child_arguments(ctx, {"dictionary": "children"}))
    assert pairs <= GRAPHQL_PAIRS
    assert len({parent for parent, _ in pairs}) == 2


def test_graphql_child_binding_probability_decides_how_often_the_pair_is_drawn(ctx):
    pairs = _graphql_child_arguments(ctx, {"dictionary": "children", "probability": 0.5})
    assert any(pair in GRAPHQL_PAIRS for pair in pairs)
    assert any(child not in {"c11", "c12", "c21", "c22"} for _, child in pairs)


# Parent 1 has lockers `10` and `x1`, and only `10` fits `number: Int`.
def test_graphql_pair_is_drawn_among_values_that_fit_every_argument(ctx):
    pairs = set(_graphql_child_arguments(ctx, {"dictionary": "lockers"}, field_name="locker", child_argument="number"))
    assert pairs == {("1", "10"), ("2", "20")}


# No child ID fits `number: Int`, so `parentId` draws on its own, including parent `3` without children.
def test_graphql_argument_that_no_value_fits_leaves_the_pair_undrawn(ctx):
    pairs = _graphql_child_arguments(ctx, {"dictionary": "children"}, field_name="locker", child_argument="number")
    assert {parent for parent, _ in pairs} == {"1", "2", "3"}


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
