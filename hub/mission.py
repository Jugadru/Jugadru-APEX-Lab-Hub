"""Bounded, operator-approved multi-domain mission; no independent coding agent."""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
import uuid

from . import orchestrator as o, gateway, scheduler, research, graphics, development, health, dashboard
from .registry import Registry, encode, ident, now, MAX_SNAPSHOT

MAX_ACTIVE=16
MAX_STAGES=9
MAX_SLICE_SECONDS=900
GLOBAL_LOCK="f"*32
FIELDS={"schema","objective","targets","tasks","calibration","research","graphics","development"}
TERMINAL={"PASS","FAIL","BLOCKED","INCONCLUSIVE","INTERRUPTED","CANCELLED"}


def validate(data):
    o.require(isinstance(data,dict) and set(data)==FIELDS and type(data["schema"]) is int and data["schema"]==1,
              "Invalid mission schema")
    objective=gateway.text(data["objective"])
    targets=data["targets"]
    o.require(isinstance(targets,dict) and "hub" in targets and
              all(k in o.REPOSITORIES and isinstance(v,str) and o.SHA.fullmatch(v) for k,v in targets.items()),
              "Exact mission target revisions required")
    tasks=data["tasks"]
    o.require(isinstance(tasks,list) and 1<=len(tasks)<=gateway.MAX_TASKS,"Delegation tasks required")
    task_targets={t.get("target") for t in tasks if isinstance(t,dict) and isinstance(t.get("target"),str)}
    o.require(task_targets<=targets.keys(),"Missing delegation target")
    delegation={"schema":1,"objective":objective,"targets":{k:targets[k] for k in task_targets},"tasks":tasks}
    gateway.validate_request(delegation)
    calibration=data["calibration"]
    if calibration is not None:
        calibration=scheduler.contract(calibration)
    names=data["research"]
    o.require(isinstance(names,list) and len(names)<=1 and all(isinstance(n,str) and n in research.BUILTINS for n in names),
              "At most one reviewed research fixture per mission")
    o.require(type(data["graphics"]) is bool,"Graphics plan flag required")
    scope=data["development"]
    o.require(isinstance(scope,list),"Development scope must be a list")
    if scope:scope=development.scope(scope)
    needed={"hub"}|task_targets
    if names:
        needed|=research.PINS.keys()
        o.require(all(targets.get(k)==v for k,v in research.PINS.items()),"Reviewed research pins required")
    if data["graphics"]:
        needed.add("assets")
        o.require(targets.get("assets")==graphics.FACTORY_SHA,"Reviewed Factory pin required")
    o.require(set(targets)==needed,"Missing or unused target")
    stages=["health","delegation"]
    if calibration is not None:stages.append("calibration")
    if names:stages.extend(("research_plan","research_run"))
    if data["graphics"]:stages.append("graphics_plan")
    if scope:stages.append("development_plan")
    stages.extend(("quality","dashboard"))
    o.require(len(stages)<=MAX_STAGES,"Stage budget exceeded")
    return {"schema":1,"objective":objective,"targets":dict(targets),"delegation":delegation,
            "calibration":calibration,"research":names,"graphics":data["graphics"],
            "development":scope,"stages":stages,"attempts_per_stage":1,
            "slice_seconds":MAX_SLICE_SECONDS,"merge_authorized":False,
            "native_build_authorized":False,"code_generation_authorized":False}


def check_bindings(plan,bindings,root):
    targets=o.checked_targets(bindings)
    o.require({k:v[1] for k,v in targets.items()}==plan["targets"],"Mission checkout mismatch")
    o.require(targets["hub"][0]==Path(__file__).resolve().parent.parent,
              "Run mission from its exact approved Hub checkout")
    o.require(all(not root.is_relative_to(p) and not p.is_relative_to(root) for p,_ in targets.values()),
              "Mission registry must be outside all checkouts")
    return targets


def receipt(work,stage,value):
    p=work/(stage+".json")
    with p.open("x",encoding="utf-8") as f:
        f.write(encode(value)+"\n")
    return {"path":p.name,"bytes":p.stat().st_size,"sha256":o.digest(p)}


