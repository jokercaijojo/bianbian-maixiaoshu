"""监控核心：拉取优惠/活动 → 关键词匹配 → 去重 → 产出待推送命中。

MCP 返回的券/活动都带官方图片 URL（<img src="...">），一并解析出来，
推送时文字+配图一起发到微信，接近小程序原生的视觉体验。
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

from .mcp_client import McdMcpClient

_RE_COUPON_TITLE = re.compile(r"优惠券标题[*]*[：:]\s*(.+)")
_RE_CAMPAIGN_TITLE = re.compile(r"活动标题[*]*[：:]\s*(.+)")
_RE_IMG = re.compile(r'<img\s+src="([^"]+)"')


def _clean(title: str) -> str:
    return title.strip().rstrip("\\").strip()


def _extract_items(text: str, title_re: re.Pattern) -> list[dict]:
    """按标题切块，提取每个条目的标题与首张配图。"""
    matches = list(title_re.finditer(text))
    items = []
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        block = text[m.start():end]
        img = _RE_IMG.search(block)
        items.append({"title": _clean(m.group(1)),
                      "image_url": img.group(1) if img else ""})
    return items


@dataclass
class Hit:
    kind: str          # coupon | campaign
    title: str
    matched_keyword: str
    image_url: str = ""

    @property
    def key(self) -> str:
        raw = f"{self.kind}|{self.title}".encode()
        return hashlib.sha1(raw).hexdigest()[:16]


@dataclass
class ScanResult:
    hits: list[Hit] = field(default_factory=list)
    total_coupons: int = 0
    total_campaigns: int = 0
    error: str = ""


def _match(items: list[dict], kind: str, keywords: list[str]) -> list[Hit]:
    hits = []
    for it in items:
        if not it["title"]:
            continue
        for kw in keywords:
            if kw and kw in it["title"]:
                hits.append(Hit(kind=kind, title=it["title"],
                                matched_keyword=kw, image_url=it["image_url"]))
                break
    return hits


def scan_user(client: McdMcpClient, keywords: list[str]) -> ScanResult:
    """对单个用户执行一次扫描（不含去重）。"""
    res = ScanResult()
    try:
        coupons_text = client.available_coupons()
        coupon_items = _extract_items(coupons_text, _RE_COUPON_TITLE)
        res.total_coupons = len(coupon_items)
        res.hits += _match(coupon_items, "coupon", keywords)

        cal_text = client.campaign_calendar()
        camp_items = _extract_items(cal_text, _RE_CAMPAIGN_TITLE)
        res.total_campaigns = len(camp_items)
        res.hits += _match(camp_items, "campaign", keywords)
    except Exception as e:  # noqa: BLE001 - 监控场景不能让单次失败炸掉调度
        res.error = str(e)
    return res


def format_push_text(hits: list[Hit]) -> str:
    """麦小薯人设的报喜文案：券是"可代领"，活动是"要去 App 动手"。"""
    coupons = [h for h in hits if h.kind == "coupon"]
    campaigns = [h for h in hits if h.kind == "campaign"]
    lines = ["蹲到了蹲到了！🍟", ""]
    for h in coupons:
        lines.append(f"🎟️ 「{h.title}」——可以直接帮你领进卡包！")
    for h in campaigns:
        lines.append(f"🔥 「{h.title}」——官方活动，这个要你去 App 里参与")
    lines.append("")
    if coupons:
        lines.append("回「领券」我立马把券塞进你卡包，手慢无！")
    else:
        lines.append("打开麦当劳 App / 小程序就能冲，去晚了我可不背锅🐔")
    return "\n".join(lines)
