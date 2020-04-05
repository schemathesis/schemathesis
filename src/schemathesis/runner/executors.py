import multiprocessing
from queue import Empty
from typing import Any, Callable, Dict, Generator, Iterable, Optional, Tuple
from urllib.parse import urlparse

import attr

from .. import loaders
from ..models import CheckFunction
from ..schemas import BaseSchema
from ..types import Filter, RawAuth
from ..utils import dict_true_values, file_exists, get_base_url, get_requests_auth, import_app
from . import events
from .impl import BaseRunner, SingleThreadRunner, SingleThreadWSGIRunner, ThreadPoolRunner, ThreadPoolWSGIRunner

SUBPROCESS_WAIT_TIMEOUT = 30


# pylint: disable=too-many-instance-attributes
@attr.s(slots=True)
class ExecutorConfig:
    schema_uri: str = attr.ib()
    checks: Iterable[CheckFunction] = attr.ib()
    hypothesis_options: Dict[str, Any] = attr.ib()
    loader: Callable = attr.ib(default=loaders.from_uri)
    base_url: Optional[str] = attr.ib(default=None)
    endpoint: Optional[Filter] = attr.ib(default=None)
    method: Optional[Filter] = attr.ib(default=None)
    tag: Optional[Filter] = attr.ib(default=None)
    app: Optional[str] = attr.ib(default=None)
    validate_schema: bool = attr.ib(default=True)
    workers_num: int = attr.ib(default=1)
    auth: Optional[RawAuth] = attr.ib(default=None)
    auth_type: Optional[str] = attr.ib(default=None)
    headers: Optional[Dict[str, Any]] = attr.ib(default=None)
    request_timeout: Optional[int] = attr.ib(default=None)
    seed: Optional[int] = attr.ib(default=None)
    exit_first: bool = attr.ib(default=False)


def execute_in_subprocess(config: ExecutorConfig) -> Generator[events.ExecutionEvent, None, None]:
    """Execute tests in a subprocess.

    Communication with subprocess is implemented via `multiprocessing.Queue`. This function works as a wrapper around it
    to provide the same behavior as the in-process counterpart does.
    """
    event = None
    queue: multiprocessing.Queue = multiprocessing.Queue()
    process = multiprocessing.Process(target=_execute_in_subprocess, args=(queue, config),)
    process.start()
    try:
        while not isinstance(event, events.Finished):
            try:
                event = queue.get(timeout=SUBPROCESS_WAIT_TIMEOUT)
                yield event
            except Empty as exc:
                # Something went wrong, e.g. the subprocess was killed without emitting `Interrupted` event.
                yield events.InternalError.from_exc(exc)
    finally:
        process.join()


def _execute_in_subprocess(queue: multiprocessing.Queue, config: ExecutorConfig) -> None:
    """A simple proxy that puts events into a multiprocessing queue."""
    for event in execute_from_schema(config):
        queue.put(event)


def execute_from_schema(config: ExecutorConfig,) -> Generator[events.ExecutionEvent, None, None]:
    """Execute tests for the given schema.

    Provides the main testing loop and preparation step.
    """
    # pylint: disable=too-many-locals
    try:
        app = config.app
        if app is not None:
            app = import_app(app)
        schema = load_schema(
            config.schema_uri,
            base_url=config.base_url,
            loader=config.loader,
            app=app,
            validate_schema=config.validate_schema,
            auth=config.auth,
            auth_type=config.auth_type,
            headers=config.headers,
            endpoint=config.endpoint,
            method=config.method,
            tag=config.tag,
        )

        runner: BaseRunner
        if config.workers_num > 1:
            if schema.app:
                runner = ThreadPoolWSGIRunner(
                    schema=schema,
                    checks=config.checks,
                    hypothesis_settings=config.hypothesis_options,
                    auth=config.auth,
                    auth_type=config.auth_type,
                    headers=config.headers,
                    seed=config.seed,
                    workers_num=config.workers_num,
                    exit_first=config.exit_first,
                )
            else:
                runner = ThreadPoolRunner(
                    schema=schema,
                    checks=config.checks,
                    hypothesis_settings=config.hypothesis_options,
                    auth=config.auth,
                    auth_type=config.auth_type,
                    headers=config.headers,
                    seed=config.seed,
                    request_timeout=config.request_timeout,
                    exit_first=config.exit_first,
                )
        else:
            if schema.app:
                runner = SingleThreadWSGIRunner(
                    schema=schema,
                    checks=config.checks,
                    hypothesis_settings=config.hypothesis_options,
                    auth=config.auth,
                    auth_type=config.auth_type,
                    headers=config.headers,
                    seed=config.seed,
                    exit_first=config.exit_first,
                )
            else:
                runner = SingleThreadRunner(
                    schema=schema,
                    checks=config.checks,
                    hypothesis_settings=config.hypothesis_options,
                    auth=config.auth,
                    auth_type=config.auth_type,
                    headers=config.headers,
                    seed=config.seed,
                    request_timeout=config.request_timeout,
                    exit_first=config.exit_first,
                )
        yield from runner.execute()
    except Exception as exc:
        yield events.InternalError.from_exc(exc)


def load_schema(
    schema_uri: str,
    *,
    base_url: Optional[str] = None,
    loader: Callable = loaders.from_uri,
    app: Any = None,
    validate_schema: bool = True,
    # Network request parameters
    auth: Optional[Tuple[str, str]] = None,
    auth_type: Optional[str] = None,
    headers: Optional[Dict[str, str]] = None,
    # Schema filters
    endpoint: Optional[Filter] = None,
    method: Optional[Filter] = None,
    tag: Optional[Filter] = None,
) -> BaseSchema:
    """Load schema via specified loader and parameters."""
    loader_options = dict_true_values(base_url=base_url, endpoint=endpoint, method=method, tag=tag, app=app)

    if file_exists(schema_uri):
        loader = loaders.from_path
    elif app is not None and not urlparse(schema_uri).netloc:
        # If `schema` is not an existing filesystem path or an URL then it is considered as an endpoint with
        # the given app
        loader = loaders.get_loader_for_app(app)
    else:
        loader_options.update(dict_true_values(headers=headers, auth=auth, auth_type=auth_type))

    if "base_url" not in loader_options:
        loader_options["base_url"] = get_base_url(schema_uri)
    if loader is loaders.from_uri and loader_options.get("auth"):
        loader_options["auth"] = get_requests_auth(loader_options["auth"], loader_options.pop("auth_type", None))

    return loader(schema_uri, validate_schema=validate_schema, **loader_options)
