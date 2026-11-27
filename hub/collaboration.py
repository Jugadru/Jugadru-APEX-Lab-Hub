"""Bounded collaborative planning across operator-assigned roles, no agent runtime."""
import argparse
from collections import Counter
import json
from pathlib import Path
import sqlite3
import sys
import uuid

from . import ai_gateway as ai, departments as d, gateway as g, orchestrator as o
from .registry import Registry, encode, ident, now

MAX_ACTIVE=16
MAX_ROUNDS=3
MAX_PARTICIPANTS=8
VOTES={"accept","revise","reject"}
ACTIVE={"PROPOSED","CONFLICT","AGREED","HANDED_OFF"}

def packet(request,participants,evidence,k):
    p=d.plan(request)
    o.require(isinstance(participants,list) and 2<=len(participants)<=MAX_PARTICIPANTS and
              all(isinstance(x,str) for x in participants) and len(set(participants))==len(participants),
              "Choose 2..8 distinct roles")
    for role in participants:g.department(role)
    mandatory={a["owner"] for a in p["assignments"].values()} | set(p["reviewers"]) | {"quality"}
    o.require(mandatory<=set(participants),"Missing owner or review role")
    o.require(isinstance(evidence,dict) and set(evidence)==set(participants),"Explicit context selection per role required")
    contexts={}
    for role in sorted(participants):
        ids=evidence[role]
        o.require(isinstance(ids,list) and len(ids)<=4 and all(isinstance(x,str) for x in ids)
                  and len(set(ids))==len(ids),"Select at most four memory records per role")
        o.require(not ids or k is not None,"Evidence source required")
        contexts[role]=[ai.summarize(k.get(ident(i)),role) for i in ids]
    return {"schema":1,"request":request,"department_plan":p,"participants":sorted(participants),
            "contexts":contexts,"execution_authorized":False,"merge_authorized":False,
            "identity":"operator-assigned roles, not independent agents"}

def envelope(request):
    # Revisions may change ordering/dependencies or objective, never add scope.
    return {"targets":request["targets"],"tasks":{t["id"]:{k:v for k,v in t.items()
                if k not in ("depends_on","timeout_seconds")} for t in request["tasks"]}}

def revise_allowed(previous,new):
    o.require(envelope(previous)==envelope(new),"Revision cannot expand or change task scope")
    limits={t["id"]:t["timeout_seconds"] for t in previous["tasks"]}
    o.require(all(t["timeout_seconds"]<=limits[t["id"]] for t in new["tasks"]),"Revision cannot increase task budget")

