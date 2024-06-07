from __future__ import annotations

from typing import Callable, Dict, Union

from ....stateful.state_machine import StepResult

StatusCode = str
LinkName = str
TargetName = str
SourceName = str
ResponseCounter = Dict[Union[int, None], int]
FilterFunction = Callable[["StepResult"], bool]
