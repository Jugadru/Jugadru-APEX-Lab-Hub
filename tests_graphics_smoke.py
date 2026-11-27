"""Actual public Factory validator and plan; synthetic GLB only, no native rendering."""
import json,os,pathlib,subprocess,sys,struct
from hub.graphics import Graphics,FACTORY_SHA
from hub.geometry import Geometry
from hub.dashboard import export
root=pathlib.Path(os.environ["RUNNER_TEMP"])
factory=pathlib.Path(os.environ["GITHUB_WORKSPACE"])/"factory"
sha=subprocess.check_output(["git","rev-parse","HEAD"],text=True).strip()
# Run the domain's own test suite against exact public revision.
subprocess.run([sys.executable,"-B","-m","unittest","discover","-s","tools/tests","-v"],cwd=factory,check=True,timeout=120)
state=root/"advanced-graphics-state"
p=Graphics(state).run(factory,FACTORY_SHA,factory/"tools/asset_factory/demo.json")
assert p["state"]=="PASS" and p["native"]=="NOT_RUN",p
bad=root/"bad-request.json";bad.write_text(json.dumps({**p["request"],"references":["unreviewed"]}))
assert Graphics(state).run(factory,FACTORY_SHA,bad)["state"]=="BLOCKED"
# Reuse domain fixture generator, not duplicate the validator.
sys.path.insert(0,str(factory/"tools/tests"))
from test_advanced_validator import fixture,encode
doc,raw=fixture()
a=root/"a.glb";b=root/"b.glb";c=root/"c.glb"
a.write_bytes(encode(doc,raw))
struct.pack_into("<f",raw,72,5e-8);b.write_bytes(encode(doc,raw))
struct.pack_into("<f",raw,72,.001);c.write_bytes(encode(doc,raw))
g=Geometry(state)
r=g.run(factory,FACTORY_SHA,a)
assert r["state"]=="PASS" and r["result"]["triangles"]==1,r
eq=g.run(factory,FACTORY_SHA,a,b)
assert eq["state"]=="PASS" and eq["result"]["numeric_equivalent"] and not eq["result"]["binary_equal"],eq
neq=g.run(factory,FACTORY_SHA,a,c)
assert neq["state"]=="FAIL" and not neq["result"]["numeric_equivalent"],neq
c.write_bytes(b"invalid")
reject=g.run(factory,FACTORY_SHA,c)
assert reject["state"]=="FAIL",reject
out=root/"hub-smoke/advanced-graphics";out.mkdir()
(out/"report.json").write_text(json.dumps({"plan":p,"inspect":r,"equivalent":eq,"different":neq,"invalid":reject,
    "native_build":"NOT_RUN","visual_review":"NOT_RUN","physical_gpu":"NOT_RUN"},indent=2))
report=export([("graphics",state)],out/"dashboard")
assert report["registries"][0]["sections"]["geometry_audits"]["counts"]=={"PASS":2,"FAIL":2}
print("REAL_ADVANCED_GRAPHICS_PASS",sha,FACTORY_SHA,r["id"],eq["id"])
