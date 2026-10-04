import json
from uuid import uuid4

import pytest

from schemathesis.core.behaviors import (
    MAX_RETAINED_KEYS,
    MAX_TRACKED_VALUES,
    MIN_OBSERVATIONS_TO_JUDGE,
    BehaviorAlphabet,
    BehaviorCensus,
)


def _body(**payload):
    return json.dumps(payload).encode()


def test_responses_differing_only_in_status_are_distinct():
    alphabet = BehaviorAlphabet()
    for status in (200, 404, 409):
        alphabet.record(status_code=status, body=_body(detail="x"))

    assert alphabet.distinct == 3


def test_short_string_values_separate_behaviors_sharing_a_shape():
    alphabet = BehaviorAlphabet()
    for index in range(40):
        alphabet.record(status_code=200, body=_body(result=f"bucket-{index % 20}"))

    assert alphabet.distinct == 20


def test_identifier_field_does_not_inflate_species():
    alphabet = BehaviorAlphabet()
    for index in range(100):
        alphabet.record(status_code=200, body=_body(result=f"bucket-{index % 5}", request_id=uuid4().hex[:12]))

    assert alphabet.distinct == 5


def test_a_number_that_never_repeats_is_dropped_like_any_identifier():
    alphabet = BehaviorAlphabet()
    for index in range(30):
        alphabet.record(status_code=200, body=_body(result="ok", count=index))

    assert alphabet.distinct == 1


def test_rare_field_survives_until_it_has_been_seen_enough():
    # A message seen twice is not evidence of an identifier, and may name a rare behavior.
    alphabet = BehaviorAlphabet()
    for _ in range(40):
        alphabet.record(status_code=200, body=_body(result="ok"))
    alphabet.record(status_code=400, body=_body(error="value must be non-zero"))
    alphabet.record(status_code=400, body=_body(error="value overflows int32"))

    assert alphabet.distinct == 3


def test_opaque_bodies_collapse_to_one_species_per_status():
    alphabet = BehaviorAlphabet()
    for index in range(10):
        alphabet.record(status_code=200, body=f"not json {index}".encode())

    assert alphabet.distinct == 1


def test_missing_body_is_still_a_behavior():
    alphabet = BehaviorAlphabet()
    alphabet.record(status_code=204, body=None)

    assert alphabet.distinct == 1


def test_a_field_counts_again_once_its_values_start_repeating():
    # Twenty categories seen once each look exactly like twenty identifiers; only repetition tells
    # them apart, and it arrives later than the first judgement would.
    alphabet = BehaviorAlphabet()
    for index in range(20):
        alphabet.record(status_code=200, body=_body(result=f"bucket-{index}"))

    assert alphabet.distinct == 1

    for _ in range(3):
        for index in range(20):
            alphabet.record(status_code=200, body=_body(result=f"bucket-{index}"))

    assert alphabet.distinct == 20


def test_retained_keys_stay_bounded_under_a_flood_of_identifiers():
    alphabet = BehaviorAlphabet()
    for _ in range(MAX_RETAINED_KEYS + 500):
        alphabet.record(status_code=200, body=_body(result="ok", request_id=uuid4().hex[:12]))

    assert len(alphabet.counts) <= MAX_RETAINED_KEYS
    assert alphabet.distinct == 1


def test_discovery_probability_falls_as_behaviors_stop_being_new():
    # Good-Turing: the share of behaviors seen exactly once estimates the chance the next
    # response shows something not seen yet.
    alphabet = BehaviorAlphabet()
    for index in range(10):
        alphabet.record(status_code=200, body=_body(result=f"bucket-{index}"))

    assert alphabet.total == 10
    assert alphabet.discovery_probability == 1.0

    for _ in range(90):
        alphabet.record(status_code=200, body=_body(result="bucket-0"))

    assert alphabet.total == 100
    assert alphabet.discovery_probability == pytest.approx(0.09)


def test_discovery_probability_of_an_empty_alphabet():
    # Nothing has been looked for yet, so everything is still to be found.
    assert BehaviorAlphabet().discovery_probability == 1.0


def test_a_field_is_kept_when_most_observations_repeat_a_value():
    # 60 values over 100 observations: most of what was seen repeats, so the field names behaviors
    # even though the distinct-value share alone would look identifier-like.
    alphabet = BehaviorAlphabet()
    for _ in range(2):
        for index in range(40):
            alphabet.record(status_code=200, body=_body(result=f"bucket-{index}"))
    for index in range(20):
        alphabet.record(status_code=200, body=_body(result=f"rare-{index}"))

    assert alphabet.distinct == 60


def test_a_field_whose_values_never_repeat_is_dropped():
    alphabet = BehaviorAlphabet()
    for index in range(100):
        alphabet.record(status_code=200, body=_body(token=f"t{index}"))

    assert alphabet.distinct == 1


