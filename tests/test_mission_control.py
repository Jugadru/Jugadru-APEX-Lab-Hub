import json
from pathlib import Path
import tempfile
import unittest
from hub import mission_control as m, dashboard as d
from hub.gateway import Gateway
from hub.registry import Registry
from hub.health import Health

class MissionControlTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.reg=Registry(self.root/"state")
    def test_missing_not_zero_workload(self):
        s=m.summarize(d.collect([("local",self.reg.root)]))
        self.assertEqual(s["groups"]["Graphics"]["coverage"],{"NOT_RUN":2})
        self.assertEqual(s["groups"]["Missions"]["coverage"],{"NOT_RUN":1,"EMPTY":1})
        self.assertFalse(s["execution_authorized"])
    def test_readonly_export_and_tamper(self):
        g=Gateway(self.reg.root);p=g.propose_ci(42,"a"*40,"main")
        g.approve(p["id"],p["plan_sha256"],"SECRET_FIXTURE <script>alert(1)</script>")
        before=self.reg.db.read_bytes();out=self.root/"out"
        s=m.export([("local",self.reg.root)],out)
        self.assertEqual(before,self.reg.db.read_bytes())
        self.assertEqual(s["recent_queue"][0]["priority"],"active")
        self.assertEqual(m.verify(out)["status"],"PASS")
        for path in out.iterdir():
            self.assertEqual(path.stat().st_mode&0o777,0o600)
            self.assertNotIn("SECRET_FIXTURE",path.read_text())
            self.assertNotIn("<script>",path.read_text())
        (out/"mission-control.html").write_text("changed")
        with self.assertRaises(ValueError):m.verify(out)
    def test_lifecycle_counts_not_recent_counts(self):
        g=Gateway(self.reg.root)
        for _ in range(24):g.propose_ci(42,"a"*40,"main")
        s=m.summarize(d.collect([("local",self.reg.root)]))
        self.assertEqual(s["groups"]["Departments"]["counts"],{"PROPOSED":24})
        self.assertEqual(s["lifecycle_totals"]["review"],24)
        self.assertEqual(s["queue_matching_recent"],20)
    def test_outcome_and_state_distinct(self):
        source=d.collect([("local",self.reg.root)])
        section=source["registries"][0]["sections"]["department_plans"]
        section.update(status="RECORDED",total=1,counts={"OBSERVED":1},
                       recent=[{"id":"a"*32,"state":"OBSERVED","details":{"observed_outcome":"FAIL"}}])
        s=m.summarize(source)
        self.assertEqual(s["lifecycle_totals"]["recorded"],1)
        self.assertEqual(s["recent_queue"][0]["priority"],"attention")
        self.assertEqual(s["recent_queue"][0]["outcome"],"FAIL")
    def test_health_not_aggregate_pass(self):
        Health(self.reg.root).scan()
        s=m.summarize(d.collect([("local",self.reg.root)]))
        self.assertEqual(s["groups"]["Infrastructure"]["unclassified"],1)
        self.assertEqual(len(s["health_latest_per_registry"]),1)
        self.assertEqual(s["health_latest_per_registry"][0]["observations"]["repositories"]["hyper"]["remote"],"NOT_RUN")
    def test_real_collaboration_pending_is_review(self):
        from hub.collaboration import Collaboration
        from test_gateway import task
        c=Collaboration(self.reg.root)
        item=task("revision","software","repo.revision","hub")
        del item["department"]
        item["topic"]="auto"
        req={"schema":1,"objective":"Review revision","targets":{"hub":"a"*40},"tasks":[item]}
        p=c.propose(req,["software","quality"],{"software":[],"quality":[]})
        source=d.collect([("local",self.reg.root)])
        row=source["registries"][0]["sections"]["collaboration_cases"]["recent"][0]
        self.assertEqual(row["details"]["outcome"],"UNKNOWN")
        q=m.summarize(source)["recent_queue"][0]
        self.assertEqual(q["id"],p["id"])
        self.assertEqual(q["priority"],"review")
        self.assertIsNone(q["outcome"])

    def test_running_default_and_terminal_outcome(self):
        source=d.collect([("local",self.reg.root)])
        section=source["registries"][0]["sections"]["sessions"]
        section.update(status="RECORDED",total=2,counts={"RUNNING":1,"FINISHED":1},
                       recent=[{"id":"a"*32,"state":"RUNNING","details":{"outcome":"BLOCKED"}},
                               {"id":"b"*32,"state":"FINISHED","details":{"outcome":"BLOCKED"}}])
        rows={x["id"]:x for x in m.summarize(source)["recent_queue"]}
        self.assertEqual(rows["a"*32]["priority"],"active")
        self.assertIsNone(rows["a"*32]["outcome"])
        self.assertEqual(rows["b"*32]["priority"],"attention")
        self.assertEqual(rows["b"*32]["outcome"],"BLOCKED")

    def test_terminal_unknown_remains_attention(self):
        source=d.collect([("local",self.reg.root)])
        sec=source["registries"][0]["sections"]["collaboration_cases"]
        sec.update(status="RECORDED",total=1,counts={"OBSERVED":1},
                   recent=[{"id":"a"*32,"state":"OBSERVED","details":{"outcome":"UNKNOWN"}}])
        q=m.summarize(source)["recent_queue"][0]
        self.assertEqual(q["priority"],"attention")

    def test_queue_cap(self):
        source=d.collect([("local",self.reg.root)])
        for table in ("gateway_cases","missions","collaboration_cases","research_cases"):
            sec=source["registries"][0]["sections"][table]
            sec.update(status="RECORDED",total=20,counts={"PROPOSED":20},
                       recent=[{"id":format(i,"032x"),"state":"PROPOSED","details":{}} for i in range(20)])
        s=m.summarize(source)
        self.assertEqual(len(s["recent_queue"]),60)
        self.assertEqual(s["queue_omitted_recent"],20)
if __name__=="__main__":unittest.main()
