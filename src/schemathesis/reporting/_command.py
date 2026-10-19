from __future__ import annotations

import os
import sys
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING

from schemathesis.core.output.sanitization import is_sensitive_key, sanitize_url

if TYPE_CHECKING:
    from schemathesis.config import SanitizationConfig


def _sanitize_header(value: str, config: SanitizationConfig) -> str:
    name, separator, rest = value.partition(":")
    if not separator or not is_sensitive_key(
        name.strip(), keys_to_sanitize=config.keys_to_sanitize, sensitive_markers=config.sensitive_markers
    ):
        return value
    padding = rest[: len(rest) - len(rest.lstrip())]
    return f"{name}{separator}{padding}{config.replacement}"


def _sanitize_secret(value: str, config: SanitizationConfig) -> str:
    return config.replacement


def _sanitize_url(value: str, config: SanitizationConfig) -> str:
    return sanitize_url(value, config=config)


_SANITIZERS: dict[str, Callable[[str, SanitizationConfig], str]] = {
    "-H": _sanitize_header,
    "--header": _sanitize_header,
    "-a": _sanitize_secret,
    "--auth": _sanitize_secret,
    "--proxy": _sanitize_url,
    "-u": _sanitize_url,
    "--url": _sanitize_url,
}


def sanitize_args(args: Sequence[str], *, config: SanitizationConfig) -> list[str]:
    """Obscure credentials passed on the command line."""
    result: list[str] = []
    pending: Callable[[str, SanitizationConfig], str] | None = None
    for arg in args:
        if pending is not None:
            result.append(pending(arg, config))
            pending = None
            continue
        if arg.startswith("-") and not arg.startswith("--") and len(arg) > 2:
            # Short option glued to its value, e.g. `-aSECRET` or `-HAuthorization: token`
            sanitizer = _SANITIZERS.get(arg[:2])
            if sanitizer is not None:
                result.append(f"{arg[:2]}{sanitizer(arg[2:], config)}")
                continue
        option, separator, value = arg.partition("=")
        sanitizer = _SANITIZERS.get(option)
        if sanitizer is None:
            # The schema location is positional, and any option may take a URL we don't know about
            if arg.startswith(("http://", "https://")):
                result.append(_sanitize_url(arg, config))
            elif option.startswith("--") and separator and value.startswith(("http://", "https://")):
                result.append(f"{option}{separator}{_sanitize_url(value, config)}")
            else:
                result.append(arg)
        elif separator:
            result.append(f"{option}{separator}{sanitizer(value, config)}")
        else:
            result.append(arg)
            pending = sanitizer
    return result


UNKNOWN_ENTRYPOINT = "<unknown entrypoint>"
_MODULE_ENTRYPOINTS = ("schemathesis", "pytest")


def get_command_representation(sanitization: SanitizationConfig | None = None) -> str:
    """Get how the current process was invoked."""
    basename = os.path.basename(sys.argv[0])
    executable = basename.removesuffix(".exe")
    raw = sys.argv[1:]
    args = " ".join(
        sanitize_args(raw, config=sanitization) if sanitization is not None and sanitization.enabled else raw
    )
    if executable in ("schemathesis", "st"):
        return f"st {args}"
    if "pytest" in basename or executable == "py.test":
        return f"pytest {args}"
    if basename == "__main__.py":
        # `python -m <package>` puts the path to the package's `__main__.py` into `argv[0]`
        package = os.path.basename(os.path.dirname(sys.argv[0]))
        if package in _MODULE_ENTRYPOINTS:
            return f"python -m {package} {args}"
    return UNKNOWN_ENTRYPOINT
