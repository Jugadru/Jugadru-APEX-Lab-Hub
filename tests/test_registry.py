import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from hub import orchestrator as lab
from hub.registry import Registry
from test_orchestrator import repo


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root / "registry"
        self.store = Registry(self.path)

    def sample(self):
        return {"status": "BLOCKED", "tasks": [{"id": "one", "status": "NOT_RUN", "attempts": 0}]}

    def test_durable_history_and_no_duplicate_checkpoint(self):
        report = self.sample()
        with self.store.session(report) as session:
            session.checkpoint(report)
            report["tasks"][0].update(status="PASS", attempts=1)
            session.checkpoint(report)
            report["status"] = "PASS"
            session.finish(report)
        sid = report["session_id"]
        other = Registry(self.path)
        self.assertEqual(other.show(sid)["report"], report)
        self.assertEqual(other.show(sid)["phase"], "FINISHED")
        self.assertEqual([x["kind"] for x in other.history(sid)], ["START", "CHECKPOINT", "FINISH"])
        self.assertEqual(other.list(phase="FINISHED")[0]["id"], sid)
        with self.assertRaises(ValueError):
            other.recover(sid)

    def test_active_lock_and_recover(self):
        report = self.sample()
        with self.store.session(report) as session:
            report["tasks"][0].update(status="BLOCKED", attempts=1)
            session.checkpoint(report)
            with self.assertRaises(ValueError):
                Registry(self.path).recover(session.sid)
        sid = report["session_id"]
        row = self.store.recover(sid)
        self.assertEqual((row["phase"], row["outcome"]), ("INTERRUPTED", "BLOCKED"))
        self.assertEqual(row["report"]["tasks"], report["tasks"])
        self.assertEqual(self.store.history(sid)[-1]["kind"], "RECOVER")
        with self.assertRaises(ValueError):
            self.store.recover(sid)

    def test_process_crash_committed_snapshot(self):
        script = (
            "import os,sys; from hub.registry import Registry; "
            "r=Registry(sys.argv[1]); p={'status':'BLOCKED','tasks':[{'id':'x','status':'NOT_RUN','attempts':0}]}; "
            "cm=r.session(p); s=cm.__enter__(); p['tasks'][0].update(status='BLOCKED',attempts=1); "
            "s.checkpoint(p); os._exit(17)"
        )
        proc = subprocess.run([sys.executable, "-c", script, str(self.path)], check=False)
        self.assertEqual(proc.returncode, 17)
        sid = self.store.list(phase="RUNNING")[0]["id"]
        self.assertEqual(self.store.recover(sid)["report"]["tasks"][0]["attempts"], 1)

    def test_failed_transaction_rolls_back(self):
        report = self.sample()
        with self.store.session(report) as session:
            with self.store.connect() as db:
                db.execute("CREATE TRIGGER reject_history BEFORE INSERT ON history BEGIN SELECT RAISE(ABORT,'fixture'); END")
            report["tasks"][0]["status"] = "PASS"
            import sqlite3
            with self.assertRaises(sqlite3.Error):
                session.checkpoint(report)
            self.assertEqual(self.store.show(session.sid)["report"]["tasks"][0]["status"], "NOT_RUN")
            self.assertEqual(len(self.store.history(session.sid)), 1)

    def test_multi_sessions_and_input_validation(self):
        for _ in range(3):
            report = self.sample()
            with self.store.session(report):
                pass
        self.assertEqual(len(self.store.list(limit=2)), 2)
        self.assertEqual(len(self.store.list(phase="RUNNING")), 3)
        with self.assertRaises(ValueError):
            self.store.show("../escape")
        with self.assertRaises(ValueError):
            self.store.list(limit=101)
        self.path.chmod(0o755)
        with self.assertRaises(ValueError):
            Registry(self.path)

    def test_real_orchestrator_registry(self):
        bindings = [repo(self.root / name, name) for name in ("hub", "game", "hyper")]
        targets = lab.checked_targets(bindings)
        report = lab.run(lab.default_manifest(), "read", targets, self.root / "evidence", registry_path=self.path)
        row = self.store.show(report["session_id"])
        self.assertEqual(row["phase"], "FINISHED")
        self.assertEqual(row["report"], report)
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(json.loads((self.root / "evidence/report.json").read_text()), report)
        history = self.store.history(report["session_id"])
        self.assertEqual(history[0]["kind"], "START")
        self.assertEqual(history[-1]["kind"], "FINISH")
        self.assertTrue(any(t["attempts"] == 1 and t["status"] == "BLOCKED"
                            for event in history for t in event["report"]["tasks"]))
        with self.assertRaises(ValueError):
            lab.run(lab.default_manifest(), "read", targets, self.root / "evidence2", registry_path=self.root / "hub/db")
        self.assertFalse((self.root / "hub/db").exists())

    def test_interrupted_orchestrator(self):
        targets = lab.checked_targets([repo(self.root / name, name) for name in ("hub", "game", "hyper")])
        def stop(*args):
            raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            lab.run(lab.default_manifest(), "read", targets, self.root / "evidence", runner=stop, registry_path=self.path)
        sid = self.store.list(phase="RUNNING")[0]["id"]
        row = self.store.recover(sid)
        tasks = {t["id"]: t for t in row["report"]["tasks"]}
        self.assertEqual(tasks["hub-revision"]["status"], "PASS")
        self.assertEqual((tasks["inventory"]["status"], tasks["inventory"]["attempts"]), ("BLOCKED", 1))
        self.assertEqual(tasks["architecture"]["status"], "NOT_RUN")


if __name__ == "__main__":
    unittest.main()
