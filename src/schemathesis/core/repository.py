from dataclasses import dataclass


@dataclass
class LocationHeaderEntry:
    """Value of `Location` coming from API response with a given status code."""

    status_code: int
    value: str

    __slots__ = ("status_code", "value")
