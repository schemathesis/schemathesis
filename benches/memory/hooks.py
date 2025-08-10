from unittest.mock import patch

import requests

from schemathesis.core.transport import Response

RESPONSE = Response(
    status_code=200,
    headers={"Content-Type": ["application/json"]},
    content=b"{}",
    request=requests.Request(method="GET", url="http://127.0.0.1/test").prepare(),
    elapsed=0.1,
    verify=False,
)
patch("schemathesis.Case.call", return_value=RESPONSE).start()
