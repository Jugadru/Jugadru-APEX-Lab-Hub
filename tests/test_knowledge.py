import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from hub import knowledge as k, departments as d, gateway as g, research
from test_gateway import task
from test_orchestrator import repo

class KnowledgeTests(unittest.TestCase):
    def setUp(self):
        self.t=tempfile.TemporaryDirectory();self.addCleanup(self.t.cleanup)
        self.root=Path(self.t.name)
        self.source=self.root/"source"
        self.k=k.Knowledge(self.root/"memory")
        self.bindings=[repo(self.root/"hub","hub"),repo(self.root/"game","game")]
        self.d=d.Departments(self.source)
    def record(self):
        t=task("revision","software","repo.revision","hub")
        del t["department"];t["topic"]="auto"
        req={"schema":1,"objective":"Review source","targets":{"hub":self.bindings[0][2]},"tasks":[t]}
        p=self.d.propose(req);self.d.review(p["id"],p["plan_sha256"],"quality","accept","Reviewed")
        row=self.d.delegate(p["id"],p["plan_sha256"]);gateway=g.Gateway(self.source)
        case=gateway.get(row["gateway_id"]);gateway.approve(case["id"],case["plan_sha256"],"Execute")
        result=gateway.run(case["id"],case["plan_sha256"],self.bindings[:1])
        self.assertEqual(result["state"],"PASS")
        self.d.observe(p["id"])
        return p["id"],result
    def annotation(self,e):
        return {"schema":1,"kind":"decision","department":"software","title":"Keep exact revisions","body":"Operator declaration",
                "tags":["architecture"],"evidence_ids":[e["id"]],"supersedes":None,"provenance":None}
    def test_real_ingest_dedup_search_audit(self):
        sid,_=self.record();e=self.k.ingest("department",self.source,sid)
        self.assertEqual(e["state"],"VERIFIED")
        self.assertEqual(e,self.k.ingest("department",self.source,sid))
        self.assertEqual(self.k.search("repo.revision","software"),[e])
        self.assertEqual(self.k.search("repo.revision","graphics"),[])
        self.assertEqual(self.k.audit(e["id"],self.source)["status"],"PASS")
        self.assertEqual(k.Knowledge(self.root/"memory").get(e["id"]),e)
    def test_tamper_blocks_ingest_and_reaudit(self):
        sid,result=self.record();e=self.k.ingest("department",self.source,sid)
        (self.source/result["receipt"]["path"]).write_text("{}")
        with self.assertRaises(ValueError):self.k.ingest("department",self.source,sid)
        self.assertEqual(self.k.audit(e["id"],self.source)["status"],"FAIL")
        self.assertEqual(self.k.get(e["id"]),e)
    def test_annotation_supersession_and_untrusted_provenance(self):
        sid,_=self.record();e=self.k.ingest("department",self.source,sid)
        a=self.k.annotate(self.annotation(e));self.assertEqual(a["state"],"DECLARED")
        n=self.annotation(e);n["supersedes"]=a["id"];n["body"]="Revised declaration"
        b=self.k.annotate(n)
        self.assertEqual(self.k.search("Keep exact revisions"),[b])
        self.assertEqual(len(self.k.search("Keep exact revisions",include_superseded=True)),2)
        with self.assertRaises(ValueError):self.k.annotate(n)
        n=self.annotation(e);n["evidence_ids"]=[a["id"]]
        with self.assertRaises(ValueError):self.k.annotate(n)
        n=self.annotation(e);n["kind"]="asset_reference"
        with self.assertRaises(ValueError):self.k.annotate(n)
        n["provenance"]={"source":"reference","author":"declared","license":"unknown","revision":"unknown"}
        self.assertFalse(self.k.annotate(n)["promotion_authorized"])
    def test_unobserved_source_and_bad_input(self):
        t=task("rev","software","repo.revision","hub");del t["department"];t["topic"]="auto"
        p=self.d.propose({"schema":1,"objective":"Review","targets":{"hub":self.bindings[0][2]},"tasks":[t]})
        with self.assertRaises(ValueError):self.k.ingest("department",self.source,p["id"])
        with self.assertRaises(ValueError):self.k.ingest("shell",self.source,p["id"])
        with self.assertRaises(ValueError):self.k.search(limit=101)
    def test_research_comparison_preserves_negative_status(self):
        r=research.Research(self.source);sid="a"*32
        folder=self.source/("research-"+sid);folder.mkdir()
        name="physics_aero";spec={"hypothesis":"fixture","sources":["REF-001"],"config":{"seed":42},
                                "criteria":{"x":{"op":"le","threshold":1,"unit":"m"}}}
        # This is a synthetic stored comparison fixture, not a new scientific result.
        from unittest.mock import patch
        plan={"revisions":research.PINS,"promotion":"NOT_AUTHORIZED","execution_authorized":False,"experiments":{name:spec}}
        comp={"status":"FAIL","promotion":"NOT_AUTHORIZED","experiments":[{"experiment":name,"status":"FAIL",
              "metrics":[{"metric":"x","observed":2,"op":"le","threshold":1,"unit":"m","criterion_met":False}]}]}
        for filename,value in (("frozen-plan.json",plan),("comparison.json",comp)):
            (folder/filename).write_text(json.dumps(value))
        row={"id":sid,"state":"FAIL","attempts":1,"promotion":"NOT_AUTHORIZED","scientific_validation":"NOT_PERFORMED",
             "revisions":research.PINS,"experiments":[name],"completed":[{"experiment":name,"status":"FAIL"}],
             "plan_sha256":hashlib.sha256((folder/"frozen-plan.json").read_bytes()).hexdigest(),
             "comparison_sha256":hashlib.sha256((folder/"comparison.json").read_bytes()).hexdigest()}
        r.save(row,"fixture",True)
        with patch.object(research,"check_spec",return_value=spec):
            e=self.k.ingest("research",self.source,sid)
        self.assertEqual(e["evidence"]["content"]["outcome"],"FAIL")
        self.assertFalse(e["promotion_authorized"])
    def test_memory_digest_tamper(self):
        sid,_=self.record();e=self.k.ingest("department",self.source,sid)
        e["evidence"]["content"]["outcome"]="FAIL"
        with self.k.store.connect() as db:db.execute("UPDATE knowledge_entries SET snapshot=? WHERE id=?",(json.dumps(e),e["id"]))
        with self.assertRaises(ValueError):self.k.get(e["id"])

if __name__=="__main__":unittest.main()
