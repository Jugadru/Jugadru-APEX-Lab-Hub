"""Offline read-only Mission Control Center over existing dashboard projections."""
import argparse
from collections import Counter
import json
import os
from pathlib import Path
import sqlite3
import sys

from . import dashboard as d, gateway as g, orchestrator as o

# Classifications are advisory record states, not authorization or live liveness.
BLOCKERS={"FAIL","BLOCKED","INTERRUPTED","INCONCLUSIVE","CONFLICT","DIAGNOSE","REJECTED","UNKNOWN"}
WAITING={"PROPOSED","REQUESTED","PLANNED","REVIEW","AWAITING_CI","AWAITING_RESEARCH_APPROVAL","REPAIR_READY","REVIEWED","AGREED"}
ACTIVE={"RUNNING","PREPARING","APPROVED","QUEUED","DELEGATED","HANDED_OFF","RELEASED"}
GROUPS={
    "Missions":("missions","sessions"),
    "Departments":("department_plans","gateway_cases","collaboration_cases"),
    "Research":("research_cases","experiments"),
    "Development":("development","code_candidates"),
    "Graphics":("graphics_runs","geometry_audits"),
    "Performance":("benchmarks",),
    "Infrastructure":("health_snapshots",),
    "Knowledge":("knowledge_entries","advisory_cases")
}
MAX_QUEUE=60
def category(state):
    return "attention" if state in BLOCKERS else "review" if state in WAITING else "active" if state in ACTIVE else "recorded"
def action(table,state):
    if state=="AWAITING_CI":return "Check exact candidate CI receipt"
    if state=="AWAITING_RESEARCH_APPROVAL":return "Review frozen research plan before approval"
    if state=="CONFLICT":return "Review disagreement and remaining rounds"
    if state=="INTERRUPTED":return "Inspect interruption; do not replay automatically"
    if state=="UNKNOWN":return "Inspect unsupported or inconsistent recorded state"
    if state in BLOCKERS:return "Inspect recorded evidence and disposition"
    if state in WAITING:return "Review current plan and required authorization"
    if state in ACTIVE:return "Inspect current lifecycle; no automatic retry"
    return "No lifecycle action inferred"
def observed_outcome(table,state,detail):
    # Dashboard uses UNKNOWN for absent outcomes, and running sessions carry
    # a pessimistic BLOCKED default until they finish. Neither is a result.
    if table=="sessions" and state=="FINISHED":
        return detail.get("outcome")
    if table=="department_plans" and state=="OBSERVED":
        return detail.get("observed_outcome")
    if table=="collaboration_cases" and state in ("OBSERVED","INCONCLUSIVE"):
        return detail.get("outcome")
    return None

def summarize(report):
    o.require(report["live"] is False and report["merge_authorized"] is False,"Offline projection required")
    summary={name:{"tables":list(tables),"records":0,"coverage":{},"counts":{},"unclassified":0} for name,tables in GROUPS.items()}
    queues=[];totals=Counter();sample_counts=Counter();health=[]
    for reg in report["registries"]:
        for name,tables in GROUPS.items():
            group=summary[name]
            for table in tables:
                section=reg["sections"][table]
                group["coverage"][section["status"]]=group["coverage"].get(section["status"],0)+1
                if section["total"] is None:continue
                group["records"]+=section["total"]
                # Count all stored rows, but health snapshots have no aggregate state counts.
                counts=section["counts"]
                for st,n in counts.items():
                    group["counts"][st]=group["counts"].get(st,0)+n
                    totals[category(st)]+=n
                group["unclassified"]+=section["total"]-sum(counts.values())
                for row in section["recent"]:
                    st=row["state"]
                    # Lifecycle state and outcome differ. Preserve both, no double count.
                    detail=row["details"]
                    outcome=observed_outcome(table,st,detail)
                    effective=outcome if outcome in BLOCKERS else st
                    kind=category(effective)
                    if table=="health_snapshots":
                        if row is section["recent"][0]:
                            health.append({"registry":reg["label"],"id":row["id"],"state":st,"observations":detail["observations"]})
                    if kind!="recorded":
                        sample_counts[kind]+=1
                        queues.append({"registry":reg["label"],"table":table,"id":row["id"],"state":st,
                                       "outcome":outcome if outcome in d.STATES else None,
                                       "priority":kind,"suggested_review":action(table,effective)})
    order={"attention":0,"review":1,"active":2}
    queues.sort(key=lambda x:(order[x["priority"]],x["registry"],x["table"]))
    return {"schema":1,"hub_version":report["hub_version"],"generated":report["generated"],
            "source_sha256":g.sha_json(report),"registries":len(report["registries"]),
            "groups":summary,"lifecycle_totals":dict(totals),"recent_queue":queues[:MAX_QUEUE],
            "queue_matching_recent":sum(sample_counts.values()),"queue_omitted_recent":max(0,len(queues)-MAX_QUEUE),
            "recent_queue_scope":"At most 20 latest records per table per registry, capped at 60 items; not a complete backlog",
            "counts_scope":"All stored lifecycle rows; not unique missions, outcomes or current workload; health counts unclassified",
            "health_latest_per_registry":health,"live":False,"execution_authorized":False,"merge_authorized":False}
