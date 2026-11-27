"""Bounded, isolated Godot render benchmark and evidence-based frame statistics."""
import argparse
import json
import math
import os
from pathlib import Path
import platform
import shutil
import signal
import sqlite3
import statistics
import subprocess
import sys
import time
import uuid

from . import graphics as g, geometry, orchestrator as o
from .registry import Registry, encode, ident, now

FIXTURE=Path(__file__).resolve().parent/"benchmark_scene.gd"
MODES={"headless","software","render"}
PROFILES={"eco","balanced"}
SAMPLES={120,600,1800}
DEADLINE=120
LIMIT=2*1024*1024
PROJECT='''[application]
config/name="APEX Isolated Benchmark"
[display]
window/size/viewport_width=1920
window/size/viewport_height=1080
window/size/window_width_override=1920
window/size/window_height_override=1080
window/vsync/vsync_mode=0
[rendering]
renderer/rendering_method="gl_compatibility"
renderer/rendering_method.mobile="gl_compatibility"
'''
def stats(values):
    o.require(isinstance(values,list) and len(values) in SAMPLES and
              all(type(x) in (int,float) and math.isfinite(x) and 0<x<=120_000_000 and int(x)==x for x in values),"Invalid frame samples")
    s=sorted(values)
    q=lambda p:s[math.ceil(p*len(s))-1]/1000
    return {"samples":len(s),"mean_ms":sum(s)/len(s)/1000,"median_ms":statistics.median(s)/1000,
            "p95_ms":q(.95),"p99_ms":q(.99),"max_ms":s[-1]/1000,
            "observed_loop_fps":len(s)*1_000_000/sum(s),
            "over_16_67ms":sum(x>1_000_000/60 for x in s),"over_33_33ms":sum(x>1_000_000/30 for x in s)}

def label(v):
    o.require(isinstance(v,str) and len(v)<=160 and all(c.isprintable() for c in v),"Invalid device label")
    return v

def accept(data,mode,profile,count,asset):
    o.require(isinstance(data,dict) and data.get("schema")==1 and data.get("mode")==mode and
              data.get("profile")==profile and data.get("cars")==24 and data.get("warmup")==60 and
              data.get("asset") is asset and isinstance(data.get("samples"),list) and len(data["samples"])==count,
              "Benchmark identity mismatch")
    for k in ("adapter","vendor","renderer","display"):label(data[k])
    o.require(data["renderer"]=="gl_compatibility" or (mode=="headless" and data["renderer"] in ("","dummy")),"Unexpected renderer")
    o.require(isinstance(data["viewport"],list) and len(data["viewport"])==2 and
              all(type(x) is int and 0<=x<=16384 for x in data["viewport"]) and
              g.number(data["draw_calls_peak"],0,1000000),"Invalid viewport evidence")
    if mode=="headless":
        o.require(data["display"].lower()=="headless","Expected headless display")
    else:
        o.require(data["display"].lower()!="headless" and data["viewport"]==[1920,1080] and
                  bool(data["adapter"]) and data["draw_calls_peak"]>0,"Rendered 1080p evidence missing")
    result=stats(data["samples"])
    # No FPS claim from headless loop; measured rendered loop is not GPU execution time.
    if mode=="headless":
        result["observed_loop_fps"]=None
        result["frame_budget"]="NOT_RUN"
    else:
        result["frame_budget"]="60_FPS_TARGET" if result["p95_ms"]<=1000/60 else "30_FPS_TARGET" if result["p95_ms"]<=1000/30 else "BELOW_TARGET"
    return result

def nvidia():
    path=shutil.which("nvidia-smi",path=os.defpath)
    if not path:return []
    try:
        p=subprocess.run([path,"--query-gpu=name,driver_version,memory.total,memory.used,utilization.gpu,temperature.gpu",
                          "--format=csv,noheader,nounits"],capture_output=True,text=True,timeout=2,
                         env={"PATH":os.defpath,"LANG":"C.UTF-8"},check=True)
        o.require(len(p.stdout)<=8192,"GPU telemetry too large")
        rows=[]
        for line in p.stdout.splitlines()[:16]:
            name,driver,total,used,util,temp=[x.strip() for x in line.split(",")]
            vals=[int(x) for x in (total,used,util,temp)]
            o.require(0<=vals[1]<=vals[0]<=1048576 and 0<=vals[2]<=100 and 0<=vals[3]<=150,"Invalid GPU values")
            rows.append({"name":label(name),"driver":label(driver),"vram_total_mib":vals[0],"vram_used_mib":vals[1],
                         "utilization_pct":vals[2],"temperature_c":vals[3]})
        return rows
    except (ValueError,OSError,subprocess.SubprocessError):return []

