"""Support for custom API authentication mechanisms."""

from __future__ import annotations

import inspect
import threading
import time
import warnings
from dataclasses import dataclass, field
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Generic,
    Protocol,
    TypeVar,
    Union,
    overload,
    runtime_checkable,
)

from .exceptions import UsageError
from .filters import FilterSet, FilterValue, MatcherFunc, attach_filter_chain
from .types import GenericTest

if TYPE_CHECKING:
    import requests.auth

    from .models import APIOperation, Case

DEFAULT_REFRESH_INTERVAL = 300
AUTH_STORAGE_ATTRIBUTE_NAME = "_schemathesis_auth"
Auth = TypeVar("Auth")


@dataclass
class AuthContext:
    """Holds state relevant for the authentication process.

    :ivar APIOperation operation: API operation that is currently being processed.
    :ivar app: Optional Python application if the WSGI / ASGI integration is used.
    """

    operation: APIOperation
    app: Any | None


CacheKeyFunction = Callable[["Case", "AuthContext"], Union[str, int]]


@runtime_checkable
class AuthProvider(Generic[Auth], Protocol):
    """Get authentication data for an API and set it on the generated test cases."""

    def get(self, case: Case, context: AuthContext) -> Auth | None:
        """Get the authentication data.

        :param Case case: Generated test case.
        :param AuthContext context: Holds state relevant for the authentication process.
        :return: Any authentication data you find useful for your use case. For example, it could be an access token.
        """

    def set(self, case: Case, data: Auth, context: AuthContext) -> None:
        """Set authentication data on a generated test case.

        :param Optional[Auth] data: Authentication data you got from the ``get`` method.
        :param Case case: Generated test case.
        :param AuthContext context: Holds state relevant for the authentication process.
        """


@dataclass
class CacheEntry(Generic[Auth]):
    """Cached auth data."""

    data: Auth
    expires: float


@dataclass
class RequestsAuth(Generic[Auth]):
    """Provider that sets auth data via `requests` auth instance."""

    auth: requests.auth.AuthBase

    def get(self, _: Case, __: AuthContext) -> Auth | None:
        return self.auth  # type: ignore[return-value]

    def set(self, case: Case, _: Auth, __: AuthContext) -> None:
        case._auth = self.auth


@dataclass
class CachingAuthProvider(Generic[Auth]):
    """Caches the underlying auth provider."""

    provider: AuthProvider
    refresh_interval: int = DEFAULT_REFRESH_INTERVAL
    cache_entry: CacheEntry[Auth] | None = None
    # The timer exists here to simplify testing
    timer: Callable[[], float] = time.monotonic
    _refresh_lock: threading.Lock = field(default_factory=threading.Lock)

    def get(self, case: Case, context: AuthContext) -> Auth | None:
        """Get cached auth value."""
        cache_entry = self._get_cache_entry(case, context)
        if cache_entry is None or self.timer() >= cache_entry.expires:
            with self._refresh_lock:
                cache_entry = self._get_cache_entry(case, context)
                if not (cache_entry is None or self.timer() >= cache_entry.expires):
                    # Another thread updated the cache
                    return cache_entry.data
                # We know that optional auth is possible only inside a higher-level wrapper
                data: Auth = _provider_get(self.provider, case, context)  # type: ignore[assignment]
                self._set_cache_entry(data, case, context)
                return data
        return cache_entry.data

    def _get_cache_entry(self, case: Case, context: AuthContext) -> CacheEntry[Auth] | None:
        return self.cache_entry

    def _set_cache_entry(self, data: Auth, case: Case, context: AuthContext) -> None:
        self.cache_entry = CacheEntry(data=data, expires=self.timer() + self.refresh_interval)

    def set(self, case: Case, data: Auth, context: AuthContext) -> None:
        """Set auth data on the `Case` instance.

        This implementation delegates this to the actual provider.
        """
        self.provider.set(case, data, context)


def _noop_key_function(case: Case, context: AuthContext) -> str:
    # Never used
    raise NotImplementedError


@dataclass
class KeyedCachingAuthProvider(CachingAuthProvider[Auth]):
    cache_by_key: CacheKeyFunction = _noop_key_function
    cache_entries: dict[str | int, CacheEntry[Auth] | None] = field(default_factory=dict)

    def _get_cache_entry(self, case: Case, context: AuthContext) -> CacheEntry[Auth] | None:
        key = self.cache_by_key(case, context)
        return self.cache_entries.get(key)

    def _set_cache_entry(self, data: Auth, case: Case, context: AuthContext) -> None:
        key = self.cache_by_key(case, context)
        self.cache_entries[key] = CacheEntry(data=data, expires=self.timer() + self.refresh_interval)


