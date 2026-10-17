"""模拟微信消息走 chat.handle_message 全链路（含 agent_chat）。"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from dotenv import load_dotenv  # noqa: E402
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from maimai_watchdog.chat import handle_message  # noqa: E402
from maimai_watchdog.db import Store  # noqa: E402

store = Store(os.path.join(os.path.dirname(__file__), "..", "watchdog.db"))
users = store.active_users()
if not users:
    sys.exit("数据库里没有用户")
user = users[0]
print(f"测试用户: id={user['id']} 配额={user.get('chat_quota_daily')}")

QUESTION = sys.argv[1] if len(sys.argv) > 1 else "麦香鱼堡现在多少钱？我在中山市某商圈附近"

async def main():
    async for reply in handle_message(store, user, QUESTION):
        print("---回复---")
        print(reply)

asyncio.run(main())
