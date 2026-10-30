from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass

from schemathesis.config import ProjectConfig
from schemathesis.core import Specification
from schemathesis.core.statistic import ApiStatistic
from schemathesis.engine import events
from schemathesis.schemas import APIOperation, BaseSchema


@dataclass(init=False)
class LoadingStarted(events.EngineEvent):
    location: str

    __slots__ = ("id", "timestamp", "location")

    def __init__(self, *, location: str) -> None:
        self.id = uuid.uuid4()
        self.timestamp = time.time()
        self.location = location


@dataclass(init=False)
class LoadingFinished(events.EngineEvent):
    # Annotations match `__slots__` so the NDJSON dataclass-fields walk picks them up.
    location: str
    duration: float
    base_url: str
    base_path: str
    specification: Specification
    statistic: ApiStatistic
    schema: dict
    title: str | None
    config: ProjectConfig
    find_operation_by_label: Callable[[str], APIOperation | None]
    status_name_for_code: Callable[[int], str | None]

    __slots__ = (
        "id",
        "timestamp",
        "location",
        "duration",
        "base_url",
        "base_path",
        "specification",
        "statistic",
        "schema",
        "title",
        "config",
        "find_operation_by_label",
        "status_name_for_code",
    )

    def __init__(
        self,
        *,
        location: str,
        start_time: float,
        base_url: str,
        base_path: str,
        specification: Specification,
        statistic: ApiStatistic,
        schema: dict,
        title: str | None,
        config: ProjectConfig,
        find_operation_by_label: Callable[[str], APIOperation | None],
        status_name_for_code: Callable[[int], str | None],
    ) -> None:
        self.id = uuid.uuid4()
        self.timestamp = time.time()
        self.location = location
        self.duration = self.timestamp - start_time
        self.base_url = base_url
        self.specification = specification
        self.statistic = statistic
        self.schema = schema
        self.title = title
        self.base_path = base_path
        self.config = config
        self.find_operation_by_label = find_operation_by_label
        self.status_name_for_code = status_name_for_code

    @classmethod
    def from_schema(cls, schema: BaseSchema, *, location: str, start_time: float, base_url: str) -> LoadingFinished:
        return cls(
            location=location,
            start_time=start_time,
            base_url=base_url,
            base_path=schema.base_path,
            specification=schema.specification,
            statistic=schema.statistic,
            schema=schema.raw_schema,
            title=schema.title,
            config=schema.config,
            find_operation_by_label=schema.find_operation_by_label,
            status_name_for_code=schema.status_name_for_code,
        )
