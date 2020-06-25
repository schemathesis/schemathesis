# weird mypy bug with imports
from typing import Any, Dict, Generator  # pylint: disable=unused-import

import attr

from ...models import TestResultSet
from ...utils import get_requests_auth
from .. import events
from .core import BaseRunner, asgi_test, get_session, network_test, wsgi_test


@attr.s(slots=True)  # pragma: no mutate
class SingleThreadRunner(BaseRunner):
    """Fast runner that runs tests sequentially in the main thread."""

    def _execute(self, results: TestResultSet) -> Generator[events.ExecutionEvent, None, None]:
        auth = get_requests_auth(self.auth, self.auth_type)
        with get_session(auth) as session:
            yield from self._run_tests(
                self.schema.get_all_tests,
                network_test,
                self.hypothesis_settings,
                self.seed,
                checks=self.checks,
                targets=self.targets,
                results=results,
                session=session,
                headers=self.headers,
                request_timeout=self.request_timeout,
                store_interactions=self.store_interactions,
            )


@attr.s(slots=True)  # pragma: no mutate
class SingleThreadWSGIRunner(SingleThreadRunner):
    def _execute(self, results: TestResultSet) -> Generator[events.ExecutionEvent, None, None]:
        yield from self._run_tests(
            self.schema.get_all_tests,
            wsgi_test,
            self.hypothesis_settings,
            self.seed,
            checks=self.checks,
            targets=self.targets,
            results=results,
            auth=self.auth,
            auth_type=self.auth_type,
            headers=self.headers,
            store_interactions=self.store_interactions,
        )


@attr.s(slots=True)  # pragma: no mutate
class SingleThreadASGIRunner(SingleThreadRunner):
    def _execute(self, results: TestResultSet) -> Generator[events.ExecutionEvent, None, None]:
        yield from self._run_tests(
            self.schema.get_all_tests,
            asgi_test,
            self.hypothesis_settings,
            self.seed,
            checks=self.checks,
            targets=self.targets,
            results=results,
            headers=self.headers,
            store_interactions=self.store_interactions,
        )
