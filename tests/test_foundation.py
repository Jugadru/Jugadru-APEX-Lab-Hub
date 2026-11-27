import copy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from hub import orchestrator as lab
from hub import foundation as fd
from test_orchestrator import repo

# Synthetic provider, not copied private Foundation content.
VALID = {"status": "PASS", "scope": "structural metadata only", "modules": 1,
         "capabilities": 2, "active_contracts": 1, "compatibility_pairs": 1,
         "catalog": {"status": "PASS", "scope": "catalog structure only",
                     "references": 198, "possibilities": 120, "domains": 8}}
CAP = {"id": "foundation.validate", "module": "foundation", "version": "1.0.0",
       "inputs": [], "outputs": [], "method": "python3 tools/apex_foundation_registry.py validate",
       "permissions": ["read.repository"], "restrictions": []}
OTHER = {**CAP, "id": "asset.plan", "method": "touch NEVER_EXECUTE"}
META = {"foundation_version": "0.1.0", "modules": [{"id": "foundation"}],
        "capabilities": [CAP, OTHER], "compatibility": [{}], "contracts": [{"status": "active"}]}


def manifest():
    return json.loads((Path(__file__).resolve().parents[1] / "contracts/foundation.example.json").read_text())


class FoundationTests(unittest.TestCase):
    def test_metadata_never_authorizes_execution(self):
        result = fd.discovery(META, fd.validate(VALID), "a"*40)
        caps = {r["id"]: r for r in result["capabilities"]}
        self.assertEqual(caps["foundation.validate"]["state"], "ADAPTER_MATCH")
        self.assertEqual(caps["asset.plan"]["state"], "DECLARED_ONLY")
        self.assertTrue(all(not row["execution_authorized"] for row in caps.values()))
        changed = copy.deepcopy(META)
        changed["capabilities"][0]["method"] = "touch NEVER_EXECUTE"
        result = fd.discovery(changed, fd.validate(VALID), "a"*40)
        self.assertTrue(all(row["hub_adapter"] is None for row in result["capabilities"]))

    def test_reject_bad_metadata(self):
        for change in ("version", "duplicate", "module", "count"):
            m = copy.deepcopy(META)
            if change == "version":
                m["foundation_version"] = "9.9.9"
            elif change == "duplicate":
                m["capabilities"][1]["id"] = m["capabilities"][0]["id"]
            elif change == "module":
                m["capabilities"][1]["module"] = "unknown"
            else:
                m["capabilities"].pop()
            with self.subTest(change=change), self.assertRaises(ValueError):
                fd.discovery(m, fd.validate(VALID), "a"*40)
        bad = copy.deepcopy(VALID)
        bad["catalog"]["references"] = 197
        with self.assertRaises(ValueError):
            fd.validate(bad)

    def test_dependency_and_permissions(self):
        m = manifest()
        self.assertEqual(len(lab.plan(m, "read")), 2)
        m["tasks"][1]["depends_on"] = []
        with self.assertRaises(ValueError):
            lab.plan(m, "read")
        m = manifest()
        m["tasks"][1]["capability"] = "asset.plan"
        with self.assertRaises(ValueError):
            lab.plan(m, "read")

    def test_bounded_duplicate_json(self):
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp) / "log"
            for data in ('{"a":1,"a":2}', '{"a":NaN}', " " * (fd.LIMIT + 1)):
                p.write_text(data)
                with self.assertRaises(ValueError):
                    fd.read(p)

    def execute(self, invalid=False, missing=False):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target = root / "foundation"
            bindings = []
            if not missing:
                b = repo(target, "foundation")
                for path in fd.REQUIRED:
                    p = target / path
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_text("{}")
                valid = {**VALID, "status": "FAIL"} if invalid else VALID
                script = target / "tools/apex_foundation_registry.py"
                script.write_text("import json,sys\nprint(json.dumps(" + repr(valid) +
                                  " if sys.argv[1]=='validate' else " + repr(META) + "))\n")
                subprocess.run(["git", "-C", str(target), "add", "."], check=True)
                subprocess.run(["git", "-C", str(target), "commit", "-qm", "synthetic provider"], check=True)
                b[2] = lab.git(target, "rev-parse", "HEAD")
                bindings = [b]
            report = lab.run(manifest(), "read", lab.checked_targets(bindings),
                             root / "evidence", registry_path=root / "registry")
            if not invalid and not missing:
                meta = json.loads((root / "evidence/discover/foundation-capabilities.json").read_text())
                self.assertEqual(meta["foundation_sha"], bindings[0][2])
                self.assertFalse((target / "NEVER_EXECUTE").exists())
                self.assertTrue(any(e["path"].endswith("foundation-capabilities.json")
                                    for e in report["tasks"][1]["evidence"]))
            return report

    def test_fixture_real_subprocess_and_registry(self):
        self.assertEqual(self.execute()["status"], "PASS")

    def test_invalid_provider_blocks_discovery(self):
        result = self.execute(invalid=True)
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["tasks"][1]["status"], "BLOCKED")

    def test_missing_private_checkout(self):
        result = self.execute(missing=True)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertTrue(all(t["attempts"] == 0 for t in result["tasks"]))


if __name__ == "__main__":
    unittest.main()
