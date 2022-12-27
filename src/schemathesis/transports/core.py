from typing import Any, Dict

from typing_extensions import Protocol, runtime_checkable

from ..models import Case


class Response:
    @property
    def payload(self) -> None:
        return


@runtime_checkable
class Transport(Protocol):
    """Send generated data to the application under test.

    Should only contain the logic for:
      - Transformation of the generated data into the format acceptable by the transport library
      - Converting the application's response into the common response format
    """

    def send(self, case: Case, **kwargs: Any) -> Response:
        """Send `case` data to the application under test."""
        raise NotImplementedError

    def into_kwargs(self, case: Case) -> Dict[str, Any]:
        """Transform `case` into a set of arguments suitable for the underlying transport library."""
        raise NotImplementedError
