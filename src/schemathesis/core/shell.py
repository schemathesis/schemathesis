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


@dataclass(frozen=True, slots=True)
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
    for shell_type in (ShellType.BASH, ShellType.ZSH, ShellType.FISH):
        if shell_type.value in name_lower:
            return shell_type
    return ShellType.UNKNOWN


# Above this, the curl is unrunnable anyway; bounding the per-char scans
# keeps reproduce-build O(1) on multi-MB inputs.
MAX_SHELL_SCAN_BYTES = 64 * 1024


def _utf8_length(value: str) -> int:
    if value.isascii():
        return len(value)
    return len(value.encode("utf-8", "surrogatepass"))


def _truncate_utf8(value: str) -> str:
    if value.isascii():
        return value[:MAX_SHELL_SCAN_BYTES]
    truncated = value.encode("utf-8", "surrogatepass")[:MAX_SHELL_SCAN_BYTES]
    while True:
        try:
            return truncated.decode("utf-8", "surrogatepass")
        except UnicodeDecodeError:
            truncated = truncated[:-1]


def has_non_printable(value: str | bytes) -> bool:
    """Check if value contains control, line separator or other non-printable characters."""
    if isinstance(value, bytes):
        try:
            value = value.decode("utf-8")
        except UnicodeDecodeError:
            # Binary data that can't be decoded - treat as non-printable
            return True

    # Above the cap, skip the scan and force the escape path; truncation marker comes from `escape_for_shell`.
    if _utf8_length(value) > MAX_SHELL_SCAN_BYTES:
        return True

    # Unicode line separators and C1 controls break the printed command into several lines
    return not value.isprintable()


def _truncated_marker(total_bytes: int) -> str:
    return f" <...truncated, {total_bytes} bytes total>"


def escape_for_shell(value: str, shell: ShellType | None = None) -> EscapeResult:
    """Escape value for shell use in curl commands."""
    if shell is None:
        shell = detect_shell()

    # Truncate before the per-char escape: `_escape_with_ansi_c` builds a
    # ~10x list-of-chars; an MB input would explode without this guard.
    truncated = False
    full_size = _utf8_length(value)
    if full_size > MAX_SHELL_SCAN_BYTES:
        value = _truncate_utf8(value)
        truncated = True

    # Fast path: no non-printable characters
    if not has_non_printable(value):
        if truncated:
            return EscapeResult(
                escaped_value=value + _truncated_marker(full_size),
                needs_warning=True,
                original_bytes=None,
                shell_used=shell,
            )
        return EscapeResult(
            escaped_value=value,
            needs_warning=False,
            original_bytes=None,
            shell_used=shell,
        )

    # Lone surrogates cannot be sent, but must not crash failure reporting
    original_bytes = value.encode("utf-8", "surrogatepass")
    suffix = _truncated_marker(full_size) if truncated else ""

    # Bash/Zsh: Use ANSI-C quoting $'...\xHH'
    if shell.supports_ansi_c_quoting:
        escaped = _escape_with_ansi_c(value)
        return EscapeResult(
            escaped_value=f"$'{escaped}'{suffix}",
            needs_warning=truncated,
            original_bytes=None,
            shell_used=shell,
        )

    # Fish: Use \xHH in single quotes
    if shell.supports_hex_in_quotes:
        return EscapeResult(
            escaped_value=f"{_escape_with_hex(value)}{suffix}",
            needs_warning=truncated,
            original_bytes=None,
            shell_used=shell,
        )

    # Unknown shell: Show bash-style with warning
    escaped = _escape_with_ansi_c(value)
    return EscapeResult(
        escaped_value=f"$'{escaped}'{suffix}",
        needs_warning=True,
        original_bytes=original_bytes,
        shell_used=ShellType.BASH,
    )


def _escape_with_ansi_c(value: str) -> str:
    """Escape string for ANSI-C quoting ($'...') used in bash/zsh."""
    result = []
    for char in value:
        # Readable escapes for common control characters
        if char == "\t":
            result.append("\\t")
        elif char == "\n":
            result.append("\\n")
        elif char == "\r":
            result.append("\\r")
        elif not char.isprintable():
            result.append(_hex_bytes(char))
        elif char in ("'", "\\"):
            # The only characters that end or escape inside $'...'; `$` and backticks are literal there
            result.append(f"\\{char}")
        else:
            result.append(char)

    return "".join(result)


def _hex_bytes(char: str) -> str:
    # UTF-8 bytes keep the escape independent of the shell's locale
    return "".join(f"\\x{byte:02x}" for byte in char.encode("utf-8", "surrogatepass"))


def _escape_with_hex(value: str) -> str:
    r"""Quote value for fish, with escapes outside the quotes.

    Fish reads `\xHH`, `\n` and friends only in unquoted text, so each run of printable
    characters is single-quoted and everything else is escaped between the quoted runs.
    """
    parts = []
    quoted: list[str] = []
    for char in value:
        if char.isprintable():
            # Inside fish single quotes only `'` and `\` need escaping
            quoted.append(f"\\{char}" if char in ("'", "\\") else char)
            continue
        if quoted:
            parts.append(f"'{''.join(quoted)}'")
            quoted = []
        parts.append(_READABLE_ESCAPES.get(char) or _hex_bytes(char))
    if quoted or not parts:
        parts.append(f"'{''.join(quoted)}'")
    return "".join(parts)


_READABLE_ESCAPES = {"\t": "\\t", "\n": "\\n", "\r": "\\r"}
