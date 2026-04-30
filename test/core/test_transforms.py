import pytest

from schemathesis.core.transforms import UNRESOLVABLE, resolve_pointer_all

DOC = {
    "data": [
        {"id": 1, "tags": ["a", "b"]},
        {"id": 2, "tags": ["c"]},
        {"id": 3, "tags": []},
    ],
    "meta": {"page": 1},
    "literal-star": {"*": "stays-literal"},
    "empty": [],
    "scalar": 5,
    "with~slash/key": "yes",
}

PARTIAL_BRANCHES_DOC = {"data": [{"id": 1}, {"other": 2}, {"id": 3}]}
NO_BRANCHES_MATCH_DOC = {"data": [{"a": 1}, {"b": 2}]}


@pytest.mark.parametrize(
    ("doc", "pointer", "expected"),
    [
        (DOC, "/meta/page", [1]),
        (DOC, "/data/0/id", [1]),
        (DOC, "/data/*/id", [1, 2, 3]),
        (DOC, "/data/*/tags/*", ["a", "b", "c"]),
        (DOC, "/empty/*", []),
        (DOC, "/meta/*", []),
        (DOC, "/scalar/*", []),
        # `*` fans out only over lists; literal-`*` keys are reachable via resolve_pointer.
        (DOC, "/literal-star/*", []),
        (DOC, "", [DOC]),
        (DOC, "/with~0slash~1key", ["yes"]),
        (PARTIAL_BRANCHES_DOC, "/data/*/id", [1, 3]),
        (NO_BRANCHES_MATCH_DOC, "/data/*/id", []),
    ],
    ids=[
        "literal-dict-walk",
        "literal-list-index",
        "single-wildcard",
        "nested-wildcard",
        "wildcard-empty-list",
        "wildcard-over-dict",
        "wildcard-over-scalar",
        "wildcard-over-dict-with-literal-star-key",
        "empty-pointer",
        "escaped-tilde-slash",
        "partial-branch-drop",
        "all-branches-drop",
    ],
)
def test_resolve_pointer_all_returns_matches(doc, pointer, expected):
    assert resolve_pointer_all(doc, pointer) == expected


@pytest.mark.parametrize(
    ("doc", "pointer"),
    [
        (DOC, "/missing"),
        (DOC, "/data/missing"),
        (DOC, "/data/0/missing"),
        # UNRESOLVABLE only when the pointer fails BEFORE any wildcard.
        ({"meta": {"page": 1}}, "/missing/*/id"),
        (DOC, "data/0"),
        # Mid-walk target is a scalar (neither dict nor list); fall-through path.
        ({"data": "string"}, "/data/sub"),
    ],
    ids=[
        "missing-root-key",
        "literal-segment-over-list",
        "missing-key-after-index",
        "structural-error-before-wildcard",
        "missing-leading-slash",
        "literal-segment-over-scalar",
    ],
)
def test_resolve_pointer_all_unresolvable(doc, pointer):
    assert resolve_pointer_all(doc, pointer) is UNRESOLVABLE
