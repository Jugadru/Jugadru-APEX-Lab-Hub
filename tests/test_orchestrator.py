import copy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from hub import orchestrator as lab


def repo(root, name):
    root.mkdir()
    def git(*args):
        return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()
    git("init", "-q")
    git("config", "user.email", "fixture@example.invalid")
    git("config", "user.name", "Fixture")
    git("remote", "add", "origin", "https://github.com/" + lab.REPOSITORIES[name] + ".git")
    (root / "README.md").write_text("fixture")
    if name == "game":
        (root / "tools").mkdir()
        for cap in ("source.inventory", "architecture.audit"):
            (root / lab.CAPABILITIES[cap]["script"]).write_text(
                'import pathlib, sys\npathlib.Path(sys.argv[-1]).write_text("{}")\n')
    git("add", ".")
    git("commit", "-qm", "fixture")
    return [name, str(root), git("rev-parse", "HEAD")]


class OrchestrationTests(unittest.TestCase):
    def test_plan(self):
        m = lab.default_manifest()
        m["tasks"].reverse()
        self.assertEqual(lab.plan(m, "read")[0]["id"], "hub-revision")

    def test_rejections(self):
        for field, value in (("depends_on", ["missing"]), ("depends_on", ["inventory"]),
                             ("capability", "shell"), ("timeout_seconds", 999),
                             ("timeout_seconds", True), ("permissions", ["write"]),
                             ("acceptance", "anything"), ("id", "../escape"), ("command", "ls"),
                             ("target", "arbitrary")):
            m = lab.default_manifest()
            m["tasks"][0][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                lab.plan(m, "read")
        m = lab.default_manifest()
        m["tasks"].append(copy.deepcopy(m["tasks"][0]))
        with self.assertRaises(ValueError):
            lab.plan(m, "read")
        with self.assertRaises(ValueError):
            lab.plan(lab.default_manifest(), "publication")
        m = lab.default_manifest()
        m["tasks"][1]["target"] = "hyper"
        with self.assertRaises(ValueError):
            lab.plan(m, "read")

    def test_duplicate_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "input.json"
            p.write_text('{"schema":2,"schema":2,"tasks":[]}')
            with self.assertRaises(ValueError):
                lab.read_manifest(p)

    def test_checkout_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "game"
            binding = repo(root, "game")
            self.assertEqual(lab.checked_targets([binding])["game"][1], binding[2])
            with self.assertRaises(ValueError):
                lab.checked_targets([[*binding[:2], "a" * 40]])
            with self.assertRaises(ValueError):
                lab.checked_targets([["hyper", *binding[1:]]])
            (root / "untracked.txt").touch()
            with self.assertRaises(ValueError):
                lab.checked_targets([binding])

    def execute(self, first=None, catalog=False, missing=False):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bindings = [repo(root / name, name) for name in ("hub", "game", "hyper") if not (missing and name == "hyper")]
            targets = lab.checked_targets(bindings)
            calls = []
            def runner(argv, log, timeout, cwd):
                calls.append(argv)
                log.write_text("controlled failure")
                return {"status": first}
            m = lab.default_manifest()
            if catalog:
                m["tasks"][1].update(capability="foundation.catalog", acceptance="catalog_pass", timeout_seconds=30)
            out = root / "evidence"
            result = lab.run(m, "read", targets, out, runner if first else None)
            self.assertEqual(result, json.loads((out / "report.json").read_text()))
            for task in result["tasks"]:
                for evidence in task["evidence"]:
                    self.assertEqual(evidence["sha256"], lab.digest(out / evidence["path"]))
            return result, calls

    def test_real_controlled_subprocess(self):
        result, _ = self.execute()
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(len(result["targets"]), 3)

    def test_failure_blocks_only_dependents(self):
        result, calls = self.execute("FAIL")
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(len(calls), 1)
        tasks = {t["id"]: t for t in result["tasks"]}
        self.assertEqual(tasks["architecture"]["status"], "BLOCKED")
        self.assertEqual(tasks["hyper-revision"]["status"], "PASS")

    def test_missing_catalog_and_target(self):
        for kwargs in ({"catalog": True}, {"missing": True}):
            result, _ = self.execute(**kwargs)
            self.assertEqual(result["status"], "BLOCKED")

    def test_timeout(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            import sys
            result = lab.run_case([sys.executable, "-c", "import time; time.sleep(5)"], root / "log", 0.1, root)
            self.assertEqual(result["status"], "BLOCKED")


if __name__ == "__main__":
    unittest.main()
