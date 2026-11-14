from collections import Counter
from random import Random

import pytest

from schemathesis.specs.openapi.extra_data_source import DELETE_ATTEMPT_DECAY, RECENCY_DECAY_FACTOR, VariantUsageTracker


def test_initial_weight_is_one():
    tracker = VariantUsageTracker()
    assert tracker._get_weight_unlocked("unknown_key") == 1.0


def test_weight_drops_after_draw():
    tracker = VariantUsageTracker()
    key = "variant_a"

    tracker.record_draw(key)

    assert tracker._get_weight_unlocked(key) == 0.0


def test_weight_recovers_with_other_draws():
    tracker = VariantUsageTracker()
    key_a = "variant_a"
    key_b = "variant_b"

    tracker.record_draw(key_a)

    tracker.record_draw(key_b)

    assert tracker._get_weight_unlocked(key_a) == pytest.approx(1 / (1 + RECENCY_DECAY_FACTOR))


def test_weight_increases_over_time():
    tracker = VariantUsageTracker()
    key = "variant_a"

    tracker.record_draw(key)

    for i in range(10):
        tracker.record_draw(f"other_{i}")

    expected = 10 / (10 + RECENCY_DECAY_FACTOR)
    assert tracker._get_weight_unlocked(key) == pytest.approx(expected)


def test_multiple_variants_weighted_differently():
    tracker = VariantUsageTracker()

    tracker.record_draw("a")
    tracker.record_draw("b")
    tracker.record_draw("c")

    assert tracker._get_weight_unlocked("a") == pytest.approx(2 / (2 + RECENCY_DECAY_FACTOR))
    assert tracker._get_weight_unlocked("b") == pytest.approx(1 / (1 + RECENCY_DECAY_FACTOR))
    assert tracker._get_weight_unlocked("c") == 0.0


def test_redrawing_same_variant_resets_age():
    tracker = VariantUsageTracker()
    key = "variant_a"

    tracker.record_draw(key)
    tracker.record_draw("other")
    tracker.record_draw("other2")

    assert tracker._get_weight_unlocked(key) == pytest.approx(2 / (2 + RECENCY_DECAY_FACTOR))

    tracker.record_draw(key)

    assert tracker._get_weight_unlocked(key) == 0.0


def test_eviction_when_over_limit():
    tracker = VariantUsageTracker(maxlen=3)

    tracker.record_draw("a")
    tracker.record_draw("b")
    tracker.record_draw("c")

    assert tracker._get_weight_unlocked("a") != 1.0
    assert tracker._get_weight_unlocked("b") != 1.0
    assert tracker._get_weight_unlocked("c") != 1.0

    tracker.record_draw("d")

    assert tracker._get_weight_unlocked("a") == 1.0
    assert tracker._get_weight_unlocked("b") != 1.0
    assert tracker._get_weight_unlocked("c") != 1.0
    assert tracker._get_weight_unlocked("d") != 1.0


def test_redraw_updates_lru_order():
    tracker = VariantUsageTracker(maxlen=3)

    tracker.record_draw("a")
    tracker.record_draw("b")
    tracker.record_draw("c")

    # Redrawing `a` makes `b` the oldest
    tracker.record_draw("a")

    tracker.record_draw("d")

    assert tracker._get_weight_unlocked("a") != 1.0
    assert tracker._get_weight_unlocked("b") == 1.0
    assert tracker._get_weight_unlocked("c") != 1.0
    assert tracker._get_weight_unlocked("d") != 1.0


def test_weighted_select_distributes_fairly():
    tracker = VariantUsageTracker()
    variants = ["a", "b", "c", "d", "e"]
    random = Random(42)

    # No recorded draws, so all weights are equal
    counts = Counter(tracker.weighted_select(variants, random) for _ in range(1000))

    for idx in range(len(variants)):
        assert 100 < counts[idx] < 300, f"Index {idx} got {counts[idx]}, expected ~200"


def test_weighted_select_respects_weights():
    tracker = VariantUsageTracker()
    variants = ["recently_used", "old", "never_used"]
    random = Random(42)

    tracker.record_draw("recently_used")
    for _ in range(10):
        tracker.record_draw("filler")

    counts = Counter(tracker.weighted_select(variants, random) for _ in range(1000))

    assert counts[0] < counts[2], "Recently used should be selected less than never used"


def test_weighted_select_picks_first_shuffled_index_when_every_weight_is_zero():
    tracker = VariantUsageTracker()
    tracker.record_draw("recent")

    assert tracker.weighted_select(["recent"], Random(0)) == 0
    assert tracker.weighted_select(["fresh", "other"], Random(0)) in (0, 1)


def test_successful_delete_weight_decays_exponentially():
    tracker = VariantUsageTracker()
    key = "variant_a"

    tracker.record_successful_delete(key)
    assert tracker._get_weight_unlocked(key) == pytest.approx(DELETE_ATTEMPT_DECAY**1)

    tracker.record_successful_delete(key)
    assert tracker._get_weight_unlocked(key) == pytest.approx(DELETE_ATTEMPT_DECAY**2)

    tracker.record_successful_delete(key)
    assert tracker._get_weight_unlocked(key) == pytest.approx(DELETE_ATTEMPT_DECAY**3)


def test_successful_delete_weight_does_not_recover():
    tracker = VariantUsageTracker()
    key = "variant_a"

    tracker.record_successful_delete(key)
    weight_after_delete = tracker._get_weight_unlocked(key)

    for i in range(10):
        tracker.record_draw(f"other_{i}")

    assert tracker._get_weight_unlocked(key) == weight_after_delete


def test_successful_delete_combines_with_recency():
    tracker = VariantUsageTracker()
    key = "variant_a"

    tracker.record_draw(key)
    tracker.record_successful_delete(key)

    assert tracker._get_weight_unlocked(key) == 0.0

    # Recency recovers over other draws; the delete decay stays
    for i in range(10):
        tracker.record_draw(f"other_{i}")

    expected_recency = 10 / (10 + RECENCY_DECAY_FACTOR)
    expected = expected_recency * DELETE_ATTEMPT_DECAY
    assert tracker._get_weight_unlocked(key) == pytest.approx(expected)


def test_weighted_select_avoids_deleted_resources():
    tracker = VariantUsageTracker()
    variants = ["deleted", "normal1", "normal2"]
    random = Random(42)

    tracker.record_successful_delete("deleted")
    tracker.record_successful_delete("deleted")
    tracker.record_successful_delete("deleted")

    counts = Counter(tracker.weighted_select(variants, random) for _ in range(1000))

    assert counts[0] < counts[1], "Deleted resource should be selected less"
    assert counts[0] < counts[2], "Deleted resource should be selected less"
