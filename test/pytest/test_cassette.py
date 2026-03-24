import json

import yaml


def test_vcr_cassette_written_via_config(testdir, openapi3_base_url):
    cassette_path = str(testdir.tmpdir.join("cassette.yaml"))
    testdir.make_test(
        f"""
schema.config.update(base_url="{openapi3_base_url}")
schema.config.reports.update(vcr_path=r"{cassette_path}")

@schema.parametrize()
@settings(max_examples=1)
def test_api(case):
    case.call()
""",
    )
    result = testdir.runpytest("-s")
    result.assert_outcomes(passed=1)

    with open(cassette_path) as f:
        cassette = yaml.safe_load(f)
    assert "http_interactions" in cassette
    assert len(cassette["http_interactions"]) >= 1


def test_vcr_cassette_records_check_failures(testdir, openapi3_base_url):
    cassette_path = str(testdir.tmpdir.join("cassette.yaml"))
    testdir.make_test(
        f"""
schema.config.update(base_url="{openapi3_base_url}")
schema.config.reports.update(vcr_path=r"{cassette_path}")

@schema.parametrize()
@settings(max_examples=1)
def test_api(case):
    case.call_and_validate()
""",
        schema_name="simple_openapi.yaml",
        paths={"/failure": {"get": {"responses": {"500": {"description": "Internal Server Error"}}}}},
    )
    result = testdir.runpytest("-s")
    result.assert_outcomes(failed=2)

    with open(cassette_path) as f:
        cassette = yaml.safe_load(f)
    all_checks = [c for i in cassette["http_interactions"] for c in i["checks"]]
    assert any(c["status"] == "FAILURE" for c in all_checks)


def test_vcr_cassette_no_interactions_when_call_raises(testdir, openapi3_base_url):
    cassette_path = str(testdir.tmpdir.join("cassette.yaml"))
    testdir.make_test(
        f"""
schema.config.update(base_url="{openapi3_base_url}")
schema.config.reports.update(vcr_path=r"{cassette_path}")

@schema.parametrize()
@settings(max_examples=1)
def test_api(case, monkeypatch):
    def fail(*args, **kwargs):
        raise ConnectionError("simulated network failure")
    monkeypatch.setattr("schemathesis.generation.case.Case.call", fail)
    case.call()
""",
    )
    result = testdir.runpytest("-s")
    result.assert_outcomes(failed=1)

    with open(cassette_path) as f:
        cassette = yaml.safe_load(f)
    assert cassette["http_interactions"] is None


def test_har_cassette_written_via_config(testdir, openapi3_base_url):
    cassette_path = str(testdir.tmpdir.join("cassette.har"))
    testdir.make_test(
        f"""
schema.config.update(base_url="{openapi3_base_url}")
schema.config.reports.update(har_path=r"{cassette_path}")

@schema.parametrize()
@settings(max_examples=1)
def test_api(case):
    case.call()
""",
    )
    result = testdir.runpytest("-s")
    result.assert_outcomes(passed=1)

    with open(cassette_path) as f:
        har = json.load(f)
    assert "log" in har
    assert len(har["log"]["entries"]) >= 1
