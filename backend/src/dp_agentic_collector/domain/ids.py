from __future__ import annotations

import uuid
from datetime import UTC, datetime


def new_id(prefix: str = "") -> str:
    raw = uuid.uuid4().hex
    return f"{prefix}_{raw}" if prefix else raw


def utcnow() -> datetime:
    return datetime.now(UTC)
