from typing import Any, Dict
from urllib.parse import urljoin

import attr
import requests
from requests.adapters import HTTPAdapter, Retry

from ..constants import USER_AGENT
from .constants import REQUEST_TIMEOUT
from .metadata import Metadata
from .models import ApiConfig, AuthResponse, TestRun


class ServiceClient(requests.Session):
    """A more convenient session to send requests to Schemathesis.io."""

    def __init__(self, base_url: str, token: str, *, timeout: int = REQUEST_TIMEOUT, verify: bool = True):
        super().__init__()
        self.timeout = timeout
        self.verify = verify
        self.base_url = base_url
        self.headers.update({"Authorization": f"Bearer {token}", "User-Agent": USER_AGENT})
        # Automatically check responses for 4XX and 5XX
        self.hooks["response"] = [lambda response, *args, **kwargs: response.raise_for_status()]  # type: ignore
        adapter = HTTPAdapter(max_retries=Retry(5))
        self.mount("https://", adapter)
        self.mount("http://", adapter)

    def request(self, method: str, url: str, *args: Any, **kwargs: Any) -> requests.Response:  # type: ignore
        kwargs.setdefault("timeout", self.timeout)
        kwargs.setdefault("verify", self.verify)
        # All requests will be done against the base url
        url = urljoin(self.base_url, url)
        return super().request(method, url, *args, **kwargs)

    def create_test_run(self, api_slug: str) -> TestRun:
        """Create a new test run on the Schemathesis.io side."""
        response = self.post("/runs/", json={"api_slug": api_slug})
        data = response.json()
        config = data["config"]
        return TestRun(
            run_id=data["run_id"],
            short_url=data["short_url"],
            config=ApiConfig(location=config["location"], base_url=config["base_url"]),
        )

    def finish_test_run(self, run_id: str) -> None:
        """Finish a test run on the Schemathesis.io side.

        Only needed in corner cases when Schemathesis CLI fails with an internal error in itself, not in the runner.
        """
        self.post(f"/runs/{run_id}/finish/")

    def send_event(self, run_id: str, data: Dict[str, Any]) -> None:
        """Send a single event to Schemathesis.io."""
        self.post(f"/runs/{run_id}/events/", json=data)

    def cli_login(self, metadata: Metadata) -> AuthResponse:
        """Send a login request."""
        response = self.post("/auth/cli/login/", json={"metadata": attr.asdict(metadata)})
        data = response.json()
        return AuthResponse(username=data["username"])
