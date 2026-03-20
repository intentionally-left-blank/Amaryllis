from __future__ import annotations

import importlib
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    from fastapi.testclient import TestClient
except Exception:  # pragma: no cover - dependency may be unavailable
    TestClient = None  # type: ignore[assignment]


@unittest.skipIf(TestClient is None, "fastapi dependency is not available")
class AgentRunAuditAPITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory(prefix="amaryllis-tests-run-audit-api-")
        support_dir = Path(cls._tmp.name) / "support"
        auth_tokens = {
            "admin-token": {"user_id": "admin", "scopes": ["admin", "user"]},
            "user-token": {"user_id": "user-1", "scopes": ["user"]},
            "user2-token": {"user_id": "user-2", "scopes": ["user"]},
            "service-token": {"user_id": "svc-runtime", "scopes": ["service"]},
        }
        cls._env_patch = patch.dict(
            os.environ,
            {
                "AMARYLLIS_SUPPORT_DIR": str(support_dir),
                "AMARYLLIS_AUTH_ENABLED": "true",
                "AMARYLLIS_AUTH_TOKENS": json.dumps(auth_tokens, ensure_ascii=False),
                "AMARYLLIS_MEMORY_CONSOLIDATION_ENABLED": "false",
                "AMARYLLIS_MCP_ENDPOINTS": "",
                "AMARYLLIS_SECURITY_PROFILE": "production",
            },
            clear=False,
        )
        cls._env_patch.start()

        import runtime.server as server_module

        cls.server_module = importlib.reload(server_module)
        cls._client_cm = TestClient(cls.server_module.app)
        cls.client = cls._client_cm.__enter__()

    @classmethod
    def tearDownClass(cls) -> None:
        cls._client_cm.__exit__(None, None, None)
        cls._env_patch.stop()
        cls._tmp.cleanup()

    @staticmethod
    def _auth(token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    def _wait_run_terminal(
        self,
        *,
        run_id: str,
        token: str,
        timeout_sec: float = 8.0,
    ) -> dict[str, object]:
        deadline = time.time() + timeout_sec
        last_payload: dict[str, object] = {}
        while time.time() < deadline:
            response = self.client.get(
                f"/agents/runs/{run_id}",
                headers=self._auth(token),
            )
            if response.status_code == 200:
                payload = response.json().get("run", {})
                if isinstance(payload, dict):
                    last_payload = payload
                status = str(last_payload.get("status") or "")
                if status in {"succeeded", "failed", "canceled"}:
                    return last_payload
            time.sleep(0.05)
        self.fail(f"Run {run_id} did not reach terminal status, last_payload={last_payload}")

    def _create_agent_and_run(self) -> tuple[str, str]:
        created = self.client.post(
            "/agents/create",
            headers=self._auth("user-token"),
            json={
                "name": "Audit Agent",
                "system_prompt": "audit",
                "user_id": "user-1",
                "tools": ["web_search"],
            },
        )
        self.assertEqual(created.status_code, 200)
        agent_id = str(created.json().get("id"))

        run_created = self.client.post(
            f"/agents/{agent_id}/runs",
            headers=self._auth("user-token"),
            json={
                "user_id": "user-1",
                "session_id": "audit-session-1",
                "message": "Collect context and summarize.",
                "max_attempts": 1,
            },
        )
        self.assertEqual(run_created.status_code, 200)
        run_id = str(run_created.json().get("run", {}).get("id"))
        self._wait_run_terminal(run_id=run_id, token="user-token")
        return agent_id, run_id

    def test_run_audit_timeline_contains_checkpoint_and_security_events(self) -> None:
        _, run_id = self._create_agent_and_run()

        response = self.client.get(
            f"/agents/runs/{run_id}/audit",
            headers=self._auth("user-token"),
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        audit = payload.get("audit", {})
        self.assertEqual(str(audit.get("run_id")), run_id)

        timeline = audit.get("timeline", [])
        self.assertTrue(isinstance(timeline, list) and len(timeline) >= 1)
        self.assertTrue(any(str(item.get("channel")) == "run_checkpoint" for item in timeline))
        self.assertTrue(
            any(
                str(item.get("channel")) == "security_audit"
                and str(item.get("action")) == "agent_run_create"
                for item in timeline
            )
        )

        summary = audit.get("summary", {})
        channel_counts = summary.get("channel_counts", {})
        self.assertGreaterEqual(int(channel_counts.get("run_checkpoint", 0)), 1)
        self.assertTrue(bool(payload.get("request_id")))

    def test_run_audit_export_csv_and_owner_enforcement(self) -> None:
        _, run_id = self._create_agent_and_run()

        denied = self.client.get(
            f"/agents/runs/{run_id}/audit",
            headers=self._auth("user2-token"),
        )
        self.assertEqual(denied.status_code, 403)
        denied_payload = denied.json()
        self.assertEqual(str(denied_payload.get("error", {}).get("type")), "permission_denied")

        csv_response = self.client.get(
            f"/agents/runs/{run_id}/audit/export?format=csv",
            headers=self._auth("user-token"),
        )
        self.assertEqual(csv_response.status_code, 200)
        self.assertIn("text/csv", str(csv_response.headers.get("content-type", "")))
        self.assertIn("timestamp,channel,event_id", csv_response.text)
        self.assertIn(f"run-audit-{run_id}.csv", str(csv_response.headers.get("content-disposition", "")))

        json_response = self.client.get(
            f"/agents/runs/{run_id}/audit/export?format=json",
            headers=self._auth("user-token"),
        )
        self.assertEqual(json_response.status_code, 200)
        json_payload = json_response.json()
        self.assertEqual(str(json_payload.get("audit", {}).get("run_id")), run_id)
        self.assertEqual(str(json_payload.get("export", {}).get("format")), "json")

        invalid = self.client.get(
            f"/agents/runs/{run_id}/audit/export?format=xml",
            headers=self._auth("user-token"),
        )
        self.assertEqual(invalid.status_code, 400)
        invalid_payload = invalid.json()
        self.assertEqual(str(invalid_payload.get("error", {}).get("type")), "validation_error")


if __name__ == "__main__":
    unittest.main()
