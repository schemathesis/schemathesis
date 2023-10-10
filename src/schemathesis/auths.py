"""Support for custom API authentication mechanisms."""
import inspect
import threading
import time
import warnings
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Generic, List, Optional, Type, TypeVar, Union, overload

import requests.auth
from typing_extensions import Protocol, runtime_checkable

from .exceptions import UsageError
from .filters import FilterSet, FilterValue, MatcherFunc, attach_filter_chain
from .types import GenericTest

if TYPE_CHECKING:
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

    operation: "APIOperation"
    app: Optional[Any]


@runtime_checkable
class AuthProvider(Generic[Auth], Protocol):
    """Get authentication data for an API and set it on the generated test cases."""

    def get(self, case: "Case", context: AuthContext) -> Optional[Auth]:
        """Get the authentication data.

        :param Case case: Generated test case.
        :param AuthContext context: Holds state relevant for the authentication process.
        :return: Any authentication data you find useful for your use case. For example, it could be an access token.
        """

    def set(self, case: "Case", data: Auth, context: AuthContext) -> None:
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

    def get(self, _: "Case", __: AuthContext) -> Optional[Auth]:
        return self.auth  # type: ignore[return-value]

    def set(self, case: "Case", _: Auth, __: AuthContext) -> None:
        case._auth = self.auth


@dataclass
class CachingAuthProvider(Generic[Auth]):
    """Caches the underlying auth provider."""

    provider: AuthProvider
    refresh_interval: int = DEFAULT_REFRESH_INTERVAL
    cache_entry: Optional[CacheEntry[Auth]] = None
    # The timer exists here to simplify testing
    timer: Callable[[], float] = time.monotonic
    _refresh_lock: threading.Lock = field(default_factory=threading.Lock)

    def get(self, case: "Case", context: AuthContext) -> Optional[Auth]:
        """Get cached auth value."""
        if self.cache_entry is None or self.timer() >= self.cache_entry.expires:
            with self._refresh_lock:
                if not (self.cache_entry is None or self.timer() >= self.cache_entry.expires):
                    # Another thread updated the cache
                    return self.cache_entry.data
                # We know that optional auth is possible only inside a higher-level wrapper
                data: Auth = _provider_get(self.provider, case, context)  # type: ignore[assignment]
                self.cache_entry = CacheEntry(data=data, expires=self.timer() + self.refresh_interval)
                return data
        return self.cache_entry.data

    def set(self, case: "Case", data: Auth, context: AuthContext) -> None:
        """Set auth data on the `Case` instance.

        This implementation delegates this to the actual provider.
        """
        self.provider.set(case, data, context)


class FilterableRegisterAuth(Protocol):
    """Protocol that adds filters to the return value of `register`."""

    def __call__(self, provider_class: Type[AuthProvider]) -> Type[AuthProvider]:
        pass

    def apply_to(
        self,
        func: Optional[MatcherFunc] = None,
        *,
        name: Optional[FilterValue] = None,
        name_regex: Optional[str] = None,
        method: Optional[FilterValue] = None,
        method_regex: Optional[str] = None,
        path: Optional[FilterValue] = None,
        path_regex: Optional[str] = None,
    ) -> "FilterableRegisterAuth":
        pass

    def skip_for(
        self,
        func: Optional[MatcherFunc] = None,
        *,
        name: Optional[FilterValue] = None,
        name_regex: Optional[str] = None,
        method: Optional[FilterValue] = None,
        method_regex: Optional[str] = None,
        path: Optional[FilterValue] = None,
        path_regex: Optional[str] = None,
    ) -> "FilterableRegisterAuth":
        pass


class FilterableApplyAuth(Protocol):
    """Protocol that adds filters to the return value of `apply`."""

    def __call__(self, test: GenericTest) -> GenericTest:
        pass

    def apply_to(
        self,
        func: Optional[MatcherFunc] = None,
        *,
        name: Optional[FilterValue] = None,
        name_regex: Optional[str] = None,
        method: Optional[FilterValue] = None,
        method_regex: Optional[str] = None,
        path: Optional[FilterValue] = None,
        path_regex: Optional[str] = None,
    ) -> "FilterableApplyAuth":
        pass

    def skip_for(
        self,
        func: Optional[MatcherFunc] = None,
        *,
        name: Optional[FilterValue] = None,
        name_regex: Optional[str] = None,
        method: Optional[FilterValue] = None,
        method_regex: Optional[str] = None,
        path: Optional[FilterValue] = None,
        path_regex: Optional[str] = None,
    ) -> "FilterableApplyAuth":
        pass


