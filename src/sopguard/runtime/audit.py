"""Append-only audit log of every supervisor decision (JSONL on disk, plus an in-memory view)."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from sopguard.runtime.decision import Decision


class AuditLog:
    def __init__(self, path: str | Path | None = None, policy_hash: str = "") -> None:
        self.path = Path(path) if path else None
        self.policy_hash = policy_hash
        self.records: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, decision: Decision) -> None:
        rec = decision.model_dump(mode="json")
        rec["policy_hash"] = self.policy_hash
        rec["citations"] = decision.citations()
        with self._lock:
            self.records.append(rec)
            if self.path:
                with self.path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, default=str) + "\n")

    def for_session(self, session_id: str) -> list[dict[str, Any]]:
        return [r for r in self.records if r["session_id"] == session_id]

    def summary(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for r in self.records:
            out[r["action"]] = out.get(r["action"], 0) + 1
        return out