class FilterableRegisterAuth(Protocol):
    """Protocol that adds filters to the return value of `register`."""

    def __call__(self, provider_class: type[AuthProvider]) -> type[AuthProvider]:
        pass

    def apply_to(
        self,
        func: MatcherFunc | None = None,
        *,
        name: FilterValue | None = None,
        name_regex: str | None = None,
        method: FilterValue | None = None,
        method_regex: str | None = None,
        path: FilterValue | None = None,
        path_regex: str | None = None,
    ) -> FilterableRegisterAuth:
        pass

    def skip_for(
        self,
        func: MatcherFunc | None = None,
        *,
        name: FilterValue | None = None,
        name_regex: str | None = None,
        method: FilterValue | None = None,
        method_regex: str | None = None,
        path: FilterValue | None = None,
        path_regex: str | None = None,
    ) -> FilterableRegisterAuth:
        pass


class FilterableApplyAuth(Protocol):
    """Protocol that adds filters to the return value of `apply`."""

    def __call__(self, test: GenericTest) -> GenericTest:
        pass

    def apply_to(
        self,
        func: MatcherFunc | None = None,
        *,
        name: FilterValue | None = None,
        name_regex: str | None = None,
        method: FilterValue | None = None,
        method_regex: str | None = None,
        path: FilterValue | None = None,
        path_regex: str | None = None,
    ) -> FilterableApplyAuth:
        pass

    def skip_for(
        self,
        func: MatcherFunc | None = None,
        *,
        name: FilterValue | None = None,
        name_regex: str | None = None,
        method: FilterValue | None = None,
        method_regex: str | None = None,
        path: FilterValue | None = None,
        path_regex: str | None = None,
    ) -> FilterableApplyAuth:
        pass


class FilterableRequestsAuth(Protocol):
    """Protocol that adds filters to the return value of `set_from_requests`."""

    def apply_to(
        self,
        func: MatcherFunc | None = None,
        *,
        name: FilterValue | None = None,
        name_regex: str | None = None,
        method: FilterValue | None = None,
        method_regex: str | None = None,
        path: FilterValue | None = None,
        path_regex: str | None = None,
    ) -> FilterableRequestsAuth:
        pass

    def skip_for(
        self,
        func: MatcherFunc | None = None,
        *,
        name: FilterValue | None = None,
        name_regex: str | None = None,
        method: FilterValue | None = None,
        method_regex: str | None = None,
        path: FilterValue | None = None,
        path_regex: str | None = None,
    ) -> FilterableRequestsAuth:
        pass


@dataclass
class SelectiveAuthProvider(Generic[Auth]):
    """Applies auth depending on the configured filters."""

    provider: AuthProvider
    filter_set: FilterSet

    def get(self, case: Case, context: AuthContext) -> Auth | None:
        if self.filter_set.match(context):
            return _provider_get(self.provider, case, context)
        return None

    def set(self, case: Case, data: Auth, context: AuthContext) -> None:
        self.provider.set(case, data, context)


