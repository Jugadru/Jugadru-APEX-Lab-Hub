import json
from pathlib import Path
import tempfile
import unittest
from hub import dashboard as d
from hub.registry import Registry
from hub.gateway import Gateway
from hub.health import Health

class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.t=tempfile.TemporaryDirectory();self.addCleanup(self.t.cleanup)
        self.root=Path(self.t.name)
        self.reg=Registry(self.root/"state")
    def test_missing_and_empty_distinct(self):
        r=d.collect([("local",self.reg.root)])["registries"][0]["sections"]
        self.assertEqual(r["sessions"]["status"],"EMPTY")
        self.assertEqual(r["gateway_cases"]["status"],"NOT_RUN")
        self.assertIsNone(r["gateway_cases"]["total"])
    def test_read_only_projection_and_private_export(self):
        g=Gateway(self.reg.root)
        row=g.propose_ci(42,"a"*40,"main")
        g.approve(row["id"],row["plan_sha256"],"PRIVATE_TOKEN_FIXTURE <script>alert(1)</script>")
        before=self.reg.db.read_bytes()
        out=self.root/"out"
        report=d.export([("lab",self.reg.root)],out)
        self.assertEqual(before,self.reg.db.read_bytes())
        self.assertEqual(report["registries"][0]["sections"]["gateway_cases"]["counts"],{"APPROVED":1})
        self.assertFalse(report["live"])
        for p in out.iterdir():
            self.assertEqual(p.stat().st_mode&0o777,0o600)
            self.assertNotIn("PRIVATE_TOKEN_FIXTURE",p.read_text())
            self.assertNotIn("<script>",p.read_text())
        self.assertIn("APEX COMMAND CENTER",(out/"index.html").read_text())
        with self.assertRaises(ValueError):d.export([("lab",self.reg.root)],out)
    def test_multiple_sources_and_duplicates(self):
        other=Registry(self.root/"other")
        self.assertEqual(len(d.collect([("a",self.reg.root),("b",other.root)])["registries"]),2)
        for bindings in ([("a",self.reg.root),("a",other.root)],[("a",self.reg.root),("b",self.reg.root)]):
            with self.assertRaises(ValueError):d.collect(bindings)
    def test_health_projection(self):
        h=Health(self.reg.root);p=h.scan()
        r=d.collect([("local",self.reg.root)])["registries"][0]["sections"]["health_snapshots"]
        self.assertEqual(r["recent"][0]["id"],p["id"])
        self.assertEqual(r["recent"][0]["details"]["observations"]["repositories"]["hyper"]["remote"],"NOT_RUN")
    def test_insecure_and_missing_db(self):
        missing=self.root/"missing"
        with self.assertRaises(ValueError):d.collect([("a",missing)])
        self.assertFalse(missing.exists())
        self.reg.root.chmod(0o755)
        with self.assertRaises(ValueError):d.collect([("a",self.reg.root)])
        self.reg.root.chmod(0o700)
    def test_inconsistent_state_rejected(self):
        g=Gateway(self.reg.root);p=g.propose_ci(42,"a"*40,"main")
        with self.reg.connect() as db:
            db.execute("UPDATE gateway_cases SET state='PASS' WHERE id=?",(p["id"],))
        with self.assertRaises(ValueError):d.collect([("lab",self.reg.root)])

if __name__=="__main__":unittest.main()
