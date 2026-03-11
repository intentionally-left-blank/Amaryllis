from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any


class LocalTelemetry:
    def __init__(self, output_path: Path) -> None:
        self.logger = logging.getLogger("amaryllis.telemetry")
        self.output_path = Path(output_path)
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()

    @staticmethod
    def _utc_now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def emit(self, event_type: str, payload: dict[str, Any]) -> None:
        row = {
            "timestamp": self._utc_now(),
            "event_type": event_type,
            "payload": payload,
        }
        try:
            encoded = json.dumps(row, ensure_ascii=False)
            with self._lock:
                with self.output_path.open("a", encoding="utf-8") as handle:
                    handle.write(encoded)
                    handle.write("\n")
        except Exception as exc:
            self.logger.warning("telemetry_write_failed error=%s", exc)
