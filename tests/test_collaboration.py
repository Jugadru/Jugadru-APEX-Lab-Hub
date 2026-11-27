import copy
from pathlib import Path
import tempfile
import unittest
from hub import collaboration as c, departments as d, gateway as g, knowledge as k
from test_gateway import task
from test_orchestrator import repo

class CollaborationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.bindings=[repo(self.root/"hub","hub"),repo(self.root/"game","game")]
        self.c=c.Collaboration(self.root/"state")
        self.req={"schema":1,"objective":"Review scope","targets":{n:s for n,p,s in self.bindings},
                  "tasks":[{**task("revision","software","repo.revision","hub"),"topic":"auto"},
                           {**task("inventory","performance","source.inventory","game",["revision"]),"topic":"auto"}]}
        for t in self.req["tasks"]:del t["department"]
        self.roles=["software","performance","quality"];self.ev={r:[] for r in self.roles}
    def propose(self):return self.c.propose(self.req,self.roles,self.ev)
    def votes(self,p,change=False):
        for r in self.roles:
            p=self.c.vote(p["id"],p["plan_sha256"],r,"revise" if change and r=="performance" else "accept","Reviewed")
        return p
    def test_conflict_revision_and_atomic_handoff(self):
        p=self.votes(self.propose(),True);self.assertEqual(p["state"],"CONFLICT")
        with self.assertRaises(ValueError):self.c.handoff(p["id"],p["plan_sha256"])
        old=p["plan_sha256"];req=copy.deepcopy(self.req);req["objective"]="Clarify review"
        p=self.c.revise(p["id"],old,req,"Resolve requested clarification")
        self.assertEqual(p["round"],2);self.assertEqual(p["votes"],{})
        with self.assertRaises(ValueError):self.c.vote(p["id"],old,"quality","accept","Stale")
        p=self.votes(p);self.assertEqual(p["state"],"AGREED")
        p=self.c.handoff(p["id"],p["plan_sha256"])
        dep=self.c.departments.get(p["department_id"])
        self.assertEqual(dep["state"],"PROPOSED");self.assertEqual(dep["reviews"],{})
        with self.assertRaises(ValueError):self.c.handoff(p["id"],p["plan_sha256"])
        with self.assertRaises(ValueError):self.c.observe(p["id"])
        dep=self.c.departments.review(dep["id"],dep["plan_sha256"],"quality","accept","Separate review")
        dep=self.c.departments.delegate(dep["id"],dep["plan_sha256"])
        gateway=g.Gateway(self.c.store.root);q=gateway.get(dep["gateway_id"])
        self.assertEqual(q["state"],"PROPOSED")
        gateway.approve(q["id"],q["plan_sha256"],"Separate execution")
        result=gateway.run(q["id"],q["plan_sha256"],self.bindings)
        self.assertEqual(result["state"],"PASS")
        self.c.departments.observe(dep["id"])
        final=self.c.observe(p["id"])
        self.assertEqual(final["state"],"OBSERVED");self.assertEqual(final["outcome"],"PASS")
        self.assertEqual(c.Collaboration(self.c.store.root).get(p["id"]),final)
        self.assertEqual(len(self.c.history(p["id"])),10)
    def test_reject_and_duplicate_vote(self):
        p=self.propose();sid=p["id"];h=p["plan_sha256"]
        self.c.vote(sid,h,"software","accept","OK")
        with self.assertRaises(ValueError):self.c.vote(sid,h,"software","accept","Again")
        with self.assertRaises(ValueError):self.c.vote(sid,h,"graphics","accept","Outside")
        self.assertEqual(self.c.vote(sid,h,"quality","reject","Stop")["state"],"REJECTED")
        with self.assertRaises(ValueError):self.c.revise(sid,h,self.req,"Bypass")
    def test_scope_and_round_limit(self):
        p=self.votes(self.propose(),True)
        for field,value in (("target","game"),("timeout_seconds",999),("topic","quality")):
            req=copy.deepcopy(self.req);req["tasks"][0][field]=value
            with self.assertRaises(ValueError):self.c.revise(p["id"],p["plan_sha256"],req,"Change")
        for _ in range(2):
            p=self.c.revise(p["id"],p["plan_sha256"],self.req,"Retry deliberation")
            p=self.votes(p,True)
        with self.assertRaises(ValueError):self.c.revise(p["id"],p["plan_sha256"],self.req,"Exhausted")
        self.assertEqual(self.c.cancel(p["id"])["state"],"CANCELLED")
    def test_roles_and_scoped_context(self):
        with self.assertRaises(ValueError):self.c.propose(self.req,["software","quality"],{"software":[],"quality":[]})
        mem=k.Knowledge(self.root/"memory")
        with mem.store.connect() as db:
            e=mem._insert(db,{"state":"VERIFIED","evidence":{"kind":"department","source_id":"a"*32,
               "departments":["software"],"content":{"outcome":"PASS","revisions":{"hub":"b"*40},"note":"PRIVATE_TEXT"}}})
        ev={**self.ev,"software":[e["id"]]}
        p=self.c.propose(self.req,self.roles,ev,mem.store.root)
        view=self.c.view(p["id"],"software")
        self.assertEqual(view["context"][0]["memory_id"],e["id"])
        self.assertNotIn("PRIVATE_TEXT",str(view))
        self.assertEqual(self.c.view(p["id"],"performance")["context"],[])
        with self.assertRaises(ValueError):self.c.propose(self.req,self.roles,{**ev,"quality":[e["id"]]},mem.store.root)
    def test_cancelled_department_observation(self):
        p=self.votes(self.propose());p=self.c.handoff(p["id"],p["plan_sha256"])
        self.c.departments.cancel(p["department_id"])
        self.assertEqual(self.c.observe(p["id"])["state"],"INCONCLUSIVE")
    def test_handoff_rollback(self):
        from unittest.mock import patch
        p=self.votes(self.propose())
        with patch.object(self.c,"_save",side_effect=ValueError("interruption")):
            with self.assertRaises(ValueError):self.c.handoff(p["id"],p["plan_sha256"])
        self.assertEqual(self.c.departments.list(),[])
        self.assertEqual(self.c.get(p["id"])["state"],"AGREED")
if __name__=="__main__":unittest.main()
