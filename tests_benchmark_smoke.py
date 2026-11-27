"""Actual Godot headless and software-render smoke; no physical GPU claim."""
import json,os,pathlib,shutil,subprocess
from hub.benchmark import Bench
from hub.graphics import digest
from hub.dashboard import export
root=pathlib.Path(os.environ["RUNNER_TEMP"])
sha=subprocess.check_output(["git","rev-parse","HEAD"],text=True).strip()
godot=pathlib.Path(shutil.which("godot")).resolve()
b=Bench(root/"benchmark-state")
headless=b.run(godot,digest(godot),"headless","eco",120)
assert headless["state"]=="PASS" and headless["metrics"]["observed_loop_fps"] is None,headless
render=b.run(godot,digest(godot),"software","eco",120)
assert render["state"]=="PASS" and render["gpu"]["classification"]=="SOFTWARE",render
assert b.verify(headless["id"])==headless and b.verify(render["id"])==render
out=root/"hub-smoke/benchmark";out.mkdir()
(out/"report.json").write_text(json.dumps({"headless":headless,"software":render,"physical_gpu":"NOT_RUN"},indent=2))
report=export([("benchmarks",b.store.root)],out/"dashboard")
assert report["registries"][0]["sections"]["benchmarks"]["counts"]=={"PASS":2}
print("REAL_GODOT_BENCHMARK_PASS",sha,headless["id"],render["id"])
