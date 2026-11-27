"""Explicit, read-only infrastructure diagnostics with durable private snapshots."""
import argparse
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import subprocess
import sys
import urllib.request
import urllib.error
import uuid

from . import __version__, orchestrator as o, graphics, research, ci_receipt
from .registry import Registry, encode, ident, now

PINS={**research.PINS,"assets":graphics.FACTORY_SHA}
# Owner-approved public source policy; private evidence is a separate concern.
PRIVATE={k:k=="architect" for k in o.REPOSITORIES}
WORKFLOWS={
    "hub":("Hub contracts and cross-repository smoke",),
    "game":("APEX Core 2 - Godot 4.7.2 CI",),
    "hyper":("Hyper FAST - isolated lab","Hyper Game FAST"),
    "engineering":("Engineering Lab QA","Experimental Engineering","Physics Research Lab",
                   "Racecraft Research Lab","Racecraft Track Evidence","Stage Closure Gate","Racecraft Platform Replay","Historical Pace Holdout","Native Engine Comparison","Historical Outline Reconstruction"),
    "architect":("Python regression",),
    "foundation":("APEX Foundation CI",),
    "assets":("Asset Factory - contracts",)
}
MAX_JSON=2_000_000
MAX_FILE=64*1024*1024
MAX_TOTAL=256*1024*1024
MAX_FILES=128
HEX=re.compile(r"[a-f0-9]{64}\Z")
PATH=re.compile(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*\Z")
TOKENS={k:"APEX_HEALTH_"+k.upper()+"_TOKEN" for k in o.REPOSITORIES}
RUNTIMES={"blender":r"Blender 5\.3\.[0-9]+(?:[^\r\n]*)",
          "godot":r"4\.7\.2\.[^\r\n]+"}


def status(state,reason="",**fields):
    return {"status":state,"reason":reason,**fields}


def valid_sha(x):
    return isinstance(x,str) and o.SHA.fullmatch(x) is not None


def load(path):
    p=Path(path)
    o.require(p.is_file() and not p.is_symlink(),"Missing report")
    with p.open("rb") as f:
        raw=f.read(MAX_JSON+1)
    o.require(len(raw)<=MAX_JSON,"Report too large")
    return json.loads(raw,object_pairs_hook=o.unique_object,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Nonfinite JSON")))


def repo_api(target,suffix):
    """Fixed endpoints selected by code. Never forward credentials to other hosts."""
    o.require(target in o.REPOSITORIES,"Unknown target")
    headers={"Accept":"application/vnd.github+json","User-Agent":"APEX-Lab-Hub",
             "X-GitHub-Api-Version":"2022-11-28"}
    token=os.environ.get(TOKENS[target])
    if token:
        headers["Authorization"]="Bearer "+token
    opener=urllib.request.build_opener(ci_receipt.NoRedirect())
    req=urllib.request.Request("https://api.github.com/repos/"+o.REPOSITORIES[target]+suffix,headers=headers)
    try:
        with opener.open(req,timeout=10) as f:
            raw=f.read(MAX_JSON+1)
    except urllib.error.HTTPError as exc:
        raise ValueError("GitHub HTTP "+str(exc.code)+"; check scoped access or rate limit") from None
    except (OSError,TimeoutError):
        raise ValueError("GitHub API unavailable") from None
    o.require(len(raw)<=MAX_JSON,"GitHub response exceeds limit")
    return json.loads(raw,object_pairs_hook=o.unique_object)


def observe_ci(target,sha,data):
    rows=data.get("workflow_runs")
    total=data.get("total_count")
    o.require(isinstance(rows,list) and type(total) is int and total>=0 and len(rows)<=100,
              "Invalid CI listing")
    if total!=len(rows):
        return status("INCONCLUSIVE","Incomplete CI listing")
    expected=WORKFLOWS[target]
    selected={}
    for row in rows:
        if row.get("name") not in expected:
            continue
        o.require(row.get("head_sha")==sha and row.get("event")=="push" and row.get("head_branch")=="main"
                  and row.get("repository",{}).get("full_name")==o.REPOSITORIES[target]
                  and ci_receipt.positive(row.get("id")),"CI identity mismatch")
        # REST returns newest runs first. Explicitly choose newest run ID, never an older success.
        name=row["name"]
        if name not in selected or row["id"]>selected[name]["id"]:
            selected[name]={k:row.get(k) for k in ("id","name","status","conclusion","head_sha","run_attempt")}
    records=[selected.get(n,{"name":n,"status":"NOT_RUN","conclusion":None}) for n in expected]
    if any(x["status"]=="completed" and x["conclusion"] in ("failure","timed_out","action_required","startup_failure") for x in records):
        outcome="FAIL"
    elif all(x["status"]=="completed" and x["conclusion"]=="success" for x in records):
        outcome="PASS"
    else:
        outcome="INCONCLUSIVE"
    return status(outcome,"Workflow-level observation only; not FAST/FULL or exact-step acceptance",runs=records)


def remote(target,reader=repo_api):
    result={"repository":o.REPOSITORIES[target],"metadata":status("BLOCKED"),"ci":status("NOT_RUN")}
    try:
        m=reader(target,"")
        o.require(m.get("full_name")==o.REPOSITORIES[target] and m.get("default_branch")=="main",
                  "Unexpected repository identity or default branch")
        b=reader(target,"/branches/main")
        sha=b.get("commit",{}).get("sha")
        o.require(b.get("name")=="main" and valid_sha(sha),"Invalid main revision")
        o.require(type(m.get("private")) is bool,"Invalid visibility")
        result["metadata"]=status("PASS" if m["private"]==PRIVATE[target] else "FAIL",
            "Visibility matches reviewed policy" if m["private"]==PRIVATE[target] else "Unexpected visibility",
            sha=sha,private=m["private"])
    except (OSError,ValueError,KeyError,TypeError,AttributeError) as exc:
        result["metadata"]=status("BLOCKED",str(exc)[:160] if isinstance(exc,ValueError) else "Invalid/unavailable repository response")
        return result
    try:
        result["ci"]=observe_ci(target,sha,reader(target,"/actions/runs?head_sha="+sha+"&branch=main&event=push&per_page=100"))
    except (OSError,ValueError,KeyError,TypeError,AttributeError) as exc:
        result["ci"]=status("BLOCKED",str(exc)[:160] if isinstance(exc,ValueError) else "Invalid/unavailable Actions response")
    return result


def local_repos(bindings):
    result={k:status("NOT_RUN","Checkout not supplied") for k in o.REPOSITORIES}
    seen=set()
    for name,path,sha in bindings:
        o.require(name in result and name not in seen,"Unknown or duplicate checkout")
        seen.add(name)
        try:
            root,head=o.checked_targets([(name,path,sha)])[name]
            result[name]=status("PASS","Clean reviewed checkout and matching origin",sha=head)
        except (ValueError,OSError,subprocess.SubprocessError):
            # Do not echo paths/remotes: a misconfigured origin could contain credentials.
            result[name]=status("BLOCKED","Check exact revision, origin and clean working tree")
    return result


def compatibility(local,remotes):
    result={}
    for k,pin in PINS.items():
        here=local[k].get("sha")
        latest=remotes[k]["metadata"].get("sha")
        if here is not None and here!=pin:
            state,reason="BLOCKED","Local checkout differs from reviewed consumer pin"
        elif latest is not None and latest!=pin:
            state,reason="BLOCKED","Main advanced; review and revalidate before updating consumer pin"
        elif here==pin or latest==pin:
            state,reason="PASS","Observed revision matches pin; compatibility execution not inferred"
        else:
            state,reason="NOT_RUN","No provider revision observed"
        result[k]=status(state,reason,pinned_sha=pin,local_sha=here,main_sha=latest)
    return result


def probe(argv,cwd,work,name):
    home=work/"probe-home"
    home.mkdir(mode=0o700,exist_ok=True)
    log=work/(name+".log")
    code=graphics.execute(argv,cwd,log,15,home)
    o.require(code==0 and log.stat().st_size<=65536,"Version probe failed or oversized")
    return log.read_text(encoding="utf-8").splitlines()


def runtimes(bindings,work):
    result={k:status("NOT_RUN","No trusted binary supplied") for k in RUNTIMES}
    seen=set()
    for name,path,sha in bindings:
        o.require(name in RUNTIMES and name not in seen,"Unknown or duplicate runtime")
        seen.add(name)
        try:
            binary=graphics.executable(path,sha,(work,))
            lines=probe([str(binary),"--version"],work,work,name)
            o.require(lines and graphics.digest(binary)==sha,"Binary changed or empty version")
            first=lines[0][:160]
            # Report only reviewed format, not arbitrary executable output.
            if not re.fullmatch(RUNTIMES[name],first):
                result[name]=status("BLOCKED","Version outside reviewed graphics contract",sha256=sha)
            else:
                result[name]=status("PASS","Version and supplied digest match; publisher authenticity not verified",
                                    version=first,sha256=sha)
        except (ValueError,OSError,TypeError,subprocess.SubprocessError):
            result[name]=status("BLOCKED","Binary absent, digest mismatch or version probe failed")
    result["python"]=status("PASS" if sys.version_info>=(3,10) else "FAIL",
                            "Hub interpreter requirement",version=".".join(map(str,sys.version_info[:3])))
    path=shutil.which("git",path=os.defpath)
    if path:
        try:
            lines=probe([path,"--version"],work,work,"git")
            o.require(lines and re.fullmatch(r"git version [0-9.]+[A-Za-z0-9.()-]*",lines[0]),"Unknown Git version")
            result["git"]=status("PASS","Git responds",version=lines[0])
        except (ValueError,OSError,subprocess.SubprocessError):
            result["git"]=status("BLOCKED","Git probe failed")
    else:
        result["git"]=status("BLOCKED","Git unavailable")
    result["capture_dependencies"]=status("PASS" if all(shutil.which(x,path=os.defpath) for x in ("xvfb-run","xauth")) else "BLOCKED",
        "Xvfb/Xauth presence only; capture not executed")
    return result


def resources(work,gpu=False):
    result={"cpu":status("NOT_RUN"),"memory":status("NOT_RUN"),"disk":status("NOT_RUN"),
            "gpu":status("NOT_RUN","GPU probe not requested")}
    count=os.cpu_count()
    if count:
        result["cpu"]=status("PASS","Logical CPU count; not throughput benchmark",logical=count,
                             affinity=len(os.sched_getaffinity(0)) if hasattr(os,"sched_getaffinity") else None)
    try:
        fields={}
        with Path("/proc/meminfo").open() as stream:
            for line in stream:
                key,_,value=line.partition(":")
                if key in ("MemTotal","MemAvailable","SwapTotal","SwapFree"):
                    fields[key]=int(value.split()[0])*1024
        total,available=fields["MemTotal"],fields["MemAvailable"]
        o.require(total>0 and 0<=available<=total,"Invalid memory values")
        result["memory"]=status("PASS" if available>=1024**3 else "BLOCKED",
            "Host memory snapshot; 1 GiB minimum headroom advisory, not a reservation",
            total_bytes=total,available_bytes=available,swap_total_bytes=fields.get("SwapTotal"),
            swap_free_bytes=fields.get("SwapFree"))
    except (ValueError,OSError,KeyError):
        result["memory"]=status("BLOCKED","Host memory telemetry unavailable")
    usage=shutil.disk_usage(work)
    result["disk"]=status("PASS" if usage.free>=1024**3 else "BLOCKED",
                         "Evidence filesystem; 1 GiB minimum headroom advisory",free_bytes=usage.free,total_bytes=usage.total)
    if gpu:
        path=shutil.which("nvidia-smi",path=os.defpath)
        if not path:
            result["gpu"]=status("BLOCKED","NVIDIA query executable unavailable")
        else:
            try:
                lines=probe([path,"--query-gpu=name,driver_version,memory.total,memory.used,utilization.gpu,temperature.gpu",
                             "--format=csv,noheader,nounits"],work,work,"nvidia")
                devices=[]
                o.require(0<len(lines)<=16,"Invalid GPU count")
                for line in lines:
                    name,driver,total,used,util,temp=[x.strip() for x in line.split(",")]
                    total,used,util,temp=map(int,(total,used,util,temp))
                    o.require(re.fullmatch(r"[A-Za-z0-9 ._()+-]{1,100}",name) and
                              re.fullmatch(r"[0-9.]{1,40}",driver) and 0<=used<=total and
                              total>0 and 0<=util<=100 and -30<=temp<=150,"Invalid GPU telemetry")
                    devices.append({"name":name,"driver":driver,"vram_total_mib":total,
                                    "vram_used_mib":used,"utilization_pct":util,"temperature_c":temp})
                result["gpu"]=status("PASS","NVIDIA telemetry only; no physical rendering benchmark",devices=devices)
            except (ValueError,OSError,subprocess.SubprocessError):
                result["gpu"]=status("BLOCKED","GPU telemetry unavailable or unsupported fields")
    return result


def evidence_rows(report):
    o.require(isinstance(report,dict),"Invalid evidence report")
    if "evidence" in report:
        rows=report["evidence"]
    elif "tasks" in report:
        o.require(isinstance(report["tasks"],list) and len(report["tasks"])<=16,"Invalid task list")
        rows=[]
        for t in report["tasks"]:
            o.require(isinstance(t,dict) and isinstance(t.get("evidence"),list),"Invalid task evidence")
            rows.extend(t["evidence"])
    elif "receipt" in report:
        rows=[report["receipt"]]
    else:
        raise ValueError("No supported evidence manifest")
    o.require(isinstance(rows,list) and 0<len(rows)<=MAX_FILES,"Invalid evidence count")
    names=set();total=0
    for row in rows:
        o.require(isinstance(row,dict) and set(row)=={"path","bytes","sha256"},"Invalid evidence entry")
        p,n,h=row["path"],row["bytes"],row["sha256"]
        o.require(isinstance(p,str) and len(p)<=240 and PATH.fullmatch(p) and
                  all(x not in (".","..",".git") for x in p.split("/")) and p not in names,
                  "Unsafe or duplicate evidence path")
        o.require(type(n) is int and 0<=n<=MAX_FILE and isinstance(h,str) and HEX.fullmatch(h),"Invalid evidence digest or size")
        names.add(p);total+=n
    o.require(total<=MAX_TOTAL,"Evidence budget exceeded")
    return rows


def verify_evidence(root,report_path):
    try:
        root=Path(root)
        o.require(root.is_dir() and not root.is_symlink(),"Invalid evidence root")
        root=root.resolve()
        report=load(report_path)
        rows=evidence_rows(report)
        checked=[]
        for row in rows:
            p=root/row["path"]
            if not p.is_file() or p.resolve()!=p:
                state="BLOCKED"
            elif p.stat().st_size!=row["bytes"]:
                state="FAIL"
            else:
                # Bound actual reads even if a concurrent writer enlarges the file.
                import hashlib
                h=hashlib.sha256()
                with p.open("rb") as f:
                    remaining=row["bytes"]+1
                    count=0
                    while remaining:
                        chunk=f.read(min(65536,remaining))
                        if not chunk:break
                        h.update(chunk);count+=len(chunk);remaining-=len(chunk)
                state="PASS" if count==row["bytes"] and h.hexdigest()==row["sha256"] else "FAIL"
            checked.append({"path":row["path"],"status":state})
        states={x["status"] for x in checked}
        return status("FAIL" if "FAIL" in states else "BLOCKED" if "BLOCKED" in states else "PASS",
                      "File size and SHA256 only; not authenticity or domain acceptance",files=checked,count=len(checked))
    except (ValueError,OSError,KeyError,TypeError):
        return status("BLOCKED","Invalid evidence manifest or unreadable files")


def security():
    return {"registry":status("PASS","Registry ownership/private modes validated by Registry"),
            "credential_presence":{k:bool(os.environ.get(v)) for k,v in TOKENS.items()},
            "token_permissions":status("NOT_RUN","Actual installation scopes and secret rotation not audited"),
            "external_agents":status("NOT_RUN","No external agent credentials configured by this module"),
            "write_authorized":False}


def recovery(store):
    # Snapshot state only. A RUNNING row may still be active; do not call recover.
    tables={"sessions":("phase",("RUNNING",)),"experiments":("state",("RUNNING",)),
            "development":("state",("AWAITING_CI","DIAGNOSE")),"graphics_runs":("state",("RUNNING",)),
            "research_cases":("state",("RUNNING","PREPARING")),"gateway_cases":("state",("RUNNING",)),"missions":("state",("RUNNING","AWAITING_RESEARCH_APPROVAL"))}
    result=[]
    with store.connect() as db:
        present={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table,(field,states) in tables.items():
            if table not in present:continue
            # Table/field names fixed above, values bound.
            for row in db.execute("SELECT id,"+field+" FROM "+table+" WHERE "+field+" IN ("+
                                  ",".join("?" for _ in states)+") LIMIT 100",states):
                result.append({"registry":table,"id":row[0],"state":row[1],
                    "recommendation":"Inspect owner/process and evidence; use domain recovery only after confirming interruption"})
    return {"status":"INCONCLUSIVE" if result else "PASS","reason":"Snapshot of tracked states; not process liveness",
            "cases":result,"automatic_recovery":False}


def summarize(report):
    checks=[]
    checks.extend(report["local_repositories"].values())
    for row in report["remote_repositories"].values():checks.extend((row["metadata"],row["ci"]))
    checks.extend(report["compatibility"].values())
    checks.extend(report["runtimes"].values())
    checks.extend(report["resources"].values())
    checks.extend(report["evidence"])
    checks.extend((report["security"]["registry"],report["security"]["token_permissions"],report["recovery"]))
    counts={k:sum(x["status"]==k for x in checks) for k in ("PASS","FAIL","BLOCKED","NOT_RUN","INCONCLUSIVE")}
    state=next((k for k in ("FAIL","BLOCKED","INCONCLUSIVE","NOT_RUN") if counts[k]),"PASS")
    return {"status":state,"counts":counts,"scope":"Observation, not blanket release approval"}


class Health:
    def __init__(self,root):
        self.store=Registry(root)
        with self.store.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS health_snapshots (id TEXT PRIMARY KEY,created TEXT NOT NULL,snapshot TEXT NOT NULL)")

    def scan(self,bindings=(),remote_targets=(),runtime_bindings=(),evidence=(),gpu=False):
        o.require(len(set(remote_targets))==len(remote_targets) and set(remote_targets)<=o.REPOSITORIES.keys(),"Invalid remote targets")
        o.require(len(evidence)<=8,"Too many evidence bundles")
        for _,path,_ in bindings:
            p=Path(path).resolve()
            o.require(not self.store.root.is_relative_to(p) and not p.is_relative_to(self.store.root),
                      "Registry outside checkout required")
        sid=uuid.uuid4().hex
        work=self.store.root/("health-"+sid)
        work.mkdir(mode=0o700)
        local=local_repos(bindings)
        remotes={k:remote(k) if k in remote_targets else
                 {"repository":v,"metadata":status("NOT_RUN","Remote query not authorized"),"ci":status("NOT_RUN")}
                 for k,v in o.REPOSITORIES.items()}
        report={"schema":1,"id":sid,"created":now(),"hub_version":__version__,
                "local_repositories":local,"remote_repositories":remotes,"compatibility":compatibility(local,remotes),
                "runtimes":runtimes(runtime_bindings,work),"resources":resources(work,gpu),
                "security":security(),"evidence":[verify_evidence(root,p) for root,p in evidence] or [status("NOT_RUN","No evidence supplied")],
                "recovery":recovery(self.store),"merge_authorized":False}
        report["summary"]=summarize(report)
        with self.store.connect() as db:
            db.execute("INSERT INTO health_snapshots VALUES (?,?,?)",(sid,report["created"],encode(report)))
        return report

    def get(self,sid):
        with self.store.connect() as db:
            row=db.execute("SELECT snapshot FROM health_snapshots WHERE id=?",(ident(sid),)).fetchone()
            o.require(row is not None,"Unknown health snapshot")
            return json.loads(row[0])

    def list(self):
        with self.store.connect() as db:
            return [{"id":r["id"],"created":r["created"],"summary":json.loads(r["snapshot"])["summary"]}
                    for r in db.execute("SELECT * FROM health_snapshots ORDER BY created DESC LIMIT 100")]


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--registry",type=Path,required=True)
    sub=p.add_subparsers(dest="mode",required=True)
    q=sub.add_parser("scan")
    q.add_argument("--checkout",nargs=3,action="append",default=[])
    q.add_argument("--remote",choices=o.REPOSITORIES,action="append",default=[])
    q.add_argument("--runtime",nargs=3,action="append",default=[])
    q.add_argument("--evidence",nargs=2,action="append",default=[])
    q.add_argument("--gpu",action="store_true")
    q.add_argument("--trusted-local-inputs",action="store_true",required=True)
    sub.add_parser("list")
    sub.add_parser("show").add_argument("id")
    a=p.parse_args()
    try:
        h=Health(a.registry)
        if a.mode=="scan":
            out=h.scan(a.checkout,a.remote,a.runtime,a.evidence,a.gpu)
        elif a.mode=="list":out=h.list()
        else:out=h.get(a.id)
        print(json.dumps(out,indent=2,ensure_ascii=True))
        # Missing optional checks is normal. Fail on observed FAIL/BLOCKED.
        return 1 if a.mode=="scan" and out["summary"]["status"] in ("FAIL","BLOCKED") else 0
    except (ValueError,OSError,TypeError,KeyError,sqlite3.Error,subprocess.SubprocessError):
        print("APEX_HEALTH_BLOCKED: invalid inputs or unavailable local infrastructure",file=sys.stderr)
        return 2


if __name__=="__main__":raise SystemExit(main())
