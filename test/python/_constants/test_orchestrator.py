import sys
import time
from pathlib import Path

import pytest

from schemathesis.python._constants.orchestrator import (
    ExtractionResult,
    extract_all,
)
from schemathesis.python._constants.registry import SourceRegistry

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def _on_path():
    sys.path.insert(0, str(FIXTURES))
    yield
    sys.path.remove(str(FIXTURES))
    for name in list(sys.modules):
        if name == "sample_pkg" or name.startswith("sample_pkg."):
            sys.modules.pop(name)


def test_orchestrator_runs_sources_and_builds_pool():
    registry = SourceRegistry()

    @registry.decorator
    def from_sample():
        return ["sample_pkg.values"]

    result = extract_all(registry=registry, adapters=[])
    assert isinstance(result, ExtractionResult)
    assert not result.pool.is_empty()
    assert 12345 in result.pool.values_for("integer")
    assert "active" in result.pool.values_for("string")


def test_orchestrator_isolates_source_errors():
    registry = SourceRegistry()

    @registry.decorator
    def boom():
        raise RuntimeError("intentional")

    @registry.decorator
    def good():
        return ["sample_pkg.values"]

    result = extract_all(registry=registry, adapters=[])
    assert any(err.source == "boom" for err in result.errors)
    assert not result.pool.is_empty()


def test_orchestrator_respects_timeout():
    registry = SourceRegistry()

    @registry.decorator
    def slow():
        time.sleep(2)
        return ["sample_pkg.values"]

    result = extract_all(registry=registry, adapters=[], timeout_seconds=0.1)
    assert result.timed_out is True


def test_orchestrator_empty_registry_yields_empty_pool():
    registry = SourceRegistry()
    result = extract_all(registry=registry, adapters=[])
    assert result.pool.is_empty()
    assert result.errors == []


def test_orchestrator_falls_back_to_app_top_level_package():
    # Niche framework: no adapter matches. Orchestrator should walk the top-level
    # package of `app.__module__` so constants in that package still get harvested.
    import sample_pkg.values as sample_module

    class NicheApp:
        # Pretend this app instance was constructed in sample_pkg.values.
        pass

    NicheApp.__module__ = sample_module.__name__

    registry = SourceRegistry()

    @registry.decorator
    def from_niche_app():
        return NicheApp()

    result = extract_all(registry=registry, adapters=[])

    assert "active" in result.pool.values_for("string")
    # No adapter participated — the contribution lands under the null-adapter bucket.
    assert None in result.per_adapter
    assert result.per_adapter[None] > 0


def test_orchestrator_records_generator_iteration_error():
    # A generator can raise mid-iteration, after the source callable already returned;
    # the failure must surface as `source_error`, not escape and abort extraction.
    registry = SourceRegistry()

    def faulty_iterator():
        yield "sample_pkg.values"
        raise RuntimeError("boom")

    @registry.decorator
    def from_faulty():
        return faulty_iterator()

    @registry.decorator
    def from_sample():
        return ["sample_pkg.values"]

    result = extract_all(registry=registry, adapters=[])

    assert any(err.source == "from_faulty" and err.reason == "source_error" for err in result.errors)
    assert "active" in result.pool.values_for("string")


def test_orchestrator_resolves_app_objects_inside_iterable():
    import sample_pkg.values as sample_module

    class FakeApp:
        pass

    FakeApp.__module__ = sample_module.__name__

    class MatchAllAdapter:
        name = "fake"

        def matches(self, app):
            return isinstance(app, FakeApp)

        def handlers(self, app):
            # Return a function whose module is `sample_pkg.values` so the orchestrator
            # routes to that package via the adapter path.
            def handler() -> None: ...

            handler.__module__ = sample_module.__name__
            return [handler]

    registry = SourceRegistry()

    @registry.decorator
    def from_apps():
        return [FakeApp()]

    result = extract_all(registry=registry, adapters=[MatchAllAdapter()])

    assert "active" in result.pool.values_for("string")
    assert result.per_adapter.get("fake", 0) > 0
