"""Minimal pinned MCP 2025-06-18 stdio subset; one approved local context."""
import argparse
import json
from pathlib import Path
import signal
import sys

from . import ai_gateway, orchestrator as o, __version__

VERSION="2025-06-18"
MAX_LINE=16384
MAX_MESSAGES=32
TOOL={"name":"apex_context","description":"Release the single operator-approved historical evidence metadata packet. No execution.",
      "inputSchema":{"type":"object","properties":{},"additionalProperties":False}}

def serve(h,sid,expected,reader,writer):
    h.ready(h.get(sid),expected,"mcp_stdio")
    phase="NEW"
    def send(value):
        writer.write(json.dumps(value,ensure_ascii=True,separators=(",",":")).encode()+b"\n")
        writer.flush()
    for _ in range(MAX_MESSAGES):
        raw=reader.readline(MAX_LINE+1)
        if not raw:return
        if len(raw)>MAX_LINE or not raw.endswith(b"\n"):return
        try:
            v=json.loads(raw,object_pairs_hook=o.unique_object,
                         parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Nonfinite JSON")))
        except (ValueError,UnicodeError):
            send({"jsonrpc":"2.0","id":None,"error":{"code":-32700,"message":"Parse error"}});continue
        if not isinstance(v,dict) or v.get("jsonrpc")!="2.0" or not isinstance(v.get("method"),str):
            send({"jsonrpc":"2.0","id":None,"error":{"code":-32600,"message":"Invalid request"}});continue
        method=v["method"];params=v.get("params",{})
        if "id" not in v:
            if method=="notifications/initialized" and phase=="INITIALIZED" and isinstance(params,dict):phase="READY"
            continue
        rid=v["id"]
        if type(rid) not in (str,int) or (isinstance(rid,str) and len(rid)>100):
            send({"jsonrpc":"2.0","id":None,"error":{"code":-32600,"message":"Invalid id"}});continue
        reply={"jsonrpc":"2.0","id":rid}
        error=None;result=None
        if not isinstance(params,dict):error=(-32602,"Invalid params")
        elif method=="initialize" and phase=="NEW":
            if not isinstance(params.get("protocolVersion"),str) or not isinstance(params.get("capabilities"),dict) or not isinstance(params.get("clientInfo"),dict):
                error=(-32602,"Invalid initialize")
            else:
                phase="INITIALIZED"
                result={"protocolVersion":VERSION,"capabilities":{"tools":{}},"serverInfo":{"name":"apex-approved-context","version":__version__}}
        elif method=="ping":result={}
        elif phase!="READY":error=(-32002,"Initialize first")
        elif method=="tools/list":
            if set(params)-{"_meta"}:error=(-32602,"Invalid list params")
            else:result={"tools":[TOOL]}
        elif method=="tools/call":
            if set(params)-{"name","arguments","_meta"} or params.get("name")!=TOOL["name"] or params.get("arguments",{})!={}:
                error=(-32602,"Only approved context without arguments is available")
            else:
                try:
                    packet=h.release(sid,expected,"mcp_stdio")
                    result={"content":[{"type":"text","text":json.dumps(packet,ensure_ascii=True)}],"isError":False}
                except (ValueError,OSError,KeyError):
                    result={"content":[{"type":"text","text":"Context unavailable: approval expired, consumed or denied."}],"isError":True}
        else:error=(-32601,"Method not found")
        if error:reply["error"]={"code":error[0],"message":error[1]}
        else:reply["result"]=result
        send(reply)

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("--registry",required=True,type=Path)
    p.add_argument("--case",required=True);p.add_argument("--plan-sha256",required=True)
    p.add_argument("--trusted-local-host",required=True,action="store_true")
    a=p.parse_args()
    # Linux process lifetime bound; never a background daemon.
    signal.alarm(60)
    try:serve(ai_gateway.Advisory(a.registry),a.case,a.plan_sha256,sys.stdin.buffer,sys.stdout.buffer)
    except (ValueError,OSError,KeyError,TypeError,AttributeError):
        print("APEX_MCP_BLOCKED",file=sys.stderr);return 2
    finally:signal.alarm(0)
    return 0
if __name__=="__main__":raise SystemExit(main())
