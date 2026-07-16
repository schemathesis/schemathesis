import functools
import sys
from pathlib import Path

import pytest
from flask import Flask, jsonify

from schemathesis.core.warnings import SchemathesisWarning
from schemathesis.python._constants import orchestrator
from schemathesis.python._constants.adapters.flask import FlaskAdapter
from schemathesis.python._constants.orchestrator import extract_all
from schemathesis.python._constants.pool import ConstantsPool
from schemathesis.python._constants.registry import SourceRegistry, constants
from schemathesis.python._constants.warnings import iter_constants_warnings
from test.python._constants.fixtures.dep_pkg import flask_app, shared
from test.python._constants.helpers import pool_values

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def _on_path():
    sys.path.insert(0, str(FIXTURES))
    yield
    sys.path.remove(str(FIXTURES))
    for name in list(sys.modules):
        for top in ("sample_pkg", "exit_pkg", "empty_module"):
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
    # The raising source is recorded so its silent failure can be surfaced to the user.
    assert [(f.source, "intentional" in f.reason) for f in result.failures] == [("boom", True)]


def test_registered_source_resolving_to_no_modules_is_recorded():
    registry = SourceRegistry()

    @registry.register
    def empty():
        return []

    result = extract_all(registry=registry, adapters=[])
    assert [f.source for f in result.failures] == ["empty"]


def test_registered_source_scanning_a_literal_free_module_is_not_recorded():
    # Scanned fine, just no keepable literals: legitimately empty, not an error.
    registry = SourceRegistry()

    @registry.register
    def no_literals():
        return ["empty_module"]

    result = extract_all(registry=registry, adapters=[])
    assert result.failures == ()


def test_app_source_failure_is_not_recorded():
    # Auto-extraction from an app is best-effort; even a raising app source stays silent.
    def failing_app():
        raise RuntimeError("boom")

    result = extract_all(registry=SourceRegistry(), adapters=[], extra_sources=[failing_app])
    assert result.failures == ()


def test_pool_failures_convert_to_schema_warnings():
    registry = SourceRegistry()

    @registry.register
    def boom():
        raise RuntimeError("bad")

    pool = extract_all(registry=registry, adapters=[])
    warnings = iter_constants_warnings(pool)
    assert [(w.kind, w.operation_label) for w in warnings] == [(SchemathesisWarning.CONSTANTS_EXTRACTION, None)]
    assert "boom" in warnings[0].message


def test_orchestrator_falls_back_to_app_module():
    # Niche framework: no adapter matches. The module defining the app still gets harvested.
    class NicheApp:
        pass

    NicheApp.__module__ = "sample_pkg.values"

    registry = SourceRegistry()

    @registry.register
    def from_niche_app():
        return NicheApp()

    assert "active" in pool_values(extract_all(registry=registry, adapters=[]), "string")


def test_orchestrator_fallback_does_not_walk_the_apps_package():
    # Walking the package imports modules the app never touches, firing their import side effects.
    class NicheApp:
        pass

    NicheApp.__module__ = "test.python._constants.fixtures.dep_pkg.flask_app"

    modules, _ = orchestrator._resolve_with_adapters(NicheApp(), adapters=[])
    assert "test.python._constants.fixtures.dep_pkg.optional" not in modules


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
    assert [f.source for f in result.failures] == ["from_faulty"]


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
    assert [f.source for f in result.failures] == ["exits"]


def test_orchestrator_follows_handler_module_local_imports():
    # The handler's magic constant lives in a module it imports, not inline; extraction
    # must follow that import so the constant still lands in the pool.
    registry = SourceRegistry()

    @registry.register
    def from_app():
        return flask_app.app

    result = extract_all(registry=registry, adapters=[FlaskAdapter()])
    assert "zx9secreta3f9c1e7" in pool_values(result, "string")


def test_orchestrator_does_not_walk_package_named_by_flask_import_name():
    # Walking the app's whole package imports modules the app never touches, firing their import side effects.
    modules, _ = orchestrator._resolve_with_adapters(
        Flask("test.python._constants.fixtures.dep_pkg"), adapters=[FlaskAdapter()]
    )
    assert "test.python._constants.fixtures.dep_pkg.optional" not in modules


def test_orchestrator_does_not_scan_installed_module_of_a_library_view():
    # A library-provided view (GraphQL, admin) lives in site-packages; its module is not user code.
    app = Flask("test.python._constants.fixtures.dep_pkg.flask_app")
    app.add_url_rule("/x", "x", jsonify)

    modules, _ = orchestrator._resolve_with_adapters(app, adapters=[FlaskAdapter()])
    assert not [name for name in modules if name == "flask" or name.startswith("flask.")]


def test_orchestrator_does_not_scan_installed_package_named_by_flask_import_name():
    # A library-built app points `import_name` at site-packages, whose constants are third-party anyway.
    modules, _ = orchestrator._resolve_with_adapters(Flask("hypothesis"), adapters=[FlaskAdapter()])
    assert not [name for name in modules if name == "hypothesis" or name.startswith("hypothesis.")]


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
