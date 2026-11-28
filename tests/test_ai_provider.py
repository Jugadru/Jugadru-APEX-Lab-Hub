import unittest
from dataclasses import FrozenInstanceError
from hub import ai_provider as p

class ProviderInterfaceTests(unittest.TestCase):
    def test_offline_catalog(self):
        c=p.catalog()
        self.assertEqual(c["schema"],1)
        self.assertIsNone(c["default_provider"])
        self.assertFalse(c["external_agents_connected"])
        self.assertFalse(c["automatic_fallback"])
        self.assertTrue(c["approval_required"])
        self.assertEqual({r["id"] for r in c["providers"] if r["enabled"]},{"mcp_stdio","manual_ai"})
        for r in c["providers"]:
            self.assertFalse(r["execution_authorized"])
            self.assertFalse(r["identity_verified"])
            self.assertFalse(r["host_cost_enforced"])
            self.assertEqual(r["hub_paid_model_calls"],0)
            self.assertEqual(r["hub_network_calls"],0)
            self.assertEqual(r["response_authority"],"advisory_only")
            if not r["enabled"]:self.assertEqual(r["goals"],[])
        c["providers"][0]["enabled"]=False
        self.assertTrue(p.catalog()["providers"][0]["enabled"])

    def test_exact_selection_and_no_fallback(self):
        for provider in ("mcp_stdio","manual_ai"):
            for goal in p.GOALS:
                self.assertEqual(p.resolve(provider,goal).id,provider)
        for provider in ("chatgpt","codex","local_ai","remote_mcp","openai_api","other_api",
                         "MCP_STDIO"," mcp_stdio","",None,{},[]):
            with self.subTest(provider=provider),self.assertRaises(ValueError):
                p.resolve(provider,"review_evidence")
        for goal in ("execute","",None,[],{}):
            with self.assertRaises(ValueError):p.resolve("manual_ai",goal)

    def test_profile_is_immutable(self):
        with self.assertRaises(FrozenInstanceError):
            p.resolve("manual_ai","review_plan").enabled=False
