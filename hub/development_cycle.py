"""Bounded, operator-approved development cycle; no autonomous model or publication."""
import argparse
import hashlib
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
    def audit(self,sid):
        """Audit the original handoff and report later recorded development state."""
        with self.store.lock(sid):
            row=self.get(sid)
            o.require(row["state"]=="AWAITING_CI","Completed local handoff required")
            p=row["plan"]
            _,q=self._recheck(row)
            with self.store.lock(q["id"]),self.store.lock(p["development_id"]):
                _,q=self._recheck(row)
                o.require(q["state"]=="PASS" and g.sha_json(q)==row["build_sha256"] and
                          q["candidate"]["sha"]==row["candidate_sha"] and
                          q["local_tests"]["count"]==row["local_tests"],"Builder evidence mismatch")
                work=self.store.root/("code-"+q["id"])
                o.require(graphics.file_evidence(work,"tests.log",graphics.MAX_LOG)==q["local_tests"]["evidence"],
                          "Test evidence changed")
                checkout,_=o.checked_targets([("hub",work/"checkout",row["candidate_sha"])])["hub"]
                o.require(o.git(checkout,"symbolic-ref","--short","HEAD")==q["candidate"]["branch"] and
                          o.git(checkout,"rev-parse","HEAD^")==p["builder_plan"]["parent"],
                          "Original candidate branch or parent changed")
                development=dev.Development(self.store.root)
                # One transaction for current row and historical snapshots.
                with self.store.connect() as db:
                    db.execute("BEGIN")
                    raw=db.execute("SELECT state,snapshot FROM development WHERE id=?",(p["development_id"],)).fetchone()
                    o.require(raw is not None,"Missing linked development")
                    current=json.loads(raw["snapshot"])
                    events=[(v["seq"],v["action"],json.loads(v["snapshot"])) for v in db.execute(
                        "SELECT seq,action,snapshot FROM development_events WHERE id=? ORDER BY seq",(p["development_id"],))]
                o.require(bool(events) and [v[0] for v in events]==list(range(1,len(events)+1)) and
                          events[-1][2]==current and current["state"]==raw["state"],"Development history mismatch")
                o.require(all(v[2]["id"]==p["development_id"] and v[2]["base"]==p["source"]["base"] and
                              v[2]["scope"]==p["scope"] and not v[2]["merge_authorized"] for v in events),
                          "Development history identity mismatch")
                matches=[(i,v[2]) for i,v in enumerate(events) if v[1]=="candidate" and
                         g.sha_json(v[2])==row["handoff_sha256"]]
                o.require(len(matches)==1,"Missing or ambiguous original handoff")
                pos,original=matches[0]
                o.require(original["state"]=="AWAITING_CI" and len(original["candidates"])==1 and
                          original["candidates"][0]["sha"]==row["candidate_sha"] and
                          original["candidates"][0]["branch"]==q["candidate"]["branch"] and
                          original["candidates"][0]["changed"]==q["candidate"]["changed"] and
                          original["candidates"][0]["receipts"]==[],"Original handoff mismatch")
                original_candidate=original["candidates"][0]
                previous=original
                for _,action,snapshot in events[pos+1:]:
                    before=previous["candidates"];after=snapshot["candidates"]
                    o.require(bool(after) and len(before)<=len(after)<=len(before)+1,
                              "Candidate history length changed")
                    for i,candidate in enumerate(before):
                        o.require(all(candidate[key]==after[i][key] for key in ("sha","branch","changed")) and
                                  after[i]["receipts"][:len(candidate["receipts"])]==candidate["receipts"],
                                  "Historical candidate or receipt changed")
                    if len(after)>len(before):
                        o.require(action=="candidate" and previous["state"]=="REPAIR_READY" and
                                  snapshot["state"]=="AWAITING_CI","Unexpected repair transition")
                    previous=snapshot
                o.require(1<=len(current["candidates"])<=dev.MAX_REPAIRS+1 and
                          current["repairs"]==len(current["candidates"])-1,"Repair history mismatch")
                for candidate in current["candidates"]:
                    o.require(o.SHA.fullmatch(candidate["sha"]) and candidate["branch"]==original_candidate["branch"] and
                              set(candidate["changed"])<=set(p["scope"]) and len(candidate["receipts"])<=1,
                              "Recorded candidate invalid")
                    for receipt in candidate["receipts"]:
                        digest=hashlib.sha256(json.dumps({key:value for key,value in receipt.items() if key!="sha256"},
                            sort_keys=True,separators=(",",":")).encode()).hexdigest()
                        o.require(receipt["sha256"]==digest and receipt["sha"]==candidate["sha"] and
                                  receipt["branch"]==candidate["branch"] and receipt["repository"]==o.REPOSITORIES["hub"] and
                                  receipt["outcome"] in ("PASS","FAIL"),"Stored CI receipt mismatch")
                latest=current["candidates"][-1]
                outcome=latest["receipts"][0]["outcome"] if latest["receipts"] else "NOT_RUN"
                o.require(current["state"] in ("AWAITING_CI","REVIEW","APPROVED","DIAGNOSE","REPAIR_READY","BLOCKED"),
                          "Unexpected downstream state")
                o.require((current["state"]=="AWAITING_CI" and outcome=="NOT_RUN") or
                          (current["state"] in ("REVIEW","APPROVED") and outcome=="PASS") or
                          (current["state"] in ("DIAGNOSE","REPAIR_READY","BLOCKED") and outcome=="FAIL"),
                          "Downstream state and stored receipt disagree")
                if current["state"]=="APPROVED":
                    o.require(current["review"]["sha"]==latest["sha"],"Downstream approval mismatch")
                return {"status":"PASS","scope":"Original local handoff, stored history and receipt consistency",
                        "original_candidate_sha":row["candidate_sha"],"original_handoff_sha256":row["handoff_sha256"],
                        "recorded_development_state":current["state"],"recorded_latest_candidate_sha":latest["sha"],
                        "recorded_ci_outcome":outcome,"live_ci":"NOT_RUN","historical_state_changed":False,
                        "merge_authorized":False}

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
    for n in ("show","history","approve","run","verify","audit","recover","cancel"):
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
