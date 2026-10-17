"""连通性测试：initialize + tools/list"""
import json, os, sys
import urllib.request

BASE = os.environ.get("MCD_MCP_URL", "https://mcp.mcd.cn")
TOKEN = None

def load_env():
    here = os.path.dirname(os.path.abspath(__file__))
    env = os.path.join(here, ".env")
    if os.path.exists(env):
        for line in open(env, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

def rpc(method, params=None, session=None, rid=1):
    body = json.dumps({"jsonrpc": "2.0", "id": rid, "method": method,
                       "params": params or {}}).encode()
    req = urllib.request.Request(BASE, data=body, method="POST")
    req.add_header("Authorization", f"Bearer {TOKEN}")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json, text/event-stream")
    if session:
        req.add_header("mcp-session-id", session)
    resp = urllib.request.urlopen(req, timeout=30)
    sid = resp.headers.get("mcp-session-id", session)
    raw = resp.read().decode("utf-8", "replace")
    # SSE 格式：取 data: 行中的 JSON
    if raw.lstrip().startswith("event:") or "\ndata:" in raw or raw.startswith("data:"):
        for line in raw.splitlines():
            if line.startswith("data:"):
                payload = line[5:].strip()
                if payload:
                    try:
                        return json.loads(payload), sid
                    except json.JSONDecodeError:
                        continue
        return {"_raw": raw}, sid
    return (json.loads(raw) if raw.strip() else {}), sid

def main():
    init_params = {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "mcd-coupon-watchdog", "version": "0.1.0"},
    }
    res, sid = rpc("initialize", init_params)
    print("== initialize ==")
    print(json.dumps(res, ensure_ascii=False, indent=2)[:1500])
    print("session:", sid)

    # initialized 通知
    body = json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}).encode()
    req = urllib.request.Request(BASE, data=body, method="POST")
    req.add_header("Authorization", f"Bearer {TOKEN}")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json, text/event-stream")
    if sid:
        req.add_header("mcp-session-id", sid)
    urllib.request.urlopen(req, timeout=30).read()

    res, _ = rpc("tools/list", {}, session=sid, rid=2)
    tools = res.get("result", {}).get("tools", [])
    print(f"\n== tools/list: {len(tools)} 个工具 ==")
    for t in tools:
        print("-", t.get("name"), "|", (t.get("description") or "")[:40])

if __name__ == "__main__":
    load_env()
    globals()["TOKEN"] = os.environ["MCD_MCP_TOKEN"]
    globals()["BASE"] = os.environ.get("MCD_MCP_URL", BASE)
    main()
