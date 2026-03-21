from __future__ import annotations

import unittest

from flow.session_manager import UnifiedSessionManager


class UnifiedSessionManagerTests(unittest.TestCase):
    def test_start_transition_and_activity(self) -> None:
        manager = UnifiedSessionManager()
        started = manager.start_session(
            user_id="user-1",
            channels=["text", "voice"],
            initial_state="listening",
            metadata={"source": "test"},
            request_id="req-1",
            actor="user-1",
        )
        self.assertTrue(str(started.get("id", "")).startswith("flow-"))
        self.assertEqual(str(started.get("user_id")), "user-1")
        self.assertEqual(str(started.get("state")), "listening")
        self.assertEqual(sorted(list(started.get("channels") or [])), ["text", "voice"])

        session_id = str(started.get("id"))
        planning = manager.transition_session(
            session_id=session_id,
            to_state="planning",
            reason="user_requested_plan",
            actor="user-1",
        )
        self.assertEqual(str(planning.get("state")), "planning")

        activity = manager.record_activity(
            session_id=session_id,
            channel="text",
            event="prompt_submitted",
            actor="user-1",
        )
        self.assertEqual(str(activity.get("state")), "planning")
        channel_activity = activity.get("channel_activity", {})
        text_activity = channel_activity.get("text", {})
        self.assertEqual(int(text_activity.get("events_count", 0)), 1)
        self.assertEqual(str(text_activity.get("last_event")), "prompt_submitted")

    def test_invalid_transition_is_blocked(self) -> None:
        manager = UnifiedSessionManager()
        started = manager.start_session(user_id="user-1", channels=["text"], initial_state="created")
        session_id = str(started.get("id"))
        manager.transition_session(
            session_id=session_id,
            to_state="closed",
            reason="done",
        )
        with self.assertRaises(ValueError):
            manager.transition_session(
                session_id=session_id,
                to_state="planning",
                reason="should_fail",
            )

    def test_record_activity_rejects_disabled_channel(self) -> None:
        manager = UnifiedSessionManager()
        started = manager.start_session(user_id="user-1", channels=["text"])
        session_id = str(started.get("id"))
        with self.assertRaises(ValueError):
            manager.record_activity(
                session_id=session_id,
                channel="voice",
                event="audio_chunk",
            )

    def test_list_sessions_user_scope(self) -> None:
        manager = UnifiedSessionManager()
        manager.start_session(user_id="user-1", channels=["text"])
        manager.start_session(user_id="user-2", channels=["text", "visual"])

        user1 = manager.list_sessions(user_id="user-1")
        self.assertEqual(len(user1), 1)
        self.assertEqual(str(user1[0].get("user_id")), "user-1")

        user2 = manager.list_sessions(user_id="user-2")
        self.assertEqual(len(user2), 1)
        self.assertEqual(str(user2[0].get("user_id")), "user-2")


if __name__ == "__main__":
    unittest.main()
