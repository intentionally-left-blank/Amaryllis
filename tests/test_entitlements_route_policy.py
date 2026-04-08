from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from runtime.config import AppConfig
from runtime.entitlements import (
    ENTITLEMENT_ERROR_CONTRACT_VERSION,
    ENTITLEMENT_ROUTE_POLICY_VERSION,
    EntitlementResolver,
)
from runtime.provider_sessions import ProviderSessionManager
from storage.database import Database


class EntitlementRoutePolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="amaryllis-tests-entitlement-route-policy-")
        self._original_env = os.environ.copy()
        base = Path(self._tmp.name)

        os.environ["AMARYLLIS_SUPPORT_DIR"] = str(base / "support")
        os.environ["AMARYLLIS_DATA_DIR"] = str(base / "support" / "data")
        os.environ["AMARYLLIS_MODELS_DIR"] = str(base / "support" / "models")
        os.environ["AMARYLLIS_DATABASE_PATH"] = str(base / "support" / "data" / "state.db")
        os.environ["AMARYLLIS_OPENAI_API_KEY"] = ""
        os.environ["AMARYLLIS_ANTHROPIC_API_KEY"] = ""
        os.environ["AMARYLLIS_OPENROUTER_API_KEY"] = ""
        os.environ["AMARYLLIS_AUTH_TOKENS"] = "token-1:user-1:user"

        self.config = AppConfig.from_env()
        self.config.ensure_directories()
        self.database = Database(self.config.database_path)
        self.sessions = ProviderSessionManager(database=self.database)
        self.resolver = EntitlementResolver(config=self.config, database=self.database)

    def tearDown(self) -> None:
        self.database.close()
        os.environ.clear()
        os.environ.update(self._original_env)
        self._tmp.cleanup()

    def test_route_policy_prefers_user_session_and_keeps_server_key_fallback(self) -> None:
        self.sessions.create_session(
            user_id="user-1",
            provider="openai",
            credential_ref="secret://vault/openai/user-1",
            scopes=["chat"],
        )

        with patch.object(self.resolver, "_server_key_available", return_value=True):
            payload = self.resolver.resolve_provider(user_id="user-1", provider="openai")

        route_policy = payload.get("route_policy", {})
        self.assertEqual(str(route_policy.get("version")), ENTITLEMENT_ROUTE_POLICY_VERSION)
        self.assertEqual(str(route_policy.get("selected_route")), "user_session")
        self.assertIn("server_api_key", list(route_policy.get("fallback_routes") or []))
        self.assertEqual(str(payload.get("access_mode")), "user_session")
        self.assertTrue(bool(payload.get("available")))
        error_contract = payload.get("error_contract", {})
        self.assertEqual(str(error_contract.get("status")), "ok")

    def test_route_policy_returns_explicit_error_contract_when_missing_access(self) -> None:
        payload = self.resolver.resolve_provider(user_id="user-1", provider="openai")
        self.assertFalse(bool(payload.get("available")))
        self.assertEqual(str(payload.get("access_mode")), "none")

        route_policy = payload.get("route_policy", {})
        self.assertEqual(str(route_policy.get("version")), ENTITLEMENT_ROUTE_POLICY_VERSION)
        self.assertEqual(str(route_policy.get("selected_route")), "none")
        self.assertEqual(list(route_policy.get("available_routes") or []), [])

        error_contract = payload.get("error_contract", {})
        self.assertEqual(str(error_contract.get("version")), ENTITLEMENT_ERROR_CONTRACT_VERSION)
        self.assertEqual(str(error_contract.get("status")), "error")
        self.assertEqual(str(error_contract.get("error_code")), "provider_access_not_configured")
        self.assertEqual(int(error_contract.get("http_status") or 0), 403)
        self.assertTrue(list(error_contract.get("next_actions") or []))


if __name__ == "__main__":
    unittest.main()
