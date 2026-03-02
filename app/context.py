from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.models import Context


def build_context(
    request_id: str,
    user_id: str,
    session_id: str | None,
    input_data: dict[str, Any],
    memory: dict[str, Any],
    metadata: dict[str, Any] | None = None,
) -> Context:
    merged_metadata = {
        **(metadata or {}),
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
    }

    return Context(
        request_id=request_id,
        user_id=user_id,
        session_id=session_id,
        input=input_data,
        memory=memory,
        metadata=merged_metadata,
    )
