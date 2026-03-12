from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from memory.consolidation_worker import MemoryConsolidationWorker
from memory.episodic_memory import EpisodicMemory
from memory.memory_manager import MemoryManager
from memory.semantic_memory import SemanticMemory
from memory.user_memory import UserMemory
from memory.working_memory import WorkingMemory
from storage.database import Database
from storage.vector_store import VectorStore


class MemoryConsolidationWorkerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="amaryllis-tests-memory-worker-")
        self.base = Path(self._tmp.name)
        self.database = Database(self.base / "state.db")
        self.vector_store = VectorStore(self.base / "vectors.faiss")
        self.manager = MemoryManager(
            episodic=EpisodicMemory(self.database),
            semantic=SemanticMemory(self.database, self.vector_store),
            user_memory=UserMemory(self.database),
            working_memory=WorkingMemory(self.database),
            telemetry=None,
        )
        self.worker = MemoryConsolidationWorker(
            database=self.database,
            memory_manager=self.manager,
            interval_sec=60,
            semantic_limit=1000,
            max_users_per_tick=10,
            telemetry=None,
        )

    def tearDown(self) -> None:
        self.database.close()
        self._tmp.cleanup()

    def test_run_once_consolidates_duplicate_facts(self) -> None:
        self.manager.semantic.add(
            user_id="user-1",
            text="timezone=UTC",
            metadata={"fact_key": "timezone", "fact_value": "UTC"},
            kind="fact",
            confidence=0.9,
            importance=0.9,
            fingerprint="a1",
        )
        self.manager.semantic.add(
            user_id="user-1",
            text="timezone=CET",
            metadata={"fact_key": "timezone", "fact_value": "CET"},
            kind="fact",
            confidence=0.4,
            importance=0.3,
            fingerprint="a2",
        )

        payload = self.worker.run_once()
        self.assertGreaterEqual(int(payload.get("users_processed", 0)), 1)
        self.assertGreaterEqual(int(payload.get("semantic_deactivated_total", 0)), 1)

        active = self.database.list_semantic_entries(
            user_id="user-1",
            kind="fact",
            active_only=True,
            limit=20,
        )
        self.assertEqual(len(active), 1)
        metadata = active[0].get("metadata", {})
        self.assertEqual(str(metadata.get("fact_value")), "UTC")


if __name__ == "__main__":
    unittest.main()
