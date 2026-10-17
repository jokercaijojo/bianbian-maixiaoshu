"""抽卡养成：卡池目录、连续打卡解锁规则。

卡图文件放在 static/img/cards/，卡 ID 即文件名（不含扩展名）。
后续美术新配饰落地后，往 CARD_CATALOG 追加条目 + 在 STREAK_TIERS 挂档即可。
"""
from __future__ import annotations

import os

CARDS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "static", "img", "cards")

# 卡池目录：img 相对 /static 路径，line 是解锁时麦小薯的对白
CARD_CATALOG: dict[str, dict] = {
    "03_fries_hand": {
        "name": "薯条·手捧", "rarity": "R",
        "img": "/static/img/cards/03_fries_hand.png",
        "line": "你好呀！我是麦小薯🍟 这是我的第一张造型卡～以后天天帮你蹲优惠！",
    },
    "01_fish_beret": {
        "name": "麦香鱼·贝雷帽", "rarity": "SR",
        "img": "/static/img/cards/01_fish_beret.png",
        "line": "噔噔——麦香鱼贝雷帽！听说麦香鱼是最被低估的宝藏汉堡，有品位的才懂🐟",
    },
    "02_bigmac_backpack": {
        "name": "巨无霸·双肩包", "rarity": "SR",
        "img": "/static/img/cards/02_bigmac_backpack.png",
        "line": "背着巨无霸去上学！两层牛肉饼的满足感，就像又来找我的你🍔",
    },
    "04_chicken_bag": {
        "name": "麦乐鸡·小挎包", "rarity": "SR",
        "img": "/static/img/cards/04_chicken_bag.png",
        "line": "麦乐鸡小挎包 get！蘸甜酸酱还是蒜蓉辣酱？这个问题我们下次可以聊一轮😋",
    },
}

# 新手第一张卡（网站上人人可抽，注册后自动入库）
FIRST_CARD_ID = "03_fries_hand"

# 累计打卡天数 → 卡 ID（断签不清零；美术配饰补齐后，6/8 天档挂新卡）
STREAK_TIERS: dict[int, str] = {
    1: "01_fish_beret",
    2: "02_bigmac_backpack",
    4: "04_chicken_bag",
    # 6: 待新配饰, 8: 待新配饰
}

# 解锁下一档预告文案用的档位表
TIER_DAYS = sorted(STREAK_TIERS)


def card_file(card_id: str) -> str:
    return os.path.join(CARDS_DIR, f"{card_id}.png")


def avatar_thumb_file(card_id: str) -> str:
    """聊天头像缩略图（240px，由 scripts/make_thumbs.py 预生成）；
    缩略图缺失时回退原图。"""
    thumb = os.path.join(CARDS_DIR, "thumbs", f"{card_id}.png")
    return thumb if os.path.exists(thumb) else card_file(card_id)


def card_exists(card_id: str) -> bool:
    return os.path.exists(card_file(card_id))


def next_tier(streak: int) -> int | None:
    """当前连续天数之后，下一个有卡可解锁的打卡档位。"""
    for d in TIER_DAYS:
        if d > streak:
            return d
    return None


def check_unlocks(store, user_id: int, days: int) -> list[dict]:
    """按累计打卡天数发放入库未有的卡，返回本次新解锁的卡列表。"""
    newly = []
    for need, card_id in STREAK_TIERS.items():
        if days >= need and card_exists(card_id):
            if store.unlock_card(user_id, card_id, source=f"total_{need}"):
                card = dict(CARD_CATALOG[card_id])
                card["id"] = card_id
                card["tier_days"] = need
                newly.append(card)
    return newly
