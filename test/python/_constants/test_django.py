from schemathesis.python._constants.adapters import default_adapters
from schemathesis.python._constants.orchestrator import extract_all
from schemathesis.python._constants.registry import SourceRegistry
from test.python._constants.fixtures import django_app
from test.python._constants.helpers import pool_values


def test_constants_collected_from_django_url_handlers():
    registry = SourceRegistry()

    @registry.register
    def source():
        return django_app.make_app()

    result = extract_all(registry=registry, adapters=default_adapters())
    assert django_app.DJANGO_UNLOCK_CODE in pool_values(result, "string")
