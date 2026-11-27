"""Public synthetic candidate build; no remote publication."""
import json,os,pathlib,subprocess
from hub.code_builder import Builder
from hub.development import Development
from hub.dashboard import export
root=pathlib.Path(os.environ["RUNNER_TEMP"])
sha=subprocess.check_output(["git","rev-parse","HEAD"],text=True).strip()
d=Development(root/"code-state")
p=d.create("Synthetic local code candidate",sha,["hub/features/demo_24.py","tests/test_feature_demo_24.py"])
d.audit(p["id"],pathlib.Path.cwd())
d.plan(p["id"],"Add isolated arithmetic demo and additive test")
b=Builder(d.store.root)
q=b.propose({"schema":1,"development_id":p["id"],"edits":[
    {"path":"hub/features/demo_24.py","expected_blob":None,"content":"def double(n):\n return n * 2\n"},
    {"path":"tests/test_feature_demo_24.py","expected_blob":None,"content":"import unittest\nfrom hub.features.demo_24 import double\nclass Demo24(unittest.TestCase):\n def test_double(self): self.assertEqual(double(4),8)\n"}]})
b.approve(q["id"],q["plan_sha256"])
result=b.build(q["id"],q["plan_sha256"],pathlib.Path.cwd())
assert result["state"]=="PASS",result
assert result["local_tests"]["count"]>=112
handoff=b.handoff(q["id"])
assert handoff["state"]=="AWAITING_CI" and not handoff["merge_authorized"]
assert subprocess.check_output(["git","rev-parse","HEAD"],text=True).strip()==sha
out=root/"hub-smoke/code-builder";out.mkdir()
(out/"report.json").write_text(json.dumps({"build":result,"handoff":handoff},indent=2))
export([("code",d.store.root)],out/"dashboard")
print("REAL_LOCAL_CODE_CANDIDATE_PASS",sha,q["id"],result["candidate"]["sha"],result["local_tests"]["count"])
