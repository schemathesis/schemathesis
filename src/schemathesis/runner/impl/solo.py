# weird mypy bug with imports
from typing import Any, Dict, Generator  # pylint: disable=unused-import

import attr

from ...models import TestResultSet
from ...utils import get_requests_auth
from .. import events
from .core import BaseRunner, get_session, network_test, run_test, wsgi_test


@attr.s(slots=True)  # pragma: no mutate
class SingleThreadRunner(BaseRunner):
    """Fast runner that runs tests sequentially in the main thread."""

    def _execute(self, results: TestResultSet) -> Generator[events.ExecutionEvent, None, None]:
        auth = get_requests_auth(self.auth, self.auth_type)
        with get_session(auth, self.headers) as session:
            for endpoint, test in self.schema.get_all_tests(network_test, self.hypothesis_settings, self.seed):
                for event in run_test(
                    endpoint, test, self.checks, results, session=session, request_timeout=self.request_timeout,
                ):
                    yield event
                    if isinstance(event, events.Interrupted):
                        return


@attr.s(slots=True)  # pragma: no mutate
class SingleThreadWSGIRunner(SingleThreadRunner):
    def _execute(self, results: TestResultSet) -> Generator[events.ExecutionEvent, None, None]:
        for endpoint, test in self.schema.get_all_tests(wsgi_test, self.hypothesis_settings, self.seed):
            for event in run_test(
                endpoint, test, self.checks, results, auth=self.auth, auth_type=self.auth_type, headers=self.headers,
            ):
                yield event
                if isinstance(event, events.Interrupted):
                    return
