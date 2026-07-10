from __future__ import annotations

import os
import sys
from typing import TYPE_CHECKING

from schemathesis.core.output.sanitization import is_sensitive_key, sanitize_url

if TYPE_CHECKING:
    from schemathesis.config import SanitizationConfig


def get_command_representation(*, sanitization: SanitizationConfig | None = None) -> str:
    """Get how the current process was invoked."""
    basename = os.path.basename(sys.argv[0])
    arguments = sys.argv[1:]
    if sanitization is not None and sanitization.enabled:
        arguments = _sanitize_arguments(arguments, sanitization)
    args = " ".join(arguments)
    if basename in ("schemathesis", "st") or sys.argv[0].endswith(("schemathesis", "st")):
        return f"st {args}"
    if "pytest" in basename:
        return f"pytest {args}"
    return "<unknown entrypoint>"


def _split_option(argument: str) -> tuple[str, str, bool]:
    """Split an argv token into (option, attached value, whether the value is attached to the option)."""
    if argument.startswith("--"):
        option, separator, value = argument.partition("=")
        return option, value, bool(separator)
    if argument.startswith("-") and len(argument) > 2:
        # Short option glued to its value, e.g. `-aSECRET` or `-HAuthorization: token`.
        return argument[:2], argument[2:], True
    return argument, "", False


def _join_option(option: str, value: str) -> str:
    return f"{option}={value}" if option.startswith("--") else f"{option}{value}"


def _sanitize_arguments(arguments: list[str], config: SanitizationConfig) -> list[str]:
    sanitized = []
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        option, value, attached = _split_option(argument)
        if option in ("--auth", "-a"):
            if attached:
                sanitized.append(_join_option(option, config.replacement))
            else:
                sanitized.append(option)
                if index + 1 < len(arguments):
                    index += 1
                    sanitized.append(config.replacement)
        elif option in ("--header", "-H"):
            if attached:
                sanitized.append(_join_option(option, _sanitize_header(value, config)))
            else:
                sanitized.append(option)
                if index + 1 < len(arguments):
                    index += 1
                    sanitized.append(_sanitize_header(arguments[index], config))
        else:
            sanitized.append(_sanitize_url_argument(argument, config))
        index += 1
    return sanitized


def _sanitize_header(header: str, config: SanitizationConfig) -> str:
    name, separator, value = header.partition(":")
    if separator and is_sensitive_key(
        name.strip(), keys_to_sanitize=config.keys_to_sanitize, sensitive_markers=config.sensitive_markers
    ):
        space = " " if value.startswith(" ") else ""
        return f"{name}:{space}{config.replacement}"
    return header


def _sanitize_url_argument(argument: str, config: SanitizationConfig) -> str:
    option, separator, value = argument.partition("=")
    if separator and "://" in value:
        return f"{option}={sanitize_url(value, config=config)}"
    if "://" in argument:
        return sanitize_url(argument, config=config)
    return argument
