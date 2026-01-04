import pytest

from schemathesis.specs.openapi.extra_data_source import RECENCY_DECAY_FACTOR, VariantUsageTracker


def test_initial_weight_is_one():
    tracker = VariantUsageTracker()
    assert tracker.get_weight("unknown_key") == 1.0


def test_weight_drops_after_draw():
    tracker = VariantUsageTracker()
    key = "variant_a"

    tracker.record_draw(key)

    # Immediately after draw, age=0, weight should be 0
    assert tracker.get_weight(key) == 0.0


def test_weight_recovers_with_other_draws():
    tracker = VariantUsageTracker()
    key_a = "variant_a"
    key_b = "variant_b"

    tracker.record_draw(key_a)

    # After drawing key_b, key_a's age increases
    tracker.record_draw(key_b)

    # age=1, weight = 1/(1+3) = 0.25
    assert tracker.get_weight(key_a) == pytest.approx(1 / (1 + RECENCY_DECAY_FACTOR))


def test_weight_increases_over_time():
    tracker = VariantUsageTracker()
    key = "variant_a"

    tracker.record_draw(key)

    # Simulate other draws happening
    for i in range(10):
        tracker.record_draw(f"other_{i}")

    # age=10, weight = 10/(10+3) ≈ 0.77
    expected = 10 / (10 + RECENCY_DECAY_FACTOR)
    assert tracker.get_weight(key) == pytest.approx(expected)


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
    assert tracker.get_weight("a") == pytest.approx(2 / (2 + RECENCY_DECAY_FACTOR))
    assert tracker.get_weight("b") == pytest.approx(1 / (1 + RECENCY_DECAY_FACTOR))
    assert tracker.get_weight("c") == 0.0


def test_redrawing_same_variant_resets_age():
    tracker = VariantUsageTracker()
    key = "variant_a"

    tracker.record_draw(key)
    tracker.record_draw("other")
    tracker.record_draw("other2")

    # age=2 now
    assert tracker.get_weight(key) == pytest.approx(2 / (2 + RECENCY_DECAY_FACTOR))

    # Redraw the same key
    tracker.record_draw(key)

    # age=0 again
    assert tracker.get_weight(key) == 0.0


def test_eviction_when_over_limit():
    tracker = VariantUsageTracker(maxlen=3)

    tracker.record_draw("a")
    tracker.record_draw("b")
    tracker.record_draw("c")

    # All three should be tracked
    assert tracker.get_weight("a") != 1.0
    assert tracker.get_weight("b") != 1.0
    assert tracker.get_weight("c") != 1.0

    # Adding fourth should evict "a" (oldest)
    tracker.record_draw("d")

    # "a" evicted, returns default weight
    assert tracker.get_weight("a") == 1.0
    # Others still tracked
    assert tracker.get_weight("b") != 1.0
    assert tracker.get_weight("c") != 1.0
    assert tracker.get_weight("d") != 1.0


def test_redraw_updates_lru_order():
    tracker = VariantUsageTracker(maxlen=3)

    tracker.record_draw("a")
    tracker.record_draw("b")
    tracker.record_draw("c")

    # Redraw "a" - moves it to end, so "b" is now oldest
    tracker.record_draw("a")

    # Add new key - should evict "b" (now oldest)
    tracker.record_draw("d")

    assert tracker.get_weight("a") != 1.0  # Still tracked (was refreshed)
    assert tracker.get_weight("b") == 1.0  # Evicted
    assert tracker.get_weight("c") != 1.0  # Still tracked
    assert tracker.get_weight("d") != 1.0  # Just added
