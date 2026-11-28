"""Real full local cycle on exact Hub checkout, with earlier verified collaboration."""
import json,os,pathlib,subprocess
from hub.development_cycle import Cycle
from hub.mission_control import export,verify
root=pathlib.Path(os.environ["RUNNER_TEMP"])
sha=subprocess.check_output(["git","rev-parse","HEAD"],text=True).strip()
collab=json.loads((root/"hub-smoke/collaboration/report.json").read_text())["collaboration"]
x=Cycle(root/"collaboration-state")
p=x.prepare({"schema":1,"collaboration_id":collab["id"],"objective":"Synthetic isolated cycle candidate",
    "edits":[{"path":"hub/features/demo_30.py","expected_blob":None,"content":"def twice(n):\n return n*2\n"},
             {"path":"tests/test_feature_demo_30.py","expected_blob":None,"content":"import unittest\nfrom hub.features.demo_30 import twice\nclass Demo(unittest.TestCase):\n def test_twice(self): self.assertEqual(twice(3),6)\n"}]},pathlib.Path.cwd())
assert p["state"]=="PROPOSED",p
x.approve(p["id"],p["plan_sha256"])
r=x.run(p["id"],p["plan_sha256"],pathlib.Path.cwd())
assert r["state"]=="AWAITING_CI" and r["ci"]=="NOT_RUN" and r["local_tests"]>=144,r
assert x.verify(p["id"])["status"]=="PASS"
assert subprocess.check_output(["git","rev-parse","HEAD"],text=True).strip()==sha
out=root/"hub-smoke/development-cycle";out.mkdir()
(out/"report.json").write_text(json.dumps({"cycle":r,"history":x.history(p["id"])},indent=2))
s=export([("cycle",x.store.root)],out/"control")
assert s["groups"]["Development"]["counts"]["AWAITING_CI"]==2
assert verify(out/"control")["status"]=="PASS"
print("REAL_DEVELOPMENT_CYCLE_PASS",sha,r["id"],r["candidate_sha"],r["local_tests"])
