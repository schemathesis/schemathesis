from schemathesis.python._constants.orchestrator import extract_all
from schemathesis.python._constants.registry import SourceRegistry
from schemathesis.python.application import ApplicationCode, application_code
from test.python._constants.fixtures import django_app
from test.python._constants.fixtures.dep_pkg import flask_app, shared
from test.python._constants.helpers import pool_values
from test.utils import HERE


def test_constants_collected_from_django_url_handlers():
    registry = SourceRegistry()

    @registry.register
    def source():
        return django_app.make_app()

    result = extract_all(registry=registry)
    assert django_app.DJANGO_UNLOCK_CODE in pool_values(result, "string")


def test_constants_collected_from_nested_django_url_handlers():
    registry = SourceRegistry()

    @registry.register
    def source():
        return django_app.make_app()

    result = extract_all(registry=registry)
    assert shared.UNLOCK_TOKEN in pool_values(result, "string")


def test_django_application_code_is_located_through_url_handlers():
    assert application_code(django_app.make_app()) == ApplicationCode(
        framework="django",
        handlers=(django_app.unlock, flask_app.unlock),
        modules=(django_app.__name__, flask_app.__name__),
        roots=(HERE,),
    )
