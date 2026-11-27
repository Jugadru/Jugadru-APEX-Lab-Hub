"""Deterministic department planning and evidence-linked feedback; no model runtime."""
import argparse
from collections import Counter
import json
from pathlib import Path
import sqlite3
import sys
import uuid

from . import gateway as g, orchestrator as o, health
from .registry import Registry, encode, ident, now

MAX_ACTIVE=32
PROFILES={
 "software":("architecture","Preserve reviewed scope, compatibility and rollback."),
 "physics":("vehicle_models","Use SI units, frozen criteria and calibrated references."),
 "ai":("driver_behavior","Separate policy decisions from physical and sporting authority."),
 "racecraft":("race_strategy","Preserve legal commands, deterministic ordering and track evidence."),
 "graphics":("asset_design","Record origin, license and attribution; original-only Factory gate remains."),
 "research":("scientific_method","Freeze hypothesis, baseline, sources, seed and criteria."),
 "performance":("resource_efficiency","Measure equivalent workloads; do not infer FPS from synthetic fixtures."),
 "quality":("verification","Check exact revisions, negative results and evidence integrity.")
}
# Topic is an explicit intent hint; only the Gateway's capability policy grants execution.
PREFERENCE={
 "repo.revision":{"assets":"graphics","foundation":"research","engineering":"research",
                  "hyper":"physics","game":"software","hub":"software","architect":"software"},
 "source.inventory":{"game":"performance"},
 "architecture.audit":{"game":"software"},
 "foundation.validate":{"foundation":"research"},
 "foundation.discovery":{"foundation":"research"},
 "foundation.catalog":{"foundation":"research"},
}
TOPICS={"auto",*PROFILES}
TERMINAL={"OBSERVED","REJECTED","CANCELLED","INCONCLUSIVE"}
OUTCOMES={"PASS","FAIL","BLOCKED","NOT_RUN","INCONCLUSIVE"}

def catalog():
    return {k:{"specialty":v[0],"guideline":v[1],"capabilities":g.DEPARTMENTS[k],
               "authority":"advisory operator role, not independent AI","write_authorized":False}
            for k,v in PROFILES.items()}

def plan(request):
    o.require(isinstance(request,dict) and set(request)=={"schema","objective","targets","tasks"} and
              type(request["schema"]) is int and request["schema"]==1,"Invalid planning schema")
    o.require(isinstance(request["tasks"],list) and 1<=len(request["tasks"])<=g.MAX_TASKS,"Invalid task count")
    tasks=[];assignments={}
    for item in request["tasks"]:
        o.require(isinstance(item,dict) and set(item)==o.TASK_FIELDS|{"topic"},"Invalid planning task")
        cap,target,topic=item["capability"],item["target"],item["topic"]
        o.require(isinstance(cap,str) and isinstance(target,str) and isinstance(topic,str) and topic in TOPICS,
                  "Unknown task intent")
        choices=sorted(k for k in PROFILES if target in g.DEPARTMENTS[k].get(cap,()))
        o.require(bool(choices) and cap in o.CAPABILITIES,"No authorized department for capability")
        default=PREFERENCE.get(cap,{}).get(target)
        owner=(default if default in choices else choices[0]) if topic=="auto" else topic
        o.require(owner in choices,"Requested specialty cannot execute capability")
        task={k:v for k,v in item.items() if k!="topic"}
        tasks.append({**task,"department":owner})
        o.require(isinstance(item["id"],str),"Invalid task id")
        reviewer="quality" if owner!="quality" else "software"
        assignments[item["id"]]={"owner":owner,"reviewer":reviewer,"eligible":choices,
                                  "guideline":PROFILES[owner][1]}
    request2={"schema":1,"objective":request["objective"],"targets":request["targets"],"tasks":tasks}
    delegation=g.validate_request(request2)
    ordered={t["id"]:t for t in tasks}
    edges=[{"from":d,"to":i,"sender":assignments[d]["owner"],"recipient":assignments[i]["owner"]}
           for i in delegation["order"] for d in ordered[i]["depends_on"]
           if assignments[d]["owner"]!=assignments[i]["owner"]]
    return {"schema":1,"delegation":request2,"gateway_plan_sha256":g.sha_json(delegation),
            "order":delegation["order"],"assignments":assignments,"cross_department_dependencies":edges,
            "reviewers":sorted({x["reviewer"] for x in assignments.values()}),
            "declared_seconds":sum(t["timeout_seconds"] for t in tasks),
            "execution_authorized":False,"merge_authorized":False}

