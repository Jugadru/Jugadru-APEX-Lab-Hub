import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest

from hub import scheduler as s
from hub import experiment_worker as worker
from hub.orchestrator import git
from test_orchestrator import repo


SPEC = {"schema": 1, "capability": "synthetic.calibration", "seed": 42, "samples": 2000,
        "stop_hits": 0, "priority": 50, "cpu_seconds": 3, "memory_mib": 128, "wall_seconds": 5}


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.checkout = self.root / "hub"
        repo(self.checkout, "hub")
        p = self.checkout / s.WORKER
        p.parent.mkdir()
        p.write_bytes(Path(worker.__file__).read_bytes())
        subprocess.run(["git", "-C", str(self.checkout), "add", "."], check=True)
        subprocess.run(["git", "-C", str(self.checkout), "commit", "-qm", "worker"], check=True)
        self.sha = git(self.checkout, "rev-parse", "HEAD")
        self.queue = s.Scheduler(self.root / "registry")

    def run_next(self):
        return self.queue.run_next(str(self.checkout), self.sha)

    def test_priority_reproducibility_and_stop(self):
        a = self.queue.enqueue(SPEC, self.sha)
        b = self.queue.enqueue({**SPEC, "priority": 90}, self.sha)
        c = self.queue.enqueue({**SPEC, "stop_hits": 10}, self.sha)
        first, second, third = self.run_next(), self.run_next(), self.run_next()
        self.assertEqual([first["id"], second["id"], third["id"]], [b["id"], a["id"], c["id"]])
        self.assertTrue(all(x["state"] == "PASS" for x in (first, second, third)))
        self.assertEqual(first["telemetry"]["result"], second["telemetry"]["result"])
        self.assertEqual(third["telemetry"]["result"]["stop_reason"], "hit_target")
        self.assertEqual(third["telemetry"]["result"]["hits"], 10)
        self.assertEqual(s.Scheduler(self.root / "registry").get(a["id"]), second)
        self.assertEqual(len(self.queue.history(a["id"])), 3)
        self.assertEqual(self.run_next()["state"], "EMPTY")

    def test_reject_contract_and_revision(self):
        for patch in ({"command": "ls"}, {"seed": True}, {"memory_mib": 999},
                      {"capability": "shell"}, {"stop_hits": 3000}, {"wall_seconds": 0}):
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                self.queue.enqueue({**SPEC, **patch}, self.sha)
        a = self.queue.enqueue(SPEC, "a"*40)
        with self.assertRaises(ValueError):
            self.run_next()
        self.assertEqual(self.queue.get(a["id"])["attempts"], 0)

    def test_cancel_queued(self):
        a = self.queue.enqueue(SPEC, self.sha)
        self.assertEqual(self.queue.cancel(a["id"])["state"], "CANCELLED")
        self.assertEqual(self.run_next()["state"], "EMPTY")

    def test_running_cancel_and_worker_lock(self):
        a = self.queue.enqueue({**SPEC, "samples": 20_000_000, "cpu_seconds": 15, "wall_seconds": 20}, self.sha)
        results = []
        thread = threading.Thread(target=lambda: results.append(self.run_next()))
        thread.start()
        try:
            deadline = time.monotonic() + 5
            while self.queue.get(a["id"])["state"] != "RUNNING" and time.monotonic() < deadline:
                time.sleep(.005)
            self.assertEqual(self.queue.get(a["id"])["state"], "RUNNING")
            with self.assertRaises(ValueError):
                self.queue.recover(a["id"])
            self.queue.cancel(a["id"])
        finally:
            thread.join(25)
        self.assertEqual(results[0]["state"], "CANCELLED")

    def test_recover_abandoned_no_replay(self):
        a = self.queue.enqueue(SPEC, self.sha)
        with self.queue.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self.queue._get(db, a["id"])
            row.update(state="RUNNING", attempts=1)
            self.queue._save(db, row, "fixture claim")
        with self.assertRaises(ValueError):
            self.run_next()
        self.assertEqual(self.queue.recover(a["id"])["state"], "INTERRUPTED")
        self.assertEqual(self.run_next()["state"], "EMPTY")

    def test_real_memory_limit(self):
        code = "from hub.experiment_worker import apply_limits; apply_limits(3,64,5); x=bytearray(100*1024*1024)"
        proc = subprocess.run([sys.executable, "-B", "-c", code], capture_output=True, timeout=8)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn(b"MemoryError", proc.stderr)

    def test_real_cpu_limit(self):
        code = "from hub.experiment_worker import apply_limits; apply_limits(1,128,5)\nwhile True: pass"
        proc = subprocess.run([sys.executable, "-B", "-c", code], capture_output=True, timeout=8)
        self.assertIn(proc.returncode, (-signal.SIGXCPU, -signal.SIGKILL))

    def test_real_wall_limit(self):
        code = "from hub.experiment_worker import apply_limits; import time; apply_limits(3,128,1); time.sleep(10)"
        proc = subprocess.run([sys.executable, "-B", "-c", code], capture_output=True, timeout=5)
        self.assertEqual(proc.returncode, -signal.SIGALRM)


if __name__ == "__main__":
    unittest.main()
