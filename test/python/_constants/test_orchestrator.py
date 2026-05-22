import functools
import sys
from pathlib import Path

import pytest

from schemathesis.python._constants import orchestrator
from schemathesis.python._constants.adapters.flask import FlaskAdapter
from schemathesis.python._constants.orchestrator import extract_all
from schemathesis.python._constants.pool import ConstantsPool
from schemathesis.python._constants.registry import SourceRegistry, constants
from test.python._constants.fixtures.dep_pkg import flask_app, shared
from test.python._constants.helpers import pool_values

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def _on_path():
    sys.path.insert(0, str(FIXTURES))
    yield
    sys.path.remove(str(FIXTURES))
    for name in list(sys.modules):
        for top in ("sample_pkg", "exit_pkg"):
            if name == top or name.startswith(f"{top}."):
                sys.modules.pop(name)


def test_orchestrator_survives_module_that_exits_on_import():
    # A scanned module calling sys.exit() (or pytest.skip) at import raises BaseException,
    # which must not abort extraction or propagate out to kill the engine run.
    registry = SourceRegistry()

    @registry.register
    def from_modules():
        return ["exit_at_import", "sample_pkg.values"]

    result = extract_all(registry=registry, adapters=[])
    assert "active" in pool_values(result, "string")


def test_orchestrator_survives_subpackage_that_exits_during_walk():
    # A subpackage that sys.exit()s mid-walk must not discard the whole source.
    registry = SourceRegistry()

    @registry.register
    def from_pkg():
        return ["exit_pkg"]

    result = extract_all(registry=registry, adapters=[])
    assert "exitpkg_ok" in pool_values(result, "string")


def test_orchestrator_runs_sources_and_builds_pool():
    registry = SourceRegistry()

    @registry.register
    def from_sample():
        return ["sample_pkg.values"]

    result = extract_all(registry=registry, adapters=[])
    assert isinstance(result, ConstantsPool)
    assert not result.is_empty()
    assert 12345 in pool_values(result, "integer")
    assert "active" in pool_values(result, "string")


def test_orchestrator_isolates_source_errors():
    registry = SourceRegistry()

    @registry.register
    def boom():
        raise RuntimeError("intentional")

    @registry.register
    def good():
        return ["sample_pkg.values"]

    result = extract_all(registry=registry, adapters=[])
    assert not result.is_empty()


def test_orchestrator_falls_back_to_app_top_level_package():
    # Niche framework: no adapter matches. Orchestrator should walk the top-level
    # package of `app.__module__` so constants in that package still get harvested.
    class NicheApp:
        pass

    NicheApp.__module__ = "sample_pkg.values"

    registry = SourceRegistry()

    @registry.register
    def from_niche_app():
        return NicheApp()

    assert "active" in pool_values(extract_all(registry=registry, adapters=[]), "string")


def test_orchestrator_isolates_generator_iteration_error():
    # A generator can raise mid-iteration, after the source callable already returned; that
    # failure must not escape and abort extraction - later sources still contribute.
    registry = SourceRegistry()

    def faulty_iterator():
        yield "sample_pkg.values"
        raise RuntimeError("boom")

    @registry.register
    def from_faulty():
        return faulty_iterator()

    @registry.register
    def from_sample():
        return ["sample_pkg.values"]

    result = extract_all(registry=registry, adapters=[])
    assert "active" in pool_values(result, "string")


def test_source_without_name_is_handled():
    # A `functools.partial` (or callable instance) has no `__name__`; extraction must not crash on it.
    registry = SourceRegistry()
    registry.register(functools.partial(lambda: ["sample_pkg.values"]))
    result = extract_all(registry=registry, adapters=[])
    assert "active" in pool_values(result, "string")


def test_symlinked_third_party_package_is_not_user_code(tmp_path, monkeypatch):
    # `spec.origin` comes back via the symlink; roots are realpath'd, so the compare must resolve symlinks too.
    root = tmp_path / "site-packages"
    real_pkg = root / "vendored_pkg"
    real_pkg.mkdir(parents=True)
    (real_pkg / "__init__.py").touch()
    link_parent = tmp_path / "elsewhere"
    link_parent.mkdir()
    (link_parent / "vendored_pkg").symlink_to(real_pkg)

    monkeypatch.syspath_prepend(str(link_parent))
    monkeypatch.setattr(orchestrator, "_THIRD_PARTY_ROOTS", (str(root),))
    assert orchestrator._is_likely_user_package("vendored_pkg") is False


