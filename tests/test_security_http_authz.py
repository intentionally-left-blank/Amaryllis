from __future__ import annotations

import importlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    from fastapi.testclient import TestClient
except Exception:  # pragma: no cover - dependency may be unavailable in base test env
    TestClient = None  # type: ignore[assignment]


@unittest.skipIf(TestClient is None, "fastapi dependency is not available")
class SecurityHTTPAuthzTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory(prefix="amaryllis-tests-http-security-")
        support_dir = Path(cls._tmp.name) / "support"
        auth_tokens = {
            "admin-token": {
                "user_id": "admin",
                "scopes": ["admin", "user"],
            },
            "user-token": {
                "user_id": "user-1",
                "scopes": ["user"],
            },
            "user2-token": {
                "user_id": "user-2",
                "scopes": ["user"],
            },
            "service-token": {
                "user_id": "svc-runtime",
                "scopes": ["service"],
            },
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
        services = cls.server_module.app.state.services
        if services.tool_registry.get("dangerous_echo") is None:
            services.tool_registry.register(
                name="dangerous_echo",
                description="Test-only high-risk tool for action receipt coverage.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "echo": {"type": "string"},
                    },
                },
                handler=lambda arguments: {"ok": True, "echo": str(arguments.get("echo", ""))},
                source="test",
                risk_level="high",
                approval_mode="none",
                isolation="process_internal",
            )

    @classmethod
    def tearDownClass(cls) -> None:
        cls._client_cm.__exit__(None, None, None)
        cls._env_patch.stop()
        cls._tmp.cleanup()

    @staticmethod
    def _auth(token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    def test_missing_token_returns_401_with_structured_error(self) -> None:
        response = self.client.get("/models")
        self.assertEqual(response.status_code, 401)
        payload = response.json()
        self.assertEqual(payload["error"]["type"], "authentication_error")
        self.assertTrue(str(payload["error"]["request_id"]).strip())

    def test_invalid_token_returns_401_with_structured_error(self) -> None:
        response = self.client.get("/models", headers=self._auth("invalid-token"))
        self.assertEqual(response.status_code, 401)
        payload = response.json()
        self.assertEqual(payload["error"]["type"], "authentication_error")
        self.assertTrue(str(payload["error"]["request_id"]).strip())

    def test_user_token_cannot_access_admin_endpoints(self) -> None:
        response = self.client.get("/security/identity", headers=self._auth("user-token"))
        self.assertEqual(response.status_code, 403)
        payload = response.json()
        self.assertEqual(payload["error"]["type"], "permission_denied")
        self.assertIn("Admin scope is required", payload["error"]["message"])

        response = self.client.get("/debug/models/failover", headers=self._auth("user-token"))
        self.assertEqual(response.status_code, 403)
        payload = response.json()
        self.assertEqual(payload["error"]["type"], "permission_denied")

    def test_service_scope_isolated_from_user_endpoints(self) -> None:
        response = self.client.get("/service/health", headers=self._auth("service-token"))
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["actor"], "svc-runtime")
        self.assertIn("service", payload["scopes"])
        self.assertEqual(str(payload.get("autonomy_level")), "l3")

        response = self.client.get("/models", headers=self._auth("service-token"))
        self.assertEqual(response.status_code, 403)
        payload = response.json()
        self.assertEqual(payload["error"]["type"], "permission_denied")
        self.assertIn("User scope is required", payload["error"]["message"])

    def test_service_kill_switch_requires_service_scope(self) -> None:
        denied = self.client.post(
            "/service/runs/kill-switch",
            headers=self._auth("user-token"),
            json={"include_running": False, "include_queued": True},
        )
        self.assertEqual(denied.status_code, 403)
        denied_payload = denied.json()
        self.assertEqual(denied_payload["error"]["type"], "permission_denied")

    def test_service_kill_switch_success_and_validation(self) -> None:
        invalid = self.client.post(
            "/service/runs/kill-switch",
            headers=self._auth("service-token"),
            json={
                "include_running": False,
                "include_queued": False,
                "reason": "validation-check",
            },
        )
        self.assertEqual(invalid.status_code, 400)
        invalid_payload = invalid.json()
        self.assertEqual(invalid_payload["error"]["type"], "validation_error")

        response = self.client.post(
            "/service/runs/kill-switch",
            headers=self._auth("service-token"),
            json={
                "include_running": False,
                "include_queued": True,
                "limit": 1,
                "reason": "security-http-authz",
            },
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(str(payload.get("actor")), "svc-runtime")
        self.assertIn("service", payload.get("scopes", []))
        kill_switch = payload.get("kill_switch", {})
        self.assertEqual(bool(kill_switch.get("include_running")), False)
        self.assertEqual(bool(kill_switch.get("include_queued")), True)
        self.assertTrue(isinstance(kill_switch.get("canceled_total"), int))
        receipt = payload.get("action_receipt", {})
        self.assertTrue(bool(receipt.get("signature")))

    def test_admin_can_rotate_identity(self) -> None:
        first = self.client.get("/security/identity", headers=self._auth("admin-token"))
        self.assertEqual(first.status_code, 200)
        first_key = str(first.json()["identity"]["key_id"])

        rotate = self.client.post(
            "/security/identity/rotate",
            headers=self._auth("admin-token"),
            json={"reason": "wave1-hardening"},
        )
        self.assertEqual(rotate.status_code, 200)
        rotate_payload = rotate.json()
        self.assertTrue(bool(rotate_payload["action_receipt"].get("signature")))
        self.assertEqual(
            str(rotate_payload["rotation"]["previous"]["key_id"]),
            first_key,
        )

        second = self.client.get("/security/identity", headers=self._auth("admin-token"))
        self.assertEqual(second.status_code, 200)
        second_key = str(second.json()["identity"]["key_id"])
        self.assertNotEqual(first_key, second_key)

    def test_authn_and_authz_denials_are_audited(self) -> None:
        self.client.get("/models")
        self.client.get("/security/identity", headers=self._auth("user-token"))

        response = self.client.get(
            "/security/audit",
            headers=self._auth("admin-token"),
            params={"status": "failed", "limit": 500},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        event_types = {str(item.get("event_type")) for item in payload["items"]}
        self.assertIn("authn_fail", event_types)
        self.assertIn("authz_deny", event_types)

    def test_cross_tenant_endpoints_are_blocked(self) -> None:
        create_agent = self.client.post(
            "/agents/create",
            headers=self._auth("user-token"),
            json={
                "name": "Tenant1 Agent",
                "system_prompt": "assist",
                "user_id": "user-1",
                "tools": [],
            },
        )
        self.assertEqual(create_agent.status_code, 200)
        agent_id = str(create_agent.json()["id"])

        cross_chat = self.client.post(
            f"/agents/{agent_id}/chat",
            headers=self._auth("user2-token"),
            json={
                "user_id": "user-2",
                "message": "hi",
            },
        )
        self.assertEqual(cross_chat.status_code, 403)
        self.assertEqual(cross_chat.json()["error"]["type"], "permission_denied")

        cross_run = self.client.post(
            f"/agents/{agent_id}/runs",
            headers=self._auth("user2-token"),
            json={
                "user_id": "user-2",
                "message": "run",
            },
        )
        self.assertEqual(cross_run.status_code, 403)
        self.assertEqual(cross_run.json()["error"]["type"], "permission_denied")

        cross_automation = self.client.post(
            "/automations/create",
            headers=self._auth("user2-token"),
            json={
                "agent_id": agent_id,
                "user_id": "user-2",
                "message": "auto",
                "interval_sec": 60,
            },
        )
        self.assertEqual(cross_automation.status_code, 403)
        self.assertEqual(cross_automation.json()["error"]["type"], "permission_denied")

        own_run = self.client.post(
            f"/agents/{agent_id}/runs",
            headers=self._auth("user-token"),
            json={
                "user_id": "user-1",
                "message": "my run",
            },
        )
        self.assertEqual(own_run.status_code, 200)
        run_id = str(own_run.json()["run"]["id"])

        foreign_get = self.client.get(
            f"/agents/runs/{run_id}",
            headers=self._auth("user2-token"),
        )
        self.assertEqual(foreign_get.status_code, 403)
        self.assertEqual(foreign_get.json()["error"]["type"], "permission_denied")

        foreign_cancel = self.client.post(
            f"/agents/runs/{run_id}/cancel",
            headers=self._auth("user2-token"),
        )
        self.assertEqual(foreign_cancel.status_code, 403)
        self.assertEqual(foreign_cancel.json()["error"]["type"], "permission_denied")

        foreign_diagnostics = self.client.get(
            f"/agents/runs/{run_id}/diagnostics",
            headers=self._auth("user2-token"),
        )
        self.assertEqual(foreign_diagnostics.status_code, 403)
        self.assertEqual(foreign_diagnostics.json()["error"]["type"], "permission_denied")

        own_diagnostics = self.client.get(
            f"/agents/runs/{run_id}/diagnostics",
            headers=self._auth("user-token"),
        )
        self.assertEqual(own_diagnostics.status_code, 200)
        own_payload = own_diagnostics.json()
        self.assertEqual(str(own_payload.get("diagnostics", {}).get("run_id")), run_id)

        foreign_package = self.client.get(
            f"/agents/runs/{run_id}/diagnostics/package",
            headers=self._auth("user2-token"),
        )
        self.assertEqual(foreign_package.status_code, 403)
        self.assertEqual(foreign_package.json()["error"]["type"], "permission_denied")

        own_package = self.client.get(
            f"/agents/runs/{run_id}/diagnostics/package",
            headers=self._auth("user-token"),
        )
        self.assertEqual(own_package.status_code, 200)
        own_package_payload = own_package.json()
        self.assertEqual(str(own_package_payload.get("package", {}).get("run", {}).get("run_id")), run_id)

        own_replay_filtered = self.client.get(
            f"/agents/runs/{run_id}/replay",
            headers=self._auth("user-token"),
            params={"stage": "running", "timeline_limit": 10},
        )
        self.assertEqual(own_replay_filtered.status_code, 200)
        replay_payload = own_replay_filtered.json().get("replay", {})
        self.assertEqual(
            replay_payload.get("timeline_filters", {}).get("stages"),
            ["running"],
        )

    def test_high_risk_tool_receipts_include_policy_and_rollback_context(self) -> None:
        session_id = "security-http-authz-high-risk-session"
        arguments = {"echo": "receipt-check"}

        denied = self.client.post(
            "/mcp/tools/dangerous_echo/invoke",
            headers=self._auth("user-token"),
            json={
                "arguments": arguments,
                "user_id": "user-1",
                "session_id": session_id,
            },
        )
        self.assertEqual(denied.status_code, 403)
        denied_payload = denied.json()
        self.assertEqual(denied_payload["error"]["type"], "permission_denied")

        prompts_response = self.client.get(
            "/tools/permissions/prompts",
            headers=self._auth("user-token"),
            params={"status": "pending", "limit": 100},
        )
        self.assertEqual(prompts_response.status_code, 200)
        prompts_payload = prompts_response.json()
        prompt = next(
            (
                item
                for item in prompts_payload.get("items", [])
                if str(item.get("tool_name")) == "dangerous_echo"
                and str(item.get("session_id") or "") == session_id
            ),
            None,
        )
        self.assertIsNotNone(prompt)
        prompt_id = str(prompt.get("id"))
        self.assertTrue(prompt_id)

        approve = self.client.post(
            f"/tools/permissions/prompts/{prompt_id}/approve",
            headers=self._auth("user-token"),
        )
        self.assertEqual(approve.status_code, 200)

        allowed = self.client.post(
            "/mcp/tools/dangerous_echo/invoke",
            headers=self._auth("user-token"),
            json={
                "arguments": arguments,
                "user_id": "user-1",
                "session_id": session_id,
                "permission_id": prompt_id,
            },
        )
        self.assertEqual(allowed.status_code, 200)
        allowed_payload = allowed.json()
        self.assertTrue(bool(allowed_payload.get("action_receipt", {}).get("signature")))
        self.assertEqual(str(allowed_payload.get("result", {}).get("tool")), "dangerous_echo")
        high_risk_action = allowed_payload.get("high_risk_action", {})
        self.assertTrue(bool(high_risk_action.get("high_risk")))
        self.assertEqual(str(high_risk_action.get("risk_level")), "high")
        self.assertEqual(str(high_risk_action.get("policy_level")), "l3")
        self.assertEqual(str(high_risk_action.get("actor")), "user-1")
        self.assertEqual(str(high_risk_action.get("session_id")), session_id)
        self.assertEqual(str(high_risk_action.get("permission_id")), prompt_id)
        self.assertIn("rollback", str(high_risk_action.get("rollback_hint", "")).lower())

        audit = self.client.get(
            "/security/audit",
            headers=self._auth("admin-token"),
            params={"action": "tool_invoke", "limit": 500},
        )
        self.assertEqual(audit.status_code, 200)
        items = audit.json().get("items", [])
        receipt_events = [
            item
            for item in items
            if str(item.get("event_type")) == "high_risk_action_receipt"
            and str(item.get("target_id")) == "dangerous_echo"
        ]
        self.assertTrue(receipt_events)
        failed_event = next((item for item in receipt_events if str(item.get("status")) == "failed"), None)
        self.assertIsNotNone(failed_event)
        succeeded_event = next((item for item in receipt_events if str(item.get("status")) == "succeeded"), None)
        self.assertIsNotNone(succeeded_event)
        failed_details = failed_event.get("details", {})
        succeeded_details = succeeded_event.get("details", {})
        self.assertEqual(str(failed_details.get("policy_level")), "l3")
        self.assertEqual(str(succeeded_details.get("policy_level")), "l3")
        self.assertEqual(str(failed_details.get("actor")), "user-1")
        self.assertEqual(str(succeeded_details.get("actor")), "user-1")
        self.assertEqual(str(succeeded_details.get("permission_id")), prompt_id)
        self.assertIn("rollback", str(failed_details.get("rollback_hint", "")).lower())
        self.assertIn("rollback", str(succeeded_details.get("rollback_hint", "")).lower())
        self.assertTrue(str(failed_details.get("error", "")).strip())


if __name__ == "__main__":
    unittest.main()