@dataclass
class AuthStorage(Generic[Auth]):
    """Store and manage API authentication."""

    providers: list[AuthProvider] = field(default_factory=list)

    @property
    def is_defined(self) -> bool:
        """Whether there is an auth provider set."""
        return bool(self.providers)

    @overload
    def __call__(
        self,
        *,
        refresh_interval: int | None = DEFAULT_REFRESH_INTERVAL,
        cache_by_key: CacheKeyFunction | None = None,
    ) -> FilterableRegisterAuth:
        pass

    @overload
    def __call__(
        self,
        provider_class: type[AuthProvider],
        *,
        refresh_interval: int | None = DEFAULT_REFRESH_INTERVAL,
        cache_by_key: CacheKeyFunction | None = None,
    ) -> FilterableApplyAuth:
        pass

    def __call__(
        self,
        provider_class: type[AuthProvider] | None = None,
        *,
        refresh_interval: int | None = DEFAULT_REFRESH_INTERVAL,
        cache_by_key: CacheKeyFunction | None = None,
    ) -> FilterableRegisterAuth | FilterableApplyAuth:
        if provider_class is not None:
            return self.apply(provider_class, refresh_interval=refresh_interval, cache_by_key=cache_by_key)
        return self.register(refresh_interval=refresh_interval, cache_by_key=cache_by_key)

    def set_from_requests(self, auth: requests.auth.AuthBase) -> FilterableRequestsAuth:
        """Use `requests` auth instance as an auth provider."""
        filter_set = FilterSet()
        self.providers.append(SelectiveAuthProvider(provider=RequestsAuth(auth), filter_set=filter_set))

        class _FilterableRequestsAuth:
            pass

        attach_filter_chain(_FilterableRequestsAuth, "apply_to", filter_set.include)
        attach_filter_chain(_FilterableRequestsAuth, "skip_for", filter_set.exclude)

        return _FilterableRequestsAuth  # type: ignore[return-value]

    def _set_provider(
        self,
        *,
        provider_class: type[AuthProvider],
        refresh_interval: int | None = DEFAULT_REFRESH_INTERVAL,
        cache_by_key: CacheKeyFunction | None = None,
        filter_set: FilterSet,
    ) -> None:
        if not issubclass(provider_class, AuthProvider):
            raise TypeError(
                f"`{provider_class.__name__}` is not a valid auth provider. "
                f"Check `schemathesis.auths.AuthProvider` documentation for examples."
            )
        provider: AuthProvider
        # Apply caching if desired
        instance = provider_class()
        if refresh_interval is not None:
            if cache_by_key is None:
                provider = CachingAuthProvider(instance, refresh_interval=refresh_interval)
            else:
                provider = KeyedCachingAuthProvider(
                    instance, refresh_interval=refresh_interval, cache_by_key=cache_by_key
                )
        else:
            provider = instance
        # Store filters if any
        if not filter_set.is_empty():
            provider = SelectiveAuthProvider(provider, filter_set)
        self.providers.append(provider)

    def register(
        self,
        *,
        refresh_interval: int | None = DEFAULT_REFRESH_INTERVAL,
        cache_by_key: CacheKeyFunction | None = None,
    ) -> FilterableRegisterAuth:
        """Register a new auth provider.

        .. code-block:: python

            @schemathesis.auth()
            class TokenAuth:
                def get(self, context):
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
        filter_set = FilterSet()

        def wrapper(provider_class: type[AuthProvider]) -> type[AuthProvider]:
            self._set_provider(
                provider_class=provider_class,
                refresh_interval=refresh_interval,
                filter_set=filter_set,
                cache_by_key=cache_by_key,
            )
            return provider_class

        attach_filter_chain(wrapper, "apply_to", filter_set.include)
        attach_filter_chain(wrapper, "skip_for", filter_set.exclude)

        return wrapper  # type: ignore[return-value]

    def unregister(self) -> None:
        """Unregister the currently registered auth provider.

        No-op if there is no auth provider registered.
        """
        self.providers = []

    def apply(
        self,
        provider_class: type[AuthProvider],
        *,
        refresh_interval: int | None = DEFAULT_REFRESH_INTERVAL,
        cache_by_key: CacheKeyFunction | None = None,
    ) -> FilterableApplyAuth:
        """Register auth provider only on one test function.

        :param Type[AuthProvider] provider_class: Authentication provider class.
        :param Optional[int] refresh_interval: Cache duration in seconds.

        .. code-block:: python

            class Auth:
                ...


            @schema.auth(Auth)
            @schema.parametrize()
            def test_api(case):
                ...

        """
        filter_set = FilterSet()

        def wrapper(test: GenericTest) -> GenericTest:
            auth_storage = self.add_auth_storage(test)
            auth_storage._set_provider(
                provider_class=provider_class,
                refresh_interval=refresh_interval,
                filter_set=filter_set,
                cache_by_key=cache_by_key,
            )
            return test

        attach_filter_chain(wrapper, "apply_to", filter_set.include)
        attach_filter_chain(wrapper, "skip_for", filter_set.exclude)

        return wrapper  # type: ignore[return-value]

    @classmethod
    def add_auth_storage(cls, test: GenericTest) -> AuthStorage:
        """Attach a new auth storage instance to the test if it is not already present."""
        if not hasattr(test, AUTH_STORAGE_ATTRIBUTE_NAME):
            setattr(test, AUTH_STORAGE_ATTRIBUTE_NAME, cls())
        else:
            raise UsageError(f"`{test.__name__}` has already been decorated with `apply`.")
        return getattr(test, AUTH_STORAGE_ATTRIBUTE_NAME)

    def set(self, case: Case, context: AuthContext) -> None:
        """Set authentication data on a generated test case."""
        if not self.is_defined:
            raise UsageError("No auth provider is defined.")
        for provider in self.providers:
            data: Auth | None = _provider_get(provider, case, context)
            if data is not None:
                provider.set(case, data, context)
                break


def _provider_get(auth_provider: AuthProvider, case: Case, context: AuthContext) -> Auth | None:
    # A shim to provide a compatibility layer between previously used convention for `AuthProvider.get`
    # where it used to accept a single `context` argument
    method = auth_provider.get
    parameters = inspect.signature(method).parameters
    if len(parameters) == 1:
        # Old calling convention
        warnings.warn(
            "The method 'get' of your AuthProvider is using the old calling convention, "
            "which is deprecated and will be removed in Schemathesis 4.0. "
            "Please update it to accept both 'case' and 'context' as arguments.",
            DeprecationWarning,
            stacklevel=1,
        )
        return method(context)  # type: ignore
    # New calling convention
    return method(case, context)


def set_on_case(case: Case, context: AuthContext, auth_storage: AuthStorage | None) -> None:
    """Set authentication data on this case.

    If there is no auth defined, then this function is no-op.
    """
    if auth_storage is not None:
        auth_storage.set(case, context)
    elif case.operation.schema.auth.is_defined:
        case.operation.schema.auth.set(case, context)
    elif GLOBAL_AUTH_STORAGE.is_defined:
        GLOBAL_AUTH_STORAGE.set(case, context)


def get_auth_storage_from_test(test: GenericTest) -> AuthStorage | None:
    """Extract the currently attached auth storage from a test function."""
    return getattr(test, AUTH_STORAGE_ATTRIBUTE_NAME, None)


# Global auth API
GLOBAL_AUTH_STORAGE: AuthStorage = AuthStorage()
register = GLOBAL_AUTH_STORAGE.register
unregister = GLOBAL_AUTH_STORAGE.unregister
