"""手动跑一次完整闭环：扫描 → 匹配 → 去重 → 推送（或打印）。

用法：
  python scripts/run_once.py                # 用 .env 里的 Token，默认关键词「麦香鱼」
  python scripts/run_once.py 麦香鱼 薯条     # 指定关键词
  python scripts/run_once.py --demo-hit      # 演示模式：加入一个今天必然命中的关键词
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from maimai_watchdog.config import DEFAULT_DB, load_env  # noqa: E402
from maimai_watchdog.db import Store  # noqa: E402
from maimai_watchdog.mcp_client import McdMcpClient  # noqa: E402
from maimai_watchdog.monitor import format_push_text, scan_user  # noqa: E402
from maimai_watchdog.notifiers import default_notifier  # noqa: E402


def main() -> int:
    load_env()
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    demo_hit = "--demo-hit" in sys.argv
    keywords = args or ["麦香鱼"]
    if demo_hit:
        keywords = keywords + ["脆薯饼"]  # 今日可领券中存在的条目，用于验证命中链路

    store = Store(os.environ.get("WATCHDOG_DB", DEFAULT_DB))

    # 确保有一个本地用户（owner）
    users = store.active_users()
    if not users:
        uid = store.add_user(os.environ["MCD_MCP_TOKEN"], name="owner")
        store.set_keywords(uid, keywords)
        print(f"已创建本地用户 id={uid}")
    user = store.active_users()[0]
    store.set_keywords(user["id"], keywords)

    print(f"扫描关键词: {store.get_keywords(user['id'])}")
    client = McdMcpClient(user["mcp_token"], os.environ.get("MCD_MCP_URL",
                                                          "https://mcp.mcd.cn"))
    res = scan_user(client, store.get_keywords(user["id"]))
    if res.error:
        print("扫描失败:", res.error)
        return 1
    print(f"拉取到可领券 {res.total_coupons} 条、活动 {res.total_campaigns} 条")

    # 注意：filter_new 必须只调用一次（先取出结果再判断），
    # 否则列表推导每轮迭代都会重复写库，后续命中会被误判为已推送
    new_keys = set(store.filter_new(user["id"], [x.key for x in res.hits]))
    seen_titles: set[tuple[str, str]] = set()
    new_hits = []
    for h in res.hits:
        sig = (h.kind, h.title)
        if h.key in new_keys and sig not in seen_titles:
            seen_titles.add(sig)
            new_hits.append(h)
    if not new_hits:
        print("暂无新命中（或命中条目此前已推送过）。")
        return 0

    text = format_push_text(new_hits)
    print("---- 推送内容 ----")
    print(text)
    print("------------------")
    notifier = default_notifier()
    ok = notifier.send(user, text)
    print("推送结果:", "成功" if ok else "未配置推送通道或发送失败（本次仅打印）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
