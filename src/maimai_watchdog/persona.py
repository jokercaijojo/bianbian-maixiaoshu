"""人格与记忆：SOUL.md / MEMES.md 加载，长期记忆抽取与注入。

设计：
- SOUL.md  = 人格与说话风格（全局一份，进 git，改文件即改性格）
- MEMES.md = 按场景分类的梗库，对话时随机抽样作为风格示例
- 记忆     = SQLite user_facts，每轮对话后正则预筛 → GLM 抽取 JSON → upsert
"""
from __future__ import annotations

import json
import logging
import os
import random
import re

from .db import Store

logger = logging.getLogger("maimai.persona")

_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
SOUL_FILE = os.path.join(_ROOT, "SOUL.md")
MEMES_FILE = os.path.join(_ROOT, "MEMES.md")

# ---- 文件加载（带缓存）----
_cache: dict[str, tuple[float, str]] = {}


def _load(path: str) -> str:
    try:
        mtime = os.path.getmtime(path)
        if path in _cache and _cache[path][0] == mtime:
            return _cache[path][1]
        with open(path, encoding="utf-8") as f:
            text = f.read()
        _cache[path] = (mtime, text)
        return text
    except OSError:
        return ""


def soul_text() -> str:
    return _load(SOUL_FILE)


def _meme_sections() -> dict[str, list[str]]:
    """把 MEMES.md 解析成 {场景: [梗...]}。兼容解析失败时返回空。"""
    text = _load(MEMES_FILE)
    sections: dict[str, list[str]] = {}
    current = "闲聊捧场"
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("## "):
            current = line.lstrip("# ").split("（")[0].strip()
            sections.setdefault(current, [])
        elif line.startswith("- ") and current in sections:
            sections[current].append(line[2:].strip())
    return sections


_SCENE_KEYWORDS = {
    "蹲点报喜": ("蹲到", "优惠", "有券", "活动"),
    "吃法达人": ("怎么点", "怎么吃", "点单", "推荐", "搭配", "好吃"),
    "减脂话题": ("减肥", "减脂", "胖", "热量", "大卡", "卡路里", "健身"),
    "疯狂星期四": ("星期四", "周四", "疯狂", "v我50", "V我50"),
}


def sample_memes(user_text: str = "", n: int = 3) -> list[str]:
    """按用户话语命中的场景抽梗；无命中就从闲聊池抽。"""
    sections = _meme_sections()
    pool: list[str] = []
    for scene, keys in _SCENE_KEYWORDS.items():
        if any(k in user_text for k in keys):
            pool += sections.get(scene, [])
    if not pool:
        pool = sections.get("闲聊捧场", []) + sections.get("吃法达人", [])
    if not pool:
        return []
    return random.sample(pool, min(n, len(pool)))


# ---- 长期记忆 ----
_MEMORY_TRIGGER = re.compile(
    r"(我叫|叫我|我是|我在|我住|我家|我爱|我喜欢|我喜欢吃|我不吃|我讨厌|我忌口|"
    r"我过敏|减肥|减脂|健身|不吃辣|不要酸|每次|经常|一般|平时|工资|生日|"
    r"记住|记得我)")

_EXTRACT_PROMPT = """从下面的用户话语中，提取值得长期记住的关于用户的事实。
只提取：称呼/位置/饮食偏好/忌口/生活习惯/明确说"记住"的事。
不要提取：一次性问题、价格查询、客套话。
严格输出 JSON：{"facts":[{"key":"简短类别","value":"事实内容"}]}，没有就 {"facts":[]}。

用户说：%s
你的回复：%s"""


def maybe_extract_memories(store: Store, user_id: int,
                           user_text: str, bot_reply: str) -> None:
    """正则预筛命中才调 GLM 抽取，控制成本。失败静默。"""
    if not _MEMORY_TRIGGER.search(user_text):
        return
    if not os.environ.get("ZHIPU_API_KEY"):
        return
    try:
        from .agent import _glm
        msg = _glm([{"role": "user", "content":
                     _EXTRACT_PROMPT % (user_text[:300], bot_reply[:300])}],
                   tools=False, max_tokens=256, temperature=0.1)
        content = (msg.get("content") or "").strip()
        m = re.search(r"\{.*\}", content, re.S)
        if not m:
            return
        data = json.loads(m.group(0))
        for f in data.get("facts", [])[:5]:
            k, v = str(f.get("key", "")).strip(), str(f.get("value", "")).strip()
            if k and v:
                store.upsert_fact(user_id, k, v)
                logger.info("user %s memory +[%s]=%s", user_id, k, v[:30])
    except Exception:  # noqa: BLE001
        logger.exception("memory extract failed")


def memory_block(store: Store, user_id: int) -> str:
    """生成注入 system prompt 的记忆段落。"""
    facts = store.get_facts(user_id)
    if not facts:
        return ""
    lines = ["", "你记得关于 TA 的事（自然地融入对话，别生硬背诵）："]
    lines += [f"· {k}：{v}" for k, v in facts]
    return "\n".join(lines)
