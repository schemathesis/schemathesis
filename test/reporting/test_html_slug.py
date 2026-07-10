from hashlib import md5

from schemathesis.reporting.html.slug import operation_filename


def test_operation_filename_basic():
    seen = set()
    assert operation_filename("POST /orders", seen) == "POST__orders"
    assert operation_filename("GET /users/{id}", seen) == "GET__users_id"


def test_operation_filename_special_characters():
    assert operation_filename("GET /a b/c%20d", set()) == "GET__a-b_c-20d"


def test_operation_filename_collision_gets_hash_suffix():
    seen = set()
    first = operation_filename("GET /a/b", seen)
    second = operation_filename("GET /a{/}b", seen)
    assert first == "GET__a_b"
    assert second != first
    assert second.startswith("GET__a_b-")
    assert len(second.rsplit("-", 1)[1]) == 8


def test_operation_filename_case_insensitive_collision():
    # Case-insensitive filesystems (macOS, Windows) would overwrite otherwise.
    seen = set()
    first = operation_filename("GET /users", seen)
    second = operation_filename("GET /USERS", seen)
    assert first != second


def test_operation_filename_length_cap():
    label = "GET /" + "a" * 300
    assert len(operation_filename(label, set())) <= 100


def test_operation_filename_deterministic():
    assert operation_filename("PATCH /items/{itemId}", set()) == operation_filename("PATCH /items/{itemId}", set())


def test_operation_filename_resolves_second_order_collision():
    # Even when the hash-suffixed stem is already taken, two operations must never share a page file.
    label = "GET /users/{id}"
    digest = md5(label.encode(), usedforsecurity=False).hexdigest()[:8]
    seen = {"get__users_id", f"get__users_id-{digest}"}
    stem = operation_filename(label, seen)
    assert stem.lower() not in {"get__users_id", f"get__users_id-{digest}"}
    assert stem.lower() in seen
