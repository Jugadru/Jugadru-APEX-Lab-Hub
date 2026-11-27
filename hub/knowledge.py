"""Private, evidence-linked engineering memory. No execution or automatic learning."""
import argparse
from contextlib import closing
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import sys
from types import SimpleNamespace
import uuid

from . import dashboard, departments, gateway as g, mission, orchestrator as o, research
from .registry import Registry, encode, ident, now, MAX_SNAPSHOT

MAX_ENTRIES=2048
TABLES={"department":"department_plans","mission":"missions","research":"research_cases"}
NOTE_KINDS={"decision","lesson","algorithm","asset_reference"}
TAG=re.compile(r"[a-z][a-z0-9_-]{0,39}\Z")
HASH=re.compile(r"[0-9a-f]{64}\Z")
OUTCOMES={"PASS","FAIL","INCONCLUSIVE","BLOCKED","NOT_RUN"}

def sha(value):
    o.require(isinstance(value,str) and HASH.fullmatch(value),"Invalid evidence digest")
    return value

def read_hashed(root,relative,expected):
    sha(expected)
    p=root/relative
    o.require(p.is_file() and p.resolve()==p,"Missing or unsafe evidence")
    with p.open("rb") as f:raw=f.read(MAX_SNAPSHOT+1)
    o.require(len(raw)<=MAX_SNAPSHOT and hashlib.sha256(raw).hexdigest()==expected,"Evidence changed or oversized")
    obj=json.loads(raw,object_pairs_hook=o.unique_object,
                   parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Nonfinite JSON")))
    o.require(isinstance(obj,dict),"Invalid evidence object")
    return obj

def stored(db,table,sid):
    # The table argument is selected by fixed code, not input text.
    row=db.execute("SELECT state,snapshot FROM "+table+" WHERE id=?",(ident(sid),)).fetchone()
    o.require(row is not None,"Source record not found")
    value=dashboard.unpack(row["snapshot"])
    o.require(value["id"]==sid and value["state"]==row["state"],"Source identity mismatch")
    return value

def revisions(value):
    o.require(isinstance(value,dict) and bool(value) and all(k in o.REPOSITORIES and
              isinstance(v,str) and o.SHA.fullmatch(v) for k,v in value.items()),"Invalid source revision")
    return value

def project(kind,source,sid):
    o.require(kind in TABLES,"Unknown evidence source")
    root=dashboard.checked_db(source).parent
    with closing(sqlite3.connect((root/"registry.sqlite3").as_uri()+"?mode=ro",uri=True,timeout=5)) as db:
        db.row_factory=sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        db.execute("PRAGMA trusted_schema=OFF")
        db.execute("BEGIN")
        o.require(db.execute("PRAGMA user_version").fetchone()[0]==1,"Unsupported registry version")
        row=stored(db,TABLES[kind],sid)
        if kind=="department":
            o.require(row["state"]=="OBSERVED" and row["plan_sha256"]==g.sha_json(row["plan"]),"Observed exact plan required")
            case=stored(db,"gateway_cases",row["gateway_id"])
            obs=departments.observed(SimpleNamespace(root=root),case,row["plan"])
            o.require(obs==row["observation"],"Recorded observation differs from verified result")
            tasks=[{"id":t["id"],"capability":t["capability"],"target":t["target"],
                    "department":t["department"]} for t in row["plan"]["delegation"]["tasks"]]
            owners=sorted({t["department"] for t in tasks})
            payload={"outcome":obs["outcome"],"revisions":revisions(case["plan"]["targets"]),
                     "gateway_id":case["id"],"receipt_sha256":sha(obs["receipt_sha256"]),"tasks":tasks,
                     "task_counts":obs["task_counts"],"lessons":obs["lessons"],
                     "verification_scope":"Gateway report and domain artifact bytes"}
        elif kind=="mission":
            check=mission.audit_completed(row,root/("mission-"+sid))
            o.require(check["status"]=="PASS","Completed audited mission required")
            owners=sorted({t["department"] for t in row["plan"]["delegation"]["tasks"]})
            payload={"outcome":row["state"],"revisions":revisions(row["plan"]["targets"]),
                     "stages":[{"stage":t["stage"],"status":t["status"],"sha256":sha(t["sha256"])} for t in row["stages"]],
                     "plan_sha256":sha(row["plan_sha256"]),"verification_scope":"Mission receipts, domain evidence and dashboard bytes"}
        else:
            o.require(row["state"] in ("PASS","FAIL","INCONCLUSIVE") and row["attempts"]==1 and
                      row["promotion"]=="NOT_AUTHORIZED" and row["scientific_validation"]=="NOT_PERFORMED",
                      "Completed research comparison required")
            rev=revisions(row["revisions"])
            o.require(set(rev)=={"foundation","engineering"},"Invalid research revisions")
            folder="research-"+sid+"/"
            frozen=read_hashed(root,folder+"frozen-plan.json",row["plan_sha256"])
            comp=read_hashed(root,folder+"comparison.json",row["comparison_sha256"])
            o.require(frozen["revisions"]==rev and frozen["promotion"]=="NOT_AUTHORIZED" and
                      frozen["execution_authorized"] is False and comp["promotion"]=="NOT_AUTHORIZED" and
                      comp["status"]==row["state"],"Research provenance mismatch")
            specs=frozen["experiments"]
            names=row["experiments"]
            o.require(isinstance(names,list) and 1<=len(names)<=4 and len(set(names))==len(names) and
                      set(names)==set(specs) and all(n in research.BUILTINS for n in names),"Research experiment mismatch")
            actual=comp["experiments"]
            o.require(len(actual)==len(names) and {x["experiment"] for x in actual}==set(names),"Missing comparison results")
            completed={x["experiment"]:x["status"] for x in row["completed"]}
            results=[]
            for x in actual:
                n=x["experiment"];status=x["status"];spec=research.check_spec(specs[n])
                o.require(status in OUTCOMES and completed.get(n)==status,"Comparison outcome mismatch")
                metric_rows=[]
                if status in ("PASS","FAIL"):
                    metrics=x["metrics"]
                    o.require(isinstance(metrics,list) and len(metrics)==len(spec["criteria"]) and
                              {m["metric"] for m in metrics}==set(spec["criteria"]),"Missing metrics")
                    checks=[]
                    for m in metrics:
                        rule=spec["criteria"][m["metric"]]
                        o.require(all(m[k]==rule[k] for k in ("op","threshold","unit")) and
                                  research.numeric(m["observed"]),"Changed comparison criteria")
                        a,b=m["observed"],rule["threshold"]
                        ok=a==b if rule["op"]=="eq" else a<=b if rule["op"]=="le" else a>=b
                        o.require(m["criterion_met"] is ok,"Comparison criterion mismatch")
                        checks.append(ok)
                        metric_rows.append({k:m[k] for k in ("metric","observed","op","threshold","unit","criterion_met")})
                    o.require((status=="PASS")==all(checks),"Comparison status mismatch")
                results.append({"experiment":n,"status":status,"sources":spec["sources"],"seed":spec["config"]["seed"],
                                "metrics":metric_rows})
            outcome="FAIL" if any(x["status"]=="FAIL" for x in results) else "PASS" if all(x["status"]=="PASS" for x in results) else "INCONCLUSIVE"
            o.require(outcome==row["state"],"Research summary mismatch")
            owners=["research"]
            payload={"outcome":outcome,"revisions":rev,"plan_sha256":sha(row["plan_sha256"]),
                     "comparison_sha256":sha(row["comparison_sha256"]),"experiments":results,
                     "verification_scope":"Frozen plan and comparison bytes, metric consistency; underlying experiment artifacts not independently re-audited",
                     "scientific_validation":"NOT_PERFORMED","gameplay_validation":"NOT_RUN"}
        o.require(all(k in departments.PROFILES for k in owners),"Invalid department")
        db.rollback()
    return {"kind":kind,"source_id":sid,"source_sha256":g.sha_json(row),"departments":owners,"content":payload,
            "execution_authorized":False,"promotion_authorized":False}

def note_contract(data):
    fields={"schema","kind","department","title","body","tags","evidence_ids","supersedes","provenance"}
    o.require(isinstance(data,dict) and set(data)==fields and type(data["schema"]) is int and data["schema"]==1,
              "Invalid annotation schema")
    o.require(data["kind"] in NOTE_KINDS,"Invalid annotation kind")
    g.department(data["department"])
    title=g.text(data["title"],120);body=g.text(data["body"],4000)
    tags=data["tags"];ids=data["evidence_ids"]
    o.require(isinstance(tags,list) and len(tags)<=12 and all(isinstance(t,str) and TAG.fullmatch(t) for t in tags)
              and len(set(tags))==len(tags),"Invalid tags")
    o.require(isinstance(ids,list) and 1<=len(ids)<=8 and all(isinstance(x,str) for x in ids)
              and len(set(ids))==len(ids),"Link 1..8 evidence records")
    for x in ids:ident(x)
    parent=data["supersedes"]
    if parent is not None:ident(parent)
    p=data["provenance"]
    if p is not None:
        o.require(isinstance(p,dict) and set(p)=={"source","author","license","revision"},"Invalid declared provenance")
        p={k:g.text(v,500) for k,v in p.items()}
    o.require(data["kind"]!="asset_reference" or p is not None,"Asset reference needs declared provenance")
    return {**data,"title":title,"body":body,"provenance":p}

class Knowledge:
    def __init__(self,root):
        self.store=Registry(root)
        with self.store.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS knowledge_entries (id TEXT PRIMARY KEY,state TEXT NOT NULL,snapshot TEXT NOT NULL,source_key TEXT UNIQUE,supersedes TEXT UNIQUE REFERENCES knowledge_entries(id))")
    def _get(self,db,sid):
        row=db.execute("SELECT state,snapshot FROM knowledge_entries WHERE id=?",(ident(sid),)).fetchone()
        o.require(row is not None,"Unknown memory record")
        value=dashboard.unpack(row["snapshot"])
        o.require(value["id"]==sid and value["state"]==row["state"] and
                  value["digest"]==g.sha_json({k:v for k,v in value.items() if k!="digest"}),"Memory record integrity mismatch")
        return value
    def get(self,sid):
        with self.store.connect() as db:return self._get(db,sid)
    def _insert(self,db,value,key=None,parent=None):
        o.require(db.execute("SELECT COUNT(*) FROM knowledge_entries").fetchone()[0]<MAX_ENTRIES,"Memory capacity reached")
        row={"id":uuid.uuid4().hex,"created":now(),**value,"execution_authorized":False,"promotion_authorized":False}
        row["digest"]=g.sha_json(row)
        db.execute("INSERT INTO knowledge_entries VALUES (?,?,?,?,?)",(row["id"],row["state"],encode(row),key,parent))
        return row
    def ingest(self,kind,source,sid):
        value=project(kind,source,sid)
        key=kind+":"+sid
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing=db.execute("SELECT id FROM knowledge_entries WHERE source_key=?",(key,)).fetchone()
            if existing:
                row=self._get(db,existing["id"])
                o.require(row["evidence"]==value,"Source differs from recorded immutable memory")
                return row
            return self._insert(db,{"state":"VERIFIED","evidence":value},key)
    def annotate(self,data):
        note=note_contract(data)
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            links=[]
            for sid in note["evidence_ids"]:
                e=self._get(db,sid)
                o.require(e["state"]=="VERIFIED","Only evidence entries may be linked")
                links.append({"id":sid,"digest":e["digest"]})
            parent=note["supersedes"]
            if parent:
                prev=self._get(db,parent)
                o.require(prev["state"]=="DECLARED" and prev["annotation"]["kind"]==note["kind"] and
                          prev["annotation"]["department"]==note["department"],"Incompatible supersession")
                o.require(db.execute("SELECT 1 FROM knowledge_entries WHERE supersedes=?",(parent,)).fetchone() is None,
                          "Annotation already superseded")
            return self._insert(db,{"state":"DECLARED","annotation":note,"links":links,
                      "authority":"Operator declaration; not independent validation or license clearance"},parent=parent)
    def search(self,query="",department=None,limit=20,include_superseded=False):
        o.require(isinstance(query,str) and len(query)<=120 and type(limit) is int and 1<=limit<=100,"Invalid search bounds")
        if department is not None:g.department(department)
        with self.store.connect() as db:
            superseded={x[0] for x in db.execute("SELECT supersedes FROM knowledge_entries WHERE supersedes IS NOT NULL")}
            out=[]
            for r in db.execute("SELECT id FROM knowledge_entries ORDER BY rowid DESC LIMIT ?",(MAX_ENTRIES,)):
                v=self._get(db,r["id"])
                if not include_superseded and v["id"] in superseded:continue
                if v["state"]=="VERIFIED":
                    e=v["evidence"];owners=e["departments"];hay=json.dumps(e,ensure_ascii=False)
                else:
                    a=v["annotation"];owners=[a["department"]];hay=json.dumps(a,ensure_ascii=False)
                if (department is None or department in owners) and query.casefold() in hay.casefold():
                    out.append(v)
                    if len(out)==limit:break
            return out
    def audit(self,sid,source):
        row=self.get(sid)
        o.require(row["state"]=="VERIFIED","Only evidence can be re-audited")
        e=row["evidence"]
        try:
            same=project(e["kind"],source,e["source_id"])==e
        except (ValueError,OSError,KeyError,TypeError,AttributeError,sqlite3.Error):
            same=False
        return {"id":sid,"status":"PASS" if same else "FAIL","historical_state_changed":False,
                "scope":"Current source comparison to preserved projection; no new execution"}

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--registry",required=True,type=Path)
    s=p.add_subparsers(dest="mode",required=True)
    q=s.add_parser("ingest");q.add_argument("kind",choices=TABLES);q.add_argument("id");q.add_argument("--source",required=True,type=Path)
    s.add_parser("annotate").add_argument("manifest",type=Path)
    s.add_parser("show").add_argument("id")
    q=s.add_parser("audit");q.add_argument("id");q.add_argument("--source",required=True,type=Path)
    q=s.add_parser("search");q.add_argument("--query",default="");q.add_argument("--department",choices=departments.PROFILES)
    q.add_argument("--limit",type=int,default=20);q.add_argument("--include-superseded",action="store_true")
    a=p.parse_args()
    try:
        k=Knowledge(a.registry)
        if a.mode=="ingest":out=k.ingest(a.kind,a.source,a.id)
        elif a.mode=="annotate":out=k.annotate(g.read_request(a.manifest))
        elif a.mode=="audit":out=k.audit(a.id,a.source)
        elif a.mode=="show":out=k.get(a.id)
        else:out=k.search(a.query,a.department,a.limit,a.include_superseded)
        print(json.dumps(out,indent=2,ensure_ascii=True))
        return 1 if a.mode=="audit" and out["status"]!="PASS" else 0
    except (ValueError,OSError,KeyError,TypeError,AttributeError,sqlite3.Error):
        print("APEX_KNOWLEDGE_BLOCKED: invalid input, source or evidence",file=sys.stderr);return 2

if __name__=="__main__":raise SystemExit(main())
