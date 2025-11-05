import pytest

from schemathesis.cli.commands.run.responses import ResponseBucket, ResponseStatistic


@pytest.mark.parametrize(
    ("status_code", "expected_bucket"),
    [
        # 1xx Informational
        (100, "informational"),
        (101, "informational"),
        (103, "informational"),
        (199, "informational"),
        # 2xx Success
        (200, "success"),
        (201, "success"),
        (204, "success"),
        (299, "success"),
        # 3xx Redirection
        (300, "redirect"),
        (301, "redirect"),
        (302, "redirect"),
        (399, "redirect"),
        # 4xx Client Error
        (400, "client_error"),
        (404, "client_error"),
        (418, "client_error"),
        (499, "client_error"),
        # 5xx Server Error
        (500, "server_error"),
        (502, "server_error"),
        (503, "server_error"),
        (599, "server_error"),
        # Edge cases - Other
        (0, "other"),
        (99, "other"),
        (600, "other"),
        (999, "other"),
        (-1, "other"),
    ],
)
def test_record_categorization(status_code, expected_bucket):
    stats = ResponseStatistic()
    stats.record(status_code)

    assert stats.total == 1
    assert getattr(stats, expected_bucket) == 1


def test_record_and_total():
    stats = ResponseStatistic()
    for code in [100, 200, 201, 302, 418, 500, 700]:
        stats.record(code)

    assert stats.total == 7
    assert stats.informational == 1
    assert stats.success == 2
    assert stats.redirect == 1
    assert stats.client_error == 1
    assert stats.server_error == 1
    assert stats.other == 1


def test_iter_nonzero_buckets_with_emoji():
    stats = ResponseStatistic()
    stats.record(200)
    stats.record(404)
    stats.record(500)

    buckets = list(stats.iter_nonzero_buckets(use_emoji=True))

    assert len(buckets) == 3
    assert all(isinstance(bucket, ResponseBucket) for bucket in buckets)

    assert buckets[0].icon == "✅"
    assert buckets[0].total == 1
    assert buckets[0].label == "success"
    assert buckets[0].color == "green"

    assert buckets[1].icon == "⛔"
    assert buckets[1].total == 1
    assert buckets[1].label == "client error"
    assert buckets[1].color == "yellow"

    assert buckets[2].icon == "🚫"
    assert buckets[2].total == 1
    assert buckets[2].label == "server error"
    assert buckets[2].color == "red"


def test_iter_nonzero_buckets_without_emoji():
    stats = ResponseStatistic()
    stats.record(200)
    stats.record(404)

    buckets = list(stats.iter_nonzero_buckets(use_emoji=False))

    assert len(buckets) == 2
    assert buckets[0].icon == "[2XX]"
    assert buckets[1].icon == "[4XX]"


def test_iter_nonzero_buckets_empty():
    stats = ResponseStatistic()
    stats.record(200)

    buckets = list(stats.iter_nonzero_buckets())

    assert len(buckets) == 1
    assert buckets[0].label == "success"


def test_iter_nonzero_buckets_all_categories():
    stats = ResponseStatistic()
    stats.record(100)
    stats.record(200)
    stats.record(300)
    stats.record(400)
    stats.record(500)
    stats.record(999)

    buckets = list(stats.iter_nonzero_buckets(use_emoji=True))

    assert len(buckets) == 6
    labels = [bucket.label for bucket in buckets]
    assert labels == ["informational", "success", "redirect", "client error", "server error", "other"]


def test_multiple_recordings_same_code():
    stats = ResponseStatistic()
    for _ in range(5):
        stats.record(200)

    assert stats.total == 5
    assert stats.success == 5


def test_empty_stats():
    stats = ResponseStatistic()

    assert stats.total == 0
    assert list(stats.iter_nonzero_buckets()) == []
