"""Web Fuzzing Commons (WFC) integration for Schemathesis.

This module provides support for WFC authentication format, allowing Schemathesis
to read and use authentication configurations defined in the WFC standard.

WFC is a spec-agnostic standard that works with OpenAPI, GraphQL, and other API types.

Reference: https://github.com/WebFuzzing/Commons
"""

from .auth import (
    AuthDocument,
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
    "AuthDocument",
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