class Collaboration:
    def __init__(self,root):
        self.store=Registry(root)
        self.departments=d.Departments(self.store.root)
        with self.store.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS collaboration_cases (id TEXT PRIMARY KEY,state TEXT NOT NULL,snapshot TEXT NOT NULL,department_id TEXT UNIQUE)")
            db.execute("CREATE TABLE IF NOT EXISTS collaboration_events (id TEXT NOT NULL REFERENCES collaboration_cases(id),seq INTEGER NOT NULL,at TEXT NOT NULL,action TEXT NOT NULL,snapshot TEXT NOT NULL,PRIMARY KEY(id,seq))")
    def _get(self,db,sid):
        r=db.execute("SELECT state,snapshot,department_id FROM collaboration_cases WHERE id=?",(ident(sid),)).fetchone()
        o.require(r is not None,"Unknown collaboration")
        row=json.loads(r["snapshot"])
        o.require(row["id"]==sid and row["state"]==r["state"] and row.get("department_id")==r["department_id"]
                  and row["plan_sha256"]==g.sha_json(row["plan"]),"Collaboration identity mismatch")
        return row
    def get(self,sid):
        with self.store.connect() as db:return self._get(db,sid)
    def history(self,sid):
        with self.store.connect() as db:
            self._get(db,sid)
            return [{"seq":x["seq"],"action":x["action"],"snapshot":json.loads(x["snapshot"])}
                    for x in db.execute("SELECT * FROM collaboration_events WHERE id=? ORDER BY seq",(sid,))]
    def _save(self,db,row,action,new=False):
        raw=encode(row)
        if new:db.execute("INSERT INTO collaboration_cases VALUES (?,?,?,NULL)",(row["id"],row["state"],raw))
        else:db.execute("UPDATE collaboration_cases SET state=?,snapshot=?,department_id=? WHERE id=?",
                        (row["state"],raw,row.get("department_id"),row["id"]))
        seq=db.execute("SELECT COALESCE(MAX(seq),0)+1 FROM collaboration_events WHERE id=?",(row["id"],)).fetchone()[0]
        db.execute("INSERT INTO collaboration_events VALUES (?,?,?,?,?)",(row["id"],seq,now(),action,raw))
    def propose(self,request,participants,evidence,source=None):
        p=packet(request,participants,evidence,ai.ReadOnlyMemory(source) if source is not None else None)
        row={"id":uuid.uuid4().hex,"created":now(),"state":"PROPOSED","round":1,"plan":p,
             "plan_sha256":g.sha_json(p),"votes":{},"resolution":None,"execution_authorized":False,"merge_authorized":False}
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            o.require(db.execute("SELECT COUNT(*) FROM collaboration_cases WHERE state IN ('PROPOSED','CONFLICT','AGREED','HANDED_OFF')").fetchone()[0]<MAX_ACTIVE,"Collaboration backlog full")
            self._save(db,row,"propose",True)
        return row
    def view(self,sid,role):
        row=self.get(sid);g.department(role)
        o.require(role in row["plan"]["participants"],"Role outside collaboration")
        p=row["plan"]
        return {"id":sid,"round":row["round"],"state":row["state"],"plan_sha256":row["plan_sha256"],
                "role":role,"request":p["request"],"assignments":p["department_plan"]["assignments"],
                "context":p["contexts"][role],"guideline":d.PROFILES[role][1],
                "execution_authorized":False}
    def vote(self,sid,expected,role,decision,note):
        g.department(role);note=g.text(note)
        o.require(decision in VOTES,"Invalid position")
        with self.store.lock(sid),self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE");row=self._get(db,sid)
            o.require(row["state"]=="PROPOSED" and expected==row["plan_sha256"] and
                      role in row["plan"]["participants"] and role not in row["votes"],"Vote denied")
            row["votes"][role]={"decision":decision,"note":note,"plan_sha256":expected,"at":now(),
                                "identity":"operator declaration"}
            if decision=="reject":row["state"]="REJECTED"
            elif set(row["votes"])==set(row["plan"]["participants"]):
                row["state"]="AGREED" if all(v["decision"]=="accept" for v in row["votes"].values()) else "CONFLICT"
            self._save(db,row,"vote");return row
    def revise(self,sid,expected,request,note):
        note=g.text(note)
        with self.store.lock(sid),self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE");row=self._get(db,sid)
            o.require(row["state"]=="CONFLICT" and expected==row["plan_sha256"] and row["round"]<MAX_ROUNDS,
                      "Conflicted exact plan and remaining round required")
            p=d.plan(request)
            revise_allowed(row["plan"]["request"],request)
            mandatory={a["owner"] for a in p["assignments"].values()}|set(p["reviewers"])|{"quality"}
            o.require(mandatory<=set(row["plan"]["participants"]),"Revision missing role")
            # Keep frozen role contexts; bind revision number and resolution to the next digest.
            nxt={**row["plan"],"request":request,"department_plan":p,
                 "previous_sha256":expected,"round":row["round"]+1,"resolution":note}
            row.update(state="PROPOSED",round=row["round"]+1,plan=nxt,plan_sha256=g.sha_json(nxt),
                       votes={},resolution=note)
            self._save(db,row,"revise");return row
    def handoff(self,sid,expected):
        with self.store.lock(sid),self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE");row=self._get(db,sid);p=row["plan"]
            o.require(row["state"]=="AGREED" and expected==row["plan_sha256"] and
                      set(row["votes"])==set(p["participants"]) and
                      all(v["decision"]=="accept" and v["plan_sha256"]==expected for v in row["votes"].values())
                      and d.plan(p["request"])==p["department_plan"],"Unanimous exact plan required")
            o.require(db.execute("SELECT COUNT(*) FROM department_plans WHERE state IN ('PROPOSED','REVIEWED','DELEGATED')").fetchone()[0]<d.MAX_ACTIVE,
                      "Department backlog full")
            # Atomic new PROPOSED department case. Never forward votes as authorization.
            dep={"id":uuid.uuid4().hex,"schema":1,"created":now(),"state":"PROPOSED","request":p["request"],
                 "plan":p["department_plan"],"plan_sha256":g.sha_json(p["department_plan"]),"reviews":{},"merge_authorized":False}
            self.departments._save(db,dep,"propose",True)
            row.update(state="HANDED_OFF",department_id=dep["id"])
            self._save(db,row,"handoff");return row
    def observe(self,sid):
        with self.store.lock(sid):
            row=self.get(sid)
            o.require(row["state"]=="HANDED_OFF","Handed-off collaboration required")
            depid=row["department_id"]
            with self.store.lock(depid):
                dep=self.departments.get(depid)
                o.require(dep["id"]==depid and dep["plan"]==row["plan"]["department_plan"] and
                          dep["plan_sha256"]==g.sha_json(dep["plan"]),"Department plan mismatch")
                if dep["state"]=="OBSERVED":
                    gateway=g.Gateway(self.store.root)
                    with self.store.lock(dep["gateway_id"]):
                        obs=d.observed(self.store,gateway.get(dep["gateway_id"]),dep["plan"])
                        o.require(obs==dep["observation"],"Observation mismatch")
                    row.update(state="OBSERVED",outcome=obs["outcome"],receipt_sha256=obs["receipt_sha256"])
                elif dep["state"] in ("CANCELLED","REJECTED","INCONCLUSIVE"):
                    row.update(state="INCONCLUSIVE",outcome="INCONCLUSIVE",closure=dep["state"])
                else:raise ValueError("Department work not terminal")
                with self.store.connect() as db:
                    db.execute("BEGIN IMMEDIATE");self._save(db,row,"observe")
            return row
    def cancel(self,sid):
        with self.store.lock(sid),self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE");row=self._get(db,sid)
            o.require(row["state"] in ("PROPOSED","CONFLICT","AGREED"),"Cancel handed-off work via department")
            row["state"]="CANCELLED";self._save(db,row,"cancel");return row

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("--registry",required=True,type=Path)
    sub=p.add_subparsers(dest="mode",required=True)
    q=sub.add_parser("propose");q.add_argument("manifest",type=Path);q.add_argument("--source",type=Path)
    for n in ("show","history","view","vote","revise","handoff","observe","cancel"):
        q=sub.add_parser(n);q.add_argument("id")
        if n in ("vote","revise","handoff"):q.add_argument("--plan-sha256",required=True)
        if n in ("view","vote"):q.add_argument("--role",required=True)
        if n=="vote":q.add_argument("--decision",choices=sorted(VOTES),required=True);q.add_argument("--note",required=True)
        if n=="revise":q.add_argument("manifest",type=Path);q.add_argument("--note",required=True)
    a=p.parse_args()
    try:
        c=Collaboration(a.registry)
        if a.mode=="propose":
            v=g.read_request(a.manifest);o.require(set(v)=={"request","participants","evidence"},"Invalid collaboration manifest")
            out=c.propose(v["request"],v["participants"],v["evidence"],a.source)
        elif a.mode=="vote":out=c.vote(a.id,a.plan_sha256,a.role,a.decision,a.note)
        elif a.mode=="revise":out=c.revise(a.id,a.plan_sha256,g.read_request(a.manifest),a.note)
        elif a.mode=="handoff":out=c.handoff(a.id,a.plan_sha256)
        elif a.mode=="view":out=c.view(a.id,a.role)
        else:out=getattr(c,"get" if a.mode=="show" else a.mode)(a.id)
        print(json.dumps(out,indent=2,ensure_ascii=True));return 0
    except (ValueError,OSError,KeyError,TypeError,AttributeError,sqlite3.Error):
        print("APEX_COLLABORATION_BLOCKED: invalid role, scope, state or evidence",file=sys.stderr);return 2
if __name__=="__main__":raise SystemExit(main())
