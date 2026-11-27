import copy
from pathlib import Path
import subprocess
import tempfile
import unittest

from hub import ci_receipt as ci
from hub.development import Development
from hub.orchestrator import git
from test_orchestrator import repo


def fake(sha, branch, outcome="success", run_id=42):
    run = {"id": run_id, "head_sha": sha, "head_branch": branch, "event": "push",
           "workflow_id": ci.WORKFLOW_ID, "path": ci.WORKFLOW, "repository": {"full_name": ci.REPO},
           "head_repository": {"full_name": ci.REPO}, "run_attempt": 1, "status": "completed", "conclusion": outcome}
    job = {"id": 99, "name": "contracts", "run_id": run_id, "run_attempt": 1, "head_sha": sha,
           "status": "completed", "conclusion": outcome,
           "steps": [{"name": n, "status": "completed", "conclusion": outcome} for n in ci.STEPS]}
    return run, {"total_count": 1, "jobs": [job]}


def reader(run, jobs):
    return lambda path: jobs if "/jobs?" in path else run


class DevelopmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.checkout = self.root / "hub"
        repo(self.checkout, "hub")
        (self.checkout / "AGENTS.md").write_text("rules")
        path = self.checkout / ci.WORKFLOW
        path.parent.mkdir(parents=True)
        path.write_text("fixed workflow")
        self.commit("base")
        self.base = git(self.checkout, "rev-parse", "HEAD")
        self.d = Development(self.root / "state")
        self.sid = self.d.create("Improve README", self.base, ["README.md"])["id"]

    def cmd(self, *args):
        subprocess.run(["git", "-C", str(self.checkout), *args], check=True, stdout=subprocess.DEVNULL)

    def commit(self, message):
        self.cmd("add", ".")
        self.cmd("commit", "-qm", message)

    def setup_candidate(self):
        self.d.audit(self.sid, str(self.checkout))
        self.d.plan(self.sid, "Edit README only")
        self.cmd("checkout", "-qb", "feature/readme")
        return self.new_candidate("first")

    def new_candidate(self, value):
        (self.checkout / "README.md").write_text(value)
        self.commit(value)
        sha = git(self.checkout, "rev-parse", "HEAD")
        self.d.candidate(self.sid, str(self.checkout), sha)
        return sha

    def ci(self, sha, outcome="success"):
        return self.d.check_ci(self.sid, 42, reader(*fake(sha, "feature/readme", outcome)))

    def test_full_flow_and_persistence(self):
        sha = self.setup_candidate()
        self.assertEqual(self.ci(sha)["state"], "REVIEW")
        with self.assertRaises(ValueError):
            self.d.approve(self.sid, "a"*40, "wrong")
        self.assertEqual(self.d.approve(self.sid, sha, "Reviewed")["state"], "APPROVED")
        other = Development(self.root / "state")
        self.assertFalse(other.draft(self.sid)["merge_authorized"])
        self.assertEqual(len(other.history(self.sid)), 6)
        self.assertEqual(other.get(self.sid)["state"], "APPROVED")

    def test_repair_budget_and_new_sha(self):
        sha = self.setup_candidate()
        for n in range(3):
            self.assertEqual(self.ci(sha, "failure")["state"], "DIAGNOSE")
            state = self.d.diagnose(self.sid, "Inspect failed checks")["state"]
            if n < 2:
                self.assertEqual(state, "REPAIR_READY")
                with self.assertRaises(ValueError):
                    self.d.candidate(self.sid, str(self.checkout), sha)
                sha = self.new_candidate("repair" + str(n))
            else:
                self.assertEqual(state, "BLOCKED")
        self.assertEqual(self.d.get(self.sid)["repairs"], 2)

    def test_scope_and_order(self):
        with self.assertRaises(ValueError):
            self.d.plan(self.sid, "skip audit")
        with self.assertRaises(ValueError):
            self.d.create("bad", self.base, [ci.WORKFLOW])
        self.d.audit(self.sid, str(self.checkout))
        self.d.plan(self.sid, "scope")
        self.cmd("checkout", "-qb", "feature/readme")
        (self.checkout / "extra").touch()
        self.commit("outside")
        with self.assertRaises(ValueError):
            self.d.candidate(self.sid, str(self.checkout), git(self.checkout, "rev-parse", "HEAD"))

    def test_whitespace_path_cannot_impersonate_scope(self):
        self.d.audit(self.sid, str(self.checkout))
        self.d.plan(self.sid, "scope")
        self.cmd("checkout", "-qb", "feature/readme")
        (self.checkout / " README.md").touch()
        self.commit("outside whitespace path")
        with self.assertRaises(ValueError):
            self.d.candidate(self.sid, str(self.checkout), git(self.checkout, "rev-parse", "HEAD"))

    def test_ci_identity_and_skipped(self):
        sha = self.setup_candidate()
        for key, value in (("head_sha", "b"*40), ("event", "pull_request"), ("workflow_id", 123),
                           ("head_repository", {"full_name": "other/repo"})):
            run, jobs = fake(sha, "feature/readme")
            run[key] = value
            with self.assertRaises(ValueError):
                self.d.check_ci(self.sid, 42, reader(run, jobs))
        run, jobs = fake(sha, "feature/readme")
        jobs["jobs"][0]["steps"][0]["conclusion"] = "skipped"
        self.assertEqual(self.d.check_ci(self.sid, 42, reader(run, jobs))["state"], "AWAITING_CI")
        self.assertEqual(self.ci(sha)["state"], "REVIEW")

    def test_incomplete_and_job_mismatch(self):
        sha = self.setup_candidate()
        run, jobs = fake(sha, "feature/readme")
        run["status"] = "in_progress"
        self.assertEqual(self.d.check_ci(self.sid, 42, reader(run, jobs))["state"], "AWAITING_CI")
        run["status"] = "completed"
        jobs["jobs"][0]["run_attempt"] = 2
        with self.assertRaises(ValueError):
            self.d.check_ci(self.sid, 42, reader(run, jobs))


if __name__ == "__main__":
    unittest.main()