def verify_receipt(work,entry):
    o.require(entry["path"]==entry["stage"]+".json" and entry["stage"] in {"health","delegation","calibration","research_plan","research_run","graphics_plan","development_plan","quality","dashboard"}, "Receipt identity mismatch")
    p=work/entry["path"]
    o.require(p.is_file() and p.resolve()==p and type(entry["bytes"]) is int and
              0<entry["bytes"]<=MAX_SNAPSHOT+1 and p.stat().st_size==entry["bytes"],"Mission receipt size/path changed")
    with p.open("rb") as f:raw=f.read(MAX_SNAPSHOT+2)
    o.require(len(raw)==entry["bytes"] and hashlib.sha256(raw).hexdigest()==entry["sha256"],"Mission receipt changed")
    value=json.loads(raw,object_pairs_hook=o.unique_object)
    o.require(isinstance(value,dict),"Mission receipt must be an object")
    return value


def stage_result(work,stage,plan,targets,prior):
    domain=work/"domain"
    bindings=[(k,str(p),s) for k,(p,s) in targets.items()]
    hub,sha=targets.get("hub",(None,None))
    if stage=="health":
        result=health.Health(domain).scan(bindings)
        good=all(result["local_repositories"][k]["status"]=="PASS" for k in targets)
        good=good and all(result["runtimes"][k]["status"]=="PASS" for k in ("python","git"))
        good=good and all(result["resources"][k]["status"]=="PASS" for k in ("cpu","memory","disk"))
        # Global NOT_RUN is not erased; only explicitly required local checks gate this mission.
        return ("PASS" if good else "BLOCKED"),result
    if stage=="delegation":
        g=gateway.Gateway(domain)
        p=g.propose(plan["delegation"])
        g.approve(p["id"],p["plan_sha256"],"Covered by approved mission delegation")
        selected=[b for b in bindings if b[0] in plan["delegation"]["targets"]]
        result=g.run(p["id"],p["plan_sha256"],selected)
        return result["state"],result
    if stage=="calibration":
        s=scheduler.Scheduler(domain)
        o.require(not s.list(),"Mission calibration registry must be empty")
        p=s.enqueue(plan["calibration"],sha)
        result=s.run_next(hub,sha)
        o.require(result.get("id")==p["id"],"Wrong scheduled experiment")
        return result["state"],result
    if stage=="research_plan":
        result=research.Research(domain).prepare(targets["foundation"][0],targets["engineering"][0],
                                                plan["research"],plan["objective"])
        return ("PASS" if result["state"]=="PLANNED" else "BLOCKED"),result
    if stage=="research_run":
        p=prior["research_plan"]
        result=research.Research(domain).run(p["id"],targets["foundation"][0],targets["engineering"][0],p["plan_sha256"])
        return result["state"],result
    if stage=="graphics_plan":
        p,s=targets["assets"]
        result=graphics.Graphics(domain).run(p,s,p/"tools/asset_factory/demo.json")
        return result["state"],result
    if stage=="development_plan":
        d=development.Development(domain)
        p=d.create(plan["objective"],sha,plan["development"])
        d.audit(p["id"],hub)
        result=d.plan(p["id"],"Approved mission scope; candidate implementation and CI remain separate operator actions.")
        return ("PASS" if result["state"]=="PLANNED" else "BLOCKED"),result
    if stage=="quality":
        checks={}
        for k,v in prior.items():
            if k=="delegation":
                # Gateway receipt and the underlying DAG artifacts must both match.
                checks[k]=health.verify_evidence(domain,work/"delegation.json")
                p=domain/v["receipt"]["path"]
                checks["delegated_artifacts"]=health.verify_evidence(p.parent,p)
            elif k=="calibration":
                checks[k]=health.verify_evidence(domain,work/"calibration.json")
            elif k=="graphics_plan":
                checks[k]=health.verify_evidence(domain/("graphics-"+v["id"]),work/"graphics_plan.json")
            elif k=="research_plan":
                p=domain/("research-"+v["id"])/"frozen-plan.json"
                checks[k]={"status":"PASS" if p.is_file() and not p.is_symlink() and o.digest(p)==v["plan_sha256"] else "FAIL"}
            elif k=="research_run":
                p=domain/("research-"+v["id"])/"comparison.json"
                checks[k]={"status":"PASS" if p.is_file() and not p.is_symlink() and o.digest(p)==v.get("comparison_sha256") else "FAIL"}
        good=bool(checks) and all(v["status"]=="PASS" for v in checks.values())
        return ("PASS" if good else "BLOCKED"),{"checks":checks,"scope":"Recorded byte integrity and domain acceptance, not scientific or game approval"}
    if stage=="dashboard":
        result=dashboard.export([("mission-domains",domain)],work/"dashboard")
        files=[graphics.file_evidence(work,"dashboard/"+n,4*1024*1024) for n in ("index.html","dashboard.json")]
        return "PASS",{"registries":len(result["registries"]),"evidence":files,"live":False}
    raise ValueError("Unknown stage")


