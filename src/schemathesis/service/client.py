from typing import Any, Dict
from urllib.parse import urljoin

import requests
from requests.adapters import HTTPAdapter, Retry

from .constants import REQUEST_TIMEOUT
from .models import TestJob


class ServiceClient(requests.Session):
    """A more convenient session to send requests to Schemathesis.io."""

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
        # All requests will be done against the base url
        url = urljoin(self.base_url, url)
        return super().request(method, url, *args, **kwargs)

    def create_test_job(self) -> TestJob:
        """Create a new test job on the Schemathesis.io side."""
        response = self.post("/jobs/")
        data = response.json()
        return TestJob(job_id=data["job_id"], short_url=data["short_url"])

    def finish_test_job(self, job_id: str) -> None:
        """Finish a test job on the Schemathesis.io side.

        Only needed in corner cases when Schemathesis CLI fails with an internal error in itself, not in the runner.
        """
        self.post(f"/jobs/{job_id}/finish/")

    def send_event(self, job_id: str, data: Dict[str, Any]) -> None:
        """Send a single event to Schemathesis.io."""
        self.post(f"/jobs/{job_id}/events/", json=data)
