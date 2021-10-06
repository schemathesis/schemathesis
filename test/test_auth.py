import uuid
import json
import requests
import pytest
from datetime import datetime, timedelta
from schemathesis import auth


def perform_auth():
    resp = requests.request(url="localhost/test", method="POST")
    return resp


def auth_api_response_mock(*args, **kwargs):
    response = requests.Response()
    response.status_code = 200
    response._content = json.dumps({"token": uuid.uuid4().hex}).encode()
    return response


class DatetimeMock:
    current_timestamp = datetime(1992, 11, 19, 14, 21)

    @classmethod
    def now(cls):
        return cls.current_timestamp

    @classmethod
    def _update_current_timestamp_mock(cls, timediff_in_seconds: float):
        cls.current_timestamp += timedelta(seconds=timediff_in_seconds)


@pytest.fixture
def auth_token():
    return auth.AuthToken(token_value=uuid.uuid4().hex, timestamp=DatetimeMock.now())


class AuthStorage(auth.BaseAuthStorage):
    login_url = "localhost/test"


def test_register_auth_provider_on_expiration(auth_token, monkeypatch):
    refresh_intervall = 100
    monkeypatch.setattr(requests, "request", auth_api_response_mock)
    monkeypatch.setattr(auth, "datetime", DatetimeMock)

    @auth.register_auth_provider(AuthStorage, auth_token=auth_token, auto_refresh=False, refresh_interval=100)
    def get_current_auth_token() -> auth.AuthToken:
        return AuthStorage.auth_token

    assert get_current_auth_token() == auth_token

    DatetimeMock._update_current_timestamp_mock(timediff_in_seconds=refresh_intervall+1)
    new_auth_token = get_current_auth_token()
    assert new_auth_token != auth_token
    assert new_auth_token.timestamp == DatetimeMock.current_timestamp
