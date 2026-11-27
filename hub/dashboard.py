"""Private offline dashboard from existing SQLite registries. No actions, no network."""
import argparse
from collections import Counter
from contextlib import closing
from html import escape
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import sys

from . import __version__, orchestrator as o
from .registry import now, IDENT

TABLES={"sessions":("phase","report"),"development":("state","snapshot"),
        "experiments":("state","snapshot"),"graphics_runs":("state","snapshot"),
        "research_cases":("state","snapshot"),"gateway_cases":("state","snapshot"),
        "health_snapshots":(None,"snapshot"),"missions":("state","snapshot"),"department_plans":("state","snapshot"),"knowledge_entries":("state","snapshot")}
LABELS={"sessions":"Orchestration","development":"Development","experiments":"Experiments",
        "graphics_runs":"Graphics","research_cases":"Research","gateway_cases":"Departments",
        "health_snapshots":"Infrastructure","missions":"Engineering Missions","department_plans":"Department Planning","knowledge_entries":"Engineering Knowledge"}
STATES={"PASS","FAIL","BLOCKED","NOT_RUN","INCONCLUSIVE","RUNNING","FINISHED","INTERRUPTED",
        "QUEUED","CANCELLED","REQUESTED","AUDITED","PLANNED","AWAITING_CI","REVIEW",
        "DIAGNOSE","REPAIR_READY","APPROVED","PREPARING","PROPOSED","AWAITING_RESEARCH_APPROVAL","REVIEWED","DELEGATED","OBSERVED","REJECTED","VERIFIED","DECLARED","UNKNOWN"}
MAX_RECORD=2_000_000
MAX_REGISTRIES=8
RECENT=20
SHA=re.compile(r"[0-9a-f]{40}\Z")
HASH=re.compile(r"[0-9a-f]{64}\Z")
LABEL=re.compile(r"[A-Za-z0-9_-]{1,32}\Z")
TIME=re.compile(r"[0-9TZ:+.-]{10,40}\Z")
SAFE_NAMES=re.compile(r"[A-Za-z0-9_. -]{1,100}\Z")


def state(v):
    return v if isinstance(v,str) and v in STATES else "UNKNOWN"


def digest(v,pattern=SHA):
    return v if isinstance(v,str) and pattern.fullmatch(v) else None


def integer(v):
    return v if type(v) is int and 0<=v<=2**63-1 else None


def stamp(v):
    return v if isinstance(v,str) and TIME.fullmatch(v) else None


def named(v):
    return v if isinstance(v,str) and SAFE_NAMES.fullmatch(v) else None


def checked_db(root):
    p=Path(root).expanduser()
    o.require(p.is_dir() and not p.is_symlink(),"Missing/unsafe registry directory")
    p=p.resolve()
    st=p.stat()
    o.require(st.st_uid==os.getuid() and stat.S_IMODE(st.st_mode)&0o077==0,"Registry must be private")
    db=p/"registry.sqlite3"
    st=db.lstat()
    o.require(stat.S_ISREG(st.st_mode) and st.st_uid==os.getuid() and
              stat.S_IMODE(st.st_mode)&0o077==0,"Registry database must be private regular file")
    return db


def unpack(raw):
    o.require(isinstance(raw,str) and len(raw)<=MAX_RECORD,"Invalid stored report length")
    v=json.loads(raw)
    o.require(isinstance(v,dict),"Invalid stored report")
    return v


