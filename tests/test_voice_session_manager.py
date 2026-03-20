from __future__ import annotations

import unittest

from voice.session_manager import VoiceSessionManager


class VoiceSessionManagerTests(unittest.TestCase):
    def test_start_session_creates_listening_session_with_transitions(self) -> None:
        events: list[tuple[str, dict[str, object]]] = []
        manager = VoiceSessionManager(telemetry_emitter=lambda event, payload: events.append((event, payload)))

        session = manager.start_session(
            user_id="user-1",
            mode="ptt",
            input_device="built-in-mic",
            sample_rate_hz=16000,
            language="en",
            metadata={"origin": "unit-test"},
            request_id="req-1",
        )

        self.assertTrue(str(session.get("id", "")).startswith("voice-"))
        self.assertEqual(str(session.get("user_id")), "user-1")
        self.assertEqual(str(session.get("state")), "listening")
        self.assertEqual(int(session.get("sample_rate_hz", 0)), 16000)
        transitions = session.get("transitions", [])
        self.assertEqual(len(transitions), 2)
        self.assertEqual(str(transitions[0].get("to_state")), "created")
        self.assertEqual(str(transitions[1].get("to_state")), "listening")
        event_names = [name for name, _ in events]
        self.assertIn("voice_session_transition", event_names)
        self.assertIn("voice_session_started", event_names)

    def test_stop_session_transitions_to_stopped_and_is_idempotent(self) -> None:
        manager = VoiceSessionManager()
        created = manager.start_session(user_id="user-1")
        session_id = str(created.get("id"))

        stopped = manager.stop_session(
            session_id=session_id,
            reason="unit-test-stop",
            actor="user-1",
            request_id="req-2",
        )
        self.assertEqual(str(stopped.get("state")), "stopped")
        self.assertTrue(str(stopped.get("stopped_at", "")).strip())
        self.assertIsNotNone(stopped.get("duration_ms"))
        transitions = stopped.get("transitions", [])
        self.assertTrue(any(str(item.get("to_state")) == "stopping" for item in transitions))
        self.assertTrue(any(str(item.get("to_state")) == "stopped" for item in transitions))

        again = manager.stop_session(session_id=session_id, reason="idempotent")
        self.assertEqual(str(again.get("state")), "stopped")
        self.assertEqual(str(again.get("id")), session_id)

    def test_list_sessions_is_scoped_and_filterable(self) -> None:
        manager = VoiceSessionManager()
        one = manager.start_session(user_id="user-1")
        two = manager.start_session(user_id="user-2")
        manager.stop_session(session_id=str(one.get("id")), reason="done")

        user1_sessions = manager.list_sessions(user_id="user-1")
        self.assertEqual(len(user1_sessions), 1)
        self.assertEqual(str(user1_sessions[0].get("user_id")), "user-1")
        self.assertEqual(str(user1_sessions[0].get("state")), "stopped")

        user2_active = manager.list_sessions(user_id="user-2", state="listening")
        self.assertEqual(len(user2_active), 1)
        self.assertEqual(str(user2_active[0].get("id")), str(two.get("id")))

    def test_start_session_validates_mode(self) -> None:
        manager = VoiceSessionManager()
        with self.assertRaises(ValueError):
            manager.start_session(user_id="user-1", mode="continuous")


if __name__ == "__main__":
    unittest.main()
