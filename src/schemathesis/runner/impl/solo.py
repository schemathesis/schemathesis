from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Generator

from ...models import TestResultSet
from ...transports.auth import get_requests_auth
from .. import events
from .core import BaseRunner, asgi_test, get_session, network_test, wsgi_test


@dataclass
class SingleThreadRunner(BaseRunner):
    """Fast runner that runs tests sequentially in the main thread."""

    def _execute(
        self, results: TestResultSet, stop_event: threading.Event
    ) -> Generator[events.ExecutionEvent, None, None]:
        for event in self._execute_impl(results):
            yield event
            if stop_event.is_set() or self._should_stop(event):
                break

    def _execute_impl(self, results: TestResultSet) -> Generator[events.ExecutionEvent, None, None]:
        auth = get_requests_auth(self.auth, self.auth_type)
        with get_session(auth) as session:
            yield from self._run_tests(
                maker=self.schema.get_all_tests,
                template=network_test,
                settings=self.hypothesis_settings,
                generation_config=self.generation_config,
                seed=self.seed,
                checks=self.checks,
                max_response_time=self.max_response_time,
                targets=self.targets,
                results=results,
                session=session,
                headers=self.headers,
                request_config=self.request_config,
                store_interactions=self.store_interactions,
                dry_run=self.dry_run,
            )


@dataclass
class SingleThreadWSGIRunner(SingleThreadRunner):
    def _execute_impl(self, results: TestResultSet) -> Generator[events.ExecutionEvent, None, None]:
        yield from self._run_tests(
            maker=self.schema.get_all_tests,
            template=wsgi_test,
            settings=self.hypothesis_settings,
            generation_config=self.generation_config,
            seed=self.seed,
            checks=self.checks,
            max_response_time=self.max_response_time,
            targets=self.targets,
            results=results,
            auth=self.auth,
            auth_type=self.auth_type,
            headers=self.headers,
            store_interactions=self.store_interactions,
            dry_run=self.dry_run,
        )


@dataclass
class SingleThreadASGIRunner(SingleThreadRunner):
    def _execute_impl(self, results: TestResultSet) -> Generator[events.ExecutionEvent, None, None]:
        yield from self._run_tests(
            maker=self.schema.get_all_tests,
            template=asgi_test,
            settings=self.hypothesis_settings,
            generation_config=self.generation_config,
            seed=self.seed,
            checks=self.checks,
            max_response_time=self.max_response_time,
            targets=self.targets,
            results=results,
            headers=self.headers,
            store_interactions=self.store_interactions,
            dry_run=self.dry_run,
        )
