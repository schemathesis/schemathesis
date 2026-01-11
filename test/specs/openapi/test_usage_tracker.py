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

    # Immediately after draw, age=0, weight should be 0
    assert tracker._get_weight_unlocked(key) == 0.0


def test_weight_recovers_with_other_draws():
    tracker = VariantUsageTracker()
    key_a = "variant_a"
    key_b = "variant_b"

    tracker.record_draw(key_a)

    # After drawing key_b, key_a's age increases
    tracker.record_draw(key_b)

    # age=1, weight = 1/(1+3) = 0.25
    assert tracker._get_weight_unlocked(key_a) == pytest.approx(1 / (1 + RECENCY_DECAY_FACTOR))


def test_weight_increases_over_time():
    tracker = VariantUsageTracker()
    key = "variant_a"

    tracker.record_draw(key)

    # Simulate other draws happening
    for i in range(10):
        tracker.record_draw(f"other_{i}")

    # age=10, weight = 10/(10+3) ≈ 0.77
    expected = 10 / (10 + RECENCY_DECAY_FACTOR)
    assert tracker._get_weight_unlocked(key) == pytest.approx(expected)


def test_multiple_variants_weighted_differently():
    tracker = VariantUsageTracker()

    # Draw variants in sequence
    tracker.record_draw("a")  # step 1
    tracker.record_draw("b")  # step 2
    tracker.record_draw("c")  # step 3

    # At step 3:
    # a: age=2, weight = 2/(2+3) = 0.4
    # b: age=1, weight = 1/(1+3) = 0.25
    # c: age=0, weight = 0
    assert tracker._get_weight_unlocked("a") == pytest.approx(2 / (2 + RECENCY_DECAY_FACTOR))
    assert tracker._get_weight_unlocked("b") == pytest.approx(1 / (1 + RECENCY_DECAY_FACTOR))
    assert tracker._get_weight_unlocked("c") == 0.0


def test_redrawing_same_variant_resets_age():
    tracker = VariantUsageTracker()
    key = "variant_a"

    tracker.record_draw(key)
    tracker.record_draw("other")
    tracker.record_draw("other2")

    # age=2 now
    assert tracker._get_weight_unlocked(key) == pytest.approx(2 / (2 + RECENCY_DECAY_FACTOR))

    # Redraw the same key
    tracker.record_draw(key)

    # age=0 again
    assert tracker._get_weight_unlocked(key) == 0.0


def test_eviction_when_over_limit():
    tracker = VariantUsageTracker(maxlen=3)

    tracker.record_draw("a")
    tracker.record_draw("b")
    tracker.record_draw("c")

    # All three should be tracked
    assert tracker._get_weight_unlocked("a") != 1.0
    assert tracker._get_weight_unlocked("b") != 1.0
    assert tracker._get_weight_unlocked("c") != 1.0

    # Adding fourth should evict "a" (oldest)
    tracker.record_draw("d")

    # "a" evicted, returns default weight
    assert tracker._get_weight_unlocked("a") == 1.0
    # Others still tracked
    assert tracker._get_weight_unlocked("b") != 1.0
    assert tracker._get_weight_unlocked("c") != 1.0
    assert tracker._get_weight_unlocked("d") != 1.0


def test_redraw_updates_lru_order():
    tracker = VariantUsageTracker(maxlen=3)

    tracker.record_draw("a")
    tracker.record_draw("b")
    tracker.record_draw("c")

    # Redraw "a" - moves it to end, so "b" is now oldest
    tracker.record_draw("a")

    # Add new key - should evict "b" (now oldest)
    tracker.record_draw("d")

    assert tracker._get_weight_unlocked("a") != 1.0  # Still tracked (was refreshed)
    assert tracker._get_weight_unlocked("b") == 1.0  # Evicted
    assert tracker._get_weight_unlocked("c") != 1.0  # Still tracked
    assert tracker._get_weight_unlocked("d") != 1.0  # Just added


def test_weighted_select_distributes_fairly():
    tracker = VariantUsageTracker()
    variants = ["a", "b", "c", "d", "e"]
    random = Random(42)

    # With no recorded draws, all weights are 1.0 (equal)
    counts = Counter(tracker.weighted_select(variants, random) for _ in range(1000))

    # Each variant should get roughly 20% of selections
    for idx in range(len(variants)):
        assert 100 < counts[idx] < 300, f"Index {idx} got {counts[idx]}, expected ~200"


def test_weighted_select_respects_weights():
    tracker = VariantUsageTracker()
    variants = ["recently_used", "old", "never_used"]
    random = Random(42)

    # Make "recently_used" have weight 0 (just drawn)
    tracker.record_draw("recently_used")
    # Make "old" have some weight (drawn earlier)
    for _ in range(10):
        tracker.record_draw("filler")

    counts = Counter(tracker.weighted_select(variants, random) for _ in range(1000))

    # "recently_used" (idx 0) should be selected less than others
    # "never_used" (idx 2) has weight 1.0, should be selected most
    assert counts[0] < counts[2], "Recently used should be selected less than never used"


def test_successful_delete_weight_decays_exponentially():
    tracker = VariantUsageTracker()
    key = "variant_a"

    # Multiple successful deletes cause exponential decay
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

    # Unlike recency, delete weight doesn't recover with other draws
    for i in range(10):
        tracker.record_draw(f"other_{i}")

    # Weight from deletes is still the same
    # (recency part recovers, but delete decay is permanent)
    # Since key was never drawn, recency weight is 1.0
    # Total weight = recency (1.0) * delete_decay (0.3) = 0.3
    assert tracker._get_weight_unlocked(key) == weight_after_delete


def test_successful_delete_combines_with_recency():
    tracker = VariantUsageTracker()
    key = "variant_a"

    # Draw the key (recency weight becomes 0)
    tracker.record_draw(key)
    # Also mark it as deleted
    tracker.record_successful_delete(key)

    # Weight should be 0 (recency=0 * delete_decay=0.3 = 0)
    assert tracker._get_weight_unlocked(key) == 0.0

    # After some other draws, recency recovers but delete decay remains
    for i in range(10):
        tracker.record_draw(f"other_{i}")

    # age=10, recency_weight = 10/(10+3) ≈ 0.77
    # delete_weight = 0.3^1 = 0.3
    # combined = 0.77 * 0.3 ≈ 0.23
    expected_recency = 10 / (10 + RECENCY_DECAY_FACTOR)
    expected = expected_recency * DELETE_ATTEMPT_DECAY
    assert tracker._get_weight_unlocked(key) == pytest.approx(expected)


def test_weighted_select_avoids_deleted_resources():
    tracker = VariantUsageTracker()
    variants = ["deleted", "normal1", "normal2"]
    random = Random(42)

    # Mark "deleted" as having been deleted multiple times
    tracker.record_successful_delete("deleted")
    tracker.record_successful_delete("deleted")
    tracker.record_successful_delete("deleted")

    counts = Counter(tracker.weighted_select(variants, random) for _ in range(1000))

    # "deleted" (idx 0) should be selected much less than others
    # Its weight is 0.3^3 = 0.027, while others have weight 1.0
    assert counts[0] < counts[1], "Deleted resource should be selected less"
    assert counts[0] < counts[2], "Deleted resource should be selected less"
