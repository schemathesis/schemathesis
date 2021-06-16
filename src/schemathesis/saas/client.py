from typing import Any, Dict
from urllib.parse import urljoin

import requests
from requests.adapters import HTTPAdapter, Retry

from .constants import REQUEST_TIMEOUT


class SaaSClient(requests.Session):
    """A more convenient session to send requests to SaaS."""

    def __init__(self, base_url: str, token: str, timeout: int = REQUEST_TIMEOUT):
        super().__init__()
        self.timeout = timeout
        self.base_url = base_url
        self.headers["Authorization"] = f"Bearer {token}"
        # Automatically check responses for 4XX and 5XX
        self.hooks["response"] = [lambda response, *args, **kwargs: response.raise_for_status()]
        adapter = HTTPAdapter(max_retries=Retry(5))
        self.mount("https://", adapter)
        self.mount("http://", adapter)

    def request(self, method: str, url: str, *args: Any, **kwargs: Any) -> requests.Response:  # type: ignore
        kwargs.setdefault("timeout", self.timeout)
        url = urljoin(self.base_url, url)
        return super().request(method, url, *args, **kwargs)

    def create_test_job(self) -> str:
        """Create a new test job on the SaaS side."""
        response = self.post("/jobs/")
        return response.json()["job_id"]

    def finish_test_job(self, job_id: str) -> None:
        """Finish a test job on the SaaS side.

        Only needed in corner cases when Schemathesis CLI fails with an internal error in itself, not in the runner.
        """
        self.post(f"/jobs/{job_id}/finish/")

    def send_event(self, job_id: str, data: Dict[str, Any]) -> None:
        """Send a single event to SaaS."""
        self.post(f"/jobs/{job_id}/events/", json=data)
