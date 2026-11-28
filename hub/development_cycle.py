"""Bounded, operator-approved development cycle; no autonomous model or publication."""
import argparse
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import uuid

from . import collaboration as c, departments as d, gateway as g, development as dev
from . import code_builder as b, knowledge as k, graphics, orchestrator as o
from .registry import Registry, encode, ident, now

MAX_ACTIVE=16
ACTIVE=("PREPARING","PROPOSED","APPROVED","RUNNING")

def collaboration_evidence(root,sid):
    # Local trusted registry. Hold the existing locks while checking evidence.
    collab=c.Collaboration(root)
    with collab.store.lock(sid):
        row=collab.get(sid)
        o.require(row["state"]=="OBSERVED" and row["outcome"]=="PASS","Passing collaboration observation required")
        p=row["plan"]
        o.require(g.sha_json(p)==row["plan_sha256"] and d.plan(p["request"])==p["department_plan"],
                  "Collaboration plan changed")
        o.require({"software","quality"}<=set(p["participants"]) and
                  set(row["votes"])==set(p["participants"]) and
                  all(v["decision"]=="accept" and v["plan_sha256"]==row["plan_sha256"] for v in row["votes"].values()),
                  "Exact unanimous collaboration required")
        base=p["request"]["targets"].get("hub")
        o.require(base and any(t["capability"]=="repo.revision" and t["target"]=="hub" for t in p["request"]["tasks"]),
                  "Hub revision inspection required")
        with collab.store.lock(row["department_id"]):
            dep=collab.departments.get(row["department_id"])
            o.require(dep["state"]=="OBSERVED" and dep["plan"]==p["department_plan"] and
                      dep["plan_sha256"]==g.sha_json(dep["plan"]),"Department evidence mismatch")
            gateway=g.Gateway(root)
            with collab.store.lock(dep["gateway_id"]):
                obs=d.observed(collab.store,gateway.get(dep["gateway_id"]),dep["plan"])
                o.require(obs==dep["observation"] and obs["outcome"]=="PASS" and
                          obs["receipt_sha256"]==row["receipt_sha256"],"Collaboration receipt changed")
        return {"collaboration_id":sid,"collaboration_sha256":g.sha_json(row),
                "department_id":dep["id"],"receipt_sha256":obs["receipt_sha256"],"base":base}

def contract(req,root):
    o.require(isinstance(req,dict) and set(req)=={"schema","collaboration_id","objective","edits"} and
              type(req["schema"]) is int and req["schema"]==1,"Invalid development cycle request")
    evidence=collaboration_evidence(root,ident(req["collaboration_id"]))
    objective=dev.note(req["objective"])
    edits=req["edits"]
    o.require(isinstance(edits,list) and 1<=len(edits)<=b.MAX_FILES and all(isinstance(x,dict) for x in edits),
              "Invalid edit list")
    scope=dev.scope([e["path"] for e in edits])
    # Preflight existing policy before creating any development request.
    placeholder={"id":"0"*32,"state":"PLANNED","base":evidence["base"],"scope":scope,"candidates":[]}
    b.manifest({"schema":1,"development_id":placeholder["id"],"edits":edits},placeholder)
    return evidence,objective,scope

