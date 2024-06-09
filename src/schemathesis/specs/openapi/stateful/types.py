from __future__ import annotations

from typing import Callable, Dict, TypedDict, Union

from ....stateful.state_machine import StepResult

StatusCode = str
LinkName = str
TargetName = str
SourceName = str
ResponseCounter = Dict[Union[int, None], int]
FilterFunction = Callable[["StepResult"], bool]
AggregatedResponseCounter = TypedDict("AggregatedResponseCounter", {"2xx": int, "4xx": int, "5xx": int, "Total": int})
