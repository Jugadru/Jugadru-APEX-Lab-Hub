"""Hosted one-shot MCP wire smoke. No model API calls."""
import json,os,pathlib,subprocess,sys
from hub.ai_gateway import Advisory
from hub.dashboard import export
root=pathlib.Path(os.environ["RUNNER_TEMP"])
sha=subprocess.check_output(["git","rev-parse","HEAD"],text=True).strip()
source=json.loads((root/"hub-smoke/knowledge/report.json").read_text())["evidence"][0]
h=Advisory(root/"advisory-state")
p=h.propose({"schema":1,"department":"software","provider":"mcp_stdio","goal":"review_evidence",
             "evidence_ids":[source["id"]]},root/"knowledge-state")
h.approve(p["id"],p["plan_sha256"])
messages=[{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"apex-hosted-smoke","version":"1"}}},
          {"jsonrpc":"2.0","method":"notifications/initialized"},
          {"jsonrpc":"2.0","id":2,"method":"tools/list"},
          {"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"apex_context","arguments":{}}},
          {"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"apex_context","arguments":{}}}]
proc=subprocess.run([sys.executable,"-B","-m","hub.mcp_server","--registry",str(h.store.root),"--case",p["id"],
                     "--plan-sha256",p["plan_sha256"],"--trusted-local-host"],input="".join(json.dumps(x)+"\n" for x in messages),
                     text=True,capture_output=True,timeout=20,check=True)
rows={x["id"]:x for x in map(json.loads,proc.stdout.splitlines())}
assert rows[1]["result"]["protocolVersion"]=="2025-06-18"
assert rows[2]["result"]["tools"][0]["name"]=="apex_context"
packet=json.loads(rows[3]["result"]["content"][0]["text"])
assert packet["context"]==p["plan"]["context"] and rows[4]["result"]["isError"]
assert h.get(p["id"])["releases"]==1
out=root/"hub-smoke/advisory";out.mkdir()
(out/"exchange.json").write_text(json.dumps({"case":h.get(p["id"]),"responses":rows},indent=2))
report=export([("advisory",h.store.root)],out/"dashboard")
assert report["registries"][0]["sections"]["advisory_cases"]["counts"]=={"RELEASED":1}
print("REAL_APPROVED_MCP_STDIO_PASS",sha,p["id"])