def audit_completed(row,work):
    """Check preserved mission bytes and domain acceptance, not a new execution."""
    result={"id":row["id"],"recorded_state":row["state"],"status":"NOT_RUN","checks":{},
            "historical_state_changed":False,"production_approved":False}
    if row["state"]!="PASS":return result
    checks=result["checks"]
    try:
        plan=row["plan"]
        o.require(row["plan_sha256"]==row["approval"]["sha256"]==gateway.sha_json(plan),"Plan approval mismatch")
        o.require(row["merge_authorized"] is False and row["production_approved"] is False,"Unexpected authorization")
        o.require([e["stage"] for e in row["stages"]]==plan["stages"] and
                  all(e["status"]=="PASS" for e in row["stages"]),"Incomplete stage sequence")
        prior={entry["stage"]:verify_receipt(work,entry) for entry in row["stages"]}
        checks["receipts"]={"status":"PASS","count":len(prior)}
        if "research_plan" in prior:
            o.require(row["research_approval"]["sha256"]==prior["research_plan"]["plan_sha256"],"Research approval mismatch")
        domain=work/"domain"
        o.require(domain.is_dir() and domain.resolve()==domain,"Unsafe domain registry")
        # Re-use quality gate without executing any domain action.
        outcome,quality=stage_result(work,"quality",plan,{},prior)
        checks["domain_evidence"]={"status":outcome,"details":quality["checks"]}
        checks["dashboard"]=health.verify_evidence(work,work/"dashboard.json")
        o.require(prior["delegation"]["state"]=="PASS" and prior["delegation"]["merge_authorized"] is False,"Delegation acceptance mismatch")
        for name in ("calibration","research_run","graphics_plan"):
            if name in prior:o.require(prior[name]["state"]=="PASS","Domain acceptance mismatch")
        for name in ("development_plan","research_plan"):
            if name in prior:o.require(prior[name]["state"]=="PLANNED","Planning acceptance mismatch")
        if "graphics_plan" in prior:
            o.require(prior["graphics_plan"]["native"]=="NOT_RUN" and prior["graphics_plan"]["promotion"]=="BLOCKED","Unexpected graphics authorization")
        checks["domain_states"]={"status":"PASS"}
        result["status"]="PASS" if all(x["status"]=="PASS" for x in checks.values()) else "FAIL"
    except (ValueError,OSError,KeyError,TypeError,AttributeError,subprocess.SubprocessError):
        result.update(status="FAIL",reason="Missing, changed or inconsistent recorded evidence")
    return result


