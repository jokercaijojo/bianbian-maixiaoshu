"""实弹测试：扫描 owner 关注 → 文字+官方配图推送到微信（忽略去重）。"""
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

from maimai_watchdog.channel_claw import ClawGateway  # noqa: E402
from maimai_watchdog.config import DEFAULT_DB, load_env  # noqa: E402
from maimai_watchdog.db import Store  # noqa: E402
from maimai_watchdog.mcp_client import McdMcpClient  # noqa: E402
from maimai_watchdog.monitor import format_push_text, scan_user  # noqa: E402

CRED_FILE = os.path.join(os.path.dirname(__file__), "..", "claw_credentials.json")


async def main():
    load_env()
    store = Store(os.environ.get("WATCHDOG_DB", DEFAULT_DB))
    user = store.active_users()[0]
    kws = store.get_keywords(user["id"])
    print("关注清单:", kws)

    res = scan_user(McdMcpClient(user["mcp_token"]), kws)
    print(f"扫到 {len(res.hits)} 个命中（可领券 {res.total_coupons} / 活动 {res.total_campaigns}）")
    for h in res.hits:
        print(f"  [{h.kind}] {h.title}  img={'有' if h.image_url else '无'}")

    if not res.hits:
        print("无命中，推一条纯文本测试")
        text = "麦小薯测试播报：今天还没蹲到你关注的优惠，我继续盯着🍟"
        imgs = []
    else:
        seen, hits = set(), []
        for h in res.hits:
            if (h.kind, h.title) not in seen:
                seen.add((h.kind, h.title))
                hits.append(h)
        text = format_push_text(hits)
        imgs = [h.image_url for h in hits if h.image_url]

    print("---推送文案---")
    print(text)
    print("---配图---", imgs)

    gateway = ClawGateway(os.path.abspath(CRED_FILE))
    await gateway.start()
    ok = await gateway.push_parts(user["claw_account_id"], text, imgs)
    print("push_parts 结果:", ok)
    await gateway.stop()


asyncio.run(main())
