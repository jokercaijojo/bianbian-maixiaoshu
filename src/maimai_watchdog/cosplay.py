"""变身皮肤：从萌娘百科抓角色资料，GLM 蒸馏成"人设卡"。

流程（对应 chat.py 的「变身 X」指令）：
  1. search_candidates(query)  → opensearch 拿候选条目（消歧用）
  2. fetch_extract(title)      → 拉条目正文（含性格/经历板块）
  3. distill_persona(title, extract) → GLM 浓缩成 200 字人设卡

数据源：moegirl.icu（萌娘百科镜像，主站 zh.moegirl.org.cn 国内直连超时）。
"""
from __future__ import annotations

import json
import logging
import os
import urllib.parse
import urllib.request

logger = logging.getLogger("maimai.cosplay")

_API = "https://moegirl.icu/api.php"
_UA = {"User-Agent": "Mozilla/5.0 (maimai-watchdog; cosplay lookup)"}
_MAX_EXTRACT = 4000  # 正文截断，控制蒸馏成本


def _get(params: dict) -> dict:
    url = _API + "?" + urllib.parse.urlencode({**params, "format": "json"})
    req = urllib.request.Request(url, headers=_UA)
    return json.loads(urllib.request.urlopen(req, timeout=15).read().decode())


def search_candidates(query: str, limit: int = 8) -> list[str]:
    """按名字全文搜索候选条目（比 opensearch 覆盖更全，能命中全名条目）。"""
    try:
        r = _get({"action": "query", "list": "search",
                  "srsearch": query, "srlimit": limit})
        return [it["title"] for it in r.get("query", {}).get("search", [])]
    except Exception:  # noqa: BLE001
        logger.exception("moegirl search failed: %s", query)
        return []


def is_disambiguation(extract: str) -> bool:
    """判断正文是否为人名消歧页（如"明日香可以是指……"）。"""
    head = extract[:300]
    return ("可以是指" in head) or ("消歧义" in head) or ("可以指" in head)


def fetch_extract(title: str) -> str:
    """拉条目纯文本正文（跟随重定向），失败返回空串。"""
    try:
        r = _get({"action": "query", "prop": "extracts", "explaintext": 1,
                  "exsectionformat": "plain", "titles": title, "redirects": 1})
        for p in r.get("query", {}).get("pages", {}).values():
            return (p.get("extract") or "")[:_MAX_EXTRACT]
    except Exception:  # noqa: BLE001
        logger.exception("moegirl extract failed: %s", title)
    return ""


_DISTILL_PROMPT = """把下面的百科资料浓缩成一张"角色扮演人设卡"，供聊天机器人模仿该角色使用。
要求：
1. 开头一行：角色全名（《作品名》）
2. 性格 3 条以内，每条 10 字内
3. 口头禅/经典口癖 3 条以内（给出原话示例）
4. 说话风格要点 2 条（语气、自称、对他人的称呼）
5. 总字数 200 字以内，直接输出卡片内容，不要任何解释

角色条目：%s
百科资料：
%s"""


def distill_persona(title: str, extract: str) -> str:
    """用 GLM 把百科正文蒸馏成人设卡，失败返回空串。"""
    key = os.environ.get("ZHIPU_API_KEY", "")
    if not key or not extract:
        return ""
    payload = {
        "model": os.environ.get("ZHIPU_MODEL", "glm-5.3-flash"),
        "messages": [{"role": "user", "content": _DISTILL_PROMPT % (title, extract)}],
        "max_tokens": 2048, "temperature": 0.3,
        "thinking": {"type": "enabled", "effort": "low"},
    }
    req = urllib.request.Request(
        "https://open.bigmodel.cn/api/paas/v4/chat/completions",
        data=json.dumps(payload).encode(), method="POST")
    req.add_header("Authorization", f"Bearer {key}")
    req.add_header("Content-Type", "application/json")
    try:
        r = json.loads(urllib.request.urlopen(req, timeout=60).read().decode())
        return (r["choices"][0]["message"].get("content") or "").strip()
    except Exception:  # noqa: BLE001
        logger.exception("persona distill failed")
        return ""
