from pathlib import Path
import tempfile
import unittest
from hub import development_cycle as a, collaboration as c, gateway as g, code_builder as b, development as d, orchestrator as o, ci_receipt as ci
from test_gateway import task
from test_orchestrator import repo

class CycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.src=self.root/"source";repo(self.src,"hub")
        (self.src/"AGENTS.md").write_text("rules")
        p=self.src/ci.WORKFLOW;p.parent.mkdir(parents=True);p.write_text("fixed")
        (self.src/"tests").mkdir()
        (self.src/"tests/test_base.py").write_text("import unittest\nclass Base(unittest.TestCase):\n def test_ok(self): self.assertTrue(True)\n")
        b.command(self.src,self.root,"add",".");b.command(self.src,self.root,"commit","-qm","base")
        self.sha=o.git(self.src,"rev-parse","HEAD")
        self.c=c.Collaboration(self.root/"state")
        t=task("revision","software","repo.revision","hub");del t["department"];t["topic"]="auto"
        p=self.c.propose({"schema":1,"objective":"Audit source","targets":{"hub":self.sha},"tasks":[t]},
                         ["software","quality"],{"software":[],"quality":[]})
        self.x=a.Cycle(self.c.store.root)
        self.req={"schema":1,"collaboration_id":p["id"],"objective":"Add isolated feature","edits":[
            {"path":"hub/features/demo.py","expected_blob":None,"content":"def twice(x):\n return x*2\n"},
            {"path":"tests/test_feature_demo.py","expected_blob":None,"content":"import unittest\nfrom hub.features.demo import twice\nclass T(unittest.TestCase):\n def test_twice(self): self.assertEqual(twice(4),8)\n"}]}
    def observed(self):
        p=self.c.get(self.req["collaboration_id"])
        for role in ("software","quality"):p=self.c.vote(p["id"],p["plan_sha256"],role,"accept","Reviewed")
        p=self.c.handoff(p["id"],p["plan_sha256"])
        dep=self.c.departments.get(p["department_id"])
        dep=self.c.departments.review(dep["id"],dep["plan_sha256"],"quality","accept","Reviewed")
        dep=self.c.departments.delegate(dep["id"],dep["plan_sha256"])
        gateway=g.Gateway(self.c.store.root);q=gateway.get(dep["gateway_id"])
        gateway.approve(q["id"],q["plan_sha256"],"Read only")
        self.assertEqual(gateway.run(q["id"],q["plan_sha256"],[("hub",self.src,self.sha)])["state"],"PASS")
        self.c.departments.observe(dep["id"])
        self.c.observe(p["id"])
        return q["id"]
    def prepare(self):
        self.observed()
        p=self.x.prepare(self.req,self.src)
        self.assertEqual(p["state"],"PROPOSED",p)
        return p
    def test_actual_cycle_and_no_replay(self):
        p=self.prepare()
        with self.assertRaises(ValueError):self.x.run(p["id"],p["plan_sha256"],self.src)
        with self.assertRaises(ValueError):self.x.approve(p["id"],"b"*64)
        p=self.x.approve(p["id"],p["plan_sha256"])
        r=self.x.run(p["id"],p["plan_sha256"],self.src)
        self.assertEqual(r["state"],"AWAITING_CI",r)
        self.assertEqual(r["local_tests"],2)
        self.assertEqual(r["ci"],"NOT_RUN")
        self.assertFalse(r["merge_authorized"])
        self.assertEqual(self.x.verify(p["id"])["status"],"PASS")
        self.assertEqual(o.git(self.src,"rev-parse","HEAD"),self.sha)
        self.assertEqual(o.git(self.src,"status","--porcelain"),"")
        with self.assertRaises(ValueError):self.x.run(p["id"],p["plan_sha256"],self.src)
        with self.assertRaises(ValueError):self.x.prepare(self.req,self.src)
        self.assertEqual(a.Cycle(self.c.store.root).get(p["id"]),r)
    def test_unobserved_rejected(self):
        with self.assertRaises(ValueError):self.x.prepare(self.req,self.src)
    def test_scope_rejected_before_claim(self):
        self.observed()
        self.req["edits"][0]["path"]="hub/registry.py"
        with self.assertRaises(ValueError):self.x.prepare(self.req,self.src)
        self.assertEqual(d.Development(self.c.store.root).list(),[])
    def test_failed_local_test_no_handoff(self):
        self.req["edits"][1]["content"]=self.req["edits"][1]["content"].replace("),8","),9")
        p=self.prepare();self.x.approve(p["id"],p["plan_sha256"])
        r=self.x.run(p["id"],p["plan_sha256"],self.src)
        self.assertEqual(r["state"],"FAIL",r)
        self.assertEqual(d.Development(self.c.store.root).get(p["development_id"])["state"],"PLANNED")
    def test_changed_source_blocks_approval(self):
        qid=self.observed()
        p=self.x.prepare(self.req,self.src)
        q=g.Gateway(self.c.store.root).get(qid)
        (self.c.store.root/q["receipt"]["path"]).write_text("{}")
        with self.assertRaises(ValueError):self.x.approve(p["id"],p["plan_sha256"])
    def completed(self):
        p=self.prepare();self.x.approve(p["id"],p["plan_sha256"])
        r=self.x.run(p["id"],p["plan_sha256"],self.src)
        self.assertEqual(r["state"],"AWAITING_CI",r)
        return r

    def test_audit_after_recorded_ci_and_review(self):
        from test_development import fake,reader
        r=self.completed()
        dev=d.Development(self.c.store.root)
        q=b.Builder(self.c.store.root).get(r["builder_id"])
        before=self.x.audit(r["id"])
        self.assertEqual(before["recorded_ci_outcome"],"NOT_RUN")
        dev.check_ci(r["development_id"],42,reader(*fake(r["candidate_sha"],q["candidate"]["branch"])))
        dev.approve(r["development_id"],r["candidate_sha"],"Fixture review")
        original=self.c.store.db.read_bytes()
        report=self.x.audit(r["id"])
        self.assertEqual(report["recorded_development_state"],"APPROVED")
        self.assertEqual(report["recorded_ci_outcome"],"PASS")
        self.assertEqual(report["live_ci"],"NOT_RUN")
        self.assertFalse(report["merge_authorized"])
        self.assertEqual(original,self.c.store.db.read_bytes())
        self.assertEqual(self.x.get(r["id"]),r)
        with self.assertRaises(ValueError):self.x.verify(r["id"])

    def test_audit_detects_lost_historical_handoff(self):
        r=self.completed()
        with self.c.store.connect() as db:
            db.execute("DELETE FROM development_events WHERE id=? AND action='candidate'",(r["development_id"],))
        with self.assertRaises(ValueError):self.x.audit(r["id"])

    def test_audit_rejects_tampered_stored_receipt(self):
        import json
        from hub.registry import encode
        from test_development import fake,reader
        r=self.completed()
        dev=d.Development(self.c.store.root)
        q=b.Builder(self.c.store.root).get(r["builder_id"])
        v=dev.check_ci(r["development_id"],42,reader(*fake(r["candidate_sha"],q["candidate"]["branch"])))
        v["candidates"][0]["receipts"][0]["run_id"]=43
        with self.c.store.connect() as db:
            db.execute("UPDATE development SET snapshot=? WHERE id=?",(encode(v),r["development_id"]))
            db.execute("UPDATE development_events SET snapshot=? WHERE id=? AND action='ci'",(encode(v),r["development_id"]))
        with self.assertRaises(ValueError):self.x.audit(r["id"])

    def test_cancel(self):
        p=self.prepare();r=self.x.cancel(p["id"])
        self.assertEqual(r["state"],"CANCELLED")
        self.assertEqual(b.Builder(self.c.store.root).get(p["builder_id"])["state"],"CANCELLED")
        with self.assertRaises(ValueError):self.x.approve(p["id"],p["plan_sha256"])
if __name__=="__main__":unittest.main()
