import warnings

import pytest

from schemathesis.auths import AuthContext, AuthStorage, CachingAuthProvider
from schemathesis.exceptions import UsageError
from schemathesis.models import Case

TOKEN = "EXAMPLE-TOKEN"


@pytest.fixture
def token():
    return TOKEN


@pytest.fixture
def auth_storage():
    return AuthStorage()


@pytest.fixture(params=["old", "new"])
def calling_convention(request):
    return request.param


@pytest.fixture
def auth_provider_class(calling_convention, token):
    class Auth:
        def __init__(self):
            self.get_calls = 0
            self.set_calls = 0

        if calling_convention == "old":

            def get(self, context):
                self.get_calls += 1
                return token

        else:

            def get(self, case, context):
                self.get_calls += 1
                return token

        def set(self, case, data, context):
            self.set_calls += 1
            case.headers = {"Authorization": f"Bearer {data}"}

    return Auth


def test_cache(calling_convention, auth_provider_class, token, mocker):
    current_time = 0.0

    def timer():
        return current_time

    context = mocker.create_autospec(AuthContext)
    # When caching provider is used
    provider = CachingAuthProvider(auth_provider_class(), timer=timer)
    with warnings.catch_warnings(record=True) as logs:
        # Then all `get` calls are cached
        assert provider.get(None, context) == token
        assert provider.get(None, context) == token
        assert provider.provider.get_calls == 1
        # And refresh happens when the refresh period has passed
        current_time += provider.refresh_interval
        assert provider.get(None, context) == token
        assert provider.provider.get_calls == 2
        assert provider.get(None, context) == token
        assert provider.provider.get_calls == 2  # No increase

    if calling_convention == "old":
        assert len(logs) == 2
        expected = (
            "The method 'get' of your AuthProvider is using the old calling convention, which is deprecated and "
            "will be removed in Schemathesis 4.0. Please update it to accept both 'case' and 'context' as arguments."
        )
        assert str(logs[0].message) == str(logs[1].message) == expected


def test_register_invalid(auth_storage):
    # When the class implementation is wrong
    # Then it should not be possible to register it

    with pytest.raises(TypeError, match="`Invalid` is not a valid auth provider"):

        @auth_storage.register()
        class Invalid:
            pass


def test_apply_twice(openapi3_schema, auth_provider_class):
    # When auth is registered twice
    # Then it is an error
    with pytest.raises(UsageError, match="`test` has already been decorated with `apply`"):

        @openapi3_schema.auth(auth_provider_class)
        @openapi3_schema.auth(auth_provider_class)
        def test(case):
            pass


def test_register_valid(auth_storage, auth_provider_class):
    # When the class implementation is valid
    # Then it should be possible to register it without issues
    auth_storage.register(refresh_interval=None)(auth_provider_class)
    assert auth_storage.providers
    assert isinstance(auth_storage.providers[0], auth_provider_class)


def test_register_cached(auth_storage, auth_provider_class):
    # When the `refresh_interval` is not None
    auth_storage.register()(auth_provider_class)
    # Then the actual provider should be cached
    assert auth_storage.providers
    assert isinstance(auth_storage.providers[0], CachingAuthProvider)
    assert isinstance(auth_storage.providers[0].provider, auth_provider_class)


def test_set_noop(auth_storage, mocker):
    # When `AuthStorage.set` is called without `provider`
    with pytest.raises(UsageError, match="No auth provider is defined."):
        auth_storage.set(mocker.create_autospec(Case), mocker.create_autospec(AuthContext))
    # This normally should not happen, as it is checked before.
