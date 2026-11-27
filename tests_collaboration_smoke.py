"""Real operator-role collaboration, conflict, fresh reviews and Gateway observation."""
import json,os,pathlib,subprocess
from hub.collaboration import Collaboration
from hub.gateway import Gateway
from hub.knowledge import Knowledge
from hub.dashboard import export
root=pathlib.Path(os.environ["RUNNER_TEMP"]);w=pathlib.Path(os.environ["GITHUB_WORKSPACE"])
sha=subprocess.check_output(["git","rev-parse","HEAD"],text=True).strip()
game=subprocess.check_output(["git","-C",str(w/"game"),"rev-parse","HEAD"],text=True).strip()
prior=json.loads((root/"hub-smoke/knowledge/report.json").read_text())["evidence"][0]
c=Collaboration(root/"collaboration-state")
req={"schema":1,"objective":"Collaborative source review","targets":{"hub":sha,"game":game},
     "tasks":[{"id":"revision","objective":"Validate hub-revision","scope":"reviewed exact checkout","priority":90,"depends_on":[],"capability":"repo.revision","target":"hub","permissions":["read"],"acceptance":"exit_zero","timeout_seconds":15,"topic":"auto"},
              {"id":"inventory","objective":"Validate inventory","scope":"reviewed exact checkout","priority":80,"depends_on":["revision"],"capability":"source.inventory","target":"game","permissions":["read"],"acceptance":"exit_zero","timeout_seconds":90,"topic":"auto"}]}
roles=["software","performance","quality"]
p=c.propose(req,roles,{"software":[prior["id"]],"performance":[],"quality":[]},root/"knowledge-state")
for role in roles:p=c.vote(p["id"],p["plan_sha256"],role,"revise" if role=="performance" else "accept","Synthetic operator position")
assert p["state"]=="CONFLICT"
p=c.revise(p["id"],p["plan_sha256"],{**req,"objective":"Clarified read-only source review"},"Clarify scope")
assert p["round"]==2 and not p["votes"]
for role in roles:p=c.vote(p["id"],p["plan_sha256"],role,"accept","New round")
p=c.handoff(p["id"],p["plan_sha256"])
dep=c.departments.get(p["department_id"])
assert dep["state"]=="PROPOSED" and not dep["reviews"]
dep=c.departments.review(dep["id"],dep["plan_sha256"],"quality","accept","Separate review")
dep=c.departments.delegate(dep["id"],dep["plan_sha256"])
g=Gateway(c.store.root);q=g.get(dep["gateway_id"])
assert q["state"]=="PROPOSED"
g.approve(q["id"],q["plan_sha256"],"Separate execution approval")
assert g.run(q["id"],q["plan_sha256"],[("hub",pathlib.Path.cwd(),sha),("game",w/"game",game)])["state"]=="PASS"
c.departments.observe(dep["id"]);p=c.observe(p["id"])
assert p["state"]=="OBSERVED" and p["outcome"]=="PASS"
k=Knowledge(root/"collaboration-memory")
entry=k.ingest("department",c.store.root,dep["id"])
assert k.audit(entry["id"],c.store.root)["status"]=="PASS"
out=root/"hub-smoke/collaboration";out.mkdir()
(out/"report.json").write_text(json.dumps({"collaboration":p,"history":c.history(p["id"]),"memory":entry},indent=2))
report=export([("collaboration",c.store.root)],out/"dashboard")
assert report["registries"][0]["sections"]["collaboration_cases"]["counts"]=={"OBSERVED":1}
print("REAL_COLLABORATION_PASS",sha,p["id"],dep["id"],p["round"])
