from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from memory.episodic_memory import EpisodicMemory
from memory.memory_manager import MemoryManager
from memory.semantic_memory import SemanticMemory
from memory.user_memory import UserMemory
from memory.working_memory import WorkingMemory
from storage.database import Database
from storage.vector_store import VectorStore


class _TelemetrySink:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def emit(self, event_type: str, payload: dict) -> None:
        self.events.append((event_type, payload))


class MemoryManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="amaryllis-tests-memory-")
        self.base = Path(self._tmp.name)
        self.database = Database(self.base / "state.db")
        self.vector_store = VectorStore(self.base / "vectors.faiss")
        self.telemetry = _TelemetrySink()

        self.manager = MemoryManager(
            episodic=EpisodicMemory(self.database),
            semantic=SemanticMemory(self.database, self.vector_store),
            user_memory=UserMemory(self.database),
            working_memory=WorkingMemory(self.database),
            telemetry=self.telemetry,
        )

    def tearDown(self) -> None:
        self.database.close()
        self._tmp.cleanup()

    def test_profile_conflict_keeps_previous_when_incoming_confidence_is_lower(self) -> None:
        first = self.manager.set_user_preference(
            user_id="user-1",
            key="language",
            value="english",
            confidence=0.95,
        )
        second = self.manager.set_user_preference(
            user_id="user-1",
            key="language",
            value="german",
            confidence=0.40,
        )

        self.assertEqual(first, "created")
        self.assertEqual(second, "kept_previous_higher_confidence")

        stored = self.manager.user_memory.get("user-1", "language")
        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertEqual(stored["value"], "english")

        conflicts = self.manager.list_conflicts("user-1")
        self.assertTrue(
            any(
                item["layer"] == "profile"
                and item["key"] == "language"
                and item["resolution"] == "kept_previous_higher_confidence"
                for item in conflicts
            )
        )

    def test_semantic_conflict_keeps_previous_fact_when_incoming_confidence_is_lower(self) -> None:
        first_id = self.manager.semantic.add(
            user_id="user-1",
            text="user_name=Alice",
            metadata={"fact_key": "name", "fact_value": "Alice"},
            kind="fact",
            confidence=0.95,
            importance=0.8,
            fingerprint=self.manager._fingerprint("user_name=Alice"),  # noqa: SLF001
        )

        self.manager.ingest_user_turn(
            user_id="user-1",
            agent_id="agent-1",
            session_id="session-1",
            content="my name is Bob",
        )

        active_facts = self.database.list_semantic_entries(
            user_id="user-1",
            kind="fact",
            active_only=True,
            limit=20,
        )
        active_ids = {int(item["id"]) for item in active_facts}
        self.assertIn(first_id, active_ids)
        self.assertFalse(any("Bob" in str(item.get("text", "")) for item in active_facts))

        conflicts = self.manager.list_conflicts("user-1")
        self.assertTrue(
            any(
                item["layer"] == "semantic"
                and item["key"] == "name"
                and item["resolution"] == "kept_previous_higher_confidence"
                for item in conflicts
            )
        )

    def test_retrieval_scoring_prefers_high_confidence_and_importance(self) -> None:
        high_id = self.manager.semantic.add(
            user_id="user-1",
            text="project alpha deadline",
            metadata={"fact_key": "project", "fact_value": "alpha"},
            kind="fact",
            confidence=0.95,
            importance=0.95,
            fingerprint=self.manager._fingerprint("project alpha deadline"),  # noqa: SLF001
        )
        low_id = self.manager.semantic.add(
            user_id="user-1",
            text="project alpha deadline",
            metadata={"fact_key": "project", "fact_value": "alpha-low"},
            kind="fact",
            confidence=0.2,
            importance=0.2,
            fingerprint=self.manager._fingerprint("project alpha deadline v2"),  # noqa: SLF001
        )

        trace = self.manager.debug_retrieval(
            user_id="user-1",
            query="project alpha deadline",
            top_k=5,
        )

        self.assertGreaterEqual(len(trace), 2)
        self.assertEqual(trace[0]["semantic_id"], high_id)
        self.assertEqual(trace[1]["semantic_id"], low_id)
        self.assertGreaterEqual(float(trace[0]["score"]), float(trace[1]["score"]))
        self.assertIn("vector_score", trace[0])
        self.assertIn("recency_score", trace[0])

        self.assertTrue(any(name == "memory_retrieval_debug" for name, _ in self.telemetry.events))


if __name__ == "__main__":
    unittest.main()
