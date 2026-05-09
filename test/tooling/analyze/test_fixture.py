import json


def test_fixture_ndjson_exists_and_nonempty(analyzer_ndjson):
    assert analyzer_ndjson.exists()
    assert analyzer_ndjson.stat().st_size > 0


def test_fixture_ndjson_first_line_is_initialize(analyzer_ndjson):
    first = analyzer_ndjson.read_text(encoding="utf-8").splitlines()[0]
    event = json.loads(first)
    assert "Initialize" in event
