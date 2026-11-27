"""Real public exact-pin research integration; fixed synthetic tests only."""
import json,os,pathlib,subprocess
from hub import research as r
from hub.knowledge import Knowledge
from hub.dashboard import export
root=pathlib.Path(os.environ["RUNNER_TEMP"])
w=pathlib.Path(os.environ["GITHUB_WORKSPACE"])
sha=subprocess.check_output(["git","rev-parse","HEAD"],text=True).strip()
state=root/"racecraft-research-state"
lab=r.Research(state)
k=Knowledge(root/"racecraft-memory")
# Use small same-domain batches, no dynamic admission and no criteria changes.
groups=[sorted(r.PHYSICS_AND_LEGACY),sorted(r.RACECRAFT),sorted(r.STRATEGY)]
out=root/"hub-smoke/racecraft";out.mkdir()
cases=[];reports=[];failures=[]
for group in groups:
    for i in range(0,len(group),4):
        names=group[i:i+4]
        p=lab.prepare(w/"foundation",w/"engineering",names,"Fixed public synthetic consumer compatibility")
        assert p["state"]=="PLANNED",p
        # Fixed CI fixture scope: digest gate exercised; not independent human scientific review.
        with open(state/("research-"+p["id"])/"frozen-plan.json") as f:plan=json.load(f)
        assert plan["catalog_binding"]["status"]=="MATCH"
        final=lab.run(p["id"],w/"foundation",w/"engineering",p["plan_sha256"])
        cases.append(final)
        (out/"cases.json").write_text(json.dumps(cases,indent=2))
        if final["state"] not in ("PASS","FAIL","INCONCLUSIVE"):
            raise AssertionError(final)
        c=r.load(state/("research-"+p["id"])/"comparison.json")
        reports.extend(c["experiments"])
        if final["state"]!="PASS":failures.append(final["id"])
        entry=k.ingest("research",state,p["id"])
        assert k.audit(entry["id"],state)["status"]=="PASS"
        if set(names)&(r.RACECRAFT|r.STRATEGY):assert "racecraft" in entry["evidence"]["departments"]
        (out/"comparison.json").write_text(json.dumps({"revisions":r.PINS,"experiments":reports,
            "scientific_validation":"NOT_PERFORMED","gameplay_validation":"NOT_RUN","promotion":"NOT_AUTHORIZED"},indent=2))
assert len(reports)==34 and {x["experiment"] for x in reports}==r.BUILTINS
assert not failures,failures
assert len(k.search(department="racecraft"))==6
report=export([("research",state),("knowledge",k.store.root)],out/"dashboard")
assert report["registries"][0]["sections"]["research_cases"]["counts"]=={"PASS":len(cases)}
print("REAL_ENGINEERING_RACECRAFT_PASS",sha,r.ENGINEERING_SHA,len(reports),len(cases))