def health_view(v):
    repositories={}
    rem=v.get("remote_repositories",{})
    loc=v.get("local_repositories",{})
    pins=v.get("compatibility",{})
    for k in o.REPOSITORIES:
        r=rem.get(k,{})
        m=r.get("metadata",{})
        c=r.get("ci",{})
        l=loc.get(k,{})
        p=pins.get(k,{})
        repositories[k]={"local":state(l.get("status")),"local_sha":digest(l.get("sha")),
            "remote":state(m.get("status")),"main_sha":digest(m.get("sha")),
            "ci":state(c.get("status")),"pin":state(p.get("status")) if k in pins else "NOT_RUN",
            "pinned_sha":digest(p.get("pinned_sha"))}
    runtime={}
    for k in ("python","git","blender","godot","capture_dependencies"):
        r=v.get("runtimes",{}).get(k,{})
        runtime[k]={"status":state(r.get("status")),"version":named(r.get("version"))}
    resources={}
    for k,fields in {"cpu":("logical","affinity"),"memory":("total_bytes","available_bytes","swap_total_bytes","swap_free_bytes"),
                      "disk":("free_bytes","total_bytes")}.items():
        r=v.get("resources",{}).get(k,{})
        resources[k]={"status":state(r.get("status")),**{f:integer(r.get(f)) for f in fields}}
    gpu=v.get("resources",{}).get("gpu",{})
    resources["gpu"]={"status":state(gpu.get("status")),"devices":[
        {"name":named(x.get("name")),"driver":named(x.get("driver")),
         **{k:integer(x.get(k)) for k in ("vram_total_mib","vram_used_mib","utilization_pct","temperature_c")}}
        for x in gpu.get("devices",[])[:16] if isinstance(x,dict)]}
    return {"created":stamp(v.get("created")),"repositories":repositories,"runtime":runtime,"resources":resources,
            "evidence":[{"status":state(x.get("status")),"count":integer(x.get("count"))}
                        for x in v.get("evidence",[])[:8] if isinstance(x,dict)],
            "token_permissions":state(v.get("security",{}).get("token_permissions",{}).get("status")),
            "recovery":state(v.get("recovery",{}).get("status"))}


def project(table,row,v):
    sid=row["id"]
    o.require(isinstance(sid,str) and IDENT.fullmatch(sid),"Invalid stored ID")
    phase=state(row["phase"] if table=="sessions" else row["state"] if table!="health_snapshots" else v.get("summary",{}).get("status"))
    if table=="sessions":
        o.require(v.get("session_id")==sid,"Session identity mismatch")
        extra={"outcome":state(row["outcome"]),"task_count":len(v.get("tasks",[])),
               "task_states":dict(Counter(state(t.get("status")) for t in v.get("tasks",[]) if isinstance(t,dict)))}
    else:
        o.require(v.get("id")==sid,"Snapshot identity mismatch")
        if table!="health_snapshots":
            o.require(v.get("state")==row["state"],"Snapshot state mismatch")
        extra={}
        if table=="development":
            extra={"base":digest(v.get("base")),"repairs":integer(v.get("repairs")),
                   "candidates":len(v.get("candidates",[]))}
        elif table=="experiments":
            extra={"revision":digest(v.get("hub_sha")),"seed":integer(v.get("spec",{}).get("seed")),
                   "priority":integer(v.get("spec",{}).get("priority")),"attempts":integer(v.get("attempts")),
                   "result_sha256":digest((v.get("telemetry") or {}).get("result",{}).get("result_sha256"),HASH)}
        elif table=="graphics_runs":
            report=v.get("report",{})
            asset=report.get("asset",{})
            extra={"factory_sha":digest(v.get("factory_sha")),"native":state(v.get("native")),
                   "capture":state(report.get("stages",{}).get("capture")),
                   "triangles":integer(asset.get("mesh_triangles")),"asset_sha256":digest(asset.get("sha256"),HASH)}
        elif table=="research_cases":
            extra={"engineering_sha":digest(v.get("revisions",{}).get("engineering")),
                   "planned":len(v.get("experiments",[])),"completed":len(v.get("completed",[])),
                   "comparison_sha256":digest(v.get("comparison_sha256"),HASH)}
        elif table=="gateway_cases":
            extra={"attempts":integer(v.get("attempts")),"messages":len(v.get("messages",[])),
                   "handoffs":len(v.get("handoffs",[])),"receipt_sha256":digest(v.get("receipt",{}).get("sha256"),HASH)}
        elif table=="knowledge_entries":
            extra={"digest":digest(v.get("digest"),HASH),"evidence_kind":named(v.get("evidence",{}).get("kind")),
                   "annotation_kind":named(v.get("annotation",{}).get("kind")),
                   "source_id":v.get("evidence",{}).get("source_id") if isinstance(v.get("evidence",{}).get("source_id"),str) and IDENT.fullmatch(v["evidence"]["source_id"]) else None}
        elif table=="department_plans":
            extra={"tasks":len(v.get("plan",{}).get("assignments",{})),
                   "reviews":len(v.get("reviews",{})),"gateway_id":v.get("gateway_id") if isinstance(v.get("gateway_id"),str) and IDENT.fullmatch(v["gateway_id"]) else None,
                   "observed_outcome":state(v.get("observation",{}).get("outcome"))}
        elif table=="missions":
            extra={"plan_sha256":digest(v.get("plan_sha256"),HASH),
                   "completed_stages":len(v.get("stages",[])),
                   "planned_stages":len(v.get("plan",{}).get("stages",[]))}
        elif table=="health_snapshots":
            extra={"observations":health_view(v)}
    return {"id":sid,"state":phase,"details":extra}


