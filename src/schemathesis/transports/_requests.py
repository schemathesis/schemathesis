from typing import Any, Dict

import attr
import requests

from .. import Case
from .core import Response, Transport


@attr.s(slots=True)
class RequestsTransport(Transport):
    session: requests.Session = attr.ib(factory=requests.Session)

    def send(self, case: Case, **kwargs: Any) -> Response:
        data = self.into_kwargs(case)
        data.update(**kwargs)
        response = self.session.request(**data)
        return self.into_response(response)

    def into_response(self, response: requests.Response) -> Response:
        pass

    def into_kwargs(self, case: Case) -> Dict[str, Any]:
        return {}
