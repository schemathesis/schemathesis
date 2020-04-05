from enum import IntEnum
from typing import Any, Callable, Dict, Generator, Iterable, List, Optional, Tuple, Union

import hypothesis.errors

from .. import loaders
from ..checks import DEFAULT_CHECKS
from ..models import CheckFunction
from ..types import Filter, NotSet
from ..utils import dict_not_none_values
from . import events, executors


class RunnerExecutionMode(IntEnum):
    """Execution mode for a test run.

    It might happen in the same process, or may be offloaded to a separate one.
    """

    inprocess = 1
    subprocess = 2


def prepare(  # pylint: disable=too-many-arguments
    schema_uri: str,
    *,
    # Runtime behavior
    execution_mode: RunnerExecutionMode = RunnerExecutionMode.inprocess,
    checks: Iterable[CheckFunction] = DEFAULT_CHECKS,
    workers_num: int = 1,
    seed: Optional[int] = None,
    exit_first: bool = False,
    # Schema loading
    loader: Callable = loaders.from_uri,
    base_url: Optional[str] = None,
    auth: Optional[Tuple[str, str]] = None,
    auth_type: Optional[str] = None,
    headers: Optional[Dict[str, str]] = None,
    request_timeout: Optional[int] = None,
    endpoint: Optional[Filter] = None,
    method: Optional[Filter] = None,
    tag: Optional[Filter] = None,
    app: Optional[str] = None,
    validate_schema: bool = True,
    # Hypothesis-specific configuration
    hypothesis_deadline: Optional[Union[int, NotSet]] = None,
    hypothesis_derandomize: Optional[bool] = None,
    hypothesis_max_examples: Optional[int] = None,
    hypothesis_phases: Optional[List[hypothesis.Phase]] = None,
    hypothesis_report_multiple_bugs: Optional[bool] = None,
    hypothesis_suppress_health_check: Optional[List[hypothesis.HealthCheck]] = None,
    hypothesis_verbosity: Optional[hypothesis.Verbosity] = None,
) -> Generator[events.ExecutionEvent, None, None]:
    """Prepare a generator that will run test cases against the given API definition."""
    # pylint: disable=too-many-locals

    if auth is None:
        # Auth type doesn't matter if auth is not passed
        auth_type = None  # type: ignore
    hypothesis_options = prepare_hypothesis_options(
        deadline=hypothesis_deadline,
        derandomize=hypothesis_derandomize,
        max_examples=hypothesis_max_examples,
        phases=hypothesis_phases,
        report_multiple_bugs=hypothesis_report_multiple_bugs,
        suppress_health_check=hypothesis_suppress_health_check,
        verbosity=hypothesis_verbosity,
    )
    config = executors.ExecutorConfig(
        schema_uri=schema_uri,
        loader=loader,
        base_url=base_url,
        endpoint=endpoint,
        method=method,
        tag=tag,
        app=app,
        validate_schema=validate_schema,
        checks=checks,
        hypothesis_options=hypothesis_options,
        seed=seed,
        workers_num=workers_num,
        exit_first=exit_first,
        auth=auth,
        auth_type=auth_type,
        headers=headers,
        request_timeout=request_timeout,
    )
    if execution_mode == RunnerExecutionMode.subprocess:
        executor = executors.execute_in_subprocess
    else:
        executor = executors.execute_from_schema
    return executor(config)


def prepare_hypothesis_options(  # pylint: disable=too-many-arguments
    deadline: Optional[Union[int, NotSet]] = None,
    derandomize: Optional[bool] = None,
    max_examples: Optional[int] = None,
    phases: Optional[List[hypothesis.Phase]] = None,
    report_multiple_bugs: Optional[bool] = None,
    suppress_health_check: Optional[List[hypothesis.HealthCheck]] = None,
    verbosity: Optional[hypothesis.Verbosity] = None,
) -> Dict[str, Any]:
    options = dict_not_none_values(
        derandomize=derandomize,
        max_examples=max_examples,
        phases=phases,
        report_multiple_bugs=report_multiple_bugs,
        suppress_health_check=suppress_health_check,
        verbosity=verbosity,
    )
    # `deadline` is special, since Hypothesis allows to pass `None`
    if deadline is not None:
        if isinstance(deadline, NotSet):
            options["deadline"] = None
        else:
            options["deadline"] = deadline
    return options
