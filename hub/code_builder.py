"""Approved bounded local candidate construction, not an autonomous code model."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import uuid

from . import development as d, gateway as g, graphics, orchestrator as o
from .registry import Registry, encode, ident, now

MAX_FILES=8
MAX_BYTES=32768
MAX_TOTAL=131072
MAX_ACTIVE=16
FILE=re.compile(r"(?:docs/[A-Za-z0-9_-]+\.md|hub/features/[a-z][a-z0-9_]*\.py|tests/test_feature_[a-z0-9_]+\.py)\Z")
COUNT=re.compile(r"^Ran ([1-9][0-9]*) tests? in [0-9.]+s$",re.M)

def command(root,home,*args):
    env={"PATH":os.defpath,"HOME":str(home),"XDG_CONFIG_HOME":str(home),
         "GIT_CONFIG_NOSYSTEM":"1","GIT_CONFIG_GLOBAL":"/dev/null","GIT_TERMINAL_PROMPT":"0",
         "GIT_AUTHOR_NAME":"APEX local candidate","GIT_AUTHOR_EMAIL":"apex-candidate@example.invalid",
         "GIT_COMMITTER_NAME":"APEX local candidate","GIT_COMMITTER_EMAIL":"apex-candidate@example.invalid",
         "LANG":"C.UTF-8"}
    return subprocess.check_output(["git","-C",str(root),"-c","core.hooksPath=/dev/null",
        "-c","commit.gpgsign=false",*args],env=env,timeout=40,stderr=subprocess.DEVNULL).decode("utf-8").strip()

def file_blob(root,home,sha,path):
    value=command(root,home,"ls-tree",sha,"--",path)
    if not value:return None
    mode,typ,blob=value.split("\t",1)[0].split()
    o.require(mode=="100644" and typ=="blob" and o.SHA.fullmatch(blob),"Only regular non-executable file allowed")
    return blob

def manifest(req,row):
    o.require(isinstance(req,dict) and set(req)=={"schema","development_id","edits"} and
              type(req["schema"]) is int and req["schema"]==1 and req["development_id"]==row["id"],
              "Invalid candidate manifest")
    o.require(row["state"] in ("PLANNED","REPAIR_READY"),"Reviewed development plan required")
    edits=req["edits"]
    o.require(isinstance(edits,list) and 1<=len(edits)<=MAX_FILES,"Edit count exceeds limit")
    total=0;seen=set()
    for e in edits:
        o.require(isinstance(e,dict) and set(e)=={"path","expected_blob","content"},"Invalid edit")
        p=e["path"];v=e["content"];b=e["expected_blob"]
        o.require(isinstance(p,str) and FILE.fullmatch(p) and p in row["scope"] and p not in seen,
                  "Path outside approved candidate allowlist")
        d.scope([p]);seen.add(p)
        o.require(b is None or isinstance(b,str) and o.SHA.fullmatch(b),"Invalid expected Git blob")
        o.require(not p.startswith("tests/") or b is None,"Existing test changes denied")
        o.require(isinstance(v,str) and "\x00" not in v and "\r" not in v,"Invalid UTF-8 text")
        size=len(v.encode("utf-8"));total+=size
        o.require(0<size<=MAX_BYTES and total<=MAX_TOTAL,"Edit size exceeds limit")
        if p.endswith(".py"):
            try:compile(v,p,"exec")
            except SyntaxError as exc:raise ValueError("Invalid Python syntax: "+p) from exc
    parent=row["candidates"][-1]["sha"] if row["candidates"] else row["base"]
    branch=row["candidates"][-1]["branch"] if row["candidates"] else "feature/local-"+row["id"]
    return {"schema":1,"development_id":row["id"],"development_sha256":g.sha_json(row),
            "base":row["base"],"parent":parent,"branch":branch,"edits":edits,
            "max_attempts":1,"test_timeout_seconds":120,"remote_write_authorized":False,"merge_authorized":False}

def safe_path(root,path):
    p=root/path
    o.require(p.resolve()==p,"Symlink or path escape")
    for parent in p.parents:
        if parent==root:break
        o.require(not parent.is_symlink() and (not parent.exists() or parent.is_dir()),"Unsafe parent")
    return p

class Builder:
    def __init__(self,root):
        self.store=Registry(root)
        with self.store.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS code_candidates (id TEXT PRIMARY KEY,state TEXT NOT NULL,snapshot TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS code_candidate_events (id TEXT NOT NULL REFERENCES code_candidates(id),seq INTEGER NOT NULL,at TEXT NOT NULL,action TEXT NOT NULL,snapshot TEXT NOT NULL,PRIMARY KEY(id,seq))")
    def get(self,sid):
        with self.store.connect() as db:
            r=db.execute("SELECT state,snapshot FROM code_candidates WHERE id=?",(ident(sid),)).fetchone()
            o.require(r is not None,"Unknown build case")
            row=json.loads(r["snapshot"])
            o.require(row["id"]==sid and row["state"]==r["state"] and row["plan_sha256"]==g.sha_json(row["plan"]),"Candidate identity mismatch")
            return row
    def _save(self,row,action,new=False):
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if new:
                o.require(db.execute("SELECT COUNT(*) FROM code_candidates WHERE state IN ('PROPOSED','APPROVED','RUNNING')").fetchone()[0]<MAX_ACTIVE,"Build queue full")
                db.execute("INSERT INTO code_candidates VALUES (?,?,?)",(row["id"],row["state"],encode(row)))
            else:db.execute("UPDATE code_candidates SET state=?,snapshot=? WHERE id=?",(row["state"],encode(row),row["id"]))
            seq=db.execute("SELECT COALESCE(MAX(seq),0)+1 FROM code_candidate_events WHERE id=?",(row["id"],)).fetchone()[0]
            db.execute("INSERT INTO code_candidate_events VALUES (?,?,?,?,?)",(row["id"],seq,now(),action,encode(row)))
    def propose(self,req):
        row=d.Development(self.store.root).get(req["development_id"])
        plan=manifest(req,row)
        p={"id":uuid.uuid4().hex,"state":"PROPOSED","created":now(),"plan":plan,"plan_sha256":g.sha_json(plan),
           "attempts":0,"merge_authorized":False}
        self._save(p,"propose",True);return p
    def approve(self,sid,expected):
        with self.store.lock(sid):
            row=self.get(sid)
            o.require(row["state"]=="PROPOSED" and expected==row["plan_sha256"],"Exact candidate approval required")
            row.update(state="APPROVED",approval={"sha256":expected,"at":now(),"authority":"operator approves supplied text and trusted local test execution"})
            self._save(row,"approve");return row
    def recover(self,sid):
        with self.store.lock(sid):
            row=self.get(sid);o.require(row["state"]=="RUNNING","Only abandoned build may be recovered")
            row.update(state="INTERRUPTED",reason="No replay; inspect workspace and child processes")
            self._save(row,"recover");return row
    def cancel(self,sid):
        with self.store.lock(sid):
            row=self.get(sid);o.require(row["state"] in ("PROPOSED","APPROVED"),"Cannot cancel active or terminal build")
            row["state"]="CANCELLED";self._save(row,"cancel");return row
    def build(self,sid,expected,checkout):
        with self.store.lock(sid):
            row=self.get(sid);p=row["plan"]
            o.require(row["state"]=="APPROVED" and row["attempts"]==0 and expected==row["plan_sha256"]==row["approval"]["sha256"],"Exact approval required")
            dev=d.Development(self.store.root)
            prior=dev.get(p["development_id"])
            o.require(g.sha_json(prior)==p["development_sha256"],"Development plan changed")
            o.require(manifest({"schema":1,"development_id":prior["id"],"edits":p["edits"]},prior)==p,"Policy changed")
            source,_=o.checked_targets([("hub",checkout,p["parent"])])["hub"]
            o.require(not self.store.root.is_relative_to(source) and not source.is_relative_to(self.store.root),"Registry outside source required")
            work=self.store.root/("code-"+sid)
            o.require(not work.exists() and not work.is_symlink(),"Workspace already exists")
            row.update(state="RUNNING",attempts=1)
            self._save(row,"claim")
            try:
                work.mkdir(mode=0o700);home=work/"home";home.mkdir(mode=0o700)
                dest=work/"checkout"
                # No network, no shared object hardlinks, no source writes or inherited credentials.
                command(work,home,"clone","--no-local","--no-checkout","--no-tags","--",str(source),str(dest))
                command(dest,home,"remote","set-url","origin","https://github.com/"+o.REPOSITORIES["hub"]+".git")
                command(dest,home,"checkout","-q","-B",p["branch"],p["parent"])
                # clone may already have a local branch with this name when repairing.
                for e in p["edits"]:
                    f=safe_path(dest,e["path"])
                    actual=file_blob(dest,home,p["parent"],e["path"])
                    o.require(actual==e["expected_blob"],"Expected blob mismatch")
                    o.require((actual is None and not f.exists()) or (actual is not None and f.is_file()),"Unexpected working file")
                    if actual is not None:
                        o.require(f.read_bytes()!=e["content"].encode(),"No-op edit")
                    f.parent.mkdir(parents=True,exist_ok=True)
                    f.write_bytes(e["content"].encode())
                    command(dest,home,"add","--",e["path"])
                command(dest,home,"commit","-qm","APEX: approved bounded candidate")
                sha=command(dest,home,"rev-parse","HEAD")
                o.require(command(dest,home,"rev-parse","HEAD^")==p["parent"],"Candidate parent mismatch")
                changed=command(dest,home,"diff","--name-only","--no-renames",p["parent"],sha).splitlines()
                o.require(set(changed)=={e["path"] for e in p["edits"]},"Unexpected candidate diff")
                o.checked_targets([("hub",dest,sha)])
                o.checked_targets([("hub",source,p["parent"])])
                row["candidate"]={"sha":sha,"branch":p["branch"],"changed":sorted(changed)}
                self._save(row,"commit")
                log=work/"tests.log"
                code=graphics.execute([sys.executable,"-B","-m","unittest","discover","-s","tests","-v"],dest,log,120,home)
                data=log.read_text(encoding="utf-8",errors="replace")
                counts=COUNT.findall(data)
                ok=code==0 and len(counts)==1 and data.rstrip().endswith("OK")
                o.checked_targets([("hub",dest,sha)])
                o.checked_targets([("hub",source,p["parent"])])
                o.require(g.sha_json(dev.get(prior["id"]))==p["development_sha256"],"Development plan changed during build")
                row.update(state="PASS" if ok else "FAIL",local_tests={"exit_code":code,"count":int(counts[0]) if len(counts)==1 else None,
                    "evidence":graphics.file_evidence(work,"tests.log",graphics.MAX_LOG)},
                    ci="NOT_RUN",publication="NOT_AUTHORIZED")
                self._save(row,"finish")
            except (ValueError,OSError,KeyError,TypeError,AttributeError,subprocess.SubprocessError) as exc:
                row.update(state="BLOCKED",reason=type(exc).__name__,ci="NOT_RUN")
                self._save(row,"blocked")
            return row
    def handoff(self,sid):
        row=self.get(sid);o.require(row["state"]=="PASS","Passing local candidate required")
        p=row["plan"];c=row["candidate"];work=self.store.root/("code-"+sid)
        e=row["local_tests"]["evidence"]
        o.require(graphics.file_evidence(work,"tests.log",graphics.MAX_LOG)==e,"Local test evidence changed")
        dev=d.Development(self.store.root)
        o.require(g.sha_json(dev.get(p["development_id"]))==p["development_sha256"],"Development plan changed")
        # Existing independent exact diff/branch/gate checks still apply.
        return dev.candidate(p["development_id"],work/"checkout",c["sha"])

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("--registry",required=True,type=Path)
    s=p.add_subparsers(dest="mode",required=True)
    s.add_parser("propose").add_argument("manifest",type=Path)
    for n in ("show","approve","build","handoff","recover","cancel"):
        q=s.add_parser(n);q.add_argument("id")
        if n in ("approve","build"):q.add_argument("--plan-sha256",required=True)
        if n=="build":
            q.add_argument("--checkout",required=True,type=Path)
            q.add_argument("--trusted-candidate",required=True,action="store_true")
    a=p.parse_args()
    try:
        b=Builder(a.registry)
        if a.mode=="propose":out=b.propose(g.read_request(a.manifest))
        elif a.mode=="approve":out=b.approve(a.id,a.plan_sha256)
        elif a.mode=="build":out=b.build(a.id,a.plan_sha256,a.checkout)
        else:out=getattr(b,"get" if a.mode=="show" else a.mode)(a.id)
        print(json.dumps(out,indent=2,ensure_ascii=True))
        return 1 if a.mode=="build" and out["state"]!="PASS" else 0
    except (ValueError,OSError,KeyError,TypeError,AttributeError,sqlite3.Error,subprocess.SubprocessError):
        print("APEX_CODE_BUILD_BLOCKED: invalid state, scope or evidence",file=sys.stderr);return 2
if __name__=="__main__":raise SystemExit(main())
