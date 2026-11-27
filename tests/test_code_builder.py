import json
from pathlib import Path
import tempfile
import unittest
from hub import code_builder as b, development as d, ci_receipt as ci, orchestrator as o
from test_orchestrator import repo

class CodeBuilderTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.src=self.root/"source";repo(self.src,"hub")
        (self.src/"AGENTS.md").write_text("rules")
        f=self.src/ci.WORKFLOW;f.parent.mkdir(parents=True);f.write_text("fixed")
        (self.src/"tests").mkdir()
        (self.src/"tests/test_base.py").write_text("import unittest\nclass Base(unittest.TestCase):\n def test_ok(self): self.assertTrue(True)\n")
        b.command(self.src,self.root,"add",".");b.command(self.src,self.root,"commit","-qm","base")
        self.sha=o.git(self.src,"rev-parse","HEAD")
        self.dev=d.Development(self.root/"state")
        self.builder=b.Builder(self.root/"state")
        self.sid=self.dev.create("Add feature",self.sha,["hub/features/demo.py","tests/test_feature_demo.py","docs/demo.md"])["id"]
        self.dev.audit(self.sid,self.src);self.dev.plan(self.sid,"Reviewed additive scope")
        self.req={"schema":1,"development_id":self.sid,"edits":[
            {"path":"hub/features/demo.py","expected_blob":None,"content":"def double(n):\n return n*2\n"},
            {"path":"tests/test_feature_demo.py","expected_blob":None,"content":"import unittest\nfrom hub.features.demo import double\nclass Demo(unittest.TestCase):\n def test_double(self): self.assertEqual(double(3),6)\n"}]}
    def approved(self):
        p=self.builder.propose(self.req);return self.builder.approve(p["id"],p["plan_sha256"])
    def test_actual_candidate_and_handoff(self):
        p=self.approved();r=self.builder.build(p["id"],p["plan_sha256"],self.src)
        self.assertEqual(r["state"],"PASS",r)
        self.assertEqual(r["local_tests"]["count"],2)
        self.assertEqual(o.git(self.src,"rev-parse","HEAD"),self.sha)
        self.assertEqual(o.git(self.src,"status","--porcelain"),"")
        self.assertFalse(r["merge_authorized"])
        h=self.builder.handoff(p["id"])
        self.assertEqual(h["state"],"AWAITING_CI")
        self.assertEqual(h["candidates"][0]["sha"],r["candidate"]["sha"])
        with self.assertRaises(ValueError):self.builder.handoff(p["id"])
    def test_reject_scope_and_syntax(self):
        for path,content in [("../x","x"),("hub/registry.py","x"),("hub/features/demo.py","def broken(")]:
            req={**self.req,"edits":[{"path":path,"expected_blob":None,"content":content}]}
            with self.subTest(path=path),self.assertRaises(ValueError):self.builder.propose(req)
    def test_exact_approval_no_replay(self):
        p=self.builder.propose(self.req)
        with self.assertRaises(ValueError):self.builder.build(p["id"],p["plan_sha256"],self.src)
        with self.assertRaises(ValueError):self.builder.approve(p["id"],"b"*64)
        self.builder.approve(p["id"],p["plan_sha256"])
        self.builder.build(p["id"],p["plan_sha256"],self.src)
        with self.assertRaises(ValueError):self.builder.build(p["id"],p["plan_sha256"],self.src)
    def test_blob_mismatch_blocks(self):
        self.req["edits"][0]["expected_blob"]="b"*40
        p=self.approved();r=self.builder.build(p["id"],p["plan_sha256"],self.src)
        self.assertEqual(r["state"],"BLOCKED")
    def test_failing_tests_no_handoff(self):
        self.req["edits"][1]["content"]=self.req["edits"][1]["content"].replace("),6","),7")
        p=self.approved();r=self.builder.build(p["id"],p["plan_sha256"],self.src)
        self.assertEqual(r["state"],"FAIL",r)
        with self.assertRaises(ValueError):self.builder.handoff(p["id"])
    def test_tampered_log(self):
        p=self.approved();r=self.builder.build(p["id"],p["plan_sha256"],self.src)
        self.assertEqual(r["state"],"PASS",r)
        (self.builder.store.root/("code-"+p["id"])/"tests.log").write_text("fake")
        with self.assertRaises(ValueError):self.builder.handoff(p["id"])

if __name__=="__main__":unittest.main()
