import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_jwt_secret_must_be_configured(monkeypatch):
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_jwt_secret_must_be_at_least_32_characters():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, jwt_secret_key="short")
