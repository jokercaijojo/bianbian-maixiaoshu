"""说话风格皮肤：从公开百科抓角色条目，GLM 蒸馏成"风格卡"。

流程（对应 chat.py 的「变身 X」指令）：
  1. search_candidates(query)  → opensearch 拿候选条目（消歧用）
  2. fetch_extract(title)      → 拉条目正文（含性格/经历板块）
  3. distill_persona(title, extract) → GLM 浓缩成 200 字风格卡

只提炼语气、自称、称呼等**风格特征**，明确不抄录原作台词——避免复刻第三方作品内容。
数据源：公开动漫百科的 MediaWiki 接口（镜像站，主站国内直连超时）。
"""
from __future__ import annotations

import json
import logging
import os
import re
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
    """判断正文是否为人名消歧页（如"某某可以是指……"）。"""
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


_DISTILL_PROMPT = """把下面的百科资料浓缩成一张"说话风格参考卡"，供聊天机器人在被点名扮演时参考语气。
要求：
1. 开头一行：角色名（可带作品名，仅作标识）
2. 性格 3 条以内，每条 10 字内
3. 说话风格要点 4 条以内（语气、自称、对他人的称呼、常用语气词）
4. 只概括风格特征，不要抄录原作台词、口头禅原话、歌词等受版权保护的内容
5. 总字数 200 字以内，直接输出卡片内容，不要任何解释

角色条目：%s
百科资料：
%s"""


_STYLE_PROMPT = """用户在跟一个麦当劳优惠助手聊天，希望她把说话的调调换成「%s」。
请写一张"说话风格卡"（纯风格，不要牵涉任何具体作品、角色、明星、真人）：
1. 开头一行：风格名
2. 性格 3 条以内，每条 10 字内
3. 说话风格要点 4 条以内（语气、自称、对他人的称呼、常用语气词）
4. 总字数 150 字以内，直接输出卡片内容，不要任何解释

用户想要的感觉：%s"""


def _glm_text(prompt: str) -> str:
    """调 GLM 出一段纯文本，失败返回空串。"""
    key = os.environ.get("ZHIPU_API_KEY", "")
    if not key:
        return ""
    payload = {
        "model": os.environ.get("ZHIPU_MODEL", "glm-5.3-flash"),
        "messages": [{"role": "user", "content": prompt}],
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


# 一整行就是一句引号台词（如 ①“你白痴啊？” / 「我可是EVA驾驶员！」）
_CARD_QUOTE_LINE = re.compile(r"^[①②③④⑤⑥⑦⑧⑨⑩\d.、)\-*\s]*[“\"「『]")


def scrub_card(card: str) -> str:
    """兜底过滤人设卡里的"原台词"。

    提示词已明令禁止复刻台词，但模型偶尔仍会带出来；这里再做一道：
    删掉含"口头禅"的整行、以及整行以引号开头的例句行。
    只留语气/自称/称呼/性格这些风格描述，不影响扮演效果。
    """
    if not card:
        return card
    kept: list[str] = []
    for line in card.splitlines():
        s = line.strip()
        if s and ("口头禅" in s or _CARD_QUOTE_LINE.match(s)):
            continue
        kept.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()


def distill_persona(title: str, extract: str) -> str:
    """用 GLM 把百科正文蒸馏成风格卡，失败返回空串。"""
    if not extract:
        return ""
    return scrub_card(_glm_text(_DISTILL_PROMPT % (title, extract)))


def distill_style(name: str) -> str:
    """百科查不到时，直接用 GLM 造一张纯风格卡（不绑定任何作品或角色）。

    这样「换成元气点的语气」这种风格词也能生效，不依赖百科是否有对应条目。
    """
    name = (name or "").strip()
    if not name:
        return ""
    return scrub_card(_glm_text(_STYLE_PROMPT % (name, name)))


# 常见"说话风格"词：用户说这些时不必查百科（百科是全文搜索，
# "元气"会搜出"元气骑士"一堆游戏角色，反而把用户带偏）。
_STYLE_WORDS = {
    "元气", "活泼", "开朗", "阳光", "温柔", "知性", "沉稳", "冷静", "高冷",
    "严肃", "正经", "幽默", "搞笑", "沙雕", "可爱", "呆萌", "天然", "撒娇",
    "傲娇", "毒舌", "腹黑", "御姐", "大叔", "学霸", "暖心", "文艺", "话痨",
    "元气少女", "元气姐姐", "温柔姐姐", "温柔学长", "活泼少女", "沉稳大叔",
    "高冷御姐", "可爱妹妹", "知性姐姐", "搞笑担当",
}


def looks_like_style(query: str) -> bool:
    """判断用户说的是"说话风格"而不是某位具体角色。"""
    q = (query or "").strip()
    if not q:
        return False
    if q in _STYLE_WORDS:
        return True
    return any(k in q for k in ("语气", "风格", "说话方式", "调调", "口吻"))
