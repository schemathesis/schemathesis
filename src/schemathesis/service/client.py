from typing import Any, Optional
from urllib.parse import urljoin

import attr
import requests
from requests.adapters import HTTPAdapter, Retry

from ..constants import USER_AGENT
from .constants import REPORT_CORRELATION_ID_HEADER, REQUEST_TIMEOUT
from .metadata import Metadata
from .models import ApiDetails, AuthResponse, UploadResponse


class ServiceClient(requests.Session):
    """A more convenient session to send requests to Schemathesis.io."""

    def __init__(self, base_url: str, token: Optional[str], *, timeout: int = REQUEST_TIMEOUT, verify: bool = True):
        super().__init__()
        self.timeout = timeout
        self.verify = verify
        self.base_url = base_url
        self.headers["User-Agent"] = USER_AGENT
        if token is not None:
            self.headers["Authorization"] = f"Bearer {token}"
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

    def get_api_details(self, name: str) -> ApiDetails:
        """Get information about an API."""
        response = self.get(f"/apis/{name}/")
        data = response.json()
        return ApiDetails(location=data["location"], base_url=data["base_url"])

    def login(self, metadata: Metadata) -> AuthResponse:
        """Send a login request."""
        response = self.post("/auth/cli/login/", json={"metadata": attr.asdict(metadata)})
        data = response.json()
        return AuthResponse(username=data["username"])

    def upload_report(self, report: bytes, correlation_id: Optional[str] = None) -> UploadResponse:
        """Upload test run report to Schemathesis.io."""
        headers = {"Content-Type": "application/x-gtar"}
        if correlation_id is not None:
            headers[REPORT_CORRELATION_ID_HEADER] = correlation_id
        response = self.post("/reports/upload/", report, headers=headers)
        data = response.json()
        return UploadResponse(message=data["message"], next_url=data["next"], correlation_id=data["correlation_id"])