class FilterableRequestsAuth(Protocol):
    """Protocol that adds filters to the return value of `set_from_requests`."""

    def apply_to(
        self,
        func: Optional[MatcherFunc] = None,
        *,
        name: Optional[FilterValue] = None,
        name_regex: Optional[str] = None,
        method: Optional[FilterValue] = None,
        method_regex: Optional[str] = None,
        path: Optional[FilterValue] = None,
        path_regex: Optional[str] = None,
    ) -> "FilterableRequestsAuth":
        pass

    def skip_for(
        self,
        func: Optional[MatcherFunc] = None,
        *,
        name: Optional[FilterValue] = None,
        name_regex: Optional[str] = None,
        method: Optional[FilterValue] = None,
        method_regex: Optional[str] = None,
        path: Optional[FilterValue] = None,
        path_regex: Optional[str] = None,
    ) -> "FilterableRequestsAuth":
        pass


@dataclass
class SelectiveAuthProvider(Generic[Auth]):
    """Applies auth depending on the configured filters."""

    provider: AuthProvider
    filter_set: FilterSet

    def get(self, case: "Case", context: AuthContext) -> Optional[Auth]:
        if self.filter_set.match(context):
            return _provider_get(self.provider, case, context)
        return None

    def set(self, case: "Case", data: Auth, context: AuthContext) -> None:
        self.provider.set(case, data, context)


@dataclass
class AuthStorage(Generic[Auth]):
    """Store and manage API authentication."""

    providers: List[AuthProvider] = field(default_factory=list)

    @property
    def is_defined(self) -> bool:
        """Whether there is an auth provider set."""
        return bool(self.providers)

    @overload
    def __call__(
        self,
        *,
        refresh_interval: Optional[int] = DEFAULT_REFRESH_INTERVAL,
    ) -> FilterableRegisterAuth:
        pass

    @overload
    def __call__(
        self,
        provider_class: Type[AuthProvider],
        *,
        refresh_interval: Optional[int] = DEFAULT_REFRESH_INTERVAL,
    ) -> FilterableApplyAuth:
        pass

    def __call__(
        self,
        provider_class: Optional[Type[AuthProvider]] = None,
        *,
        refresh_interval: Optional[int] = DEFAULT_REFRESH_INTERVAL,
    ) -> Union[FilterableRegisterAuth, FilterableApplyAuth]:
        if provider_class is not None:
            return self.apply(provider_class, refresh_interval=refresh_interval)
        return self.register(refresh_interval=refresh_interval)

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
        provider_class: Type[AuthProvider],
        refresh_interval: Optional[int] = DEFAULT_REFRESH_INTERVAL,
        filter_set: FilterSet,
    ) -> None:
        if not issubclass(provider_class, AuthProvider):
            raise TypeError(
                f"`{provider_class.__name__}` is not a valid auth provider. "
                f"Check `schemathesis.auths.AuthProvider` documentation for examples."
            )
        provider: AuthProvider
        # Apply caching if desired
        if refresh_interval is not None:
            provider = CachingAuthProvider(provider_class(), refresh_interval=refresh_interval)
        else:
            provider = provider_class()
        # Store filters if any
        if not filter_set.is_empty():
            provider = SelectiveAuthProvider(provider, filter_set)
        self.providers.append(provider)

    def register(self, *, refresh_interval: Optional[int] = DEFAULT_REFRESH_INTERVAL) -> FilterableRegisterAuth:
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

        def wrapper(provider_class: Type[AuthProvider]) -> Type[AuthProvider]:
            self._set_provider(provider_class=provider_class, refresh_interval=refresh_interval, filter_set=filter_set)
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
        self, provider_class: Type[AuthProvider], *, refresh_interval: Optional[int] = DEFAULT_REFRESH_INTERVAL
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
                provider_class=provider_class, refresh_interval=refresh_interval, filter_set=filter_set
            )
            return test

        attach_filter_chain(wrapper, "apply_to", filter_set.include)
        attach_filter_chain(wrapper, "skip_for", filter_set.exclude)

        return wrapper  # type: ignore[return-value]

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
        if not self.is_defined:
            raise UsageError("No auth provider is defined.")
        for provider in self.providers:
            data: Optional[Auth] = _provider_get(provider, case, context)
            if data is not None:
                provider.set(case, data, context)
                break


def _provider_get(auth_provider: AuthProvider, case: "Case", context: AuthContext) -> Optional[Auth]:
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
