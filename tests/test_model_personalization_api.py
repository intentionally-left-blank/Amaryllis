from __future__ import annotations

import hashlib
import hmac
import importlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    from fastapi.testclient import TestClient
except Exception:  # pragma: no cover
    TestClient = None  # type: ignore[assignment]


@unittest.skipIf(TestClient is None, "fastapi dependency is not available")
class ModelPersonalizationAPITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory(prefix="amaryllis-tests-model-personalization-api-")
        support_dir = Path(cls._tmp.name) / "support"
        auth_tokens = {
            "admin-token": {
                "user_id": "admin",
                "scopes": ["admin", "user"],
            }
        }
        cls._signing_key = "adapter-signing-key-fixture"
        cls._key_id = "adapter-kid-fixture"
        cls._env_patch = patch.dict(
            os.environ,
            {
                "AMARYLLIS_SUPPORT_DIR": str(support_dir),
                "AMARYLLIS_AUTH_ENABLED": "true",
                "AMARYLLIS_AUTH_TOKENS": json.dumps(auth_tokens, ensure_ascii=False),
                "AMARYLLIS_MEMORY_CONSOLIDATION_ENABLED": "false",
                "AMARYLLIS_MCP_ENDPOINTS": "",
                "AMARYLLIS_SECURITY_PROFILE": "production",
                "AMARYLLIS_DEFAULT_PROVIDER": "mlx",
                "AMARYLLIS_DEFAULT_MODEL": "mlx-community/Qwen2.5-1.5B-Instruct-4bit",
                "AMARYLLIS_ADAPTER_SIGNING_KEY": cls._signing_key,
                "AMARYLLIS_ADAPTER_KEY_ID": cls._key_id,
            },
            clear=False,
        )
        cls._env_patch.start()

        import runtime.server as server_module

        cls.server_module = importlib.reload(server_module)
        cls.client_cm = TestClient(cls.server_module.app)
        cls.client = cls.client_cm.__enter__()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client_cm.__exit__(None, None, None)
        cls._env_patch.stop()
        cls._tmp.cleanup()

    @staticmethod
    def _auth() -> dict[str, str]:
        return {"Authorization": "Bearer admin-token"}

    @classmethod
    def _signature(
        cls,
        *,
        user_id: str,
        adapter_id: str,
        base_package_id: str,
        artifact_sha256: str,
        recipe_id: str,
        metadata: dict[str, object],
    ) -> dict[str, str]:
        unsigned_payload = {
            "adapter_id": adapter_id,
            "artifact_sha256": artifact_sha256,
            "base_package_id": base_package_id,
            "metadata": metadata,
            "recipe_id": recipe_id,
            "user_id": user_id,
        }
        canonical = json.dumps(unsigned_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        signature = hmac.new(
            cls._signing_key.encode("utf-8"),
            canonical.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return {
            "algorithm": "hmac-sha256",
            "key_id": cls._key_id,
            "value": signature,
            "trust_level": "managed",
        }

    def test_personalization_contract_endpoint_exposes_policy(self) -> None:
        response = self.client.get("/models/personalization/contract", headers=self._auth())
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(str(payload.get("contract_version")), "personalization_adapter_contract_v1")
        self.assertIn("policy", payload)
        self.assertIn("request_id", payload)

    def test_register_and_list_personalization_adapter(self) -> None:
        user_id = "admin"
        adapter_id = "adapter-v1"
        base_package_id = "mlx::adapter-base"
        artifact_sha256 = "a" * 64
        recipe_id = "adapter-recipe-v1"
        metadata = {"domain": "assistant", "style": "concise"}
        signature = self._signature(
            user_id=user_id,
            adapter_id=adapter_id,
            base_package_id=base_package_id,
            artifact_sha256=artifact_sha256,
            recipe_id=recipe_id,
            metadata=metadata,
        )

        register = self.client.post(
            "/models/personalization/adapters/register",
            headers=self._auth(),
            json={
                "user_id": user_id,
                "adapter_id": adapter_id,
                "base_package_id": base_package_id,
                "artifact_sha256": artifact_sha256,
                "recipe_id": recipe_id,
                "metadata": metadata,
                "signature": signature,
                "activate": True,
            },
        )
        self.assertEqual(register.status_code, 200)
        register_payload = register.json()
        self.assertEqual(str(register_payload.get("status")), "activated")
        self.assertIn("adapter", register_payload)
        self.assertIn("request_id", register_payload)
        self.assertTrue(bool((register_payload.get("action_receipt") or {}).get("signature")))

        listed = self.client.get(
            "/models/personalization/adapters",
            params={"user_id": user_id, "base_package_id": base_package_id},
            headers=self._auth(),
        )
        self.assertEqual(listed.status_code, 200)
        listed_payload = listed.json()
        self.assertEqual(int(listed_payload.get("count", 0)), 1)
        active_by_base = listed_payload.get("active_by_base_package", {})
        self.assertEqual(str(active_by_base.get(base_package_id)), adapter_id)

    def test_activate_and_rollback_personalization_stack(self) -> None:
        user_id = "admin"
        base_package_id = "mlx::adapter-base-rollback"

        for adapter_id in ("adapter-r1", "adapter-r2"):
            artifact_sha256 = (adapter_id[0] * 64)[:64]
            recipe_id = f"recipe-{adapter_id}"
            metadata = {"adapter_id": adapter_id}
            signature = self._signature(
                user_id=user_id,
                adapter_id=adapter_id,
                base_package_id=base_package_id,
                artifact_sha256=artifact_sha256,
                recipe_id=recipe_id,
                metadata=metadata,
            )
            response = self.client.post(
                "/models/personalization/adapters/register",
                headers=self._auth(),
                json={
                    "user_id": user_id,
                    "adapter_id": adapter_id,
                    "base_package_id": base_package_id,
                    "artifact_sha256": artifact_sha256,
                    "recipe_id": recipe_id,
                    "metadata": metadata,
                    "signature": signature,
                    "activate": True,
                },
            )
            self.assertEqual(response.status_code, 200)

        rollback = self.client.post(
            "/models/personalization/adapters/rollback",
            headers=self._auth(),
            json={"user_id": user_id, "base_package_id": base_package_id},
        )
        self.assertEqual(rollback.status_code, 200)
        payload = rollback.json()
        self.assertEqual(str(payload.get("status")), "rolled_back")
        self.assertEqual(str((payload.get("active_adapter") or {}).get("adapter_id")), "adapter-r1")
        self.assertEqual(str((payload.get("rolled_back_adapter") or {}).get("adapter_id")), "adapter-r2")
        self.assertIn("request_id", payload)

    def test_register_rejects_signature_mismatch(self) -> None:
        response = self.client.post(
            "/models/personalization/adapters/register",
            headers=self._auth(),
            json={
                "user_id": "admin",
                "adapter_id": "adapter-bad-signature",
                "base_package_id": "mlx::adapter-base-invalid",
                "artifact_sha256": "f" * 64,
                "recipe_id": "recipe-invalid",
                "metadata": {"test": True},
                "signature": {
                    "algorithm": "hmac-sha256",
                    "key_id": self._key_id,
                    "value": "0" * 64,
                    "trust_level": "managed",
                },
                "activate": False,
            },
        )
        self.assertEqual(response.status_code, 400)
        payload = response.json()
        self.assertIn("signature", str(payload.get("error", "")).lower())


if __name__ == "__main__":
    unittest.main()
