from dataclasses import dataclass

from schemathesis.engine.recorder import ScenarioRecorder
from schemathesis.schemas import APIOperation


@dataclass(slots=True)
class LocationHeaderEntry:
    """Value of `Location` coming from API response with a given status code."""

    status_code: int
    value: str


@dataclass(slots=True)
class Observations:
    """Repository for observations collected during test execution."""

    location_headers: dict[APIOperation, list[LocationHeaderEntry]]

    def __init__(self) -> None:
        self.location_headers = {}

    def extract_observations_from(self, recorder: ScenarioRecorder) -> None:
        """Extract observations from completed test scenario."""
        for id, interaction in recorder.interactions.items():
            response = interaction.response
            if response is not None:
                location = response.headers.get("location")
                if location:
                    # Group location headers by the operation that produced them
                    entries = self.location_headers.setdefault(recorder.cases[id].value.operation, [])
                    entries.append(
                        LocationHeaderEntry(
                            status_code=response.status_code,
                            value=location[0],
                        )
                    )
