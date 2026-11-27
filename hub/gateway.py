"""Bounded operator-mediated agent gateway, not an agent runtime."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import uuid

from . import orchestrator as o, ci_receipt as ci
from .registry import Registry, encode, ident, now

MAX_TASKS = 8
MAX_SECONDS = 180
MAX_MESSAGES = 24
MAX_ACTIVE = 32
MAX_JSON = 64000
# Reviewed local policy, not editable by a task or external agent.
DEPARTMENTS = {
    "software": {"repo.revision": ("hub","game","hyper","engineering","architect"),
                 "source.inventory": ("game",), "architecture.audit": ("game",)},
    "physics": {"repo.revision": ("hyper","engineering"), "foundation.catalog": ("foundation",)},
    "ai": {"repo.revision": ("hyper","engineering"), "foundation.catalog": ("foundation",)},
    "racecraft": {"repo.revision": ("game","hyper","engineering"), "foundation.catalog": ("foundation",)},
    "graphics": {"repo.revision": ("assets",), "foundation.catalog": ("foundation",)},
    "research": {"repo.revision": ("foundation","engineering"), "foundation.validate": ("foundation",),
                 "foundation.discovery": ("foundation",), "foundation.catalog": ("foundation",)},
    "performance": {"repo.revision": ("hub","game","hyper","engineering"), "source.inventory": ("game",)},
    "quality": {"repo.revision": tuple(sorted(o.REPOSITORIES)), "source.inventory": ("game",),
                "architecture.audit": ("game",), "foundation.validate": ("foundation",),
                "foundation.discovery": ("foundation",), "foundation.catalog": ("foundation",),
                "github.ci-receipt": ("hub",)}
}
TERMINAL = {"PASS","FAIL","BLOCKED","INCONCLUSIVE","INTERRUPTED","CANCELLED"}
BRANCH = re.compile(r"(main|feature/[a-z0-9][a-z0-9_-]{0,63})\Z")
PROVIDERS = {"local": "REVIEWED_ADAPTER", "github": "READ_ONLY_CI_RECEIPT",
             "mcp": "NOT_CONFIGURED", "external_api": "NOT_CONFIGURED",
             "chatgpt": "NOT_CONFIGURED", "codex": "NOT_CONFIGURED"}


def sha_json(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=True,
                          separators=(",",":"),allow_nan=False).encode()).hexdigest()


def text(value, maximum=1000):
    o.require(isinstance(value,str) and 0<len(value.strip())<=maximum, "Invalid text")
    return value.strip()


def department(value):
    o.require(isinstance(value,str) and value in DEPARTMENTS,"Unknown department")
    return value


def allowed(agent, cap, target):
    o.require(target in DEPARTMENTS[department(agent)].get(cap,()),"Department capability denied")


def catalog():
    return {"schema":1,"departments":{k:{"capabilities":v,"identity":"operator-assigned role",
                                        "write_authorized":False} for k,v in DEPARTMENTS.items()},
            "providers":PROVIDERS,"limits":{"tasks":MAX_TASKS,"declared_seconds":MAX_SECONDS,
            "messages":MAX_MESSAGES,"attempts":1,"active_cases":MAX_ACTIVE},
            "authentication":"trusted local operator; no independent agent identity",
            "external_execution_authorized":False}


def validate_request(data):
    o.require(isinstance(data,dict) and set(data)=={"schema","objective","targets","tasks"}
              and type(data["schema"]) is int and data["schema"]==1,"Invalid delegation request")
    text(data["objective"])
    targets=data["targets"]
    o.require(isinstance(targets,dict) and 0<len(targets)<=len(o.REPOSITORIES) and
              all(k in o.REPOSITORIES and isinstance(v,str) and o.SHA.fullmatch(v)
                  for k,v in targets.items()),"Exact target revisions required")
    tasks=data["tasks"]
    o.require(isinstance(tasks,list) and 0<len(tasks)<=MAX_TASKS,"Invalid delegation task count")
    rows=[]; roles={}
    for task in tasks:
        o.require(isinstance(task,dict) and set(task)==o.TASK_FIELDS|{"department"},"Unexpected delegation fields")
        cap,target=task["capability"],task["target"]
        o.require(isinstance(cap,str) and isinstance(target,str),"Invalid capability/target")
        allowed(task["department"],cap,target)
        o.require(target in targets,"Target revision missing")
        rows.append({k:v for k,v in task.items() if k!="department"})
    manifest={"schema":o.SCHEMA,"tasks":rows}
    order=o.plan(manifest,"read")
    o.require(set(targets)=={t["target"] for t in order},"Unused target binding")
    o.require(sum(t["timeout_seconds"] for t in rows)<=MAX_SECONDS,"Delegation budget exceeded")
    roles={t["id"]:t["department"] for t in tasks}
    return {"objective":data["objective"],"kind":"local","targets":targets,
            "manifest":manifest,"departments":roles,"order":[t["id"] for t in order]}


def validate_ci(run_id,sha,branch):
    o.require(ci.positive(run_id) and isinstance(sha,str) and o.SHA.fullmatch(sha)
              and isinstance(branch,str) and BRANCH.fullmatch(branch),"Invalid CI identity")
    allowed("quality","github.ci-receipt","hub")
    return {"kind":"github","objective":"Verify exact GitHub Actions receipt","department":"quality",
            "target":"hub","sha":sha,"branch":branch,"run_id":run_id}


def read_request(path):
    with Path(path).open("rb") as f:
        raw=f.read(MAX_JSON+1)
    o.require(len(raw)<=MAX_JSON,"Request too large")
    return json.loads(raw,object_pairs_hook=o.unique_object,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Nonfinite JSON")))


class Gateway:
    def __init__(self, root):
        self.store=Registry(root)
        with self.store.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS gateway_cases (id TEXT PRIMARY KEY,state TEXT NOT NULL,snapshot TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS gateway_events (id TEXT NOT NULL REFERENCES gateway_cases(id),seq INTEGER NOT NULL,at TEXT NOT NULL,action TEXT NOT NULL,snapshot TEXT NOT NULL,PRIMARY KEY(id,seq))")

    def _get(self,db,sid):
        item=db.execute("SELECT snapshot FROM gateway_cases WHERE id=?",(ident(sid),)).fetchone()
        o.require(item is not None,"Unknown gateway case")
        return json.loads(item[0])

    def get(self,sid):
        with self.store.connect() as db:
            return self._get(db,sid)

    def list(self):
        with self.store.connect() as db:
            return [json.loads(x[0]) for x in db.execute("SELECT snapshot FROM gateway_cases ORDER BY rowid DESC LIMIT 100")]

    def history(self,sid):
        self.get(sid)
        with self.store.connect() as db:
            return [{"seq":x["seq"],"at":x["at"],"action":x["action"],"snapshot":json.loads(x["snapshot"])}
                    for x in db.execute("SELECT * FROM gateway_events WHERE id=? ORDER BY seq",(sid,))]

    def _save(self,db,row,action,start=False):
        payload=encode(row)
        if start:
            db.execute("INSERT INTO gateway_cases VALUES (?,?,?)",(row["id"],row["state"],payload))
        else:
            db.execute("UPDATE gateway_cases SET state=?,snapshot=? WHERE id=?",(row["state"],payload,row["id"]))
        seq=db.execute("SELECT COALESCE(MAX(seq),0)+1 FROM gateway_events WHERE id=?",(row["id"],)).fetchone()[0]
        db.execute("INSERT INTO gateway_events VALUES (?,?,?,?,?)",(row["id"],seq,now(),action,payload))

    def _create(self,plan):
        row={"id":uuid.uuid4().hex,"schema":1,"state":"PROPOSED","plan":plan,"plan_sha256":sha_json(plan),
             "messages":[],"attempts":0,"approval":None,"promotion":"BLOCKED","merge_authorized":False}
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            o.require(db.execute("SELECT COUNT(*) FROM gateway_cases WHERE state IN ('PROPOSED','APPROVED','RUNNING')").fetchone()[0]<MAX_ACTIVE,"Gateway queue full")
            self._save(db,row,"propose",True)
        return row

    def propose(self,data):
        return self._create(validate_request(data))

    def propose_ci(self,run_id,sha,branch):
        return self._create(validate_ci(run_id,sha,branch))

    def _transition(self,sid,states,action,change):
        with self.store.lock(sid):
            with self.store.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                row=self._get(db,sid)
                o.require(row["state"] in states,"Invalid gateway transition")
                change(row)
                self._save(db,row,action)
                return row

    def message(self,sid,sender,recipient,body):
        department(sender);department(recipient)
        o.require(sender!=recipient,"Distinct message participants required")
        body=text(body)
        def change(row):
            participants=set(row["plan"].get("departments",{}).values()) if row["plan"]["kind"]=="local" else {"quality"}
            o.require(sender in participants and recipient in participants,"Not a case participant")
            o.require(len(row["messages"])<MAX_MESSAGES,"Message budget exhausted")
            row["messages"].append({"from":sender,"to":recipient,"text":body,"at":now(),
                                    "authority":"operator relay; untrusted text; not executable"})
        return self._transition(sid,{"PROPOSED","APPROVED"}|TERMINAL,"message",change)

    def approve(self,sid,expected,note):
        note=text(note)
        def change(row):
            o.require(row["plan_sha256"]==expected==sha_json(row["plan"]),"Approval digest mismatch")
            row.update(state="APPROVED",approval={"plan_sha256":expected,"at":now(),"note":note,
                       "identity":"local operator declaration, not authenticated human identity"})
        return self._transition(sid,{"PROPOSED"},"approve",change)

    def cancel(self,sid):
        return self._transition(sid,{"PROPOSED","APPROVED"},"cancel",lambda r:r.update(state="CANCELLED"))

    def recover(self,sid):
        return self._transition(sid,{"RUNNING"},"recover",lambda r:r.update(state="INTERRUPTED",
            reason="No replay; inspect evidence and orphan processes"))

    def run(self,sid,expected,bindings):
        with self.store.lock(sid):
            row=self.get(sid)
            o.require(row["state"]=="APPROVED" and row["attempts"]==0 and
                      expected==row["plan_sha256"]==sha_json(row["plan"])==row["approval"]["plan_sha256"],
                      "Approved exact plan required")
            plan=row["plan"]
            if plan["kind"]=="local":
                original={"schema":1,"objective":plan["objective"],"targets":plan["targets"],
                          "tasks":[{**t,"department":plan["departments"][t["id"]]} for t in plan["manifest"]["tasks"]]}
                o.require(validate_request(original)==plan,"Delegation policy changed")
                targets=o.checked_targets(bindings)
                o.require({k:v[1] for k,v in targets.items()}==plan["targets"],"Approved targets differ")
                o.require(all(not self.store.root.is_relative_to(root) and not root.is_relative_to(self.store.root)
                              for root,_ in targets.values()),"Registry must be outside checkouts")
            else:
                o.require(plan==validate_ci(plan["run_id"],plan["sha"],plan["branch"]) and not bindings,
                          "Invalid GitHub receipt plan")
            out=self.store.root/("gateway-"+sid)
            out.mkdir(mode=0o700)
            row.update(state="RUNNING",attempts=1,started=now())
            with self.store.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                self._save(db,row,"run")
            try:
                if plan["kind"]=="local":
                    # Domain engine owns actual DAG validation/execution and dependency blocking.
                    result=o.run(plan["manifest"],"read",targets,out/"dag")
                    o.require(result["targets"]=={k:{"repository":o.REPOSITORIES[k],"sha":v}
                                                for k,v in plan["targets"].items()},"Result target mismatch")
                    o.require(result["manifest_sha256"]==sha_json(plan["manifest"]),"Result manifest mismatch")
                    row["handoffs"]=[{"task":t["id"],"department":plan["departments"][t["id"]],
                                      "depends_on":t["depends_on"],"status":t["status"],
                                      "attempts":t["attempts"],"evidence":t["evidence"]} for t in result["tasks"]]
                    row["state"]=result["status"]
                    evidence=out/"dag/report.json"
                else:
                    result=ci.receipt(plan["run_id"],plan["sha"],plan["branch"])
                    row["state"]=result["outcome"]
                    evidence=out/"ci-receipt.json"
                    with evidence.open("x",encoding="utf-8") as f:
                        json.dump(result,f,indent=2)
                row["receipt"]={"path":str(evidence.relative_to(self.store.root)),
                                "sha256":o.digest(evidence),"bytes":evidence.stat().st_size}
            except (OSError,ValueError,KeyError,TypeError,subprocess.SubprocessError) as exc:
                row.update(state="BLOCKED",reason=type(exc).__name__+": "+str(exc)[:180])
            row["finished"]=now()
            with self.store.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                self._save(db,row,"finish")
            return row


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--registry",type=Path,required=True)
    sub=p.add_subparsers(dest="mode",required=True)
    sub.add_parser("catalog");sub.add_parser("list")
    sub.add_parser("propose").add_argument("manifest",type=Path)
    c=sub.add_parser("propose-ci");c.add_argument("--run-id",type=int,required=True);c.add_argument("--sha",required=True);c.add_argument("--branch",required=True)
    for name in ("show","history","approve","run","message","cancel","recover"):
        q=sub.add_parser(name);q.add_argument("id")
        if name in ("approve","run"):
            q.add_argument("--plan-sha256",required=True)
        if name=="approve":q.add_argument("--note",required=True)
        if name=="run":
            q.add_argument("--checkout",nargs=3,action="append",default=[])
            q.add_argument("--trusted-checkouts",action="store_true",required=True)
        if name=="message":
            for flag in ("sender","recipient","body"):q.add_argument("--"+flag,required=True)
    a=p.parse_args()
    try:
        g=Gateway(a.registry)
        if a.mode=="catalog":out=catalog()
        elif a.mode=="list":out=g.list()
        elif a.mode=="propose":out=g.propose(read_request(a.manifest))
        elif a.mode=="propose-ci":out=g.propose_ci(a.run_id,a.sha,a.branch)
        elif a.mode=="approve":out=g.approve(a.id,a.plan_sha256,a.note)
        elif a.mode=="run":out=g.run(a.id,a.plan_sha256,a.checkout)
        elif a.mode=="message":out=g.message(a.id,a.sender,a.recipient,a.body)
        else:out=getattr(g,"get" if a.mode=="show" else a.mode)(a.id)
        print(json.dumps(out,indent=2,ensure_ascii=True))
        return 1 if a.mode=="run" and out["state"]!="PASS" else 0
    except (ValueError,OSError,TypeError,KeyError,sqlite3.Error,subprocess.SubprocessError) as exc:
        print("APEX_GATEWAY_BLOCKED: "+str(exc),file=sys.stderr)
        return 2


if __name__=="__main__":
    raise SystemExit(main())
