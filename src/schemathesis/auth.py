import requests
from enum import Enum
from typing import Optional, Any
from functools import partial
from requests.auth import HTTPBasicAuth


class AuthType(Enum):
    COOKIE = 0
    HEADER = 1
    QUERY_PARAMS = 2
    USERNAME_PASSWORD = 3


class AuthStorage:
    """Description

    Szenarios:
        No Token/Auth Set for request yet
        Token Expires

    Auth Methods
        Cookie
        Header
        request auth

    Keyword Arguments:
        username
        password
        auth_token
    """

    login_url: Optional[str] = None
    refresh_url: Optional[str] = None
    auth_type: AuthType = AuthType.HEADER
    auth_callback: Optional[Any] = None

    DEFAULT_CONFIG = {
        "username": None,
        "password": None,
        "token": None
    }

    def __init__(self, **configs):
        if self.auth_type == AuthType.HEADER:
            pass
        elif self.auth_type == AuthType.COOKIE:
            pass
        elif self.auth_type == AuthType.QUERY_PARAMS:
            pass
        elif self.auth_type == AuthType.USERNAME_PASSWORD:
            self._username = configs.get("username")
            self._password = configs.get("password")
        else:
            raise TypeError("auth_type not supported.")

    def _perform_username_password_authentication(self):
        """Global override of request.request method with auth callback

        Requirement: Each authentication needs to be valid on adding username / password combination
            per request
        """
        if not self.auth_callback:
            # Warning: Overriding static param will have singleton side effect
            self.auth_callback = HTTPBasicAuth
        requests.request = partial(
            requests.request,
            auth=self.auth_callback(
                username=self._username,
                password=self._password
            )
        )

    def perform_authentication(self):
        if self.auth_type == AuthType.HEADER:
            pass
        elif self.auth_type == AuthType.COOKIE:
            pass
        elif self.auth_type == AuthType.QUERY_PARAMS:
            pass
        elif self.auth_type == AuthType.USERNAME_PASSWORD:
            self._perform_username_password_authentication()
        else:
            raise TypeError("auth_type not supported.")


def register_auth_provider(auto_refresh: bool = True, refresh_interval: Optional[int] = None):
    """
    Programflow:
    1. Check if refresh interval is set - if yes, check if auth is expired
    2. Check if auto refresh is set. If Yes: authenticate user per request
    """
    pass
