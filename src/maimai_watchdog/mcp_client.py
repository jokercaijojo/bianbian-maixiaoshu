"""麦当劳 MCP Streamable HTTP 客户端（同步、零依赖）。

协议要点：POST JSON-RPC 到接入地址，Accept 同时声明 application/json 与
text/event-stream；响应可能是 JSON 也可能是 SSE（取 data: 行）。
"""
from __future__ import annotations

import json
import urllib.request
from typing import Any

DEFAULT_URL = "https://mcp.mcd.cn"


class McpError(RuntimeError):
    pass


class McdMcpClient:
    def __init__(self, token: str, base_url: str = DEFAULT_URL, timeout: int = 30):
        if not token:
            raise McpError("缺少 MCP Token")
        self.token = token
        self.base_url = base_url
        self.timeout = timeout
        self._session: str | None = None
        self._rid = 0

    def _post(self, payload: dict) -> dict:
        body = json.dumps(payload).encode()
        req = urllib.request.Request(self.base_url, data=body, method="POST")
        req.add_header("Authorization", f"Bearer {self.token}")
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "application/json, text/event-stream")
        if self._session:
            req.add_header("mcp-session-id", self._session)
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            self._session = resp.headers.get("mcp-session-id", self._session)
            raw = resp.read().decode("utf-8", "replace")
        return self._parse(raw)

    @staticmethod
    def _parse(raw: str) -> dict:
        if not raw.strip():
            return {}
        if raw.lstrip().startswith("{"):
            return json.loads(raw)
        # SSE：逐行取 data: 负载
        for line in raw.splitlines():
            if line.startswith("data:"):
                p = line[5:].strip()
                if p:
                    try:
                        return json.loads(p)
                    except json.JSONDecodeError:
                        continue
        return {}

    def _rpc(self, method: str, params: dict | None = None) -> dict:
        self._rid += 1
        res = self._post({"jsonrpc": "2.0", "id": self._rid, "method": method,
                          "params": params or {}})
        if "error" in res:
            raise McpError(f"RPC 错误: {res['error']}")
        return res.get("result", {})

    def connect(self) -> None:
        self._rpc("initialize", {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "maimai-watchdog", "version": "0.1.0"},
        })
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def call_tool(self, name: str, arguments: dict | None = None) -> str:
        """调用工具，返回拼接后的文本内容。"""
        if self._session is None and self._rid == 0:
            self.connect()
        result = self._rpc("tools/call", {"name": name, "arguments": arguments or {}})
        parts = [c.get("text", "") for c in result.get("content", [])
                 if c.get("type") == "text"]
        return "\n".join(p for p in parts if p)

    # ---- 常用工具封装 ----
    def available_coupons(self) -> str:
        return self.call_tool("available-coupons")

    def campaign_calendar(self) -> str:
        return self.call_tool("campaign-calendar")

    def my_coupons(self) -> str:
        return self.call_tool("query-my-coupons")

    def auto_bind_coupons(self) -> str:
        return self.call_tool("auto-bind-coupons")