def observed(store,case,planned):
    p=case["plan"]
    o.require(case["state"] in g.TERMINAL and case["state"]!="CANCELLED" and case["attempts"]==1 and
              case["plan_sha256"]==planned["gateway_plan_sha256"]==g.sha_json(p) and
              p==g.validate_request(planned["delegation"]),"Unrelated or incomplete Gateway case")
    evidence=case.get("receipt")
    expected="gateway-"+ident(case["id"])+"/dag/report.json"
    o.require(isinstance(evidence,dict) and evidence.get("path")==expected,"Gateway receipt missing")
    report_path=store.root/expected
    o.require(report_path.is_file() and report_path.resolve()==report_path and
              report_path.stat().st_size==evidence["bytes"]<=health.MAX_JSON and
              o.digest(report_path)==evidence["sha256"],"Gateway receipt bytes changed")
    report=health.load(report_path)
    o.require(report["status"]==case["state"] and report["manifest_sha256"]==g.sha_json(p["manifest"]) and
              report["targets"]=={k:{"repository":o.REPOSITORIES[k],"sha":v} for k,v in p["targets"].items()},
              "Report identity mismatch")
    checks=health.verify_evidence(report_path.parent,report_path)
    o.require(checks["status"]=="PASS","Domain artifact integrity not verified")
    tasks=report["tasks"]
    o.require([t["id"] for t in tasks]==planned["order"],"Task sequence mismatch")
    counts={k:Counter() for k in PROFILES}
    lessons=[]
    for t in tasks:
        i=t["id"];a=planned["assignments"][i];status=t["status"]
        o.require(status in OUTCOMES and type(t["attempts"]) is int and 0<=t["attempts"]<=1,
                  "Invalid observed task status")
        counts[a["owner"]][status]+=1
        if status!="PASS":
            lessons.append({"task":i,"department":a["owner"],"observed":status,
                            "recommendation":"Inspect recorded evidence and dependency results before proposing a new reviewed task.",
                            "diagnosis":"NOT_PERFORMED","retry_authorized":False})
    return {"gateway_id":case["id"],"receipt_sha256":evidence["sha256"],"outcome":case["state"],
            "artifact_integrity":"PASS","task_counts":{k:dict(v) for k,v in counts.items()},
            "lessons":lessons,"scope":"Single verified DAG; task counts, not staff productivity or runtime benchmarks"}

