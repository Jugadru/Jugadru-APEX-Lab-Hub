import copy
from pathlib import Path
import tempfile
import unittest
from hub import departments as d, gateway as g
from test_gateway import task
from test_orchestrator import repo

class DepartmentTests(unittest.TestCase):
    def setUp(self):
        self.t=tempfile.TemporaryDirectory();self.addCleanup(self.t.cleanup)
        self.root=Path(self.t.name)
        self.bindings=[repo(self.root/"hub","hub"),repo(self.root/"game","game")]
        self.d=d.Departments(self.root/"state")
        self.req={"schema":1,"objective":"Review actual sources","targets":{k:s for k,p,s in self.bindings},
                  "tasks":[{**task("revision","software","repo.revision","hub"),"topic":"auto"},
                           {**task("inventory","performance","source.inventory","game",["revision"]),"topic":"auto"}]}
        for t in self.req["tasks"]:del t["department"]
    def approved(self):
        p=self.d.propose(self.req)
        return self.d.review(p["id"],p["plan_sha256"],"quality","accept","Reviewed scope")
    def test_profiles_and_dependencies(self):
        p=d.plan(self.req)
        self.assertEqual(len(d.catalog()),8)
        self.assertEqual(p["assignments"]["revision"]["owner"],"software")
        self.assertEqual(p["assignments"]["inventory"]["owner"],"performance")
        self.assertEqual(p["cross_department_dependencies"][0]["recipient"],"performance")
        self.assertFalse(p["execution_authorized"])
    def test_reject_injection_and_unauthorized_topic(self):
        for k,v in (("topic","graphics"),("permissions",["write"]),("command","ls"),("depends_on",["missing"]),("timeout_seconds",999)):
            req=copy.deepcopy(self.req);req["tasks"][1][k]=v
            with self.subTest(k=k),self.assertRaises(ValueError):d.plan(req)
    def test_review_rejection_and_exact_digest(self):
        p=self.d.propose(self.req);sid=p["id"];h=p["plan_sha256"]
        with self.assertRaises(ValueError):self.d.delegate(sid,h)
        with self.assertRaises(ValueError):self.d.review(sid,"b"*64,"quality","accept","wrong")
        with self.assertRaises(ValueError):self.d.review(sid,h,"software","accept","wrong")
        self.assertEqual(self.d.review(sid,h,"quality","reject","scope")["state"],"REJECTED")
        with self.assertRaises(ValueError):self.d.delegate(sid,h)
    def test_quality_owner_requires_software_review(self):
        self.req["tasks"][0]["topic"]="quality"
        p=self.d.propose(self.req);sid=p["id"];h=p["plan_sha256"]
        self.assertEqual(self.d.review(sid,h,"quality","accept","review")["state"],"PROPOSED")
        self.assertEqual(self.d.review(sid,h,"software","accept","review")["state"],"REVIEWED")
    def test_atomic_handoff_and_real_observation(self):
        p=self.approved();sid=p["id"];h=p["plan_sha256"]
        row=self.d.delegate(sid,h);gateway=g.Gateway(self.d.store.root)
        q=gateway.get(row["gateway_id"])
        self.assertEqual(q["state"],"PROPOSED")
        with self.assertRaises(ValueError):self.d.delegate(sid,h)
        with self.assertRaises(ValueError):self.d.observe(sid)
        gateway.approve(q["id"],q["plan_sha256"],"Execute")
        self.assertEqual(gateway.run(q["id"],q["plan_sha256"],self.bindings)["state"],"PASS")
        observed=self.d.observe(sid)
        self.assertEqual(observed["observation"]["task_counts"]["performance"],{"PASS":1})
        self.assertEqual(self.d.metrics()["observed_cases"],1)
        self.assertEqual(d.Departments(self.d.store.root).get(sid),observed)
        with self.assertRaises(ValueError):self.d.observe(sid)
    def test_changed_receipt_blocks_observation(self):
        p=self.approved();row=self.d.delegate(p["id"],p["plan_sha256"])
        gateway=g.Gateway(self.d.store.root);q=gateway.get(row["gateway_id"])
        gateway.approve(q["id"],q["plan_sha256"],"Execute")
        result=gateway.run(q["id"],q["plan_sha256"],self.bindings)
        (self.d.store.root/result["receipt"]["path"]).write_text("{}")
        with self.assertRaises(ValueError):self.d.observe(p["id"])
        self.assertEqual(self.d.metrics()["observed_cases"],0)
    def test_close_unverified_only_terminal_and_no_metrics(self):
        p=self.approved();row=self.d.delegate(p["id"],p["plan_sha256"])
        gateway=g.Gateway(self.d.store.root);q=gateway.get(row["gateway_id"])
        with self.assertRaises(ValueError):self.d.close_unverified(p["id"],"not terminal")
        with gateway.store.connect() as db:
            q.update(state="RUNNING",attempts=1);gateway._save(db,q,"fixture")
        gateway.recover(q["id"])
        r=self.d.close_unverified(p["id"],"Missing report after interruption")
        self.assertEqual(r["state"],"INCONCLUSIVE")
        self.assertEqual(self.d.metrics()["observed_cases"],0)
        self.assertEqual(r["closure"]["gateway_state"],"INTERRUPTED")
        with self.assertRaises(ValueError):self.d.observe(p["id"])
    def test_verified_case_cannot_close_unverified(self):
        p=self.approved();row=self.d.delegate(p["id"],p["plan_sha256"])
        gateway=g.Gateway(self.d.store.root);q=gateway.get(row["gateway_id"])
        gateway.approve(q["id"],q["plan_sha256"],"Execute")
        self.assertEqual(gateway.run(q["id"],q["plan_sha256"],self.bindings)["state"],"PASS")
        with self.assertRaises(ValueError):self.d.close_unverified(p["id"],"wrong")
        self.assertEqual(self.d.observe(p["id"])["state"],"OBSERVED")
    def test_cancel(self):
        p=self.d.propose(self.req)
        self.assertEqual(self.d.cancel(p["id"])["state"],"CANCELLED")

if __name__=="__main__":unittest.main()
