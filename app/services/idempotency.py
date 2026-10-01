"""Idempotency-key handling with 24h TTL (plan sec 3.5)."""

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ValidationError
from app.repos import idempotency as repo

TTL = timedelta(hours=24)
REPLAYED_HEADER = "Idempotent-Replayed"


@dataclass
class Replay:
    status_code: int
    body: dict[str, Any]


def hash_request(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


async def lookup(
    session: AsyncSession, *, key: str, user_id: uuid.UUID
) -> repo.IdempotencyKey | None:
    record = await repo.get(session, key, user_id)
    if record is None:
        return None
    created = record.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) - created >= TTL:
        await session.delete(record)
        await session.flush()
        return None
    return record


def check_payload(record: repo.IdempotencyKey, request_hash: str) -> Replay:
    if record.request_hash != request_hash:
        raise ValidationError("idempotency key already used with a different payload")
    return Replay(status_code=record.status_code, body=record.response_body)


def to_response(replay: Replay) -> JSONResponse:
    return JSONResponse(
        status_code=replay.status_code,
        content=replay.body,
        headers={REPLAYED_HEADER: "true"},
    )


async def store(
    session: AsyncSession,
    *,
    key: str,
    user_id: uuid.UUID,
    method: str,
    path: str,
    request_hash: str,
    status_code: int,
    response_body: dict[str, Any],
) -> None:
    await repo.store(
        session,
        key=key,
        user_id=user_id,
        method=method,
        path=path,
        request_hash=request_hash,
        status_code=status_code,
        response_body=response_body,
    )


async def sweep(session: AsyncSession) -> int:
    cutoff = datetime.now(timezone.utc) - TTL
    return await repo.sweep_expired(session, cutoff)


def normalize_key(raw: str | None) -> str | None:
    if not raw:
        return None
    key = raw.strip()
    if not key:
        return None
    if len(key) > 255:
        raise ValidationError("Idempotency-Key must be at most 255 characters")
    return key
