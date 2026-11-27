import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from hub import mission as m, orchestrator as o
from test_gateway import task

SHA="a"*40
def request():
    return {"schema":1,"objective":"Audit","targets":{"hub":SHA},
            "tasks":[task("rev","software","repo.revision","hub")],
            "calibration":None,"research":[],"graphics":False,"development":[]}

class MissionTests(unittest.TestCase):
    def setUp(self):
        self.t=tempfile.TemporaryDirectory();self.addCleanup(self.t.cleanup)
        self.root=Path(self.t.name)
        self.m=m.Mission(self.root/"state")
        self.binding=patch.object(m,"check_bindings",return_value={"hub":(self.root,SHA)})
        self.binding.start();self.addCleanup(self.binding.stop)
    def test_invalid_permissions_pins_and_scope(self):
        for change in ({"command":"ls"},{"graphics":True},{"development":["hub/mission.py"]},
                       {"research":["physics_aero"]},{"targets":{"hub":SHA,"game":SHA}}):
            with self.assertRaises(ValueError):self.m.propose({**request(),**change})
        bad=request();bad["tasks"][0]["permissions"]=["write"]
        with self.assertRaises(ValueError):self.m.propose(bad)
    def test_full_lifecycle_and_receipts(self):
        p=self.m.propose(request());sid=p["id"];h=p["plan_sha256"]
        with self.assertRaises(ValueError):self.m.run(sid,h,[])
        with self.assertRaises(ValueError):self.m.approve(sid,"b"*64,"wrong")
        self.m.approve(sid,h,"approved")
        with patch.object(m,"stage_result",return_value=("PASS",{"fixture":True})) as f:
            r=self.m.run(sid,h,[])
        self.assertEqual(r["state"],"PASS")
        self.assertEqual(f.call_count,4)
        self.assertEqual(m.Mission(self.root/"state").get(sid),r)
        self.assertFalse(r["merge_authorized"])
        with self.assertRaises(ValueError):self.m.run(sid,h,[])
    def test_failure_stops_downstream(self):
        p=self.m.propose(request());sid=p["id"];h=p["plan_sha256"]
        self.m.approve(sid,h,"approved")
        with patch.object(m,"stage_result",side_effect=[("PASS",{}),("FAIL",{"failure":True})]) as f:
            r=self.m.run(sid,h,[])
        self.assertEqual(r["state"],"FAIL");self.assertEqual(f.call_count,2)
        self.assertEqual([s["stage"] for s in r["stages"]],["health","delegation"])
    def test_research_second_gate_and_tamper(self):
        req=request();req["targets"].update(m.research.PINS);req["research"]=["physics_aero"]
        p=self.m.propose(req);sid=p["id"];h=p["plan_sha256"]
        self.m.approve(sid,h,"approved")
        work=self.root/"state"/("mission-"+sid)
        def prepare(w,stage,plan,targets,prior):
            if stage=="research_plan":
                f=w/"domain"/("research-"+"c"*32)/"frozen-plan.json"
                f.parent.mkdir(parents=True);f.write_text("{}")
                return "PASS",{"id":"c"*32,"plan_sha256":o.digest(f)}
            return "PASS",{}
        with patch.object(m,"stage_result",side_effect=prepare):
            r=self.m.run(sid,h,[])
        self.assertEqual(r["state"],"AWAITING_RESEARCH_APPROVAL")
        with self.assertRaises(ValueError):self.m.run(sid,h,[])
        with self.assertRaises(ValueError):self.m.run(sid,h,[],"d"*64)
        (work/"health.json").write_text("tampered")
        with self.assertRaises(ValueError):self.m.run(sid,h,[],r["research_plan_sha256"])
    def test_research_resume(self):
        req=request();req["targets"].update(m.research.PINS);req["research"]=["physics_aero"]
        p=self.m.propose(req);sid=p["id"];h=p["plan_sha256"];self.m.approve(sid,h,"approved")
        def stage(w,name,plan,targets,prior):
            if name=="research_plan":
                f=w/"domain"/("research-"+"c"*32)/"frozen-plan.json"
                f.parent.mkdir(parents=True);f.write_text("{}")
                return "PASS",{"id":"c"*32,"plan_sha256":o.digest(f)}
            return "PASS",{}
        with patch.object(m,"stage_result",side_effect=stage) as run:
            paused=self.m.run(sid,h,[])
            done=self.m.run(sid,h,[],paused["research_plan_sha256"])
        self.assertEqual(done["state"],"PASS")
        self.assertEqual(run.call_count,6)
        self.assertEqual(done["research_approval"]["sha256"],paused["research_plan_sha256"])
    def test_recovery_and_cancel(self):
        p=self.m.propose(request());self.assertEqual(self.m.cancel(p["id"])["state"],"CANCELLED")
        p=self.m.propose(request());p["state"]="RUNNING";self.m._save(p,"fixture")
        self.assertEqual(self.m.recover(p["id"])["state"],"INTERRUPTED")
        with self.assertRaises(ValueError):self.m.run(p["id"],p["plan_sha256"],[])
    def test_budget_no_further_stage(self):
        p=self.m.propose(request());self.m.approve(p["id"],p["plan_sha256"],"approved")
        with patch.object(m.time,"monotonic",side_effect=[0,901]),patch.object(m,"stage_result") as f:
            r=self.m.run(p["id"],p["plan_sha256"],[])
        self.assertEqual(r["state"],"BLOCKED");f.assert_not_called()

if __name__=="__main__":unittest.main()
