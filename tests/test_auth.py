import pytest
from fastapi import HTTPException

from app.api import auth
from app.settings import get_settings


class Req:
    def __init__(self, path, key=None):
        self.url = type("U", (), {"path": path})()
        self.headers = {"x-api-key": key} if key else {}


@pytest.fixture
def api_key(monkeypatch):
    monkeypatch.setattr(get_settings(), "api_key", "s3cret")


async def test_auth_off_when_no_key_configured(monkeypatch):
    monkeypatch.setattr(get_settings(), "api_key", "")
    await auth.require_api_key(Req("/calls"))


async def test_key_required_and_checked(api_key):
    with pytest.raises(HTTPException):
        await auth.require_api_key(Req("/calls"))
    with pytest.raises(HTTPException):
        await auth.require_api_key(Req("/calls", "wrong"))
    await auth.require_api_key(Req("/calls", "s3cret"))


async def test_health_and_metrics_stay_open(api_key):
    await auth.require_api_key(Req("/health"))
    await auth.require_api_key(Req("/metrics"))
