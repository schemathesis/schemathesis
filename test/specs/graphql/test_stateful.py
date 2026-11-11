import hypothesis
import pytest
import strawberry

import schemathesis
from schemathesis.config import SchemathesisConfig
from test.apps.catalog.graphql import bookstore

pytestmark = pytest.mark.usefixtures("book_id_scalar")


@pytest.mark.parametrize(
    "factory",
    [bookstore.bare_slug, bookstore.relay_connection, bookstore.use_after_delete, bookstore.double_delete],
    ids=["bare-slug-arg", "relay-connection-producer", "use-after-delete", "double-delete"],
)
@pytest.mark.snapshot(replace_reproduce_with=True)
def test_stateful_finds_captured_value_bug(ctx, cli, snapshot_cli, factory):
    # Bare `slug` resolves via the enclosing return type; tombstones expose reads and deletes of a deleted resource.
    api = ctx.graphql.serve(factory())
    assert (
        cli.run(
            api.schema_url,
            "--no-shrink",
            "--max-examples=20",
            "--seed=1",
            "--phases=stateful",
            "-m",
            "positive",
            "-c",
            "not_a_server_error",
        )
        == snapshot_cli
    )


# Need enough scenarios to exercise the per-operation demote path while other resolvers stay healthy.
def test_one_slow_resolver_does_not_abort_stateful_phase(ctx, cli):
    api = ctx.graphql.serve(bookstore.slow_mutation())
    result = cli.run(
        api.schema_url,
        "--phases=stateful",
        "--max-examples=20",
        "-m",
        "positive",
        "-c",
        "not_a_server_error",
        config={"operations": [{"include-name": "Mutation.deleteBook", "request-timeout": 0.5}]},
    )
    assert "Unhealthy API" not in result.stdout
    assert "UnhealthyAPIError" not in result.stdout
    assert "Stateful" in result.stdout


def test_stateful_consumers_mostly_target_created_resources(ctx, cli):
    # Leaving a captured id unused is occasional exploration; most consumer calls must reach a created book.
    created: set[str] = set()
    updates = {"existing": 0, "unknown": 0}

    @strawberry.type
    class Book:
        id: strawberry.ID
        title: str

    @strawberry.type
    class Query:
        @strawberry.field
        def book(self, id: strawberry.ID) -> Book | None:
            return None

    @strawberry.type
    class Mutation:
        @strawberry.mutation
        def addBook(self, title: str) -> Book:
            book_id = strawberry.ID(f"book-{len(created)}")
            created.add(book_id)
            return Book(id=book_id, title=title)

        @strawberry.mutation
        def updateBook(self, id: strawberry.ID, title: str) -> Book | None:
            updates["existing" if id in created else "unknown"] += 1
            return None

    api = ctx.graphql.apps.from_schema(strawberry.Schema(Query, Mutation))
    cli.run_and_assert(
        api.schema_url,
        "--max-examples=50",
        "--seed=1",
        "--phases=stateful",
        "-m",
        "positive",
        "-c",
        "not_a_server_error",
    )
    assert updates["existing"] / (updates["existing"] + updates["unknown"]) > 0.7, updates


def test_stateful_feeds_ids_captured_through_mutation_payloads(ctx, cli):
    created: set[str] = set()
    received: dict[str, set[str]] = {"note": set(), "updateNote": set(), "deleteNote": set()}

    @strawberry.type
    class Note:
        id: strawberry.ID
        title: str | None

    @strawberry.type
    class CreateNotePayload:
        note: Note | None
        client_mutation_id: str | None

    @strawberry.type
    class UpdateNotePayload:
        note: Note | None

    @strawberry.type
    class DeleteNotePayload:
        deleted_note_id: strawberry.ID | None
        client_mutation_id: str | None

    @strawberry.input
    class NoteInput:
        title: str | None = None

    @strawberry.type
    class Query:
        @strawberry.field
        def note(self, id: strawberry.ID) -> Note | None:
            received["note"].add(id)
            return None

    @strawberry.type
    class Mutation:
        @strawberry.mutation
        def createNote(self, input: NoteInput | None = None) -> CreateNotePayload:
            note_id = strawberry.ID(f"note-{len(created)}")
            created.add(note_id)
            return CreateNotePayload(note=Note(id=note_id, title=None), client_mutation_id=None)

        @strawberry.mutation
        def updateNote(self, id: strawberry.ID, input: NoteInput | None = None) -> UpdateNotePayload:
            received["updateNote"].add(id)
            return UpdateNotePayload(note=None)

        @strawberry.mutation
        def deleteNote(self, id: strawberry.ID) -> DeleteNotePayload:
            received["deleteNote"].add(id)
            return DeleteNotePayload(deleted_note_id=id, client_mutation_id=None)

    api = ctx.graphql.apps.from_schema(strawberry.Schema(Query, Mutation))
    cli.run_and_assert(
        api.schema_url,
        "--max-examples=30",
        "--seed=1",
        "--phases=stateful",
        "-m",
        "positive",
        "-c",
        "not_a_server_error",
    )
    assert {name: bool(ids & created) for name, ids in received.items()} == {
        "note": True,
        "updateNote": True,
        "deleteNote": True,
    }


def test_hooks_and_config_headers_apply_once_to_stateful_cases(ctx):
    @strawberry.type
    class Book:
        id: strawberry.ID

    @strawberry.type
    class Query:
        @strawberry.field
        def book(self, id: strawberry.ID) -> Book | None:
            return None

    @strawberry.type
    class Mutation:
        @strawberry.mutation
        def addBook(self, title: str) -> Book:
            return Book(id=strawberry.ID("1"))

    api = ctx.graphql.apps.from_schema(strawberry.Schema(Query, Mutation))
    config = SchemathesisConfig.from_dict(
        {
            "headers": {"X-Project": "project"},
            "operations": [{"include-name": "Mutation.addBook", "headers": {"X-Operation": "operation"}}],
        }
    )
    schema = schemathesis.graphql.from_url(api.schema_url, config=config)
    seen = set()

    def increment(name):
        def hook(context, headers):
            headers = headers or {}
            headers[name] = str(int(headers.get(name, "0")) + 1)
            return headers

        return hook

    def before_call(context, case, kwargs):
        seen.add((case.operation.label, *map(case.headers.get, ("X-Global", "X-Schema", "X-Project", "X-Operation"))))

    schemathesis.hook(before_call)
    schemathesis.hook("map_headers")(increment("X-Global"))
    schema.hook("map_headers")(increment("X-Schema"))
    schema.as_state_machine().run(
        settings=hypothesis.settings(
            max_examples=5,
            deadline=None,
            database=None,
            phases=[hypothesis.Phase.generate],
            suppress_health_check=list(hypothesis.HealthCheck),
        )
    )
    assert seen == {
        ("Mutation.addBook", "1", "1", "project", "operation"),
        ("Query.book", "1", "1", "project", None),
    }
