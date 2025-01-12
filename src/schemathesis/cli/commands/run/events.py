import time
import uuid

from schemathesis.core import Specification
from schemathesis.engine import events
from schemathesis.schemas import ApiOperationsCount


class LoadingStarted(events.EngineEvent):
    __slots__ = ("id", "timestamp", "location")

    def __init__(self, *, location: str) -> None:
        self.id = uuid.uuid4()
        self.timestamp = time.time()
        self.location = location


class LoadingFinished(events.EngineEvent):
    __slots__ = ("id", "timestamp", "location", "duration", "base_url", "specification", "operations_count")

    def __init__(
        self,
        location: str,
        start_time: float,
        base_url: str,
        specification: Specification,
        operations_count: ApiOperationsCount,
    ) -> None:
        self.id = uuid.uuid4()
        self.timestamp = time.time()
        self.location = location
        self.duration = self.timestamp - start_time
        self.base_url = base_url
        self.specification = specification
        self.operations_count = operations_count
