"""Research orchestration; exact upstreams, frozen hypotheses, no runtime promotion."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import uuid

from .graphics import execute, file_evidence
from .orchestrator import checked_targets, digest, git, require, unique_object, REPOSITORIES
from .registry import Registry, encode, ident, now

FOUNDATION_SHA = "1c226891f24796d0a14535693d32f626a5b476f5"
ENGINEERING_SHA = "a006f4f75701a82ac7f2325fb293f04ab1c42b06"
PINS = {"foundation": FOUNDATION_SHA, "engineering": ENGINEERING_SHA}
BUILTINS = frozenset(("ordered_commands", "spatial_neighbors", "longitudinal_math",
    "mass_grip_sensitivity", "pit_cost_comparison", "physics_foundation", "physics_tyre",
    "physics_aero", "physics_powertrain", "physics_brakes", "physics_load", "physics_energy",
    "physics_vehicle", "physics_stability"))
SOURCE = re.compile(r"(REF|IDEA)-[0-9]{3,}\Z")
HEX = re.compile(r"[0-9a-f]{64}\Z")
LIMIT = 2 * 1024 * 1024
SCRIPTS = {"knowledge":"tools/apex_knowledge.py","architecture":"tools/apex_architecture.py",
           "data":"tools/apex_data_center.py","content":"tools/apex_content_registry.py"}


def canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()


def hash_json(obj):
    return hashlib.sha256(canonical(obj)).hexdigest()


def load(path):
    require(path.is_file() and path.resolve() == path, "Unsafe research evidence")
    with path.open("rb") as f:
        raw = f.read(LIMIT + 1)
    require(len(raw) <= LIMIT, "Research evidence too large")
    return json.loads(raw, object_pairs_hook=unique_object,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Nonfinite JSON")))


def write(path, obj):
    data = canonical(obj) + b"\n"
    require(len(data) <= LIMIT, "Research record too large")
    with path.open("xb") as f:
        f.write(data)


def bind(target, checkout):
    return checked_targets([(target, checkout, PINS[target])])[target][0]


def unchanged(target, root):
    require(bind(target, str(root)) == root, "Checkout changed")


def query(root, work, name, args, allowed=(0,), timeout=30):
    home = work / "home"
    home.mkdir(mode=0o700, exist_ok=True)
    log = work / (name + ".json")
    code = execute([sys.executable, "-E", "-B", "-s", *args], root, log, timeout, home)
    require(code in allowed, "Upstream command failed: " + name)
    result = load(log)
    require(isinstance(result, dict), "Expected upstream JSON object")
    return result


def fquery(root, work, name, tool, *args):
    return query(root, work, name, [SCRIPTS[tool], *args])


def equery(root, work, name, *args, allowed=(0,), timeout=30):
    return query(root, work, name, ["-m", "engineering_lab", *args], allowed, timeout)


def source_ids(rows):
    require(isinstance(rows, list) and 0 < len(rows) <= 40 and
            all(isinstance(x, str) and SOURCE.fullmatch(x) for x in rows) and len(set(rows)) == len(rows),
            "Invalid research sources")
    return rows


def foundation_packet(root, work, sources, dataset=("vehicle-fixture","1.1.0"),
                      content=("vehicle.demo","1.0.0")):
    """Read-only source audit; exact identities, no authority from metadata."""
    source_ids(sources)
    unchanged("foundation", root)
    work.mkdir(mode=0o700)
    k = fquery(root, work, "knowledge", "knowledge", "validate")
    a = fquery(root, work, "architecture", "architecture", "validate")
    d = fquery(root, work, "data", "data", "validate")
    c = fquery(root, work, "content", "content", "validate")
    require(all(x.get("status") == "PASS" for x in (k,a,d,c)) and
            k.get("references") == 198 and k.get("ideas") == 120 and
            k.get("implementation_authorized") is False and a.get("runtime_verified") is False and
            d.get("production_approved") == 0 and c.get("production_approved") == 0,
            "Foundation structural acceptance mismatch")
    records, plans, refs, modules = {}, {}, set(), {}
    for key in sources:
        row = fquery(root, work, "source-"+key, "knowledge", "show", key)
        require(row.get("record", {}).get("id") == key and row.get("implementation_authorized") is False,
                "Source identity/authorization mismatch")
        records[key] = row
        if key.startswith("IDEA-"):
            p = fquery(root, work, "plan-"+key, "knowledge", "plan", key)
            require(p.get("id") == key and p.get("status") == "BLOCKED" and
                    p.get("implementation_authorized") is False, "Idea gate mismatch")
            plans[key] = p
            refs.update(source_ids(p["reference_ids"]))
            module = p["owner_module"]
            require(isinstance(module, str) and re.fullmatch(r"[a-z0-9_-]+", module), "Invalid owner module")
            if module not in modules:
                m = fquery(root, work, "owner-"+module, "architecture", "show", module)
                require(m.get("module", {}).get("id") == module and m.get("runtime_verified") is False,
                        "Architecture binding mismatch")
                modules[module] = m
    for key in sorted(refs - records.keys()):
        row = fquery(root, work, "source-"+key, "knowledge", "show", key)
        require(row.get("record", {}).get("id") == key and row.get("reuse") == "BLOCKED",
                "Reference gate mismatch")
        records[key] = row
    require(all(v.get("reuse") == "BLOCKED" for k,v in records.items() if k.startswith("REF-")),
            "Unexpected license gate")
    # Initial version uses explicitly reviewed synthetic fixture pair, not arbitrary datasets.
    require(tuple(dataset) == ("vehicle-fixture","1.1.0") and tuple(content) == ("vehicle.demo","1.0.0"),
            "Unreviewed dataset/content")
    dataset_info = fquery(root, work, "dataset", "data", "show", *dataset)
    content_info = fquery(root, work, "content-entity", "content", "show", *content)
    graph = fquery(root, work, "content-graph", "content", "graph", *content)
    manifest, entity = dataset_info["manifest"], content_info["entity"]
    require((manifest["id"],manifest["version"]) == tuple(dataset) and
            manifest["provenance"]["origin"] == "synthetic" and manifest["usage"] == "TEST_ONLY" and
            (entity["id"],entity["version"]) == tuple(content) and entity["origin"] == "synthetic" and
            entity["usage"] == "TEST_ONLY" and all(x.get("production_approved") is False
                 for x in (dataset_info,content_info,graph)), "Dataset/content gate mismatch")
    unchanged("foundation", root)
    return {"revision":FOUNDATION_SHA,"sources":records,"idea_plans":plans,"architecture":modules,
            "knowledge_stats":k,"architecture_stats":a,"data_stats":d,"content_stats":c,
            "dataset":dataset_info,"content":content_info,"content_graph":graph,
            "execution_authorized":False,"production_approved":False}


def engineering_catalog(root, work):
    unchanged("engineering", root)
    work.mkdir(mode=0o700)
    catalog = equery(root,work,"experiments","experiment-list")
    ranking = equery(root,work,"ranking","research-rank")
    specs = catalog.get("experiments")
    require(isinstance(specs,dict) and BUILTINS <= specs.keys() and
            catalog.get("promotion") == "NOT_AUTHORIZED" and isinstance(ranking.get("ranking"),list),
            "Engineering catalog mismatch")
    unchanged("engineering",root)
    return specs, ranking


def numeric(x):
    return type(x) in (int,float) and abs(x) <= 1e100 and math.isfinite(x)


def check_spec(spec):
    require(isinstance(spec,dict) and set(spec)=={"hypothesis","sources","config","criteria"} and
            isinstance(spec["hypothesis"],str) and 0 < len(spec["hypothesis"]) <= 4000, "Invalid frozen hypothesis")
    source_ids(spec["sources"])
    require(isinstance(spec["config"],dict) and type(spec["config"].get("seed")) is int, "Missing deterministic seed")
    criteria=spec["criteria"]
    require(isinstance(criteria,dict) and 0<len(criteria)<=50,"Invalid criteria")
    for key,rule in criteria.items():
        require(isinstance(key,str) and re.fullmatch(r"[a-z][a-z0-9_]{0,63}",key) and
                isinstance(rule,dict) and set(rule)=={"op","threshold","unit"} and
                rule["op"] in ("eq","le","ge") and numeric(rule["threshold"]) and
                isinstance(rule["unit"],str) and rule["unit"] and "fps" not in key and rule["unit"].lower()!="fps",
                "Invalid frozen criterion")
    return spec


def accept_report(report, name, spec, experiment_id):
    require(report.get("id")==experiment_id and report.get("experiment")==name and
            report.get("revision")==ENGINEERING_SHA and report.get("hypothesis")==spec["hypothesis"] and
            report.get("sources")==spec["sources"] and report.get("configuration")==spec["config"] and
            report.get("origin")=="SYNTHETIC" and report.get("promotion")=="NOT_AUTHORIZED" and
            report.get("scientific_validation")=="NOT_PERFORMED" and report.get("gameplay_validation")=="NOT_RUN",
            "Experiment provenance mismatch")
    status=report.get("reported_status")
    require(status in ("PASS","FAIL","NOT_RUN","INCONCLUSIVE","BLOCKED"),"Invalid experiment status")
    if status in ("PASS","FAIL"):
        require(report.get("artifact_bytes")=="MATCH" and isinstance(report.get("plan_sha256"),str)
                and HEX.fullmatch(report["plan_sha256"]) and isinstance(report.get("result_sha256"),str)
                and HEX.fullmatch(report["result_sha256"]),"Unverified experiment result")
        rows=report.get("metrics")
        require(isinstance(rows,list) and len(rows)==len(spec["criteria"]) and
                {x["metric"] for x in rows}==set(spec["criteria"]),"Missing metric")
        checks=[]
        for m in rows:
            rule=spec["criteria"][m["metric"]]
            require(all(m[k]==rule[k] for k in ("op","threshold","unit")) and numeric(m["observed"]),
                    "Changed metric criteria")
            x,y=m["observed"],rule["threshold"]
            ok=x==y if rule["op"]=="eq" else x<=y if rule["op"]=="le" else x>=y
            require(m["criterion_met"] is ok,"Criterion mismatch")
            checks.append(ok)
        require((status=="PASS")==all(checks),"Status contradicts criteria")
    require(isinstance(report.get("limitations"),str) and report["limitations"],"Missing limitations")
    return report


def execute_fixture(root, work, name, spec, experiment_id):
    require(name in BUILTINS,"Unknown experiment capability")
    check_spec(spec)
    unchanged("engineering",root)
    work.mkdir(mode=0o700)
    reg=work/"experiment"
    # Lab owns plan registration, evaluator and evidence verification. Never rerun on failure.
    equery(root,work,"execution","experiment-run",name,"--id",experiment_id,"--registry",str(reg),
           allowed=(0,1),timeout=60)
    report=equery(root,work,"verified-report","experiment-report","--id",experiment_id,"--registry",str(reg),allowed=(0,1))
    accepted=accept_report(report,name,spec,experiment_id)
    unchanged("engineering",root)
    return accepted


def comparison(reports):
    # Preserve units, frozen thresholds, adverse/exploratory results. No cross-metric speedup inference.
    rows=[{"experiment":r["experiment"],"status":r["reported_status"],"metrics":r["metrics"],
           "observations":r["observations"],"limitations":r["limitations"]} for r in reports]
    status="FAIL" if any(r["status"]=="FAIL" for r in rows) else "PASS" if rows and all(r["status"]=="PASS" for r in rows) else "INCONCLUSIVE"
    return {"status":status,"scope":"synthetic criteria only; no measured runtime speedup",
            "experiments":rows,"promotion":"NOT_AUTHORIZED"}


class Research:
    def __init__(self, registry):
        self.store=Registry(registry)
        with self.store.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS research_cases (id TEXT PRIMARY KEY, state TEXT NOT NULL, snapshot TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS research_events (id TEXT NOT NULL REFERENCES research_cases(id), seq INTEGER NOT NULL, at TEXT NOT NULL, action TEXT NOT NULL, snapshot TEXT NOT NULL, PRIMARY KEY(id,seq))")

    def save(self,row,action,start=False):
        payload=encode(row)
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if start:
                db.execute("INSERT INTO research_cases VALUES (?,?,?)",(row["id"],row["state"],payload))
            else:
                db.execute("UPDATE research_cases SET state=?,snapshot=? WHERE id=?",(row["state"],payload,row["id"]))
            seq=db.execute("SELECT COALESCE(MAX(seq),0)+1 FROM research_events WHERE id=?",(row["id"],)).fetchone()[0]
            db.execute("INSERT INTO research_events VALUES (?,?,?,?,?)",(row["id"],seq,now(),action,payload))

    def get(self,sid):
        with self.store.connect() as db:
            row=db.execute("SELECT snapshot FROM research_cases WHERE id=?",(ident(sid),)).fetchone()
            require(row is not None,"Unknown research case")
            return json.loads(row[0])

    def list(self):
        with self.store.connect() as db:
            return [json.loads(r[0]) for r in db.execute("SELECT snapshot FROM research_cases ORDER BY rowid DESC LIMIT 100")]

    def history(self,sid):
        self.get(sid)
        with self.store.connect() as db:
            return [{"seq":r["seq"],"action":r["action"],"at":r["at"],"snapshot":json.loads(r["snapshot"])}
                    for r in db.execute("SELECT * FROM research_events WHERE id=? ORDER BY seq",(sid,))]

    def roots(self,foundation,engineering):
        f,e=bind("foundation",foundation),bind("engineering",engineering)
        require(f!=e and all(not self.store.root.is_relative_to(r) and not r.is_relative_to(self.store.root)
                             for r in (f,e)),"Private registry must be outside checkouts")
        return f,e

    def prepare(self,foundation,engineering,names,question):
        require(isinstance(question,str) and 0<len(question.strip())<=2000,"Invalid research question")
        require(isinstance(names,list) and 1<=len(names)<=4 and len(set(names))==len(names)
                and all(n in BUILTINS for n in names),"Choose 1..4 reviewed experiments")
        f,e=self.roots(foundation,engineering)
        sid=uuid.uuid4().hex
        work=self.store.root/("research-"+sid)
        row={"id":sid,"schema":1,"state":"PREPARING","question":question.strip(),"revisions":PINS,
             "execution_authorized":False,"promotion":"NOT_AUTHORIZED","scientific_validation":"NOT_PERFORMED"}
        with self.store.lock(sid):
            work.mkdir(mode=0o700)
            self.save(row,"start",True)
            try:
                specs,ranking=engineering_catalog(e,work/"engineering")
                selected={n:check_spec(specs[n]) for n in names}
                sources=sorted({x for s in selected.values() for x in s["sources"]})
                packet=foundation_packet(f,work/"foundation",sources)
                links=equery(e,work/"engineering","portfolio-links","research-validate","--foundation-dir",str(f/"docs/references"))
                require(links.get("foundation_links")=="MATCH" and links.get("promotion")=="NOT_AUTHORIZED",
                        "Engineering/Foundation catalog mismatch")
                # Frozen evidence before execution. Foundation dataset/content are context only, not inputs to fixtures.
                dossier={"schema":1,"question":row["question"],"revisions":PINS,"experiments":selected,
                         "foundation":packet,"portfolio_ranking":ranking,"portfolio_links":links,
                         "dataset_usage":"CONTEXT_ONLY_NOT_EXPERIMENT_INPUT",
                         "engineering_tasks":[{"idea":key,"owner":p["owner_module"],"steps":p["steps"],
                                               "status":"BLOCKED_PENDING_REVIEW","reference_ids":p["reference_ids"]}
                                              for key,p in sorted(packet["idea_plans"].items())],
                         "execution_authorized":False,"promotion":"NOT_AUTHORIZED"}
                write(work/"frozen-plan.json",dossier)
                unchanged("foundation",f); unchanged("engineering",e)
                row.update(state="PLANNED",plan_sha256=digest(work/"frozen-plan.json"),
                           experiments=list(selected),source_ids=sources)
            except (ValueError,OSError,KeyError,TypeError,subprocess.SubprocessError) as exc:
                row.update(state="BLOCKED",reason=type(exc).__name__+": "+str(exc)[:180])
            self.save(row,"prepare")
            return row

    def run(self,sid,foundation,engineering,expected_plan_sha):
        f,e=self.roots(foundation,engineering)
        with self.store.lock(sid):
            row=self.get(sid)
            require(row["state"]=="PLANNED" and row["plan_sha256"]==expected_plan_sha,"Frozen plan approval mismatch")
            work=self.store.root/("research-"+sid)
            p=work/"frozen-plan.json"
            require(digest(p)==expected_plan_sha,"Frozen plan bytes changed")
            plan=load(p)
            require(plan["revisions"]==PINS and plan["execution_authorized"] is False and
                    plan["promotion"]=="NOT_AUTHORIZED","Plan provenance")
            row.update(state="RUNNING",attempts=1)
            self.save(row,"run")
            reports=[]
            try:
                specs,_=engineering_catalog(e,work/"recheck")
                for n,spec in plan["experiments"].items():
                    require(n in BUILTINS and spec==specs[n],"Frozen experiment differs from reviewed catalog")
                for i,(n,spec) in enumerate(plan["experiments"].items()):
                    report=execute_fixture(e,work/("run-"+n),n,spec,"EXP-HUB-"+sid[:12].upper()+"-"+str(i+1))
                    reports.append(report)
                    row["completed"]=[{"experiment":x["experiment"],"status":x["reported_status"]} for x in reports]
                    self.save(row,"experiment")
                unchanged("foundation",f); unchanged("engineering",e)
                summary=comparison(reports)
                write(work/"comparison.json",summary)
                row.update(state=summary["status"],comparison_sha256=digest(work/"comparison.json"))
            except (ValueError,OSError,KeyError,TypeError,subprocess.SubprocessError) as exc:
                row.update(state="BLOCKED",reason=type(exc).__name__+": "+str(exc)[:180])
            self.save(row,"finish")
            return row

    def recover(self,sid):
        with self.store.lock(sid):
            row=self.get(sid)
            require(row["state"] in ("RUNNING","PREPARING"),"Only abandoned case may be recovered")
            row.update(state="INTERRUPTED",reason="No replay; inspect partial evidence and orphan processes")
            self.save(row,"recover")
            return row


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--registry",type=Path,required=True)
    sub=p.add_subparsers(dest="mode",required=True)
    sub.add_parser("list")
    for name in ("show","history","recover"):
        sub.add_parser(name).add_argument("id")
    for name in ("prepare","run"):
        q=sub.add_parser(name)
        q.add_argument("--foundation",required=True)
        q.add_argument("--engineering",required=True)
        q.add_argument("--trusted-checkouts",action="store_true",required=True)
        if name=="prepare":
            q.add_argument("--question",required=True)
            q.add_argument("--experiment",action="append",required=True)
        else:
            q.add_argument("id")
            q.add_argument("--plan-sha256",required=True)
    a=p.parse_args()
    try:
        r=Research(a.registry)
        if a.mode=="prepare":
            out=r.prepare(a.foundation,a.engineering,a.experiment,a.question)
        elif a.mode=="run":
            out=r.run(a.id,a.foundation,a.engineering,a.plan_sha256)
        elif a.mode=="list":
            out=r.list()
        else:
            out=getattr(r,"get" if a.mode=="show" else a.mode)(a.id)
        print(json.dumps(out,indent=2,ensure_ascii=True))
        return 1 if a.mode in ("prepare","run") and out["state"] not in ("PASS","PLANNED") else 0
    except (ValueError,OSError,KeyError,TypeError,sqlite3.Error,subprocess.SubprocessError) as exc:
        print("APEX_RESEARCH_BLOCKED: "+str(exc),file=sys.stderr)
        return 2


if __name__=="__main__":
    raise SystemExit(main())