class Mission:
    def __init__(self,root):
        self.store=Registry(root)
        with self.store.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS missions (id TEXT PRIMARY KEY,state TEXT NOT NULL,snapshot TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS mission_events (id TEXT NOT NULL REFERENCES missions(id),seq INTEGER NOT NULL,at TEXT NOT NULL,action TEXT NOT NULL,snapshot TEXT NOT NULL,PRIMARY KEY(id,seq))")

    def _save(self,row,action,start=False):
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            payload=encode(row)
            if start:
                o.require(db.execute("SELECT COUNT(*) FROM missions WHERE state IN ('PROPOSED','APPROVED','RUNNING','AWAITING_RESEARCH_APPROVAL')").fetchone()[0]<MAX_ACTIVE,"Mission capacity exceeded")
                db.execute("INSERT INTO missions VALUES (?,?,?)",(row["id"],row["state"],payload))
            else:
                db.execute("UPDATE missions SET state=?,snapshot=? WHERE id=?",(row["state"],payload,row["id"]))
            seq=db.execute("SELECT COALESCE(MAX(seq),0)+1 FROM mission_events WHERE id=?",(row["id"],)).fetchone()[0]
            db.execute("INSERT INTO mission_events VALUES (?,?,?,?,?)",(row["id"],seq,now(),action,payload))

    def get(self,sid):
        with self.store.connect() as db:
            r=db.execute("SELECT snapshot FROM missions WHERE id=?",(ident(sid),)).fetchone()
            o.require(r is not None,"Unknown mission")
            return json.loads(r[0])

    def list(self):
        with self.store.connect() as db:
            return [json.loads(r[0]) for r in db.execute("SELECT snapshot FROM missions ORDER BY rowid DESC LIMIT 100")]

    def history(self,sid):
        self.get(sid)
        with self.store.connect() as db:
            return [{"seq":r["seq"],"at":r["at"],"action":r["action"],"snapshot":json.loads(r["snapshot"])}
                    for r in db.execute("SELECT * FROM mission_events WHERE id=? ORDER BY seq",(sid,))]

    def propose(self,request):
        plan=validate(request)
        row={"id":uuid.uuid4().hex,"schema":1,"created":now(),"state":"PROPOSED","request":request,
             "plan":plan,"plan_sha256":gateway.sha_json(plan),"stages":[],"active_stage":None,
             "merge_authorized":False,"production_approved":False}
        self._save(row,"propose",True)
        return row

    def approve(self,sid,expected,note):
        with self.store.lock(sid):
            row=self.get(sid)
            o.require(row["state"]=="PROPOSED" and expected==row["plan_sha256"]==gateway.sha_json(row["plan"]),"Exact mission approval required")
            row.update(state="APPROVED",approval={"sha256":expected,"note":gateway.text(note),"at":now(),
                        "authority":"local operator declaration"})
            self._save(row,"approve")
            return row

    def cancel(self,sid):
        with self.store.lock(sid):
            row=self.get(sid)
            o.require(row["state"] in ("PROPOSED","APPROVED","AWAITING_RESEARCH_APPROVAL"),"Cannot cancel active or terminal mission")
            row.update(state="CANCELLED")
            self._save(row,"cancel")
            return row

    def audit(self,sid):
        """Read-only post-completion verification; never rewrites historical state."""
        with self.store.lock(sid):
            row=self.get(sid)
            work=self.store.root/("mission-"+sid)
            return audit_completed(row,work)

    def recover(self,sid):
        with self.store.lock(sid):
            row=self.get(sid)
            o.require(row["state"]=="RUNNING","Only abandoned running mission may be recovered")
            row.update(state="INTERRUPTED",reason="No replay. Inspect domain registry and orphan processes.")
            self._save(row,"recover")
            return row

    def run(self,sid,expected,bindings,research_sha=None):
        with self.store.lock(GLOBAL_LOCK),self.store.lock(sid):
            row=self.get(sid)
            o.require(row["state"] in ("APPROVED","AWAITING_RESEARCH_APPROVAL") and
                      expected==row["plan_sha256"]==row["approval"]["sha256"]==gateway.sha_json(row["plan"]),
                      "Approved exact mission required")
            plan=row["plan"]
            o.require(validate(row["request"])==plan,"Mission policy changed")
            targets=check_bindings(plan,bindings,self.store.root)
            work=self.store.root/("mission-"+sid)
            if row["state"]=="APPROVED":
                o.require(not row["stages"] and research_sha is None,"Unexpected resume")
                # Claim before creating workspace. A crash after claim is recoverable,
                # not a permanently APPROVED case with an existing directory.
                o.require(not work.exists() and not work.is_symlink(),"Unexpected existing mission workspace")
            else:
                o.require(isinstance(research_sha,str) and research_sha==row["research_plan_sha256"],
                          "Explicit frozen research approval required")
                row["research_approval"]={"sha256":research_sha,"at":now(),"authority":"local operator declaration"}
            prior={}
            for i,entry in enumerate(row["stages"]):
                o.require(entry["stage"]==plan["stages"][i] and entry["status"]=="PASS","Invalid completed stage sequence")
                prior[entry["stage"]]=verify_receipt(work,entry)
            if research_sha is not None:
                p=prior["research_plan"]
                frozen=work/"domain"/("research-"+p["id"])/"frozen-plan.json"
                o.require(p["plan_sha256"]==research_sha and o.digest(frozen)==research_sha,"Research plan bytes changed")
            row.update(state="RUNNING")
            self._save(row,"start_slice")
            try:
                if not row["stages"]:work.mkdir(mode=0o700)
            except OSError:
                row.update(state="BLOCKED",reason="Cannot create private workspace")
                self._save(row,"blocked")
                return row
            start=time.monotonic()
            for stage in plan["stages"][len(row["stages"]):]:
                row["active_stage"]=stage
                self._save(row,"stage_start")
                try:
                    o.require(time.monotonic()-start<MAX_SLICE_SECONDS,"Slice admission budget exhausted")
                    check_bindings(plan,bindings,self.store.root)
                    for entry in row["stages"]:verify_receipt(work,entry)
                    status,result=stage_result(work,stage,plan,targets,prior)
                    check_bindings(plan,bindings,self.store.root)
                    o.require(status in ("PASS","FAIL","BLOCKED","INCONCLUSIVE"),"Invalid stage result")
                    entry={"stage":stage,"status":status,**receipt(work,stage,result)}
                    row["stages"].append(entry)
                    prior[stage]=result
                    row["active_stage"]=None
                    if status!="PASS":
                        row["state"]=status
                    elif time.monotonic()-start>=MAX_SLICE_SECONDS:
                        row.update(state="BLOCKED",reason="Slice budget exceeded; no further stages")
                    elif stage=="research_plan":
                        row.update(state="AWAITING_RESEARCH_APPROVAL",research_plan_sha256=result["plan_sha256"])
                    self._save(row,"stage_finish")
                except (ValueError,OSError,KeyError,TypeError,AttributeError,sqlite3.Error,subprocess.SubprocessError) as exc:
                    row.update(state="BLOCKED",reason=type(exc).__name__+": "+str(exc)[:160])
                    self._save(row,"blocked")
                if row["state"]!="RUNNING":return row
            row.update(state="PASS",finished=now(),scope="Approved stages only; no native build, candidate code, merge or production approval")
            self._save(row,"finish")
            return row


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--registry",type=Path,required=True)
    sub=p.add_subparsers(dest="mode",required=True)
    sub.add_parser("propose").add_argument("manifest",type=Path)
    sub.add_parser("list")
    for name in ("show","history","approve","run","cancel","recover","audit"):
        q=sub.add_parser(name);q.add_argument("id")
        if name in ("approve","run"):q.add_argument("--plan-sha256",required=True)
        if name=="approve":q.add_argument("--note",required=True)
        if name=="run":
            q.add_argument("--checkout",nargs=3,action="append",required=True)
            q.add_argument("--trusted-checkouts",action="store_true",required=True)
            q.add_argument("--research-sha256")
    a=p.parse_args()
    try:
        m=Mission(a.registry)
        if a.mode=="propose":out=m.propose(gateway.read_request(a.manifest))
        elif a.mode=="list":out=m.list()
        elif a.mode=="approve":out=m.approve(a.id,a.plan_sha256,a.note)
        elif a.mode=="run":out=m.run(a.id,a.plan_sha256,a.checkout,a.research_sha256)
        else:out=getattr(m,"get" if a.mode=="show" else a.mode)(a.id)
        print(json.dumps(out,indent=2,ensure_ascii=True))
        return 1 if (a.mode=="run" and out["state"] not in ("PASS","AWAITING_RESEARCH_APPROVAL")) or (a.mode=="audit" and out["status"]!="PASS") else 0
    except (ValueError,OSError,KeyError,TypeError,AttributeError,sqlite3.Error,subprocess.SubprocessError) as exc:
        print("APEX_MISSION_BLOCKED: "+str(exc),file=sys.stderr)
        return 2

if __name__=="__main__":raise SystemExit(main())
