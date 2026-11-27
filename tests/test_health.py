import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from hub import health as h, orchestrator as o
from test_orchestrator import repo

SHA="a"*40
def run(name,ident=1,conclusion="success"):
    return {"name":name,"id":ident,"head_sha":SHA,"event":"push","head_branch":"main",
            "repository":{"full_name":o.REPOSITORIES["hub"]},"status":"completed","conclusion":conclusion,"run_attempt":1}

class HealthTests(unittest.TestCase):
    def setUp(self):
        self.t=tempfile.TemporaryDirectory();self.addCleanup(self.t.cleanup)
        self.root=Path(self.t.name)
    def test_local_snapshot_persistence(self):
        binding=repo(self.root/"hub","hub")
        state=h.Health(self.root/"state")
        r=state.scan([binding])
        self.assertEqual(r["local_repositories"]["hub"]["status"],"PASS")
        self.assertEqual(r["remote_repositories"]["hub"]["metadata"]["status"],"NOT_RUN")
        self.assertEqual(r["resources"]["disk"]["status"],"PASS")
        self.assertEqual(h.Health(self.root/"state").get(r["id"]),r)
        self.assertNotEqual(r["summary"]["status"],"PASS")
        self.assertFalse(r["merge_authorized"])
    def test_ci_latest_failure_and_missing(self):
        name=h.WORKFLOWS["hub"][0]
        rows=[run(name,1),run(name,2,"failure")]
        self.assertEqual(h.observe_ci("hub",SHA,{"total_count":2,"workflow_runs":rows})["status"],"FAIL")
        self.assertEqual(h.observe_ci("hub",SHA,{"total_count":3,"workflow_runs":rows})["status"],"INCONCLUSIVE")
        self.assertEqual(h.observe_ci("hub",SHA,{"total_count":0,"workflow_runs":[]})["status"],"INCONCLUSIVE")
        rows[0]["head_sha"]="b"*40
        with self.assertRaises(ValueError):h.observe_ci("hub",SHA,{"total_count":2,"workflow_runs":rows})
    def test_current_main_running_is_not_success(self):
        name=h.WORKFLOWS["hub"][0]
        rows=[run(name,1),{**run(name,2,None),"status":"in_progress"}]
        self.assertEqual(h.observe_ci("hub",SHA,{"total_count":2,"workflow_runs":rows})["status"],"INCONCLUSIVE")

    def test_remote_identity_visibility_and_access(self):
        def reader(target,suffix):
            if suffix=="":return {"full_name":o.REPOSITORIES[target],"default_branch":"main","private":False}
            if suffix=="/branches/main":return {"name":"main","commit":{"sha":SHA}}
            raise ValueError("GitHub HTTP 403")
        r=h.remote("hub",reader)
        self.assertEqual(r["metadata"]["status"],"PASS")
        self.assertEqual(h.remote("architect",reader)["metadata"]["status"],"FAIL")
        def private_reader(target,suffix):
            result=reader(target,suffix)
            if suffix=="":result["private"]=True
            return result
        self.assertEqual(h.remote("architect",private_reader)["metadata"]["status"],"PASS")
        self.assertEqual(h.remote("hub",private_reader)["metadata"]["status"],"FAIL")
        self.assertEqual(r["ci"]["status"],"BLOCKED")
        self.assertEqual(h.remote("hub",lambda *a:{})["metadata"]["status"],"BLOCKED")
    def test_pin_drift(self):
        local={k:h.status("NOT_RUN") for k in o.REPOSITORIES}
        remote={k:{"metadata":h.status("NOT_RUN")} for k in local}
        remote["engineering"]["metadata"]=h.status("PASS",sha=SHA)
        self.assertEqual(h.compatibility(local,remote)["engineering"]["status"],"BLOCKED")
        local["foundation"]=h.status("PASS",sha=h.PINS["foundation"])
        self.assertEqual(h.compatibility(local,remote)["foundation"]["status"],"PASS")
    def test_evidence_bytes_and_tamper(self):
        f=self.root/"artifact.bin";f.write_bytes(b"abc")
        p=self.root/"report.json"
        row={"path":"artifact.bin","bytes":3,"sha256":hashlib.sha256(b"abc").hexdigest()}
        p.write_text(json.dumps({"evidence":[row]}))
        self.assertEqual(h.verify_evidence(self.root,p)["status"],"PASS")
        f.write_bytes(b"xyz")
        self.assertEqual(h.verify_evidence(self.root,p)["status"],"FAIL")
        f.unlink()
        self.assertEqual(h.verify_evidence(self.root,p)["status"],"BLOCKED")
    def test_evidence_traversal_symlink_duplicates(self):
        f=self.root/"file";f.write_bytes(b"x")
        (self.root/"link").symlink_to(f)
        p=self.root/"report.json";row={"path":"link","bytes":1,"sha256":hashlib.sha256(b"x").hexdigest()}
        p.write_text(json.dumps({"evidence":[row]}))
        self.assertEqual(h.verify_evidence(self.root,p)["status"],"BLOCKED")
        for rows in ([{**row,"path":"../file"}],[row,row],[{**row,"bytes":h.MAX_FILE+1}]):
            p.write_text(json.dumps({"evidence":rows}))
            self.assertEqual(h.verify_evidence(self.root,p)["status"],"BLOCKED")
    def test_runtime_digest_and_no_secret(self):
        work=self.root/"work";work.mkdir()
        with patch.object(h.graphics,"executable",side_effect=ValueError("invalid")):
            r=h.runtimes([("blender","/missing","a"*64)],work)
        self.assertEqual(r["blender"]["status"],"BLOCKED")
        with patch.dict("os.environ",{h.TOKENS["hub"]:"secret-fixture"}):
            s=h.security()
            self.assertTrue(s["credential_presence"]["hub"])
            self.assertNotIn("secret-fixture",json.dumps(s))
    def test_recovery_advisory(self):
        from hub.gateway import Gateway
        state=h.Health(self.root/"state")
        g=Gateway(self.root/"state")
        case=g.propose_ci(42,SHA,"main")
        with g.store.connect() as db:
            row=g._get(db,case["id"]);row.update(state="RUNNING",attempts=1)
            g._save(db,row,"fixture")
        r=h.recovery(state.store)
        self.assertEqual(r["status"],"INCONCLUSIVE")
        self.assertFalse(r["automatic_recovery"])
        self.assertEqual(g.get(case["id"])["state"],"RUNNING")

if __name__=="__main__":unittest.main()
