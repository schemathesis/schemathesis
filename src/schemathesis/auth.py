"""Support for custom API authentication mechanisms."""
import threading
import time
from typing import TYPE_CHECKING, Any, Callable, Generic, Optional, Type, TypeVar

import attr
from typing_extensions import Protocol, runtime_checkable

from .exceptions import UsageError
from .types import GenericTest

if TYPE_CHECKING:
    from .models import APIOperation, Case

DEFAULT_REFRESH_INTERVAL = 300
AUTH_STORAGE_ATTRIBUTE_NAME = "_schemathesis_auth"
Auth = TypeVar("Auth")


@attr.s(slots=True)  # pragma: no mutate
class AuthContext:
    """Holds state relevant for the authentication process.

    :ivar APIOperation operation: API operation that is currently being processed.
    :ivar app: Optional Python application if the WSGI / ASGI integration is used.
    """

    operation: "APIOperation" = attr.ib()  # pragma: no mutate
    app: Optional[Any] = attr.ib()  # pragma: no mutate


@runtime_checkable
class AuthProvider(Protocol):
    """Get authentication data for an API and set it on the generated test cases."""

    def get(self, context: AuthContext) -> Auth:
        """Get the authentication data.

        :param AuthContext context: Holds state relevant for the authentication process.
        :return: Any authentication data you find useful for your use case. For example, it could be an access token.
        """

    def set(self, case: "Case", data: Auth, context: AuthContext) -> None:
        """Set authentication data on a generated test case.

        :param Optional[Auth] data: Authentication data you got from the ``get`` method.
        :param Case case: Generated test case.
        :param AuthContext context: Holds state relevant for the authentication process.
        """


@attr.s(slots=True)
class CacheEntry(Generic[Auth]):
    """Cached auth data."""

    data: Auth = attr.ib()
    expires: float = attr.ib()


@attr.s(slots=True)
class CachingAuthProvider(Generic[Auth]):
    """Caches the underlying auth provider."""

    provider: AuthProvider = attr.ib()
    refresh_interval: int = attr.ib(default=DEFAULT_REFRESH_INTERVAL)
    cache_entry: Optional[CacheEntry[Auth]] = attr.ib(default=None)
    # The timer exists here to simplify testing
    timer: Callable[[], float] = attr.ib(default=time.monotonic)
    _refresh_lock: threading.Lock = attr.ib(factory=threading.Lock)

    def get(self, context: AuthContext) -> Auth:
        """Get cached auth value."""
        if self.cache_entry is None or self.timer() >= self.cache_entry.expires:
            with self._refresh_lock:
                if not (self.cache_entry is None or self.timer() >= self.cache_entry.expires):
                    # Another thread updated the cache
                    return self.cache_entry.data
                data: Auth = self.provider.get(context)
                self.cache_entry = CacheEntry(data=data, expires=self.timer() + self.refresh_interval)
                return data
        return self.cache_entry.data

    def set(self, case: "Case", data: Auth, context: AuthContext) -> None:
        """Set auth data on the `Case` instance.

        This implementation delegates this to the actual provider.
        """
        self.provider.set(case, data, context)


@attr.s(slots=True)
class AuthStorage(Generic[Auth]):
    """Store and manage API authentication."""

    provider: Optional[AuthProvider] = attr.ib(default=None)

    @property
    def is_defined(self) -> bool:
        """Whether there is an auth provider set."""
        return self.provider is not None

    def register(
        self, refresh_interval: Optional[int] = DEFAULT_REFRESH_INTERVAL
    ) -> Callable[[Type[AuthProvider]], Type[AuthProvider]]:
        """Register a new auth provider.

        .. code-block:: python

            @schemathesis.auth.register()
            class TokenAuth:
                def get(self, context):
                    # This is a real endpoint, try it out!
                    response = requests.post(
                        "https://example.schemathesis.io/api/token/",
                        json={"username": "demo", "password": "test"},
                    )
                    data = response.json()
                    return data["access_token"]

                def set(self, case, data, context):
                    # Modify `case` the way you need
                    case.headers = {"Authorization": f"Bearer {data}"}
        """

        def wrapper(provider_class: Type[AuthProvider]) -> Type[AuthProvider]:
            if not issubclass(provider_class, AuthProvider):
                raise TypeError(
                    f"`{provider_class.__name__}` is not a valid auth provider. "
                    f"Check `schemathesis.auth.AuthProvider` documentation for examples."
                )
            # Apply caching if desired
            if refresh_interval is not None:
                self.provider = CachingAuthProvider(provider_class(), refresh_interval=refresh_interval)
            else:
                self.provider = provider_class()
            return provider_class

        return wrapper

    def unregister(self) -> None:
        """Unregister the currently registered auth provider.

        No-op if there is no auth provider registered.
        """
        self.provider = None

    def apply(
        self, provider_class: Type[AuthProvider], *, refresh_interval: Optional[int] = DEFAULT_REFRESH_INTERVAL
    ) -> Callable[[GenericTest], GenericTest]:
        """Register auth provider only on one test function.

        :param Type[AuthProvider] provider_class: Authentication provider class.
        :param Optional[int] refresh_interval: Cache duration in seconds.

        .. code-block:: python

            class Auth:
                ...


            @schema.auth.apply(Auth)
            @schema.parametrize()
            def test_api(case):
                ...

        """

        def wrapper(test: GenericTest) -> GenericTest:
            auth_storage = self.add_auth_storage(test)
            auth_storage.register(refresh_interval=refresh_interval)(provider_class)
            return test

        return wrapper

    @classmethod
    def add_auth_storage(cls, test: GenericTest) -> "AuthStorage":
        """Attach a new auth storage instance to the test if it is not already present."""
        if not hasattr(test, AUTH_STORAGE_ATTRIBUTE_NAME):
            setattr(test, AUTH_STORAGE_ATTRIBUTE_NAME, cls())
        else:
            raise UsageError(f"`{test.__name__}` has already been decorated with `apply`.")
        return getattr(test, AUTH_STORAGE_ATTRIBUTE_NAME)

    def set(self, case: "Case", context: AuthContext) -> None:
        """Set authentication data on a generated test case."""
        if self.provider is not None:
            data: Auth = self.provider.get(context)
            self.provider.set(case, data, context)
        else:
            raise UsageError("No auth provider is defined.")


def set_on_case(case: "Case", context: AuthContext, auth_storage: Optional[AuthStorage]) -> None:
    """Set authentication data on this case.

    If there is no auth defined, then this function is no-op.
    """
    if auth_storage is not None:
        auth_storage.set(case, context)
    elif case.operation.schema.auth.is_defined:
        case.operation.schema.auth.set(case, context)
    elif GLOBAL_AUTH_STORAGE.is_defined:
        GLOBAL_AUTH_STORAGE.set(case, context)


def get_auth_storage_from_test(test: GenericTest) -> Optional[AuthStorage]:
    """Extract the currently attached auth storage from a test function."""
    return getattr(test, AUTH_STORAGE_ATTRIBUTE_NAME, None)


# Global auth API
GLOBAL_AUTH_STORAGE: AuthStorage = AuthStorage()
register = GLOBAL_AUTH_STORAGE.register
unregister = GLOBAL_AUTH_STORAGE.unregister
