from __future__ import annotations

import unittest

from tools.permission_manager import ToolPermissionManager


class ToolPermissionManagerTests(unittest.TestCase):
    def test_scope_session_mismatch_prevents_consume(self) -> None:
        manager = ToolPermissionManager(default_ttl_sec=600)
        prompt = manager.request(
            tool_name="filesystem",
            arguments={"action": "write", "path": "/tmp/a.txt", "content": "x"},
            reason="need write",
            scope="session",
            session_id="session-a",
            user_id="user-1",
        )
        manager.approve(prompt["id"])

        denied = manager.consume_if_approved(
            prompt["id"],
            tool_name="filesystem",
            arguments={"action": "write", "path": "/tmp/a.txt", "content": "x"},
            session_id="session-b",
            user_id="user-1",
        )
        self.assertFalse(denied)

        allowed = manager.consume_if_approved(
            prompt["id"],
            tool_name="filesystem",
            arguments={"action": "write", "path": "/tmp/a.txt", "content": "x"},
            session_id="session-a",
            user_id="user-1",
        )
        self.assertTrue(allowed)

    def test_expired_prompt_cannot_be_consumed(self) -> None:
        manager = ToolPermissionManager(default_ttl_sec=600)
        prompt = manager.request(
            tool_name="python_exec",
            arguments={"code": "print('x')"},
            reason="exec",
            scope="request",
            request_id="r-1",
            ttl_sec=1,
        )
        manager.approve(prompt["id"])

        # Force expiration deterministically for test.
        manager._prompts[prompt["id"]]["expires_at"] = "1970-01-01T00:00:00+00:00"  # noqa: SLF001

        consumed = manager.consume_if_approved(
            prompt["id"],
            tool_name="python_exec",
            arguments={"code": "print('x')"},
            request_id="r-1",
        )
        self.assertFalse(consumed)

        rows = manager.list(status="expired", limit=10)
        self.assertTrue(any(item.get("id") == prompt["id"] for item in rows))


if __name__ == "__main__":
    unittest.main()
