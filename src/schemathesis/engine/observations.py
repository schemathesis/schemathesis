import json
from collections.abc import Iterator
from dataclasses import dataclass
from urllib.parse import urlsplit

from schemathesis.core import media_types
from schemathesis.core.transforms import encode_pointer
from schemathesis.core.transport import Response, response_content_type
from schemathesis.engine.recorder import RecordedScenario
from schemathesis.schemas import APIOperation

# Link inference needs a handful of examples per operation; keeping every one makes each pass over
# them cost more the longer the run goes.
MAX_LOCATION_HEADER_SAMPLES = 20
MAX_BODY_URL_SAMPLES = 20
# Most APIs never put URLs in bodies; stop re-parsing an operation's responses after this many fruitless scans in a row.
MAX_FRUITLESS_BODY_SCANS = 20
# Parsing cost grows with body size, while URLs to learn from show up in small bodies too.
MAX_SCANNED_BODY_SIZE = 1024 * 1024
MAX_SCANNED_STRINGS = 500


@dataclass(slots=True)
class LocationHeaderEntry:
    """Value of `Location` coming from API response with a given status code."""

    status_code: int
    value: str

    @property
    def expression(self) -> str:
        return "$response.header.Location"


@dataclass(slots=True)
class BodyUrlEntry:
    """Same-origin URL found at a JSON pointer inside a response body."""

    status_code: int
    pointer: str
    value: str

    @property
    def expression(self) -> str:
        return f"$response.body#{self.pointer}"


@dataclass(slots=True)
class Observations:
    """Repository for observations collected during test execution."""

    location_headers: dict[APIOperation, list[LocationHeaderEntry]]
    body_urls: dict[APIOperation, list[BodyUrlEntry]]
    fruitless_body_scans: dict[APIOperation, int]

    def __init__(self) -> None:
        self.location_headers = {}
        self.body_urls = {}
        self.fruitless_body_scans = {}

    def extract_observations_from(self, recorder: RecordedScenario) -> None:
        """Extract observations from completed test scenario."""
        for id, interaction in recorder.interactions.items():
            response = interaction.response
            if response is not None:
                operation = recorder.cases[id].value.operation
                location = response.headers.get("location")
                if location:
                    # Group location headers by the operation that produced them
                    entries = self.location_headers.setdefault(operation, [])
                    if len(entries) < MAX_LOCATION_HEADER_SAMPLES:
                        entries.append(
                            LocationHeaderEntry(
                                status_code=response.status_code,
                                value=location[0],
                            )
                        )
                self._extract_body_urls(operation, response)

    def _extract_body_urls(self, operation: APIOperation, response: Response) -> None:
        # Error bodies often echo the generated request URL back, which would turn garbage into links
        if not 200 <= response.status_code < 400 or response.content_size > MAX_SCANNED_BODY_SIZE:
            return
        entries = self.body_urls.setdefault(operation, [])
        fruitless = self.fruitless_body_scans.get(operation, 0)
        if len(entries) >= MAX_BODY_URL_SAMPLES or fruitless >= MAX_FRUITLESS_BODY_SCANS:
            return
        content_type = response_content_type(response)
        if not content_type or not _is_json(content_type):
            return
        try:
            # Not `response.json()`: its cache was cleared after checks and would pin the parsed body in memory again
            document = json.loads(response.text)
        except (ValueError, LookupError):
            return
        origin = urlsplit(response.request.url or "")[:2]
        seen = {(entry.status_code, entry.pointer) for entry in entries}
        found = len(entries)
        for pointer, value in _iter_strings(document):
            path = _same_origin_path(value, origin)
            if path is None or (response.status_code, pointer) in seen:
                continue
            seen.add((response.status_code, pointer))
            entries.append(BodyUrlEntry(status_code=response.status_code, pointer=pointer, value=path))
            if len(entries) >= MAX_BODY_URL_SAMPLES:
                break
        self.fruitless_body_scans[operation] = 0 if len(entries) > found else fruitless + 1


def _is_json(content_type: str) -> bool:
    try:
        return media_types.is_json(content_type)
    except ValueError:
        return False


def _iter_strings(document: object) -> Iterator[tuple[str, str]]:
    stack: list[tuple[str, object]] = [("", document)]
    budget = MAX_SCANNED_STRINGS
    while stack and budget:
        pointer, value = stack.pop()
        if isinstance(value, str):
            budget -= 1
            yield pointer, value
        elif isinstance(value, dict):
            for key, item in value.items():
                # `}` closes an embedded runtime expression, so such keys can't be addressed in a link
                if "}" not in key:
                    stack.append((f"{pointer}/{encode_pointer(key)}", item))
        elif isinstance(value, list) and value:
            # Items of a collection share a shape; the first one is enough to learn the link
            stack.append((f"{pointer}/0", value[0]))


def _same_origin_path(value: str, origin: tuple[str, str]) -> str | None:
    if value.startswith("/") and not value.startswith("//"):
        return urlsplit(value).path
    if value.startswith(("http://", "https://")):
        parsed = urlsplit(value)
        if (parsed.scheme, parsed.netloc) == origin:
            return parsed.path
    return None