def run_process(args,project,work,name,mode,collect=False):
    home=work/"home";home.mkdir(mode=0o700,exist_ok=True)
    env={"PATH":os.defpath,"LANG":"C.UTF-8","HOME":str(home),"XDG_CONFIG_HOME":str(home/"config"),
         "XDG_CACHE_HOME":str(home/"cache"),"XDG_DATA_HOME":str(home/"data")}
    if mode!="headless":
        for k in ("DISPLAY","XAUTHORITY","XDG_RUNTIME_DIR","WAYLAND_DISPLAY"):
            if k in os.environ:env[k]=os.environ[k]
        if mode=="software":env["LIBGL_ALWAYS_SOFTWARE"]="1"
    log=work/(name+".log")
    metric=work/(name+".metrics")
    timer=Path("/usr/bin/time")
    o.require(timer.is_file() and os.access(timer,os.X_OK),"GNU time required")
    telemetry=[]
    with log.open("xb") as f:
        p=subprocess.Popen([str(timer),"-f","%e %U %S %M","-o",str(metric),*args],cwd=project,
                           env=env,stdout=f,stderr=subprocess.STDOUT,start_new_session=True)
        begin=time.monotonic();next_sample=begin
        try:
            while p.poll() is None:
                now_s=time.monotonic()
                o.require(now_s-begin<DEADLINE and log.stat().st_size<=LIMIT,"Process deadline or log limit")
                if collect and mode=="render" and now_s>=next_sample:
                    devices=nvidia()
                    if devices:telemetry.append({"elapsed_s":round(now_s-begin,3),"devices":devices})
                    next_sample=now_s+1
                time.sleep(.05)
        finally:
            try:os.killpg(p.pid,signal.SIGKILL)
            except ProcessLookupError:pass
            p.wait(timeout=10)
    o.require(p.returncode==0 and log.stat().st_size<=LIMIT,"Godot process failed")
    text=log.read_text(encoding="utf-8",errors="replace")
    o.require(not any(x in text for x in ("SCRIPT ERROR","Parse Error:","ERROR:")),"Godot reported errors")
    values=metric.read_text().split()
    o.require(len(values)==4,"Invalid GNU time metrics")
    wall,user,system=map(float,values[:3]);rss=int(values[3])
    o.require(all(math.isfinite(x) and x>=0 for x in (wall,user,system)) and rss>=0,"Invalid process metrics")
    return text,{"wall_seconds":wall,"user_cpu_seconds":user,"system_cpu_seconds":system,"peak_rss_kib":rss,
                 "scope":"GNU time process-tree accounting, whole invocation, not instantaneous frame CPU"},telemetry

def gpu_summary(mode,data,telemetry):
    adapter=data["adapter"].lower()
    software=any(s in adapter for s in ("llvmpipe","softpipe","software","swiftshader"))
    matched=[d for t in telemetry for d in t["devices"] if d["name"].lower() in adapter or adapter in d["name"].lower()] if adapter else []
    if mode=="headless":kind="NOT_RUN"
    elif software:kind="SOFTWARE"
    elif mode=="software":kind="INCONCLUSIVE"
    elif matched and "nvidia" in data["vendor"].lower():kind="NVIDIA_OBSERVED"
    else:kind="INCONCLUSIVE"
    return {"classification":kind,"samples":len(telemetry),"matched_device_samples":len(matched),
            "peak_device_vram_mib":max((x["vram_used_mib"] for x in matched),default=None),
            "peak_device_utilization_pct":max((x["utilization_pct"] for x in matched),default=None),
            "scope":"Adapter and whole-device samples, not per-process VRAM or independent hardware certification"}

