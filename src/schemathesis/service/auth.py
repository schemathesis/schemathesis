from .client import ServiceClient
from .constants import DEFAULT_HOSTNAME, DEFAULT_PROTOCOL
from .metadata import collect


def login(token: str, hostname: str = DEFAULT_HOSTNAME, protocol: str = DEFAULT_PROTOCOL, verify: bool = True) -> str:
    """Make a login request to SaaS."""
    client = ServiceClient(f"{protocol}://{hostname}", token, verify=verify)
    payload = collect()
    response = client.cli_login(payload)
    return response.username
