"""Shell detection and escaping for generating reproducible curl commands."""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum


class ShellType(str, Enum):
    """Supported shell types."""

    BASH = "bash"
    ZSH = "zsh"
    FISH = "fish"
    UNKNOWN = "unknown"

    @property
    def supports_ansi_c_quoting(self) -> bool:
        r"""Whether shell supports $'...\xHH' syntax."""
        return self in (ShellType.BASH, ShellType.ZSH)

    @property
    def supports_hex_in_quotes(self) -> bool:
        r"""Whether shell interprets \xHH in single quotes."""
        return self == ShellType.FISH


@dataclass(frozen=True)
class EscapeResult:
    """Result of escaping a value for shell."""

    escaped_value: str
    """The escaped string ready for shell."""

    needs_warning: bool
    """Whether a warning should be shown to the user."""

    original_bytes: bytes | None
    """Original bytes if warning is needed, for detailed display."""

    shell_used: ShellType
    """Which shell type the escaping is for."""

    __slots__ = ("escaped_value", "needs_warning", "original_bytes", "shell_used")


_DETECTED_SHELL: ShellType | None = None


def detect_shell() -> ShellType:
    """Detect the current shell type from $SHELL environment variable."""
    global _DETECTED_SHELL

    if _DETECTED_SHELL is not None:
        return _DETECTED_SHELL

    # Check $SHELL environment variable
    shell_path = os.environ.get("SHELL", "")
    if shell_path:
        shell_name = os.path.basename(shell_path).lower()
        detected = _parse_shell_name(shell_name)
        _DETECTED_SHELL = detected
        return detected

    _DETECTED_SHELL = ShellType.UNKNOWN
    return ShellType.UNKNOWN


def _parse_shell_name(name: str) -> ShellType:
    """Parse shell name string to ShellType."""
    name_lower = name.lower()

    # Check exact matches first
    for shell_type in (ShellType.BASH, ShellType.ZSH, ShellType.FISH):
        if shell_type.value == name_lower:
            return shell_type

    # Check substring matches
    for shell_type in (ShellType.BASH, ShellType.ZSH, ShellType.FISH):
        if shell_type.value in name_lower:
            return shell_type

    return ShellType.UNKNOWN


def has_non_printable(value: str | bytes) -> bool:
    """Check if value contains ASCII control characters."""
    if isinstance(value, bytes):
        try:
            value = value.decode("utf-8")
        except UnicodeDecodeError:
            # Binary data that can't be decoded - treat as non-printable
            return True

    # Check for ASCII control characters: 0-31 and 127 (DEL)
    return any(ord(c) < 32 or ord(c) == 127 for c in value)


def escape_for_shell(value: str, shell: ShellType | None = None) -> EscapeResult:
    """Escape value for shell use in curl commands."""
    if shell is None:
        shell = detect_shell()

    # Fast path: no non-printable characters
    if not has_non_printable(value):
        return EscapeResult(
            escaped_value=value,
            needs_warning=False,
            original_bytes=None,
            shell_used=shell,
        )

    original_bytes = value.encode("utf-8")

    # Bash/Zsh: Use ANSI-C quoting $'...\xHH'
    if shell.supports_ansi_c_quoting:
        escaped = _escape_with_ansi_c(value)
        return EscapeResult(
            escaped_value=f"$'{escaped}'",
            needs_warning=False,
            original_bytes=None,
            shell_used=shell,
        )

    # Fish: Use \xHH in single quotes
    if shell.supports_hex_in_quotes:
        escaped = _escape_with_hex(value)
        return EscapeResult(
            escaped_value=f"'{escaped}'",
            needs_warning=False,
            original_bytes=None,
            shell_used=shell,
        )

    # Unknown shell: Show bash-style with warning
    escaped = _escape_with_ansi_c(value)
    return EscapeResult(
        escaped_value=f"$'{escaped}'",
        needs_warning=True,
        original_bytes=original_bytes,
        shell_used=ShellType.BASH,
    )


def _escape_with_ansi_c(value: str) -> str:
    """Escape string for ANSI-C quoting ($'...') used in bash/zsh."""
    result = []
    for char in value:
        code = ord(char)

        # Readable escapes for common control characters
        if char == "\t":
            result.append("\\t")
        elif char == "\n":
            result.append("\\n")
        elif char == "\r":
            result.append("\\r")
        elif code < 32:
            # Other control characters as hex
            result.append(f"\\x{code:02x}")
        elif code == 127:
            # DEL character
            result.append("\\x7f")
        elif char in ("'", "\\", "$", "`"):
            # Shell special characters that need escaping in $'...'
            result.append(f"\\{char}")
        else:
            result.append(char)

    return "".join(result)


def _escape_with_hex(value: str) -> str:
    r"""Escape string with \xHH notation for fish shell.

    Fish interprets \x escapes directly in single quotes.
    We still need to escape single quotes and backslashes.
    """
    result = []
    for char in value:
        code = ord(char)

        # Readable escapes for common control characters
        if char == "\t":
            result.append("\\t")
        elif char == "\n":
            result.append("\\n")
        elif char == "\r":
            result.append("\\r")
        elif code < 32 or code == 127:
            # Control characters as hex
            result.append(f"\\x{code:02x}")
        elif char == "'":
            # Escape single quote for fish
            result.append("\\'")
        elif char == "\\":
            # Escape backslash
            result.append("\\\\")
        else:
            result.append(char)

    return "".join(result)
