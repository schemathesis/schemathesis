import pytest

from schemathesis.specs.openapi.negative.mutations import (
    MutationTargetDescriptor,
    WalkStep,
    _disjoint_descriptor_pool,
)


def _descriptor(*selectors, keyword="properties"):
    walk = tuple(WalkStep(keyword=keyword, selector=s) for s in selectors)
    return MutationTargetDescriptor(walk=walk)


@pytest.mark.parametrize(
    ("primary_selectors", "candidate_selectors", "included"),
    [
        (("a",), ("b",), True),
        (("a",), ("a", "b"), False),
        (("a", "b"), ("a",), False),
        (("a",), ("a",), False),
    ],
    ids=["siblings", "descendant", "ancestor", "self"],
)
def test_disjoint_pool(primary_selectors, candidate_selectors, included):
    primary = _descriptor(*primary_selectors)
    candidate = _descriptor(*candidate_selectors)
    result = _disjoint_descriptor_pool((candidate,), chosen=[primary])
    assert (candidate in result) == included


@pytest.mark.parametrize("keyword", ["oneOf", "anyOf"])
def test_sibling_branch_excluded(keyword):
    # Both branches share the same parent, so mutating one invalidates the other.
    parent = WalkStep(keyword="properties", selector="a")
    primary = MutationTargetDescriptor(walk=(parent, WalkStep(keyword=keyword, selector=0)))
    sibling = MutationTargetDescriptor(walk=(parent, WalkStep(keyword=keyword, selector=1)))
    assert _disjoint_descriptor_pool((sibling,), chosen=[primary]) == []
