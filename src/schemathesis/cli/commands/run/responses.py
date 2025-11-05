from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator


@dataclass
class ResponseBucket:
    """Represents a single statistics bucket with display information.

    Attributes:
        icon: Visual indicator (emoji or text symbol)
        total: Number of responses in this bucket
        label: Human-readable label for the bucket
        color: Color name for terminal display

    """

    icon: str
    total: int
    label: str
    color: str

    __slots__ = ("icon", "total", "label", "color")


class ResponseStatistic:
    """Tracks HTTP response status code statistics during test execution.

    Groups responses into standard HTTP status code categories according to RFC 9110:
    - 1xx (100-199): Informational responses
    - 2xx (200-299): Successful responses
    - 3xx (300-399): Redirection messages
    - 4xx (400-499): Client error responses
    - 5xx (500-599): Server error responses
    - Other: Non-standard codes (< 100 or >= 600)

    Attributes:
        informational: Count of 1xx responses
        success: Count of 2xx responses
        redirect: Count of 3xx responses
        client_error: Count of 4xx responses
        server_error: Count of 5xx responses
        other: Count of non-standard status codes

    """

    __slots__ = ("informational", "success", "redirect", "client_error", "server_error", "other")

    def __init__(self) -> None:
        self.informational = 0
        self.success = 0
        self.redirect = 0
        self.client_error = 0
        self.server_error = 0
        self.other = 0

    def record(self, status_code: int) -> None:
        """Record a response status code into the appropriate bucket.

        Args:
            status_code: HTTP status code to categorize

        """
        if 100 <= status_code < 200:
            self.informational += 1
        elif 200 <= status_code < 300:
            self.success += 1
        elif 300 <= status_code < 400:
            self.redirect += 1
        elif 400 <= status_code < 500:
            self.client_error += 1
        elif 500 <= status_code < 600:
            self.server_error += 1
        else:
            self.other += 1

    @property
    def total(self) -> int:
        """Total number of recorded responses across all buckets."""
        return self.informational + self.success + self.redirect + self.client_error + self.server_error + self.other

    def iter_nonzero_buckets(self, *, use_emoji: bool = True) -> Iterator[ResponseBucket]:
        """Iterate over non-empty statistics buckets.

        Args:
            use_emoji: If True, use emoji icons; if False, use text symbols.
                      Set to False for terminals that don't support emoji.

        """
        if use_emoji:
            buckets = [
                ("ℹ️", self.informational, "informational", "blue"),
                ("✅", self.success, "success", "green"),
                ("↪️", self.redirect, "redirect", "cyan"),
                ("⛔", self.client_error, "client error", "yellow"),
                ("🚫", self.server_error, "server error", "red"),
                ("❔", self.other, "other", "magenta"),
            ]
        else:
            buckets = [
                ("[1XX]", self.informational, "informational", "blue"),
                ("[2XX]", self.success, "success", "green"),
                ("[3XX]", self.redirect, "redirect", "cyan"),
                ("[4XX]", self.client_error, "client error", "yellow"),
                ("[5XX]", self.server_error, "server error", "red"),
                ("[OTHER]", self.other, "other", "magenta"),
            ]

        for icon, total, label, color in buckets:
            if total:
                yield ResponseBucket(icon=icon, total=total, label=label, color=color)
