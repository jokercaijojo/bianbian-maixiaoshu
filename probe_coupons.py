"""实测只读查询链路：available-coupons / campaign-calendar / query-my-coupons / query-my-account"""
import json, os
from test_connection import rpc, load_env

def call(name, args=None, rid=10):
    res, _ = rpc("tools/call", {"name": name, "arguments": args or {}}, rid=rid)
    result = res.get("result", {})
    content = result.get("content", [])
    text = "\n".join(c.get("text", "") for c in content if c.get("type") == "text")
    print(f"\n===== {name} =====")
    print(text[:3000] if text else json.dumps(res, ensure_ascii=False)[:2000])
    return text

if __name__ == "__main__":
    load_env()
    import test_connection as tc
    tc.TOKEN = os.environ["MCD_MCP_TOKEN"]
    call("now-time-info", rid=11)
    call("campaign-calendar", rid=12)
    call("available-coupons", rid=13)
    call("query-my-coupons", rid=14)