def collect_one(label,root):
    o.require(isinstance(label,str) and LABEL.fullmatch(label),"Invalid registry label")
    dbpath=checked_db(root)
    with closing(sqlite3.connect(dbpath.as_uri()+"?mode=ro",uri=True,timeout=5)) as db:
        db.row_factory=sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        db.execute("PRAGMA trusted_schema=OFF")
        db.execute("BEGIN")
        o.require(db.execute("PRAGMA user_version").fetchone()[0]==1,"Unsupported registry version")
        tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        sections={}
        for table,(field,payload) in TABLES.items():
            if table not in tables:
                sections[table]={"status":"NOT_RUN","total":None,"counts":{},"recent":[]}
                continue
            total=db.execute("SELECT COUNT(*) FROM "+table).fetchone()[0]
            counts=Counter()
            if field:
                for raw,n in db.execute("SELECT "+field+",COUNT(*) FROM "+table+" GROUP BY "+field):
                    counts[state(raw)]+=n
            columns="id,"+payload
            if field:columns+=","+field
            if table=="sessions":columns+=",outcome"
            rows=[]
            order="created DESC,id DESC" if table=="health_snapshots" else "rowid DESC"
            for r in db.execute("SELECT "+columns+" FROM "+table+" ORDER BY "+order+" LIMIT ?",(RECENT,)):
                v=unpack(r[payload])
                rows.append(project(table,r,v))
            sections[table]={"status":"RECORDED" if total else "EMPTY","total":total,
                 "counts":dict(counts),"recent":rows,"recent_limit":RECENT,
                 "counts_scope":"all rows" if field else "not aggregated; individual observations"}
        db.rollback()
    return {"label":label,"sections":sections}


def collect(bindings):
    o.require(1<=len(bindings)<=MAX_REGISTRIES,"Supply 1..8 registries")
    labels=[b[0] for b in bindings]
    o.require(len(set(labels))==len(labels),"Duplicate registry labels")
    roots=[checked_db(b[1]) for b in bindings]
    o.require(len(set(roots))==len(roots),"Duplicate registry paths")
    return {"schema":1,"hub_version":__version__,"generated":now(),"source":"existing local SQLite snapshots",
            "live":False,"merge_authorized":False,"registries":[collect_one(*b) for b in bindings]}


def esc(v):
    return escape(str(v) if v is not None else "—",quote=True)


def badge(v):
    return '<span class="badge '+esc(state(v))+'">'+esc(state(v))+'</span>'