def render(s):
    esc=d.esc
    parts=['<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">',
           '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; base-uri \'none\'; form-action \'none\'">',
           '<title>APEX Mission Control</title><style>*{box-sizing:border-box}body{background:#10151e;color:#e9edf4;font:14px system-ui;margin:0}main{max-width:1200px;margin:auto;padding:24px}h1{color:#ffad70}p,small{color:#a6b2c5}a{color:#b7d9ff}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px}article{background:#19212d;border:1px solid #334052;padding:16px;border-radius:8px}h3{margin-top:0}.num{font-size:28px;font-weight:700}table{border-collapse:collapse;width:100%;font-size:12px}td,th{text-align:left;padding:8px;border-bottom:1px solid #334052;overflow-wrap:anywhere}.attention{color:#ffad91}.review{color:#ffda89}.active{color:#a7d7ff}code{overflow-wrap:anywhere}</style></head><body><main>',
           '<h1>APEX MISSION CONTROL</h1><p>Hub '+esc(s["hub_version"])+' · '+esc(s["generated"])+' · offline snapshot</p>',
           '<p>No execution, publication or approval actions. Historical records only. <a href="index.html">Detailed registry dashboard</a></p>',
           '<h2>Module coverage</h2><div class="grid">']
    for name,v in s["groups"].items():
        parts.append('<article><h3>'+esc(name)+'</h3><div class="num">'+esc(v["records"])+'</div><small>Stored records</small><p>'+
                     ' · '.join(esc(k)+': '+esc(n) for k,n in sorted(v["coverage"].items()))+'</p><p>'+
                     ' · '.join(esc(k)+': '+esc(n) for k,n in sorted(v["counts"].items()))+'</p></article>')
    parts.append('</div><p>'+esc(s["counts_scope"])+'</p><h2>Review queue</h2><p>'+esc(s["recent_queue_scope"])+'</p>')
    parts.append('<p>Matching recent records: '+esc(s["queue_matching_recent"])+' · omitted: '+esc(s["queue_omitted_recent"])+'</p>')
    parts.append('<table><thead><tr><th>Priority</th><th>Registry / module</th><th>Record</th><th>State / outcome</th><th>Suggested review</th></tr></thead><tbody>')
    for item in s["recent_queue"]:
        parts.append('<tr><td class="'+esc(item["priority"])+'">'+esc(item["priority"])+'</td><td>'+esc(item["registry"])+' / '+esc(d.LABELS[item["table"]])+
                     '</td><td><code>'+esc(item["id"])+'</code></td><td>'+esc(item["state"])+' / '+esc(item["outcome"])+'</td><td>'+esc(item["suggested_review"])+'</td></tr>')
    parts.append('</tbody></table><h2>Latest infrastructure observations per registry</h2>')
    for h in s["health_latest_per_registry"]:
        parts.append('<article><h3>'+esc(h["registry"])+' · '+esc(h["state"])+'</h3><p>Observed '+esc(h["observations"]["created"])+'</p><table><tr><th>Repository</th><th>Local</th><th>Remote</th><th>CI</th><th>Pin</th></tr>')
        for repo,v in h["observations"]["repositories"].items():
            parts.append('<tr><td>'+esc(repo)+'</td>'+''.join('<td>'+esc(v[k])+'</td>' for k in ("local","remote","ci","pin"))+'</tr>')
        parts.append('</table></article>')
    parts.append('<p>Source snapshot SHA-256: <code>'+esc(s["source_sha256"])+'</code></p></main></body></html>')
    return "\n".join(parts)
def export(bindings,out):
    out=Path(out).expanduser()
    source=d.export(bindings,out)
    summary=summarize(source)
    for name,body in (("mission-control.json",json.dumps(summary,indent=2,ensure_ascii=True)+"\n"),
                      ("mission-control.html",render(summary))):
        fd=os.open(out/name,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,"w",encoding="utf-8") as f:f.write(body)
    return summary
def verify(out):
    # Consistency of saved projection only. No fresh source integrity claim.
    out=Path(out).expanduser()
    o.require(out.is_dir() and not out.is_symlink(),"Invalid output directory")
    def read(name):
        p=out/name
        o.require(p.is_file() and not p.is_symlink() and p.stat().st_size<=16_000_000,"Invalid output file")
        return p.read_text(encoding="utf-8")
    source=json.loads(read("dashboard.json"),object_pairs_hook=o.unique_object)
    summary=summarize(source)
    o.require(json.loads(read("mission-control.json"),object_pairs_hook=o.unique_object)==summary
              and read("mission-control.html")==render(summary) and read("index.html")==d.render(source),
              "Snapshot output changed")
    return {"status":"PASS","scope":"Saved projection consistency; no fresh domain evidence audit","source_sha256":summary["source_sha256"]}
def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest="mode",required=True)
    q=sub.add_parser("export");q.add_argument("--registry",action="append",nargs=2,required=True,metavar=("LABEL","PATH"));q.add_argument("--output",required=True,type=Path)
    q=sub.add_parser("verify");q.add_argument("output",type=Path)
    a=p.parse_args()
    try:
        result=export(a.registry,a.output) if a.mode=="export" else verify(a.output)
        print(json.dumps({"status":"PASS","scope":"offline snapshot","result":result},indent=2));return 0
    except (ValueError,OSError,TypeError,KeyError,AttributeError,sqlite3.Error):
        print("APEX_MISSION_CONTROL_BLOCKED: invalid registry or snapshot",file=sys.stderr);return 2
if __name__=="__main__":raise SystemExit(main())
