import hashlib
import uuid
from datetime import datetime, timedelta, timezone

import jwt
from pwdlib import PasswordHash

from app.core.config import get_settings
from app.core.errors import UnauthorizedError

_password_hasher = PasswordHash.recommended()


def hash_password(password: str) -> str:
    return _password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bool(_password_hasher.verify(password, password_hash))
    except Exception:
        return False


def _now() -> datetime:
    return datetime.now(timezone.utc)


def create_access_token(user_id: uuid.UUID) -> str:
    settings = get_settings()
    now = _now()
    payload = {
        "sub": str(user_id),
        "type": "access",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=settings.access_token_expire_minutes)).timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def create_refresh_token(user_id: uuid.UUID) -> tuple[str, datetime]:
    """Returns (token, expires_at). The token embeds a jti; only its hash is stored."""
    settings = get_settings()
    now = _now()
    expires_at = now + timedelta(days=settings.refresh_token_expire_days)
    payload = {
        "sub": str(user_id),
        "type": "refresh",
        "jti": uuid.uuid4().hex,
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
    }
    return (
        jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm),
        expires_at,
    )


def decode_token(token: str, expected_type: str) -> dict:
    settings = get_settings()
    try:
        payload = jwt.decode(
            token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm]
        )
    except jwt.ExpiredSignatureError:
        raise UnauthorizedError("token expired")
    except jwt.InvalidTokenError:
        raise UnauthorizedError("invalid token")
    if payload.get("type") != expected_type:
        raise UnauthorizedError("invalid token type")
    return payload


def hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