def render(report):
    css="""*{box-sizing:border-box}body{margin:0;background:#10151e;color:#e9edf4;font:14px system-ui,sans-serif}
main{max-width:1240px;margin:auto;padding:28px}h1{margin:0;color:#fff;font-size:28px}h2{margin:26px 0 12px}h3{font-size:16px;margin:0 0 12px}
p,small{color:#a6b2c5}header{border-bottom:2px solid #fa9144;padding-bottom:16px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:12px}
article{border:1px solid #334052;background:#19212d;border-radius:8px;padding:14px;min-width:0}.num{font-size:29px;font-weight:700}
.badge{display:inline-block;font-size:11px;padding:3px 6px;border-radius:5px;background:#344154;color:#e9edf4}.PASS{color:#8be8ac}.FAIL{color:#ff9696}.BLOCKED,.INCONCLUSIVE{color:#ffce79}
table{width:100%;border-collapse:collapse;font-size:12px}td,th{text-align:left;padding:8px;border-bottom:1px solid #334052;overflow-wrap:anywhere}th{color:#a6b2c5}details{margin-top:10px}summary{cursor:pointer}code{overflow-wrap:anywhere}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px}"""
    parts=['<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">',
           '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; base-uri \'none\'; form-action \'none\'">',
           '<title>APEX Command Center</title><style>'+css+'</style></head><body><main>',
           '<header><h1>APEX COMMAND CENTER</h1><p>Hub '+esc(report["hub_version"])+' · Private offline snapshot · '+esc(report["generated"])+'</p>',
           '<p>Recorded observations only. Not live, not blanket approval. Missing tables and unexecuted checks remain visible. No control actions.</p></header>']
    for registry in report["registries"]:
        parts.append('<h2>Registry · '+esc(registry["label"])+'</h2><div class="grid">')
        for key,s in registry["sections"].items():
            parts.append('<article><h3>'+esc(LABELS[key])+'</h3><div class="num">'+esc(s["total"])+'</div><small>'+esc(s["status"])+'</small><p>'+
                         ' · '.join(esc(k)+': '+esc(v) for k,v in sorted(s["counts"].items()))+'</p></article>')
        parts.append('</div>')
        for key,s in registry["sections"].items():
            if not s["recent"]:continue
            parts.append('<h3 style="margin-top:20px">'+esc(LABELS[key])+' · recent records</h3><article>')
            for row in s["recent"]:
                parts.append('<details><summary>'+badge(row["state"])+' <code>'+esc(row["id"])+'</code></summary>')
                details=row["details"]
                if key=="health_snapshots":
                    obs=details["observations"]
                    parts.append('<p>Observed '+esc(obs["created"])+'</p><table><thead><tr><th>Repository</th><th>Local</th><th>Main</th><th>CI</th><th>Pin</th></tr></thead><tbody>')
                    for repo,x in obs["repositories"].items():
                        parts.append('<tr><td>'+esc(repo)+'</td><td>'+badge(x["local"])+'<br>'+esc(x["local_sha"])+'</td><td>'+badge(x["remote"])+'<br>'+esc(x["main_sha"])+'</td><td>'+badge(x["ci"])+'</td><td>'+badge(x["pin"])+'</td></tr>')
                    parts.append('</tbody></table>')
                parts.append('<pre>'+esc(json.dumps(details,indent=2,ensure_ascii=True))+'</pre></details>')
            parts.append('</article>')
    parts.append('</main></body></html>')
    return "\n".join(parts)


def export(bindings,out):
    out=Path(out).expanduser()
    o.require(not out.exists() and not out.is_symlink(),"Use a new output directory")
    for _,root in bindings:
        p=Path(root).resolve()
        q=out.resolve()
        o.require(not q.is_relative_to(p) and not p.is_relative_to(q),"Output must be outside registries")
    report=collect(bindings)
    out.mkdir(mode=0o700,parents=False)
    for name,body in (("dashboard.json",json.dumps(report,indent=2,ensure_ascii=True)+"\n"),
                      ("index.html",render(report))):
        fd=os.open(out/name,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,"w",encoding="utf-8") as f:f.write(body)
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--registry",action="append",nargs=2,required=True,metavar=("LABEL","PATH"))
    p.add_argument("--output",required=True,type=Path)
    a=p.parse_args()
    try:
        report=export(a.registry,a.output)
        print(json.dumps({"status":"PASS","registries":len(report["registries"]),"live":False,
                          "output_files":["dashboard.json","index.html"]}))
        return 0
    except (ValueError,OSError,TypeError,KeyError,AttributeError,sqlite3.Error):
        print("APEX_DASHBOARD_BLOCKED: invalid registry or output",file=sys.stderr)
        return 2

if __name__=="__main__":raise SystemExit(main())
