"""常驻守护进程：微信通道 + 定时监控一体化。

流程：
  1. 加载 claw_credentials.json（先跑 scripts/bind_wechat.py）
  2. 启动微信长轮询通道，用户发消息即自动绑定
  3. 每 SCAN_INTERVAL_MINUTES 分钟扫描所有活跃用户的关注关键词
  4. 新命中 → 推送微信

用法：.venv/Scripts/python scripts/daemon.py
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from maimai_watchdog.channel_claw import ClawGateway  # noqa: E402
from maimai_watchdog.chat import handle_message  # noqa: E402
from maimai_watchdog.config import DEFAULT_DB, load_env  # noqa: E402
from maimai_watchdog.db import Store  # noqa: E402
from maimai_watchdog.mcp_client import McdMcpClient  # noqa: E402
from maimai_watchdog.monitor import format_push_text, scan_user  # noqa: E402

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("maimai.daemon")

CRED_FILE = os.path.join(os.path.dirname(__file__), "..", "claw_credentials.json")
SCAN_INTERVAL = int(os.environ.get("SCAN_INTERVAL_MINUTES", "30")) * 60


async def scan_all(store: Store, gateway: ClawGateway) -> None:
    for user in store.active_users():
        keywords = store.get_keywords(user["id"])
        if not keywords:
            continue
        client = McdMcpClient(user["mcp_token"], os.environ.get("MCD_MCP_URL",
                                                                "https://mcp.mcd.cn"))
        res = await asyncio.to_thread(scan_user, client, keywords)
        if res.error:
            logger.warning("user %s scan error: %s", user["id"], res.error)
            continue
        new_keys = set(store.filter_new(user["id"], [h.key for h in res.hits]))
        seen, new_hits = set(), []
        for h in res.hits:
            if h.key in new_keys and (h.kind, h.title) not in seen:
                seen.add((h.kind, h.title))
                new_hits.append(h)
        if not new_hits:
            logger.info("user %s: no new hits", user["id"])
            continue
        text = format_push_text(new_hits)
        sid = user.get("claw_account_id")
        if sid:
            imgs = [h.image_url for h in new_hits if h.image_url]
            ok = await gateway.push_parts(sid, text, imgs)
            logger.info("user %s pushed=%s (%d hits)", user["id"], ok, len(new_hits))
        else:
            logger.info("user %s has %d hits but no claw binding", user["id"], len(new_hits))


async def main() -> int:
    load_env()
    store = Store(os.environ.get("WATCHDOG_DB", DEFAULT_DB))

    async def on_message(sid: str, text: str):
        row = store.conn.execute(
            "SELECT id FROM users WHERE claw_account_id=?", (sid,)).fetchone()
        if not row:
            # 未绑定：优先按 8 位绑定码关联网站注册账号
            import re
            # 防爆破：同一微信 subject 连续猜错即锁定，避免穷举绑定码空间
            left = store.bind_lock_remaining(sid)
            if left > 0:
                logger.warning("bind locked for subject %s (%ds left)", sid[:12], left)
                yield f"绑定码输错太多次啦，为了安全我先锁 {left // 60 + 1} 分钟，稍后再试～"
                return
            m = re.search(r"(?<!\d)(\d{8})(?!\d)", text or "")
            if m:
                uid = store.consume_binding_code(m.group(1))
                if uid:
                    store.clear_bind_fails(sid)
                    store.bind_claw(uid, sid)
                    store.log_event("bind", uid)
                    logger.info("bound subject %s -> user %s (binding code)", sid[:12], uid)
                    kws = store.get_keywords(uid)
                    yield "AVA|03_fries_hand"
                    yield ("🍟 嗨～我是百变麦小薯，你的麦门蹲点搭子！绑定成功啦！\n\n"
                           "先认识一下吧：希望我怎么称呼你呀？\n"
                           "还有还有，你最爱吃麦当劳的什么？说给我听，"
                           "我会帮你死死盯着它的优惠，一冒头就喊你！")
                    yield ("对了说个悄悄话🤫 你在外面看我的头像是不是红红的一块，"
                           "有点丑丑的……那是系统默认的啦。你可以在我的主页里"
                           "手动换成你喜欢的样子——比如我刚送你的那张薯条造型卡，"
                           "换成它，每次看到心情都会好一点哦～")
                    yield ("还有个绝活告诉你🎭 我可是「百变」的——说一句「换成元气点的语气」，"
                           "我的说话方式就跟着换，想演你喜欢的动漫角色也行（只学说话风格，"
                           "不照搬原台词），元气、温柔、幽默都由你定～"
                           "看腻了发「变回来」我就回来啦！")
                    if kws:
                        yield f"我现在正在帮你盯：{'、'.join(kws)}，有新优惠立刻喊你～"
                    return
                fails = store.record_bind_fail(sid)
                logger.warning("bind fail #%d for subject %s", fails, sid[:12])
                if fails >= store.BIND_MAX_FAILS:
                    yield "绑定码连续输错太多次，为安全起见先锁 30 分钟，回头再来试吧～"
                else:
                    yield "这个绑定码失效了（已用或超 30 分钟），回网站重新获取一个吧～"
                return
            yield ("👋 你好呀，我是麦小薯！\n"
                   "请先在网站上注册并把 8 位绑定码发给我，"
                   "我就能帮你盯麦当劳优惠啦～")
            return
        user = store.get_user(row[0])
        async for reply in handle_message(store, user, text):
            yield reply

    gateway = ClawGateway(os.path.abspath(CRED_FILE), on_message=on_message)
    await gateway.start()
    logger.info("麦麦管家已上线，扫描间隔 %d 分钟", SCAN_INTERVAL // 60)

    # 凭证文件热重载：网站端新用户扫码加入后自动接入
    async def creds_watch_loop():
        try:
            mtime = os.path.getmtime(CRED_FILE)
        except OSError:
            mtime = 0
        while True:
            await asyncio.sleep(30)
            try:
                m2 = os.path.getmtime(CRED_FILE)
            except OSError:
                continue
            if m2 != mtime:
                mtime = m2
                try:
                    await gateway.reload_accounts()
                    logger.info("weixin accounts reloaded after creds change")
                except Exception:  # noqa: BLE001
                    logger.exception("reload accounts failed")

    async def scan_loop():
        while True:
            try:
                await scan_all(store, gateway)
            except Exception:  # noqa: BLE001
                logger.exception("scan cycle failed")
            await asyncio.sleep(SCAN_INTERVAL)

    await asyncio.gather(creds_watch_loop(), scan_loop())
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:
        print("\n已停止。")