class Departments:
    def __init__(self,root):
        self.store=Registry(root)
        with self.store.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS department_plans (id TEXT PRIMARY KEY,state TEXT NOT NULL,snapshot TEXT NOT NULL,gateway_id TEXT UNIQUE)")
            db.execute("CREATE TABLE IF NOT EXISTS department_events (id TEXT NOT NULL REFERENCES department_plans(id),seq INTEGER NOT NULL,at TEXT NOT NULL,action TEXT NOT NULL,snapshot TEXT NOT NULL,PRIMARY KEY(id,seq))")
    def _get(self,db,sid):
        r=db.execute("SELECT snapshot FROM department_plans WHERE id=?",(ident(sid),)).fetchone()
        o.require(r is not None,"Unknown department plan")
        return json.loads(r[0])
    def get(self,sid):
        with self.store.connect() as db:return self._get(db,sid)
    def list(self):
        with self.store.connect() as db:return [json.loads(r[0]) for r in db.execute("SELECT snapshot FROM department_plans ORDER BY rowid DESC LIMIT 100")]
    def history(self,sid):
        self.get(sid)
        with self.store.connect() as db:
            return [{"seq":r["seq"],"at":r["at"],"action":r["action"],"snapshot":json.loads(r["snapshot"])}
                    for r in db.execute("SELECT * FROM department_events WHERE id=? ORDER BY seq",(sid,))]
    def _save(self,db,row,action,new=False):
        payload=encode(row)
        if new:db.execute("INSERT INTO department_plans VALUES (?,?,?,NULL)",(row["id"],row["state"],payload))
        else:db.execute("UPDATE department_plans SET state=?,snapshot=?,gateway_id=? WHERE id=?",
                        (row["state"],payload,row.get("gateway_id"),row["id"]))
        seq=db.execute("SELECT COALESCE(MAX(seq),0)+1 FROM department_events WHERE id=?",(row["id"],)).fetchone()[0]
        db.execute("INSERT INTO department_events VALUES (?,?,?,?,?)",(row["id"],seq,now(),action,payload))
    def propose(self,request):
        p=plan(request)
        row={"id":uuid.uuid4().hex,"schema":1,"created":now(),"state":"PROPOSED","request":request,
             "plan":p,"plan_sha256":g.sha_json(p),"reviews":{},"merge_authorized":False}
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            o.require(db.execute("SELECT COUNT(*) FROM department_plans WHERE state IN ('PROPOSED','REVIEWED','DELEGATED')").fetchone()[0]<MAX_ACTIVE,"Planning backlog full")
            self._save(db,row,"propose",True)
        return row
    def review(self,sid,expected,reviewer,decision,note):
        o.require(decision in ("accept","reject"),"Invalid review decision")
        g.department(reviewer);note=g.text(note)
        with self.store.lock(sid),self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row=self._get(db,sid)
            o.require(row["state"]=="PROPOSED" and expected==row["plan_sha256"]==g.sha_json(row["plan"]) and
                      reviewer in row["plan"]["reviewers"] and reviewer not in row["reviews"],"Review denied")
            row["reviews"][reviewer]={"decision":decision,"note":note,"at":now(),"plan_sha256":expected,
                                      "identity":"operator-assigned review role; not independent authentication"}
            if decision=="reject":row["state"]="REJECTED"
            elif set(row["reviews"])==set(row["plan"]["reviewers"]):row["state"]="REVIEWED"
            self._save(db,row,"review")
            return row
    def delegate(self,sid,expected):
        # Atomic handoff to existing Gateway registry; no execution or Gateway approval.
        gateway=g.Gateway(self.store.root)
        with self.store.lock(sid),self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row=self._get(db,sid)
            o.require(row["state"]=="REVIEWED" and expected==row["plan_sha256"]==g.sha_json(row["plan"]) and
                      plan(row["request"])==row["plan"] and
                      set(row["reviews"])==set(row["plan"]["reviewers"]) and
                      all(v["decision"]=="accept" and v["plan_sha256"]==expected for v in row["reviews"].values()),
                      "Reviewed exact plan required")
            o.require(db.execute("SELECT COUNT(*) FROM gateway_cases WHERE state IN ('PROPOSED','APPROVED','RUNNING')").fetchone()[0]<g.MAX_ACTIVE,"Gateway backlog full")
            p=g.validate_request(row["plan"]["delegation"])
            case={"id":uuid.uuid4().hex,"schema":1,"state":"PROPOSED","plan":p,"plan_sha256":g.sha_json(p),
                  "messages":[],"attempts":0,"approval":None,"promotion":"BLOCKED","merge_authorized":False}
            gateway._save(db,case,"propose",True)
            row.update(state="DELEGATED",gateway_id=case["id"])
            self._save(db,row,"delegate")
            return row
    def observe(self,sid):
        with self.store.lock(sid):
            row=self.get(sid)
            o.require(row["state"]=="DELEGATED" and row["plan_sha256"]==g.sha_json(row["plan"]),"Delegated exact plan required")
            gateway=g.Gateway(self.store.root)
            with self.store.lock(row["gateway_id"]):
                case=gateway.get(row["gateway_id"])
                if case["state"]=="CANCELLED":
                    row.update(state="CANCELLED")
                else:
                    row.update(state="OBSERVED",observation=observed(self.store,case,row["plan"]))
                with self.store.connect() as db:
                    db.execute("BEGIN IMMEDIATE");self._save(db,row,"observe")
            return row
    def close_unverified(self,sid,note):
        # Explicit closure of terminal cases without trustworthy evidence, never a retry.
        note=g.text(note)
        with self.store.lock(sid):
            row=self.get(sid)
            o.require(row["state"]=="DELEGATED" and row["plan_sha256"]==g.sha_json(row["plan"]),"Delegated exact plan required")
            gateway=g.Gateway(self.store.root)
            with self.store.lock(row["gateway_id"]):
                case=gateway.get(row["gateway_id"])
                o.require(case["state"] in g.TERMINAL and case["state"]!="CANCELLED" and
                          case["plan_sha256"]==row["plan"]["gateway_plan_sha256"],"Terminal matching case required")
                try:
                    observed(self.store,case,row["plan"])
                except (ValueError,OSError,KeyError,TypeError,AttributeError):
                    row.update(state="INCONCLUSIVE",closure={"gateway_state":case["state"],"note":note,
                        "at":now(),"artifact_integrity":"NOT_VERIFIED","retry_authorized":False})
                else:
                    raise ValueError("Verified evidence must be observed")
                with self.store.connect() as db:
                    db.execute("BEGIN IMMEDIATE");self._save(db,row,"close_unverified")
            return row
    def cancel(self,sid):
        with self.store.lock(sid),self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row=self._get(db,sid)
            o.require(row["state"] in ("PROPOSED","REVIEWED"),"Cancel delegated case through Gateway")
            row.update(state="CANCELLED");self._save(db,row,"cancel");return row
    def metrics(self):
        counts={k:Counter() for k in PROFILES}
        lessons=0;cases=0
        with self.store.connect() as db:
            for r in db.execute("SELECT snapshot FROM department_plans WHERE state='OBSERVED'"):
                v=json.loads(r[0])["observation"];cases+=1;lessons+=len(v["lessons"])
                for k,c in v["task_counts"].items():counts[k].update(c)
        return {"observed_cases":cases,"recorded_lessons":lessons,"task_counts":{k:dict(v) for k,v in counts.items()},
                "scope":"Historical verified observations only; not a live integrity recheck or productivity ranking"}

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--registry",required=True,type=Path)
    sub=p.add_subparsers(dest="mode",required=True)
    for n in ("catalog","list","metrics"):sub.add_parser(n)
    sub.add_parser("propose").add_argument("manifest",type=Path)
    for n in ("show","history","review","delegate","observe","cancel","close-unverified"):
        q=sub.add_parser(n);q.add_argument("id")
        if n in ("review","delegate"):q.add_argument("--plan-sha256",required=True)
        if n=="close-unverified":q.add_argument("--note",required=True)
        if n=="review":
            q.add_argument("--reviewer",required=True);q.add_argument("--decision",choices=("accept","reject"),required=True);q.add_argument("--note",required=True)
    a=p.parse_args()
    try:
        d=Departments(a.registry)
        if a.mode=="catalog":out=catalog()
        elif a.mode=="list":out=d.list()
        elif a.mode=="metrics":out=d.metrics()
        elif a.mode=="propose":out=d.propose(g.read_request(a.manifest))
        elif a.mode=="review":out=d.review(a.id,a.plan_sha256,a.reviewer,a.decision,a.note)
        elif a.mode=="delegate":out=d.delegate(a.id,a.plan_sha256)
        elif a.mode=="close-unverified":out=d.close_unverified(a.id,a.note)
        else:out=getattr(d,"get" if a.mode=="show" else a.mode)(a.id)
        print(json.dumps(out,indent=2,ensure_ascii=True));return 0
    except (ValueError,OSError,KeyError,TypeError,AttributeError,sqlite3.Error):
        print("APEX_DEPARTMENTS_BLOCKED: invalid input, state, policy or evidence",file=sys.stderr);return 2
if __name__=="__main__":raise SystemExit(main())
