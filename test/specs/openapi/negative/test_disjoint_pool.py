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
        (("a",), ("b",), True),  # siblings are disjoint
        (("a",), ("a", "b"), False),  # descendant excluded
        (("a", "b"), ("a",), False),  # ancestor excluded
        (("a",), ("a",), False),  # self excluded
    ],
    ids=["siblings", "descendant", "ancestor", "self"],
)
def test_disjoint_pool(primary_selectors, candidate_selectors, included):
    primary = _descriptor(*primary_selectors)
    candidate = _descriptor(*candidate_selectors)
    result = _disjoint_descriptor_pool((candidate,), chosen=[primary])
    assert (candidate in result) == included


def test_oneof_sibling_branch_excluded():
    # Both branches share the same oneOf parent — mutating one invalidates the other.
    primary = MutationTargetDescriptor(walk=(WalkStep(keyword="oneOf", selector=0),))
    sibling = MutationTargetDescriptor(walk=(WalkStep(keyword="oneOf", selector=1),))
    assert _disjoint_descriptor_pool((sibling,), chosen=[primary]) == []
