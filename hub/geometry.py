"""Explicit offline geometry audit through the reviewed Asset Factory validator."""
import argparse
from pathlib import Path
import sqlite3
import subprocess
import sys
import uuid
import json

from . import graphics as g, orchestrator as o
from .registry import Registry, encode, ident, now

VALIDATOR="tools/asset_factory/validate_glb.py"
MAX_BUDGET=20000

def accept(result,inputs,budget):
    o.require(isinstance(result,dict) and result.get("promotion")=="BLOCKED" and result.get("status") in ("PASS","FAIL"),"Invalid geometry result")
    if len(inputs)==1:
        o.require(result["status"]=="PASS" and result.get("sha256")==inputs[0]["sha256"] and
                  result.get("bytes")==inputs[0]["bytes"] and g.number(result.get("triangles"),1,budget),
                  "Geometry receipt mismatch")
    else:
        o.require(result.get("sha256")==[x["sha256"] for x in inputs] and result.get("tolerance")==1e-6 and
                  type(result.get("numeric_equivalent")) is bool and type(result.get("document_equivalent")) is bool and
                  result.get("binary_equal") is (inputs[0]["sha256"]==inputs[1]["sha256"]) and
                  (result["status"]=="PASS")==result["numeric_equivalent"] and
                  (not result["numeric_equivalent"] or result["document_equivalent"]),
                  "Comparison receipt mismatch")
    return result["status"]

class Geometry:
    def __init__(self,root):
        self.store=Registry(root)
        with self.store.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS geometry_audits (id TEXT PRIMARY KEY,state TEXT NOT NULL,snapshot TEXT NOT NULL)")
    def get(self,sid):
        with self.store.connect() as db:
            r=db.execute("SELECT state,snapshot FROM geometry_audits WHERE id=?",(ident(sid),)).fetchone()
            o.require(r is not None,"Unknown geometry audit")
            v=json.loads(r["snapshot"])
            o.require(v["id"]==sid and v["state"]==r["state"],"Audit identity mismatch")
            return v
    def save(self,row,new=False):
        with self.store.connect() as db:
            if new:db.execute("INSERT INTO geometry_audits VALUES (?,?,?)",(row["id"],row["state"],encode(row)))
            else:db.execute("UPDATE geometry_audits SET state=?,snapshot=? WHERE id=?",(row["state"],encode(row),row["id"]))
    def recover(self,sid):
        with self.store.lock(sid):
            row=self.get(sid)
            o.require(row["state"]=="RUNNING","Only interrupted running audit may be recovered")
            row.update(state="INTERRUPTED",reason="No replay; inspect private workspace")
            self.save(row);return row
    def run(self,checkout,revision,asset,second=None,budget=8000):
        root,head,source=g.check_factory(checkout,revision)
        o.require(g.number(budget,1,MAX_BUDGET),"Invalid triangle budget")
        o.require(not self.store.root.is_relative_to(root) and not root.is_relative_to(self.store.root),"Separate registry required")
        inputs=[Path(asset).expanduser().absolute()]
        if second is not None:inputs.append(Path(second).expanduser().absolute())
        for p in inputs:
            o.require(p.suffix.lower()==".glb" and p.resolve()==p and p.is_file() and
                      not p.is_relative_to(root),"Use regular GLB outside Factory checkout")
            g.file_evidence(p.parent,p.name,g.MAX_ASSET)
        sid=uuid.uuid4().hex
        row={"schema":1,"id":sid,"created":now(),"state":"RUNNING","factory_sha":head,
             "validator_sha256":source[VALIDATOR],"mode":"compare" if second is not None else "inspect",
             "budget":budget,"evidence":[],"promotion":"BLOCKED","visual_review":"NOT_RUN","physical_gpu":"NOT_RUN",
             "license_review":"NOT_RUN","native_import":"NOT_RUN"}
        with self.store.lock(sid):
            self.save(row,True)
            work=self.store.root/("geometry-"+sid)
            try:
                work.mkdir(mode=0o700);home=work/"home";home.mkdir(mode=0o700)
                # Freeze bounded bytes, do not run input or expose original paths in report.
                for i,p in enumerate(inputs):
                    before=g.file_evidence(p.parent,p.name,g.MAX_ASSET)
                    with p.open("rb") as f:raw=f.read(g.MAX_ASSET+1)
                    o.require(len(raw)==before["bytes"] and len(raw)<=g.MAX_ASSET,"Input changed or oversized")
                    name="input-"+str(i)+".glb"
                    with (work/name).open("xb") as f:f.write(raw)
                    after=g.file_evidence(work,name,g.MAX_ASSET)
                    o.require(after["sha256"]==before["sha256"] and g.file_evidence(p.parent,p.name,g.MAX_ASSET)==before,"Input changed")
                    row["evidence"].append(after)
                args=[sys.executable,"-I","-B",str(root/VALIDATOR),str(work/"input-0.glb"),"--budget",str(budget)]
                if second is not None:args.extend(["--compare",str(work/"input-1.glb"),"--tolerance","0.000001"])
                code=g.execute(args,work,work/"validator.json",60,home)
                report=g.read(work/"validator.json")
                row["evidence"].append(g.file_evidence(work,"validator.json",g.MAX_JSON))
                if code==1 and report.get("status")=="FAIL" and "error" in report:
                    o.require(report.get("promotion")=="BLOCKED","Invalid rejection receipt")
                    row.update(state="FAIL",result={"status":"FAIL","scope":"Validator rejected input","promotion":"BLOCKED"})
                else:
                    state=accept(report,row["evidence"][:len(inputs)],budget)
                    o.require(code==(0 if state=="PASS" else 1),"Validator exit mismatch")
                    row.update(state=state,result=report)
                o.require(g.check_factory(root,head)[2]==source,"Factory changed")
                for e in row["evidence"]:o.require(g.file_evidence(work,e["path"],g.MAX_ASSET)==e,"Evidence changed")
            except (OSError,ValueError,KeyError,TypeError,AttributeError,subprocess.SubprocessError) as exc:
                row.update(state="BLOCKED",reason=type(exc).__name__)
            self.save(row)
            return row

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("--registry",type=Path,required=True)
    s=p.add_subparsers(dest="mode",required=True)
    for mode in ("inspect","compare"):
        q=s.add_parser(mode);q.add_argument("--checkout",required=True,type=Path);q.add_argument("--sha",required=True)
        q.add_argument("--asset",required=True,type=Path);q.add_argument("--budget",type=int,default=8000)
        q.add_argument("--trusted-checkout",required=True,action="store_true")
        if mode=="compare":q.add_argument("--second",required=True,type=Path)
    for mode in ("show","recover"):s.add_parser(mode).add_argument("id")
    a=p.parse_args()
    try:
        v=Geometry(a.registry)
        if a.mode in ("inspect","compare"):out=v.run(a.checkout,a.sha,a.asset,getattr(a,"second",None),a.budget)
        else:out=getattr(v,"get" if a.mode=="show" else a.mode)(a.id)
        print(json.dumps(out,indent=2,ensure_ascii=True))
        return 1 if a.mode in ("inspect","compare") and out["state"]!="PASS" else 0
    except (ValueError,OSError,KeyError,TypeError,sqlite3.Error,subprocess.SubprocessError):
        print("APEX_GEOMETRY_BLOCKED: invalid input or checkout",file=sys.stderr);return 2
if __name__=="__main__":raise SystemExit(main())
