import requests
from datetime import datetime
from typing import Optional, Any, Callable, Dict
from requests.auth import HTTPBasicAuth


USERNAME: str = ""      # TODO: get value from global state
PASSWORD: str = ""      # TODO: get value from global state


class AuthToken:
    # Note: Python 3.8 and higher supports dataclasses which could be a little bit nicer here
    def __init__(self, token_value: str, timestamp: Optional[datetime] = None):
        self.value = token_value    # TODO: Map this reference to initialy set token value
        self.timestamp = timestamp if timestamp else datetime.now()


# TODO: fix *args problem for tests
def perform_api_authentication(*args, url: str, payload: Dict["str", "str"]) -> requests.Response:
    response = requests.request(
        method="POST",
        url=url,
        payload=payload
    )
    response.raise_for_status()
    return response


class APITokenAuth:
    login_url: Optional[str] = None
    perform_authentication: Callable = perform_api_authentication
    token_kwarg_name = "token"

    def __init__(self, **configs):
        self._username = configs.get("username", USERNAME)
        self._password = configs.get("password", PASSWORD)

    def get_token(self) -> str:
        payload = {"username": self._username, "password": self._password}
        perform_auth_method = self.perform_authentication
        auth_response = perform_auth_method(url=self.login_url, payload=payload)
        response_data = auth_response.json()
        return response_data[self.token_kwarg_name]


class BaseAuthStorage(APITokenAuth):
    auth_token: Optional[AuthToken] = None

    def __init__(self, auth_token: Optional[AuthToken] = None, **configs):
        if auth_token:
            BaseAuthStorage.auth_token = auth_token  # Warning: this has a singleton like effect. If this is override,
                                                     # it will be overriden global for all references
        super().__init__(**configs)

    def override_token(self):
        token = self.get_token()
        BaseAuthStorage.auth_token = AuthToken(token_value=token)


def register_auth_provider(
        auth_storage: BaseAuthStorage,
        *,
        auto_refresh: bool = True,
        refresh_interval: Optional[int] = None,
        auth_token: Optional[AuthToken] = None,
        **kwargs
):
    auth = auth_storage(auth_token=auth_token)
    def decorator(func):
        def wrapper(*args, **kwargs):
            if auto_refresh:
                auth.override_token()
            if refresh_interval:
                time_diff = datetime.now() - auth.auth_token.timestamp
                if time_diff.total_seconds() > refresh_interval:
                    auth.override_token()

            return func(*args, **kwargs)
        return wrapper
    return decorator
