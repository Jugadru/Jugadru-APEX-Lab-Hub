"""Actual 3.0 cycle evidence audit with reversible synthetic log tamper."""
import json,os,pathlib,subprocess
from hub.development_cycle import Cycle
from hub.code_builder import Builder
from hub import orchestrator as o
root=pathlib.Path(os.environ["RUNNER_TEMP"])
sha=subprocess.check_output(["git","rev-parse","HEAD"],text=True).strip()
r=json.loads((root/"hub-smoke/development-cycle/report.json").read_text())["cycle"]
x=Cycle(root/"collaboration-state")
before=x.store.db.read_bytes()
a=x.audit(r["id"])
assert a["status"]=="PASS" and a["recorded_development_state"]=="AWAITING_CI" and a["live_ci"]=="NOT_RUN"
q=Builder(x.store.root).get(r["builder_id"])
log=x.store.root/("code-"+q["id"])/"tests.log"
raw=log.read_bytes()
try:
    log.write_bytes(raw+b"\nSYNTHETIC_TAMPER\n")
    try:x.audit(r["id"])
    except ValueError:rejected=True
    else:rejected=False
    assert rejected,"Changed local evidence was accepted"
finally:log.write_bytes(raw)
assert x.audit(r["id"])==a and x.store.db.read_bytes()==before
out=root/"hub-smoke/system-audit";out.mkdir()
(out/"report.json").write_text(json.dumps({"revision":sha,"audit":a,"tamper_rejected":True,"history_unchanged":True},indent=2))
print("REAL_SYSTEM_AUDIT_PASS",sha,r["id"],a["original_handoff_sha256"])