def test_richness_uses_the_first_order_jackknife():
    # JK1 = S(n) + f1 * (n - 1) / n. Three species over 12 responses, one of them a singleton.
    alphabet = BehaviorAlphabet()
    for _ in range(6):
        alphabet.record(status_code=200, body=_body(result="a"))
    for _ in range(5):
        alphabet.record(status_code=200, body=_body(result="b"))
    alphabet.record(status_code=200, body=_body(result="c"))

    assert alphabet.richness() == pytest.approx(3 + 1 * 11 / 12)


def test_richness_never_falls_below_what_was_seen():
    alphabet = BehaviorAlphabet()
    for index in range(30):
        alphabet.record(status_code=200, body=_body(result=f"r{index % 7}"))

    assert alphabet.richness() >= alphabet.distinct


def test_discovery_probability_uses_the_whole_run():
    alphabet = BehaviorAlphabet()
    for _ in range(50):
        alphabet.record(status_code=200, body=_body(result="same"))
    alphabet.record(status_code=404, body=_body(error="gone"))

    # One singleton over 51 responses, not "everything is new" as a reset would claim.
    assert alphabet.discovery_probability == pytest.approx(1 / 51)


def test_discovery_probability_falls_back_to_laplace_without_singletons():
    alphabet = BehaviorAlphabet()
    for _ in range(20):
        alphabet.record(status_code=200, body=_body(result="same"))

    assert alphabet.discovery_probability == pytest.approx(1 / 22)


def test_run_level_discovery_probability_is_a_mean_over_operations():
    census = BehaviorCensus()
    for _ in range(20):
        census.record(operation_label="GET /a", status_code=200, body=_body(result="same"))
    for _ in range(18):
        census.record(operation_label="GET /b", status_code=200, body=_body(result="common"))
    for index in range(2):
        census.record(operation_label="GET /b", status_code=200, body=_body(result=f"rare{index}"))

    # /a has no singletons and falls back to Laplace; /b has two over twenty responses.
    expected = ((1 / 22) + (2 / 20)) / 2

    assert census.discovery_probability() == pytest.approx(expected)


def test_an_operation_with_no_responses_raises_the_run_level_number():
    census = BehaviorCensus()
    for _ in range(20):
        census.record(operation_label="GET /a", status_code=200, body=_body(result="same"))

    # "Nothing ran there yet" means everything is still to be found, not nothing.
    assert census.discovery_probability(["GET /a", "GET /untouched"]) == pytest.approx(((1 / 22) + 1.0) / 2)


def test_array_bodies_are_keyed_by_their_short_string_elements():
    alphabet = BehaviorAlphabet()
    for _ in range(10):
        alphabet.record(status_code=200, body=json.dumps(["ELEARNING_SITE"]).encode())
        alphabet.record(status_code=200, body=json.dumps(["PRINT_SHOP"]).encode())

    assert alphabet.distinct == 2


def test_scalar_bodies_are_keyed_by_their_value():
    alphabet = BehaviorAlphabet()
    for _ in range(10):
        alphabet.record(status_code=200, body=b'"accepted"')
        alphabet.record(status_code=200, body=b'"rejected"')

    assert alphabet.distinct == 2


def test_text_bodies_are_keyed_by_a_normalized_prefix():
    # Tomcat-style error pages differ by message; the numbers in them are noise.
    first = b"<html><title>Error report</title><body>Status 404 - not found</body></html>"
    second = b"<html><title>Error report</title><body>Status 500 - server blew up</body></html>"
    alphabet = BehaviorAlphabet()
    for _ in range(10):
        alphabet.record(status_code=500, body=first)
        alphabet.record(status_code=500, body=second)

    assert alphabet.distinct == 2


def test_text_bodies_that_never_repeat_are_still_dropped():
    # Numbers in a body normalise away; text that stays unique after that is an identifier.
    alphabet = BehaviorAlphabet()
    for index in range(100):
        suffix = f"{chr(97 + index % 26)}{chr(97 + index // 26)}"
        alphabet.record(status_code=500, body=f"<html>failed at {suffix}</html>".encode())

    assert alphabet.distinct == 1


def test_responses_differing_only_in_a_header_are_distinct():
    # Services signal in headers too - a redirect target, an auth scheme, a media type.
    alphabet = BehaviorAlphabet()
    for _ in range(10):
        alphabet.record(status_code=201, body=None, headers={"location": ["/users/1"]})
        alphabet.record(status_code=201, body=None, headers={"location": ["/orders/1"]})

    assert alphabet.distinct == 2


def test_per_request_headers_do_not_inflate_behaviors():
    alphabet = BehaviorAlphabet()
    for index in range(100):
        alphabet.record(
            status_code=200,
            body=_body(result="ok"),
            headers={"date": [f"Mon, 01 Jan 2026 00:00:{index:02d} GMT"], "server": ["nginx"]},
        )

    assert alphabet.distinct == 1


