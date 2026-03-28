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

    def test_service_autonomy_circuit_breaker_requires_service_scope(self) -> None:
        denied = self.client.post(
            "/service/runs/autonomy-circuit-breaker",
            headers=self._auth("user-token"),
            json={"action": "arm"},
        )
        self.assertEqual(denied.status_code, 403)
        denied_payload = denied.json()
        self.assertEqual(denied_payload["error"]["type"], "permission_denied")

    def test_service_autonomy_circuit_breaker_success_and_validation(self) -> None:
        invalid_action = self.client.post(
            "/service/runs/autonomy-circuit-breaker",
            headers=self._auth("service-token"),
            json={"action": "freeze"},
        )
        self.assertEqual(invalid_action.status_code, 400)
        invalid_action_payload = invalid_action.json()
        self.assertEqual(invalid_action_payload["error"]["type"], "validation_error")

        invalid_scope = self.client.post(
            "/service/runs/autonomy-circuit-breaker",
            headers=self._auth("service-token"),
            json={
                "action": "arm",
                "apply_kill_switch": True,
                "include_running": False,
                "include_queued": False,
            },
        )
        self.assertEqual(invalid_scope.status_code, 400)
        invalid_scope_payload = invalid_scope.json()
        self.assertEqual(invalid_scope_payload["error"]["type"], "validation_error")

        missing_user_scope = self.client.post(
            "/service/runs/autonomy-circuit-breaker",
            headers=self._auth("service-token"),
            json={
                "action": "arm",
                "scope_type": "user",
            },
        )
        self.assertEqual(missing_user_scope.status_code, 400)
        self.assertEqual(missing_user_scope.json()["error"]["type"], "validation_error")

        invalid_global_extra = self.client.post(
            "/service/runs/autonomy-circuit-breaker",
            headers=self._auth("service-token"),
            json={
                "action": "arm",
                "scope_type": "global",
                "scope_user_id": "user-1",
            },
        )
        self.assertEqual(invalid_global_extra.status_code, 400)
        self.assertEqual(invalid_global_extra.json()["error"]["type"], "validation_error")

        arm = self.client.post(
            "/service/runs/autonomy-circuit-breaker",
            headers=self._auth("service-token"),
            json={
                "action": "arm",
                "reason": "security-http-authz",
                "scope_type": "global",
                "apply_kill_switch": False,
            },
        )
        self.assertEqual(arm.status_code, 200)
        arm_payload = arm.json()
        self.assertEqual(str(arm_payload.get("actor")), "svc-runtime")
        self.assertIn("service", arm_payload.get("scopes", []))
        state = arm_payload.get("circuit_breaker", {})
        self.assertTrue(bool(state.get("armed")))
        self.assertEqual(str(state.get("status")), "armed")
        target_scope = arm_payload.get("circuit_breaker", {}).get("target_scope", {}).get("scope", {})
        self.assertEqual(str(target_scope.get("scope_type")), "global")
        self.assertTrue(bool(arm_payload.get("action_receipt", {}).get("signature")))

        disarm = self.client.post(
            "/service/runs/autonomy-circuit-breaker",
            headers=self._auth("service-token"),
            json={
                "action": "disarm",
                "scope_type": "global",
                "reason": "security-http-authz-cleanup",
            },
        )
        self.assertEqual(disarm.status_code, 200)
        disarm_payload = disarm.json()
        self.assertFalse(bool(disarm_payload.get("circuit_breaker", {}).get("armed")))

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
            params={
                "preset": "errors",
                "stage": "running",
                "status": "failed",
                "failure_class": "timeout",
                "retryable": "true",
                "timeline_limit": 10,
            },
        )
        self.assertEqual(own_replay_filtered.status_code, 200)
        replay_payload = own_replay_filtered.json().get("replay", {})
        self.assertEqual(
            replay_payload.get("timeline_filters", {}).get("stages"),
            ["running"],
        )
        self.assertEqual(
            replay_payload.get("timeline_filters", {}).get("preset"),
            "errors",
        )
        self.assertEqual(
            replay_payload.get("timeline_filters", {}).get("statuses"),
            ["failed"],
        )
        self.assertEqual(
            replay_payload.get("timeline_filters", {}).get("failure_classes"),
            ["timeout"],
        )
        self.assertEqual(
            replay_payload.get("timeline_filters", {}).get("retryable"),
            True,
        )

        invalid_preset = self.client.get(
            f"/agents/runs/{run_id}/replay",
            headers=self._auth("user-token"),
            params={"preset": "unknown"},
        )
        self.assertEqual(invalid_preset.status_code, 400)
        self.assertEqual(invalid_preset.json()["error"]["type"], "validation_error")

    def test_run_event_stream_enforces_owner_and_emits_sse_events(self) -> None:
        create_agent = self.client.post(
            "/agents/create",
            headers=self._auth("user-token"),
            json={
                "name": "Stream Agent",
                "system_prompt": "You stream run updates.",
                "user_id": "user-1",
            },
        )
        self.assertEqual(create_agent.status_code, 200)
        agent_id = str(create_agent.json()["id"])

        own_run = self.client.post(
            f"/agents/{agent_id}/runs",
            headers=self._auth("user-token"),
            json={
                "user_id": "user-1",
                "message": "stream this run",
            },
        )
        self.assertEqual(own_run.status_code, 200)
        run_id = str(own_run.json()["run"]["id"])

        foreign_stream = self.client.get(
            f"/agents/runs/{run_id}/events",
            headers=self._auth("user2-token"),
        )
        self.assertEqual(foreign_stream.status_code, 403)
        self.assertEqual(foreign_stream.json()["error"]["type"], "permission_denied")

        frames: list[str] = []
        with self.client.stream(
            "GET",
            f"/agents/runs/{run_id}/events",
            headers=self._auth("user-token"),
            params={
                "poll_interval_ms": 50,
                "timeout_sec": 8,
                "include_snapshot": "true",
                "include_heartbeat": "false",
            },
        ) as stream:
            self.assertEqual(stream.status_code, 200)
            self.assertIn("text/event-stream", str(stream.headers.get("content-type", "")))
            for line in stream.iter_lines():
                if not line:
                    continue
                decoded = line.decode("utf-8") if isinstance(line, bytes) else str(line)
                if not decoded.startswith("data: "):
                    continue
                data = decoded[6:]
                frames.append(data)
                if data == "[DONE]":
                    break

        self.assertIn("[DONE]", frames)
        events = [json.loads(item) for item in frames if item != "[DONE]"]
        event_types = {str(item.get("event")) for item in events if isinstance(item, dict)}
        self.assertIn("snapshot", event_types)
        self.assertIn("checkpoint", event_types)
        self.assertIn("done", event_types)
        done_event = next(
            (item for item in events if isinstance(item, dict) and str(item.get("event")) == "done"),
            None,
        )
        self.assertIsNotNone(done_event)
        assert isinstance(done_event, dict)
        self.assertEqual(str(done_event.get("run_id")), run_id)

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

    def test_terminal_action_receipts_are_persisted_and_scoped(self) -> None:
        session_id = "security-http-authz-terminal-receipt-session"
        arguments = {"code": "print('terminal receipt')", "timeout": 2}

        denied = self.client.post(
            "/mcp/tools/python_exec/invoke",
            headers=self._auth("user-token"),
            json={
                "arguments": arguments,
                "user_id": "user-1",
                "session_id": session_id,
            },
        )
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(denied.json()["error"]["type"], "permission_denied")

        prompts_response = self.client.get(
            "/tools/permissions/prompts",
            headers=self._auth("user-token"),
            params={"status": "pending", "limit": 100},
        )
        self.assertEqual(prompts_response.status_code, 200)
        prompt = next(
            (
                item
                for item in prompts_response.json().get("items", [])
                if str(item.get("tool_name")) == "python_exec"
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
            "/mcp/tools/python_exec/invoke",
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

        owner_receipts = self.client.get(
            "/tools/actions/terminal",
            headers=self._auth("user-token"),
            params={
                "session_id": session_id,
                "tool_name": "python_exec",
                "limit": 200,
            },
        )
        self.assertEqual(owner_receipts.status_code, 200)
        owner_items = owner_receipts.json().get("items", [])
        self.assertGreaterEqual(len(owner_items), 2)

        failed_item = next((item for item in owner_items if str(item.get("status")) == "failed"), None)
        self.assertIsNotNone(failed_item)
        assert isinstance(failed_item, dict)
        self.assertTrue(str(failed_item.get("error_message", "")).strip())

        succeeded_item = next((item for item in owner_items if str(item.get("status")) == "succeeded"), None)
        self.assertIsNotNone(succeeded_item)
        assert isinstance(succeeded_item, dict)
        self.assertEqual(str(succeeded_item.get("tool_name")), "python_exec")
        self.assertEqual(str(succeeded_item.get("actor")), "user-1")
        self.assertEqual(str(succeeded_item.get("user_id")), "user-1")
        self.assertEqual(str(succeeded_item.get("policy_level")), "l3")
        self.assertIn("revert", str(succeeded_item.get("rollback_hint", "")).lower())
        self.assertTrue(bool(succeeded_item.get("action_receipt", {}).get("signature")))
        terminal_details = succeeded_item.get("details", {}).get("terminal_action", {})
        self.assertEqual(str(terminal_details.get("tool_name")), "python_exec")
        self.assertEqual(str(terminal_details.get("policy_level")), "l3")

        cross_user = self.client.get(
            "/tools/actions/terminal",
            headers=self._auth("user2-token"),
            params={
                "session_id": session_id,
                "tool_name": "python_exec",
                "limit": 200,
            },
        )
        self.assertEqual(cross_user.status_code, 200)
        self.assertEqual(int(cross_user.json().get("count", -1)), 0)

        unauthorized_scope = self.client.get(
            "/tools/actions/terminal",
            headers=self._auth("user-token"),
            params={"user_id": "user-2"},
        )
        self.assertEqual(unauthorized_scope.status_code, 403)
        self.assertEqual(unauthorized_scope.json()["error"]["type"], "permission_denied")

        admin_view = self.client.get(
            "/tools/actions/terminal",
            headers=self._auth("admin-token"),
            params={
                "user_id": "user-1",
                "session_id": session_id,
                "tool_name": "python_exec",
                "limit": 200,
            },
        )
        self.assertEqual(admin_view.status_code, 200)
        self.assertGreaterEqual(int(admin_view.json().get("count", 0)), 2)

    def test_filesystem_patch_preview_requires_owner_approval_and_permission(self) -> None:
        session_id = "security-http-authz-fs-patch-session"
        with tempfile.TemporaryDirectory(prefix="amaryllis-http-fs-patch-", dir=Path.cwd()) as tmp:
            target = Path(tmp) / "notes.txt"
            target.write_text("before\n", encoding="utf-8")

            preview = self.client.post(
                "/tools/actions/filesystem/patches/preview",
                headers=self._auth("user-token"),
                json={
                    "path": str(target),
                    "content": "after\n",
                    "user_id": "user-1",
                    "session_id": session_id,
                },
            )
            self.assertEqual(preview.status_code, 200)
            preview_payload = preview.json()
            preview_item = preview_payload.get("preview", {})
            preview_id = str(preview_item.get("id") or "")
            self.assertTrue(preview_id)
            self.assertEqual(str(preview_item.get("status")), "pending")
            diff_summary = preview_item.get("diff", {}).get("summary", {})
            self.assertTrue(bool(diff_summary.get("changed")))
            self.assertEqual(str(diff_summary.get("path")), str(preview_item.get("path")))

            foreign_get = self.client.get(
                f"/tools/actions/filesystem/patches/{preview_id}",
                headers=self._auth("user2-token"),
            )
            self.assertEqual(foreign_get.status_code, 403)
            self.assertEqual(foreign_get.json()["error"]["type"], "permission_denied")

            approve = self.client.post(
                f"/tools/actions/filesystem/patches/{preview_id}/approve",
                headers=self._auth("user-token"),
            )
            self.assertEqual(approve.status_code, 200)
            self.assertEqual(str(approve.json().get("preview", {}).get("status")), "approved")

            apply_denied = self.client.post(
                f"/tools/actions/filesystem/patches/{preview_id}/apply",
                headers=self._auth("user-token"),
                json={},
            )
            self.assertEqual(apply_denied.status_code, 403)
            self.assertEqual(apply_denied.json()["error"]["type"], "permission_denied")

            prompts_response = self.client.get(
                "/tools/permissions/prompts",
                headers=self._auth("user-token"),
                params={"status": "pending", "limit": 200},
            )
            self.assertEqual(prompts_response.status_code, 200)
            prompt = next(
                (
                    item
                    for item in prompts_response.json().get("items", [])
                    if str(item.get("tool_name")) == "filesystem"
                    and str(item.get("session_id") or "") == session_id
                ),
                None,
            )
            self.assertIsNotNone(prompt)
            prompt_id = str(prompt.get("id"))
            self.assertTrue(prompt_id)

            prompt_approve = self.client.post(
                f"/tools/permissions/prompts/{prompt_id}/approve",
                headers=self._auth("user-token"),
            )
            self.assertEqual(prompt_approve.status_code, 200)

            apply_ok = self.client.post(
                f"/tools/actions/filesystem/patches/{preview_id}/apply",
                headers=self._auth("user-token"),
                json={"permission_id": prompt_id},
            )
            self.assertEqual(apply_ok.status_code, 200)
            apply_payload = apply_ok.json()
            self.assertEqual(str(apply_payload.get("preview", {}).get("status")), "applied")
            self.assertTrue(bool(apply_payload.get("action_receipt", {}).get("signature")))
            self.assertEqual(target.read_text(encoding="utf-8"), "after\n")

            own_item = self.client.get(
                f"/tools/actions/filesystem/patches/{preview_id}",
                headers=self._auth("user-token"),
            )
            self.assertEqual(own_item.status_code, 200)
            self.assertEqual(str(own_item.json().get("preview", {}).get("status")), "applied")

            cross_list = self.client.get(
                "/tools/actions/filesystem/patches",
                headers=self._auth("user2-token"),
                params={"session_id": session_id, "limit": 200},
            )
            self.assertEqual(cross_list.status_code, 200)
            self.assertEqual(int(cross_list.json().get("count", -1)), 0)

            unauthorized_scope = self.client.get(
                "/tools/actions/filesystem/patches",
                headers=self._auth("user-token"),
                params={"user_id": "user-2"},
            )
            self.assertEqual(unauthorized_scope.status_code, 403)
            self.assertEqual(unauthorized_scope.json()["error"]["type"], "permission_denied")


if __name__ == "__main__":
    unittest.main()
