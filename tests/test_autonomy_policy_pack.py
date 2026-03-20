from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from tools.autonomy_policy_pack import (
    AutonomyPolicyPackError,
    default_policy_pack_path,
    load_autonomy_policy_pack,
)


class AutonomyPolicyPackTests(unittest.TestCase):
    def test_default_pack_loads_and_exposes_expected_rule(self) -> None:
        pack = load_autonomy_policy_pack(default_policy_pack_path())
        self.assertEqual(pack.schema_version, 1)
        rule = pack.rule(level="l3", risk_level="high")
        self.assertTrue(rule.allow)
        self.assertTrue(rule.requires_approval)
        self.assertEqual(rule.approval_scope, "request")
        self.assertGreaterEqual(int(rule.approval_ttl_sec or 0), 1)

    def test_loader_rejects_missing_levels(self) -> None:
        payload = {
            "schema_version": 1,
            "pack": "broken",
            "description": "broken policy pack",
            "rules": {
                "l0": {
                    "low": {"allow": False, "requires_approval": False, "reason": "blocked"},
                    "medium": {"allow": False, "requires_approval": False, "reason": "blocked"},
                    "high": {"allow": False, "requires_approval": False, "reason": "blocked"},
                    "critical": {"allow": False, "requires_approval": False, "reason": "blocked"},
                }
            },
        }
        with tempfile.TemporaryDirectory(prefix="amaryllis-autonomy-pack-tests-") as tmp:
            path = Path(tmp) / "broken.json"
            path.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(AutonomyPolicyPackError, "missing rules for level"):
                load_autonomy_policy_pack(path)

    def test_loader_rejects_invalid_approval_rule(self) -> None:
        payload = {
            "schema_version": 1,
            "pack": "broken-approval",
            "description": "broken approval contract",
            "rules": {},
        }
        default_pack = load_autonomy_policy_pack(default_policy_pack_path())
        for level, rules in default_pack.levels.items():
            payload["rules"][level] = {}
            for risk, rule in rules.items():
                payload["rules"][level][risk] = {
                    "allow": bool(rule.allow),
                    "requires_approval": bool(rule.requires_approval),
                    "reason": rule.reason,
                    "approval_scope": rule.approval_scope,
                    "approval_ttl_sec": rule.approval_ttl_sec,
                }
        payload["rules"]["l2"]["medium"] = {
            "allow": True,
            "requires_approval": True,
            "reason": "approval required",
        }

        with tempfile.TemporaryDirectory(prefix="amaryllis-autonomy-pack-tests-") as tmp:
            path = Path(tmp) / "broken-approval.json"
            path.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(AutonomyPolicyPackError, "approval_scope"):
                load_autonomy_policy_pack(path)


if __name__ == "__main__":
    unittest.main()
