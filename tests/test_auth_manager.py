from __future__ import annotations

import unittest
from types import SimpleNamespace

from runtime.auth import AuthManager, AuthTokenSpec, assert_owner, resolve_user_id
from runtime.errors import AuthenticationError, PermissionDeniedError


def _request_with_headers(headers: dict[str, str]) -> SimpleNamespace:
    return SimpleNamespace(headers=headers)


class AuthManagerTests(unittest.TestCase):
    def test_authenticate_bearer_token(self) -> None:
        manager = AuthManager(
            enabled=True,
            token_specs=(
                AuthTokenSpec(token="token-1", user_id="user-1", scopes=("user",)),
            ),
        )
        request = _request_with_headers({"authorization": "Bearer token-1"})

        context = manager.authenticate_request(request)

        self.assertEqual(context.user_id, "user-1")
        self.assertFalse(context.is_admin)

    def test_authenticate_rejects_missing_token(self) -> None:
        manager = AuthManager(
            enabled=True,
            token_specs=(
                AuthTokenSpec(token="token-1", user_id="user-1", scopes=("user",)),
            ),
        )
        request = _request_with_headers({})

        with self.assertRaises(AuthenticationError):
            manager.authenticate_request(request)

    def test_resolve_user_id_blocks_cross_user_for_non_admin(self) -> None:
        manager = AuthManager(
            enabled=True,
            token_specs=(
                AuthTokenSpec(token="token-1", user_id="user-1", scopes=("user",)),
            ),
        )
        request = _request_with_headers({"authorization": "Bearer token-1"})
        context = manager.authenticate_request(request)

        with self.assertRaises(PermissionDeniedError):
            resolve_user_id(request_user_id="user-2", auth=context)

    def test_admin_can_impersonate_and_pass_owner_check(self) -> None:
        manager = AuthManager(
            enabled=True,
            token_specs=(
                AuthTokenSpec(token="admin-token", user_id="admin", scopes=("admin", "user")),
            ),
        )
        request = _request_with_headers({"authorization": "Bearer admin-token"})
        context = manager.authenticate_request(request)

        resolved = resolve_user_id(request_user_id="user-2", auth=context)
        self.assertEqual(resolved, "user-2")
        assert_owner(owner_user_id="user-3", auth=context, resource_name="run", resource_id="run-1")

    def test_service_scope_is_present_but_cannot_cross_user(self) -> None:
        manager = AuthManager(
            enabled=True,
            token_specs=(
                AuthTokenSpec(token="service-token", user_id="svc-runtime", scopes=("service",)),
            ),
        )
        request = _request_with_headers({"authorization": "Bearer service-token"})
        context = manager.authenticate_request(request)

        self.assertTrue(context.is_service)
        self.assertFalse(context.is_admin)
        self.assertFalse(context.is_user)
        self.assertTrue(context.has_any_scope("service", "admin"))

        resolved = resolve_user_id(request_user_id=None, auth=context)
        self.assertEqual(resolved, "svc-runtime")
        with self.assertRaises(PermissionDeniedError):
            resolve_user_id(request_user_id="user-2", auth=context)


if __name__ == "__main__":
    unittest.main()
