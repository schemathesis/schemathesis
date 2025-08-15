from dataclasses import dataclass

from schemathesis.core.repository import LocationHeaderEntry
from schemathesis.engine.phases import Phase, PhaseName, PhaseSkipReason
from schemathesis.engine.recorder import ScenarioRecorder
from schemathesis.schemas import APIOperation


@dataclass
class DataRepository:
    """Repository for data extracted from API responses to improve test generation."""

    location_headers: dict[APIOperation, list[LocationHeaderEntry]]
    is_enabled: bool

    __slots__ = ("location_headers", "is_enabled")

    def __init__(self, phases: list[Phase]):
        self.location_headers = {}
        self.is_enabled = True
        for phase in phases:
            if Phase.name == PhaseName.STATEFUL_TESTING and phase.skip_reason == PhaseSkipReason.DISABLED:
                self.is_enabled = False

    def process_recorder(self, recorder: ScenarioRecorder) -> None:
        for id, interaction in recorder.interactions.items():
            response = interaction.response
            if response is not None:
                location = response.headers.get("location")
                if location:
                    entries = self.location_headers.setdefault(recorder.cases[id].value.operation, [])
                    entries.append(
                        LocationHeaderEntry(
                            status_code=response.status_code,
                            value=location[0],
                        )
                    )
