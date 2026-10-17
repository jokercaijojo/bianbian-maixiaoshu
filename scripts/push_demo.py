"""演示：强制扫描一次（忽略去重）并把结果推送到微信。

用法：.venv/Scripts/python scripts/push_demo.py [关键词...]
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from maimai_watchdog.channel_claw import ClawGateway  # noqa: E402
from maimai_watchdog.config import DEFAULT_DB, load_env  # noqa: E402
from maimai_watchdog.db import Store  # noqa: E402
from maimai_watchdog.mcp_client import McdMcpClient  # noqa: E402
from maimai_watchdog.monitor import format_push_text, scan_user  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

CRED_FILE = os.path.join(os.path.dirname(__file__), "..", "claw_credentials.json")


async def main() -> int:
    load_env()
    keywords = sys.argv[1:] or ["麦香鱼", "早餐", "美式"]
    store = Store(os.environ.get("WATCHDOG_DB", DEFAULT_DB))
    user = store.active_users()[0]
    sid = user.get("claw_account_id")
    if not sid:
        print("尚未绑定微信联系人，请先启动 daemon 并给 ClawBot 发条消息。")
        return 1

    client = McdMcpClient(user["mcp_token"], os.environ.get("MCD_MCP_URL",
                                                          "https://mcp.mcd.cn"))
    res = scan_user(client, keywords)
    if res.error:
        print("扫描失败:", res.error)
        return 1

    seen, hits = set(), []
    for h in res.hits:
        if (h.kind, h.title) not in seen:
            seen.add((h.kind, h.title))
            hits.append(h)

    if hits:
        text = format_push_text(hits)
    else:
        text = ("🍔 麦麦管家巡检报告\n\n"
                f"刚帮你蹲了一波：当前可领券 {res.total_coupons} 张、"
                f"本月活动 {res.total_campaigns} 个，"
                f"但你关注的「{'、'.join(keywords)}」暂时都没有新优惠。\n"
                "我会继续盯着，一出现立刻喊你！")

    gateway = ClawGateway(os.path.abspath(CRED_FILE))
    await gateway.start()
    ok = await gateway.push_text(sid, text)
    await gateway.stop()
    print("推送", "成功 ✅" if ok else "失败 ❌")
    print("---- 内容 ----")
    print(text)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