def test_boolean_values_name_behaviors():
    alphabet = BehaviorAlphabet()
    for _ in range(10):
        alphabet.record(status_code=200, body=_body(success=True))
        alphabet.record(status_code=200, body=_body(success=False))

    assert alphabet.distinct == 2


def test_small_numbers_name_behaviors():
    alphabet = BehaviorAlphabet()
    for _ in range(10):
        for kind in (1, 2, 3):
            alphabet.record(status_code=200, body=_body(type=kind))

    assert alphabet.distinct == 3


def test_numeric_identifiers_are_still_dropped():
    alphabet = BehaviorAlphabet()
    for index in range(100):
        alphabet.record(status_code=200, body=_body(kind="order", id=index))

    assert alphabet.distinct == 1


def test_null_is_distinct_from_a_value():
    alphabet = BehaviorAlphabet()
    for _ in range(10):
        alphabet.record(status_code=200, body=_body(error=None))
        alphabet.record(status_code=200, body=_body(error="denied"))

    assert alphabet.distinct == 2


def test_nested_objects_contribute_their_values():
    alphabet = BehaviorAlphabet()
    for _ in range(10):
        alphabet.record(status_code=200, body=_body(data={"state": "active"}))
        alphabet.record(status_code=200, body=_body(data={"state": "archived"}))

    assert alphabet.distinct == 2


def test_richness_of_an_empty_alphabet():
    assert BehaviorAlphabet().richness() == 0.0


def test_run_level_discovery_probability_without_any_operations():
    assert BehaviorCensus().discovery_probability() == 1.0


def test_arrays_without_short_strings_are_keyed_by_element_type():
    alphabet = BehaviorAlphabet()
    for payload in ([1, 2, 3], [4, 5], [{"a": 1}], [{"b": 2}, {"c": 3}]):
        alphabet.record(status_code=200, body=json.dumps(payload).encode())

    # Two arrays of numbers and two arrays of objects, whatever they contain.
    assert alphabet.distinct == 2


def test_snapshot_separates_behaviors_by_how_often_they_repeat():
    alphabet = BehaviorAlphabet()
    for status, repeats in ((200, 1), (404, 2), (409, 3)):
        for _ in range(repeats):
            alphabet.record(status_code=status, body=None)

    counts = alphabet.counts_snapshot()

    assert (counts.responses, counts.distinct) == (6, 3)
    assert (counts.singletons, counts.doubletons) == (1, 1)


def test_a_list_inside_an_object_says_nothing():
    alphabet = BehaviorAlphabet()
    for items in ([1], [1, 2], [1, 2, 3]):
        alphabet.record(status_code=200, body=_body(state="ok", items=items))

    assert alphabet.distinct == 1


def test_the_curve_records_which_response_showed_each_behavior():
    alphabet = BehaviorAlphabet()
    for status in (200, 200, 404, 200, 409):
        alphabet.record(status_code=status, body=None)

    assert alphabet.counts_snapshot().discoveries == [1, 3, 5]


def test_waiting_is_counted_from_the_last_new_behavior():
    alphabet = BehaviorAlphabet()
    alphabet.record(status_code=404, body=None)
    for _ in range(9):
        alphabet.record(status_code=200, body=None)

    counts = alphabet.counts_snapshot()

    # The 200 arrived second, and nothing new has happened in the eight responses since.
    assert (counts.discoveries, counts.waited) == ([1, 2], 8)


def test_an_operation_that_has_never_repeated_itself_has_not_waited():
    alphabet = BehaviorAlphabet()
    for status in (200, 404, 409):
        alphabet.record(status_code=status, body=None)

    assert alphabet.counts_snapshot().waited == 0


def test_merged_behaviors_keep_the_earlier_of_their_positions():
    alphabet = BehaviorAlphabet()
    for _ in range(MIN_OBSERVATIONS_TO_JUDGE + 5):
        alphabet.record(status_code=200, body=_body(state="ok", id=uuid4().hex))

    counts = alphabet.counts_snapshot()

    # Every response looked new until `id` was judged an identifier; one behavior, seen first.
    assert (counts.discoveries, counts.waited) == ([1], counts.responses - 1)


def test_an_identifier_is_still_recognized_after_a_long_run():
    # Only the first `MAX_TRACKED_VALUES` values of a field are counted. Judging the field on every
    # response instead would make it look like it repeats itself once the run passes that many.
    alphabet = BehaviorAlphabet()
    for _ in range(MAX_TRACKED_VALUES * 4):
        alphabet.record(status_code=200, body=_body(state="ok", id=uuid4().hex))

    assert alphabet.distinct == 1
