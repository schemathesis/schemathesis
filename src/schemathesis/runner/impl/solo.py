from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Generator

from ...transports.auth import get_requests_auth
from .. import events
from .core import BaseRunner, asgi_test, get_session, network_test, wsgi_test

if TYPE_CHECKING:
    from .. import events
    from .context import RunnerContext


@dataclass
class SingleThreadRunner(BaseRunner):
    """Fast runner that runs tests sequentially in the main thread."""

    def _execute(self, ctx: RunnerContext) -> Generator[events.ExecutionEvent, None, None]:
        for event in self._execute_impl(ctx):
            yield event
            if ctx.is_stopped or self._should_stop(event):
                break

    def _execute_impl(self, ctx: RunnerContext) -> Generator[events.ExecutionEvent, None, None]:
        auth = get_requests_auth(self.auth, self.auth_type)
        with get_session(auth) as session:
            yield from self._run_tests(
                maker=self.schema.get_all_tests,
                test_func=network_test,
                settings=self.hypothesis_settings,
                generation_config=self.generation_config,
                checks=self.checks,
                max_response_time=self.max_response_time,
                targets=self.targets,
                ctx=ctx,
                session=session,
                headers=self.headers,
                request_config=self.request_config,
                store_interactions=self.store_interactions,
                dry_run=self.dry_run,
            )


@dataclass
class SingleThreadWSGIRunner(SingleThreadRunner):
    def _execute_impl(self, ctx: RunnerContext) -> Generator[events.ExecutionEvent, None, None]:
        yield from self._run_tests(
            maker=self.schema.get_all_tests,
            test_func=wsgi_test,
            settings=self.hypothesis_settings,
            generation_config=self.generation_config,
            checks=self.checks,
            max_response_time=self.max_response_time,
            targets=self.targets,
            ctx=ctx,
            auth=self.auth,
            auth_type=self.auth_type,
            headers=self.headers,
            store_interactions=self.store_interactions,
            dry_run=self.dry_run,
        )


@dataclass
class SingleThreadASGIRunner(SingleThreadRunner):
    def _execute_impl(self, ctx: RunnerContext) -> Generator[events.ExecutionEvent, None, None]:
        yield from self._run_tests(
            maker=self.schema.get_all_tests,
            test_func=asgi_test,
            settings=self.hypothesis_settings,
            generation_config=self.generation_config,
            checks=self.checks,
            max_response_time=self.max_response_time,
            targets=self.targets,
            ctx=ctx,
            headers=self.headers,
            store_interactions=self.store_interactions,
            dry_run=self.dry_run,
        )