def compare(a,b):
    keys=("fixture_sha256","godot_sha256","asset_sha256","mode","profile","samples","hardware")
    o.require(a["state"]==b["state"]=="PASS" and all(a[k]==b[k] for k in keys),"Non-comparable benchmark configurations")
    o.require(a["mode"]!="headless","Headless loop not a rendered FPS regression")
    old=a["metrics"]["p95_ms"];new=b["metrics"]["p95_ms"]
    o.require(old>0 and new>0,"Invalid frame metric")
    return {"status":"OBSERVED","p95_change_percent":(new/old-1)*100,
            "baseline_p95_ms":old,"candidate_p95_ms":new,
            "scope":"Single same-configuration observed pair; no statistical regression verdict"}

class Bench:
    def __init__(self,root):
        self.store=Registry(root)
        with self.store.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS benchmarks (id TEXT PRIMARY KEY,state TEXT NOT NULL,snapshot TEXT NOT NULL)")
    def save(self,row,new=False):
        with self.store.connect() as db:
            if new:db.execute("INSERT INTO benchmarks VALUES (?,?,?)",(row["id"],row["state"],encode(row)))
            else:db.execute("UPDATE benchmarks SET state=?,snapshot=? WHERE id=?",(row["state"],encode(row),row["id"]))
    def get(self,sid):
        with self.store.connect() as db:
            r=db.execute("SELECT state,snapshot FROM benchmarks WHERE id=?",(ident(sid),)).fetchone()
            o.require(r is not None,"Unknown benchmark")
            v=json.loads(r["snapshot"])
            o.require(v["id"]==sid and v["state"]==r["state"],"Benchmark identity mismatch")
            return v
    def recover(self,sid):
        with self.store.lock(sid):
            row=self.get(sid);o.require(row["state"]=="RUNNING","Only interrupted running benchmark may be recovered")
            row.update(state="INTERRUPTED",reason="No replay; inspect local workspace")
            self.save(row);return row
    def run(self,godot,expected,mode,profile,count,asset=None,factory=None):
        o.require(sys.platform=="linux" and mode in MODES and profile in PROFILES and count in SAMPLES,"Unsupported benchmark configuration")
        o.require((asset is None)==(factory is None),"Asset requires exact Factory checkout")
        source=FIXTURE.parent.parent
        o.require(not self.store.root.is_relative_to(source) and not source.is_relative_to(self.store.root),"Separate registry required")
        binary=g.executable(godot,expected,(self.store.root,source))
        sid=uuid.uuid4().hex
        row={"schema":1,"id":sid,"state":"RUNNING","created":now(),"godot_sha256":expected,
             "fixture_sha256":o.digest(FIXTURE),"asset_sha256":None,"mode":mode,"profile":profile,"samples":count,
             "gameplay_validation":"NOT_RUN","promotion":"BLOCKED","evidence":[]}
        # Serialize cooperating benchmark processes within this registry to reduce interference.
        with self.store.lock("e"*32),self.store.lock(sid):
            self.save(row,True)
            work=self.store.root/("benchmark-"+sid)
            try:
                work.mkdir(mode=0o700);project=work/"project";project.mkdir()
                (project/"project.godot").write_text(PROJECT)
                shutil.copyfile(FIXTURE,project/"benchmark.gd")
                if asset is not None:
                    checked=geometry.Geometry(self.store.root).run(factory,g.FACTORY_SHA,asset)
                    o.require(checked["state"]=="PASS","Asset geometry rejected")
                    frozen=self.store.root/("geometry-"+checked["id"])/"input-0.glb"
                    shutil.copyfile(frozen,project/"asset.glb")
                    row["asset_sha256"]=o.digest(project/"asset.glb")
                    o.require(row["asset_sha256"]==checked["result"]["sha256"],"Asset copy changed")
                    row["geometry_id"]=checked["id"]
                version,_,_=run_process([str(binary),"--version"],project,work,"version","headless")
                row["godot_version"]=label(version.strip())
                o.require(row["godot_version"].startswith("4.7.2."),"Reviewed Godot 4.7.2 required")
                if asset is not None:
                    run_process([str(binary),"--headless","--path",str(project),"--editor","--import"],project,work,"import","headless")
                cmd=[str(binary),"--path",str(project),"--rendering-method","gl_compatibility","--audio-driver","Dummy","--disable-vsync"]
                if mode=="headless":cmd.append("--headless")
                else:cmd.extend(["--resolution","1920x1080","--windowed"])
                cmd.extend(["--script","res://benchmark.gd","--",mode,profile,str(count)])
                output,cpu,telemetry=run_process(cmd,project,work,"benchmark",mode,True)
                markers=[x[len("APEX_BENCHMARK "):] for x in output.splitlines() if x.startswith("APEX_BENCHMARK ")]
                o.require(len(markers)==1,"Missing/duplicate benchmark marker")
                data=json.loads(markers[0],object_pairs_hook=o.unique_object)
                metrics=accept(data,mode,profile,count,asset is not None)
                gpu=gpu_summary(mode,data,telemetry)
                if mode=="software":o.require(gpu["classification"]=="SOFTWARE","Software renderer not confirmed")
                o.require(o.digest(binary)==expected and o.digest(FIXTURE)==row["fixture_sha256"] and
                          o.digest(project/"benchmark.gd")==row["fixture_sha256"],"Runtime or fixture changed")
                if asset is not None:o.require(o.digest(project/"asset.glb")==row["asset_sha256"],"Asset changed")
                # No hostname, device serial, usernames or environment dump.
                hardware={"cpu":platform.processor()[:160],"architecture":platform.machine(),
                          "logical_cpus":os.cpu_count(),"adapter":data["adapter"],"vendor":data["vendor"],
                          "renderer":data["renderer"],"display":data["display"],"viewport":data["viewport"],
                          "gpu_drivers":sorted({x["driver"] for t in telemetry for x in t["devices"]})}
                g.dump(work/"samples.json",data);g.dump(work/"telemetry.json",telemetry)
                names=["version.log","version.metrics","benchmark.log","benchmark.metrics","samples.json","telemetry.json",
                       "project/project.godot","project/benchmark.gd"]
                if asset is not None:names.extend(["import.log","import.metrics","project/asset.glb"])
                row.update(state="PASS",metrics=metrics,cpu=cpu,gpu=gpu,hardware=hardware,draw_calls_peak=data["draw_calls_peak"],
                           evidence=[g.file_evidence(work,n,g.MAX_ASSET) for n in names],
                           scope="24 isolated graphical instances; not full game or race")
            except (OSError,ValueError,KeyError,TypeError,AttributeError,subprocess.SubprocessError) as exc:
                row.update(state="BLOCKED",reason=type(exc).__name__+": "+str(exc)[:100])
            self.save(row);return row
    def verify(self,sid):
        row=self.get(sid);o.require(row["state"]=="PASS","Completed benchmark required")
        work=self.store.root/("benchmark-"+sid)
        for e in row["evidence"]:o.require(g.file_evidence(work,e["path"],g.MAX_ASSET)==e,"Benchmark evidence changed")
        data=g.read(work/"samples.json",LIMIT)
        o.require(accept(data,row["mode"],row["profile"],row["samples"],row["asset_sha256"] is not None)==row["metrics"],"Metrics changed")
        return row

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("--registry",required=True,type=Path)
    s=p.add_subparsers(dest="mode",required=True)
    q=s.add_parser("run");q.add_argument("--godot",required=True,type=Path);q.add_argument("--godot-sha",required=True)
    q.add_argument("--mode",dest="render_mode",choices=sorted(MODES),required=True);q.add_argument("--profile",choices=sorted(PROFILES),default="eco")
    q.add_argument("--samples",type=int,choices=sorted(SAMPLES),default=600);q.add_argument("--asset",type=Path);q.add_argument("--factory",type=Path)
    q.add_argument("--trusted-runtime",required=True,action="store_true")
    for n in ("show","recover","verify"):s.add_parser(n).add_argument("id")
    q=s.add_parser("compare");q.add_argument("baseline");q.add_argument("candidate")
    a=p.parse_args()
    try:
        b=Bench(a.registry)
        if a.mode=="run":out=b.run(a.godot,a.godot_sha,a.render_mode,a.profile,a.samples,a.asset,a.factory)
        elif a.mode=="compare":out=compare(b.verify(a.baseline),b.verify(a.candidate))
        else:out=getattr(b,"get" if a.mode=="show" else a.mode)(a.id)
        print(json.dumps(out,indent=2,ensure_ascii=True))
        return 1 if a.mode=="run" and out["state"]!="PASS" else 0
    except (ValueError,OSError,TypeError,KeyError,sqlite3.Error,subprocess.SubprocessError):
        print("APEX_BENCHMARK_BLOCKED: invalid input, state or evidence",file=sys.stderr);return 2
if __name__=="__main__":raise SystemExit(main())