class Cycle:
    def __init__(self,root):
        self.store=Registry(root)
        with self.store.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS development_cycles (id TEXT PRIMARY KEY,state TEXT NOT NULL,snapshot TEXT NOT NULL,collaboration_id TEXT NOT NULL UNIQUE)")
            db.execute("CREATE TABLE IF NOT EXISTS development_cycle_events (id TEXT NOT NULL REFERENCES development_cycles(id),seq INTEGER NOT NULL,at TEXT NOT NULL,action TEXT NOT NULL,snapshot TEXT NOT NULL,PRIMARY KEY(id,seq))")
    def get(self,sid):
        with self.store.connect() as db:
            r=db.execute("SELECT state,snapshot,collaboration_id FROM development_cycles WHERE id=?",(ident(sid),)).fetchone()
            o.require(r is not None,"Unknown development cycle")
            row=json.loads(r["snapshot"])
            o.require(row["id"]==sid and row["state"]==r["state"] and
                      row["source"]["collaboration_id"]==r["collaboration_id"],"Cycle identity mismatch")
            if "plan" in row:
                o.require(row["plan_sha256"]==g.sha_json(row["plan"]),"Cycle plan changed")
            return row
    def history(self,sid):
        self.get(sid)
        with self.store.connect() as db:
            return [{"seq":x["seq"],"action":x["action"],"snapshot":json.loads(x["snapshot"])}
                    for x in db.execute("SELECT * FROM development_cycle_events WHERE id=? ORDER BY seq",(sid,))]
    def _save(self,row,action,new=False):
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            raw=encode(row)
            if new:
                o.require(db.execute("SELECT COUNT(*) FROM development_cycles WHERE state IN ('PREPARING','PROPOSED','APPROVED','RUNNING')").fetchone()[0]<MAX_ACTIVE,"Cycle capacity exceeded")
                o.require(db.execute("SELECT 1 FROM development_cycles WHERE collaboration_id=?",(row["source"]["collaboration_id"],)).fetchone() is None,"Collaboration already consumed")
                db.execute("INSERT INTO development_cycles VALUES (?,?,?,?)",(row["id"],row["state"],raw,row["source"]["collaboration_id"]))
            else:
                db.execute("UPDATE development_cycles SET state=?,snapshot=? WHERE id=?",(row["state"],raw,row["id"]))
            seq=db.execute("SELECT COALESCE(MAX(seq),0)+1 FROM development_cycle_events WHERE id=?",(row["id"],)).fetchone()[0]
            db.execute("INSERT INTO development_cycle_events VALUES (?,?,?,?,?)",(row["id"],seq,now(),action,raw))
    def prepare(self,req,checkout):
        source,objective,scope=contract(req,self.store.root)
        root,_=o.checked_targets([("hub",checkout,source["base"])])["hub"]
        o.require(not self.store.root.is_relative_to(root) and not root.is_relative_to(self.store.root),"Separate registry required")
        row={"id":uuid.uuid4().hex,"state":"PREPARING","created":now(),"source":source,"attempts":0,
             "execution_authorized":False,"remote_write_authorized":False,"merge_authorized":False}
        with self.store.lock(row["id"]):
            self._save(row,"prepare_claim",True)
            try:
                mem=k.Knowledge(self.store.root)
                e=mem.ingest("department",self.store.root,source["department_id"])
                o.require(mem.audit(e["id"],self.store.root)["status"]=="PASS","Memory evidence changed")
                row["memory_id"]=e["id"];self._save(row,"memory")
                development=dev.Development(self.store.root)
                p=development.create(objective,source["base"],scope)
                row["development_id"]=p["id"];self._save(row,"development")
                development.audit(p["id"],root)
                development.plan(p["id"],"Operator-supplied scoped candidate; collaboration is read-only evidence, not code approval.")
                builder=b.Builder(self.store.root)
                q=builder.propose({"schema":1,"development_id":p["id"],"edits":req["edits"]})
                row["builder_id"]=q["id"];self._save(row,"builder")
                o.require(collaboration_evidence(self.store.root,source["collaboration_id"])==source,"Collaboration changed during preparation")
                plan={"schema":1,"source":source,"objective":objective,"scope":scope,
                      "memory_id":e["id"],"memory_digest":e["digest"],"development_id":p["id"],
                      "builder_id":q["id"],"builder_plan":q["plan"],"builder_plan_sha256":q["plan_sha256"],
                      "max_attempts":1,"local_only":True,"remote_write_authorized":False,"merge_authorized":False}
                row.update(state="PROPOSED",plan=plan,plan_sha256=g.sha_json(plan))
                self._save(row,"prepared")
            except (ValueError,OSError,KeyError,TypeError,AttributeError,sqlite3.Error,subprocess.SubprocessError) as exc:
                row.update(state="BLOCKED",reason=type(exc).__name__)
                self._save(row,"prepare_blocked")
            return row
    def _recheck(self,row):
        p=row["plan"]
        o.require(p["source"]==row["source"] and
                  collaboration_evidence(self.store.root,p["source"]["collaboration_id"])==p["source"],
                  "Collaboration evidence changed")
        mem=k.Knowledge(self.store.root)
        e=mem.get(p["memory_id"])
        o.require(e["digest"]==p["memory_digest"] and mem.audit(e["id"],self.store.root)["status"]=="PASS",
                  "Memory evidence changed")
        builder=b.Builder(self.store.root)
        q=builder.get(p["builder_id"])
        o.require(q["plan"]==p["builder_plan"] and q["plan_sha256"]==p["builder_plan_sha256"],
                  "Builder plan changed")
        return builder,q
    def approve(self,sid,expected):
        with self.store.lock(sid):
            row=self.get(sid)
            o.require(row["state"]=="PROPOSED" and expected==row["plan_sha256"],"Exact local code approval required")
            _,q=self._recheck(row)
            o.require(q["state"]=="PROPOSED" and q["attempts"]==0,"Builder already changed")
            row.update(state="APPROVED",approval={"sha256":expected,"at":now(),
                       "authority":"Operator approves exact supplied code, local trusted test execution and local handoff"})
            self._save(row,"approve");return row
    def run(self,sid,expected,checkout):
        with self.store.lock(sid):
            row=self.get(sid)
            o.require(row["state"]=="APPROVED" and row["attempts"]==0 and
                      expected==row["plan_sha256"]==row["approval"]["sha256"],"Exact approved cycle required")
            builder,q=self._recheck(row);p=row["plan"]
            o.require(q["state"]=="PROPOSED" and q["attempts"]==0,"Builder already changed")
            o.checked_targets([("hub",checkout,p["source"]["base"])])
            row.update(state="RUNNING",attempts=1)
            self._save(row,"run_claim")
            try:
                builder.approve(q["id"],q["plan_sha256"])
                result=builder.build(q["id"],q["plan_sha256"],checkout)
                row["build_state"]=result["state"];row["build_sha256"]=g.sha_json(result)
                self._save(row,"build")
                if result["state"]!="PASS":
                    row.update(state="FAIL" if result["state"]=="FAIL" else "BLOCKED",ci="NOT_RUN")
                else:
                    self._recheck(row)
                    handoff=builder.handoff(q["id"])
                    candidate=result["candidate"]["sha"]
                    o.require(handoff["state"]=="AWAITING_CI" and handoff["candidates"][-1]["sha"]==candidate and
                              not handoff["merge_authorized"],"Handoff identity mismatch")
                    row.update(state="AWAITING_CI",candidate_sha=candidate,local_tests=result["local_tests"]["count"],
                               handoff_sha256=g.sha_json(handoff),ci="NOT_RUN",publication="NOT_AUTHORIZED")
                self._save(row,"finish")
            except (ValueError,OSError,KeyError,TypeError,AttributeError,sqlite3.Error,subprocess.SubprocessError) as exc:
                row.update(state="BLOCKED",reason=type(exc).__name__,ci="NOT_RUN")
                self._save(row,"run_blocked")
            return row
    def verify(self,sid):
        row=self.get(sid);p=row["plan"]
        o.require(row["state"]=="AWAITING_CI","Completed local handoff required")
        _,q=self._recheck(row)
        o.require(q["state"]=="PASS" and g.sha_json(q)==row["build_sha256"],"Builder record changed")
        work=self.store.root/("code-"+q["id"])
        o.require(graphics.file_evidence(work,"tests.log",graphics.MAX_LOG)==q["local_tests"]["evidence"],"Test evidence changed")
        o.checked_targets([("hub",work/"checkout",row["candidate_sha"])])
        handoff=dev.Development(self.store.root).get(p["development_id"])
        o.require(g.sha_json(handoff)==row["handoff_sha256"],"Original handoff progressed or changed; inspect Development history")
        return {"status":"PASS","candidate_sha":row["candidate_sha"],"ci":"NOT_RUN",
                "scope":"Original local handoff and evidence; not hosted CI, publication or scientific validation"}
    def recover(self,sid):
        with self.store.lock(sid):
            row=self.get(sid)
            o.require(row["state"] in ("PREPARING","RUNNING"),"Only interrupted cycle may be recovered")
            row.update(state="INTERRUPTED",reason="No replay; inspect linked child cases and processes")
            self._save(row,"recover");return row
    def cancel(self,sid):
        with self.store.lock(sid):
            row=self.get(sid)
            o.require(row["state"] in ("PROPOSED","APPROVED"),"Cannot cancel active or terminal cycle")
            builder=b.Builder(self.store.root)
            q=builder.get(row["builder_id"])
            o.require(q["state"]=="PROPOSED","Builder already changed")
            builder.cancel(q["id"])
            row["state"]="CANCELLED";self._save(row,"cancel");return row

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("--registry",type=Path,required=True)
    sub=p.add_subparsers(dest="mode",required=True)
    q=sub.add_parser("prepare");q.add_argument("manifest",type=Path);q.add_argument("--checkout",type=Path,required=True)
    for n in ("show","history","approve","run","verify","recover","cancel"):
        q=sub.add_parser(n);q.add_argument("id")
        if n in ("approve","run"):q.add_argument("--plan-sha256",required=True)
        if n=="run":
            q.add_argument("--checkout",type=Path,required=True)
            q.add_argument("--trusted-candidate",action="store_true",required=True)
    a=p.parse_args()
    try:
        x=Cycle(a.registry)
        if a.mode=="prepare":out=x.prepare(g.read_request(a.manifest),a.checkout)
        elif a.mode=="approve":out=x.approve(a.id,a.plan_sha256)
        elif a.mode=="run":out=x.run(a.id,a.plan_sha256,a.checkout)
        else:out=getattr(x,"get" if a.mode=="show" else a.mode)(a.id)
        print(json.dumps(out,indent=2,ensure_ascii=True))
        return 1 if a.mode in ("prepare","run") and out["state"] not in ("PROPOSED","AWAITING_CI") else 0
    except (ValueError,OSError,KeyError,TypeError,AttributeError,sqlite3.Error,subprocess.SubprocessError):
        print("APEX_CYCLE_BLOCKED: invalid state, scope or evidence",file=sys.stderr);return 2
if __name__=="__main__":raise SystemExit(main())