def test_module_object_source_is_harvested():
    registry = SourceRegistry()

    @registry.register
    def from_module():
        return shared

    result = extract_all(registry=registry, adapters=[])
    assert shared.UNLOCK_TOKEN in pool_values(result, "string")


@pytest.mark.parametrize(
    "app_module",
    ["json", "__main__", "no_such_top_level_pkg_xyz", "pytest", ".weird"],
    ids=["stdlib", "dunder-main", "absent", "third-party", "empty-top-level"],
)
def test_app_from_non_user_package_yields_no_constants(app_module):
    # No adapter matches, and the fallback rejects stdlib/dunder/absent/installed/malformed
    # module names, so nothing is harvested from a niche framework's own internals.
    class App:
        pass

    App.__module__ = app_module

    registry = SourceRegistry()

    @registry.register
    def from_app():
        return App()

    assert extract_all(registry=registry, adapters=[]).is_empty()


def test_orchestrator_ignores_source_returning_none():
    registry = SourceRegistry()

    @registry.register
    def nothing():
        return None

    @registry.register
    def good():
        return ["sample_pkg.values"]

    result = extract_all(registry=registry, adapters=[])
    assert "active" in pool_values(result, "string")


def test_orchestrator_reraises_keyboard_interrupt_from_source():
    registry = SourceRegistry()

    @registry.register
    def interrupted():
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        extract_all(registry=registry, adapters=[])


def test_orchestrator_keeps_partial_results_when_source_raises_base_exception():
    # A source raising a non-Exception (e.g. SystemExit) must not abort the engine; constants
    # extracted by earlier sources survive.
    registry = SourceRegistry()

    @registry.register
    def good():
        return ["sample_pkg.values"]

    @registry.register
    def exits():
        raise SystemExit("boom")

    result = extract_all(registry=registry, adapters=[])
    assert "active" in pool_values(result, "string")


def test_orchestrator_follows_handler_module_local_imports():
    # The handler's magic constant lives in a module it imports, not inline; extraction
    # must follow that import so the constant still lands in the pool.
    registry = SourceRegistry()

    @registry.register
    def from_app():
        return flask_app.app

    result = extract_all(registry=registry, adapters=[FlaskAdapter()])
    assert "zx9secreta3f9c1e7" in pool_values(result, "string")


def test_orchestrator_merges_origins_for_overlapping_sources():
    registry = SourceRegistry()

    @registry.register
    def first_source():
        return ["sample_pkg.values"]

    @registry.register
    def second_source():
        return ["sample_pkg.values"]

    result = extract_all(registry=registry, adapters=[])

    entry = next(entry for entry in result.entries_for("string") if entry.value == "active")
    assert {origin.source for origin in entry.origins} == {"first_source", "second_source"}


def _make_source():
    def source():
        return ["sample_pkg.values"]

    return source


@pytest.mark.parametrize(
    ("registered", "expected"),
    [([0, 1], [0, 1]), ([0, 0], [0])],
    ids=["distinct-both-kept", "same-source-deduplicated"],
)
def test_registry_keeps_distinct_sources_and_dedups_identity(registered, expected):
    registry = SourceRegistry()
    sources = [_make_source(), _make_source()]
    for index in registered:
        registry.register(sources[index])
    assert registry.get_all() == [sources[index] for index in expected]


def test_registry_uses_identity_for_equal_callable_instances():
    class Source:
        def __call__(self):
            return []

        def __eq__(self, other):
            return isinstance(other, Source)

    registry = SourceRegistry()
    first = Source()
    second = Source()
    registry.register(first)
    registry.register(second)

    registered = registry.get_all()
    assert len(registered) == 2
    assert registered[0] is first
    assert registered[1] is second


def test_registry_clear_empties_the_registry():
    registry = SourceRegistry()
    registry.register(_make_source())
    registry.clear()
    assert registry.get_all() == []


def test_constants_decorator_rejects_non_callable():
    with pytest.raises(TypeError, match="expects a callable"):
        constants(123)
