"""Web Fuzzing Commons authentication support (https://github.com/WebFuzzing/Commons)."""

from .auth import (
    AuthenticationInfo,
    Header,
    HttpVerb,
    LoginEndpoint,
    PayloadUsernamePassword,
    TokenHandling,
)
from .errors import WFCError, WFCLoadError, WFCLoginError, WFCTokenExtractionError, WFCValidationError
from .loader import load_from_dict, load_from_file

__all__ = [
    "AuthenticationInfo",
    "Header",
    "HttpVerb",
    "LoginEndpoint",
    "PayloadUsernamePassword",
    "TokenHandling",
    "WFCError",
    "WFCLoadError",
    "WFCValidationError",
    "WFCLoginError",
    "WFCTokenExtractionError",
    "load_from_file",
    "load_from_dict",
]
