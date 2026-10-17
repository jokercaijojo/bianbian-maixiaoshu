"""记忆系统 + 麦门人格 全链路测试（命令行模拟微信对话）。"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from dotenv import load_dotenv  # noqa: E402
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from maimai_watchdog.chat import handle_message  # noqa: E402
from maimai_watchdog.db import Store  # noqa: E402
from maimai_watchdog.persona import sample_memes  # noqa: E402

store = Store(os.path.join(os.path.dirname(__file__), "..", "watchdog.db"))
user = store.active_users()[0]
print(f"用户 id={user['id']}")

print("\n=== 梗抽样检查 ===")
print("减肥场景:", sample_memes("我想减肥"))
print("周四场景:", sample_memes("今天星期四"))

ROUNDS = [
    "我叫阿福，住中山轻轨站附近，最爱麦香鱼，不过最近在减肥",
    "你记得我什么",
    "那你说我现在适合吃点啥",
    "忘掉 减肥",
    "你记得我什么",
]

async def main():
    for q in ROUNDS:
        print(f"\n>>> 我: {q}")
        async for reply in handle_message(store, user, q):
            print(f"<<< 薯: {reply}")

asyncio.run(main())
