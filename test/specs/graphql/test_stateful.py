import pytest
import strawberry

pytestmark = pytest.mark.usefixtures("book_id_scalar")


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_stateful_finds_non_id_chain_bug(ctx, cli, snapshot_cli):
    # A query seeds Project.fullPath; the consuming mutation errors only on a real captured path.
    api = ctx.graphql.apps.non_id_pool()
    assert (
        cli.run(
            api.schema_url,
            "--no-shrink",
            "--max-examples=30",
            "--phases=stateful",
            "-m",
            "positive",
            "-c",
            "not_a_server_error",
        )
        == snapshot_cli
    )


@pytest.mark.parametrize(
    "make_api",
    [lambda apps: apps.bare_slug(), lambda apps: apps.relay_connection()],
    ids=["bare-slug-arg", "relay-connection-producer"],
)
@pytest.mark.snapshot(replace_reproduce_with=True)
def test_stateful_finds_non_id_lookup_bug(ctx, cli, snapshot_cli, make_api):
    # Bare `slug` resolves via the enclosing return type; the producer may be a Relay connection.
    api = make_api(ctx.graphql.apps)
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


def _use_after_delete(apps):
    return apps.use_after_delete()


def _double_delete(apps):
    return apps.double_delete()


@pytest.mark.parametrize(
    "make_api",
    [_use_after_delete, _double_delete],
    ids=["use-after-delete", "double-delete"],
)
@pytest.mark.snapshot(replace_reproduce_with=True)
def test_stateful_finds_tombstone_bugs(ctx, cli, snapshot_cli, make_api):
    # Tombstones expose use-after-delete and double-delete on a known-deleted resource.
    api = make_api(ctx.graphql.apps)
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
    received: dict[str, set[str]] = {"note": set(), "updateNote": set()}

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
    assert {name: bool(ids & created) for name, ids in received.items()} == {"note": True, "updateNote": True}
