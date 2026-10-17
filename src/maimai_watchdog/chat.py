"""微信对话指令解析：让用户在 ClawBot 对话框里管理监控。

设计原则（2026-10-17 重构）：
  1. 一句话可以有好几件事。"叫我小明，然后变作月野兔"必须两件都办。
     → 先按连接词/标点切成子句，逐句处理。
  2. 只把"标准句式、零歧义"的指令留在脚本快通道里，命中就秒回、不花 token。
     说法稍微绕一点的一律交给 GLM —— 它能看到自己的全部能力
     （变身 / 换头像 / 记称呼 都已注册成它可调用的工具）。
  3. 脚本永远不"藏"能力：模型知道自己会什么，就不会再说"我做不到"。

支持的指令（快通道）：
  叫我 X / 以后喊我 X  → 记住称呼
  变身 X / 变作 X      → 动漫角色扮演
  关注/添加/监控 X     → 添加关键词
  取消/删除 X          → 移除关键词
  清单 / 我的关注      → 查看当前关注列表
  查券 / 看看 / 现在   → 立刻扫描一次并汇报（含未命中说明）
  领券 / 一键领券      → 自动领取麦麦省全部可领券
  帮助 / help         → 指令说明
  其他                → 交给 GLM（带完整能力清单 + 最近几轮上下文）
"""
from __future__ import annotations

import logging
import re
from collections.abc import AsyncIterator, Iterator

from .db import Store
from .mcp_client import McdMcpClient
from .monitor import format_push_text, scan_user

logger = logging.getLogger("maimai.chat")

HELP = (
    "🍟 麦小薯为您服务：\n"
    "· 关注 麦香鱼 —— 我替你盯它\n"
    "· 取消 麦香鱼 —— 不盯它了\n"
    "· 清单 —— 看看我在帮你盯啥\n"
    "· 查券 —— 我立马去后厨瞅一圈\n"
    "· 领券 —— 全给你塞进卡包\n"
    "· 换头像 —— 换个造型陪你聊天\n"
    "· 变身 明日香 —— 我秒变动漫角色\n"
    "· 随便聊聊 —— 查价/找店/侃大山都行"
)


def _normalize_product_name(kw: str) -> str:
    """语音同音字纠错：常见误转映射到真实产品名（指令层用，闲聊层由 GLM 兜底）。"""
    _ALIAS = {
        "卖香芋": "麦香鱼", "麦香芋": "麦香鱼", "卖香鱼": "麦香鱼", "麦香于": "麦香鱼",
        "买香鱼": "麦香鱼", "麦香鱼宝": "麦香鱼", "麦香鱼堡": "麦香鱼",
        "买辣鸡": "麦辣鸡", "麦拉鸡": "麦辣鸡", "巨无爸": "巨无霸", "巨无罢": "巨无霸",
        "板烧鸡": "板烧鸡腿堡", "麦乐基": "麦乐鸡", "卖旋风": "麦旋风",
        "麦炫风": "麦旋风", "鼠条": "薯条", "数条": "薯条",
    }
    return _ALIAS.get(kw, kw)


def _add_keyword(store: Store, user_id: int, kw: str) -> str:
    kw = _normalize_product_name(kw)
    kws = store.get_keywords(user_id)
    if kw in kws:
        return f"「{kw}」已经在你的关注清单里啦。"
    store.set_keywords(user_id, kws + [kw])
    return f"✅ 已关注「{kw}」，有它的优惠我立刻喊你。"


def _remove_keyword(store: Store, user_id: int, kw: str) -> str:
    kw = _normalize_product_name(kw)
    kws = store.get_keywords(user_id)
    if kw not in kws:
        return f"你的清单里本来就没有「{kw}」。"
    store.set_keywords(user_id, [k for k in kws if k != kw])
    return f"🗑️ 已取消关注「{kw}」。"


# ==================== 一、一句话多件事：切句 ====================

_CLAUSE_SEP = re.compile(
    r"(?:然后|接着|顺便|另外|还要|再有|同时|之后|最后|以及|再帮我|再给我|"
    r"[，,；;。！!？?\n])"
)


def _split_clauses(text: str) -> list[str]:
    """把「叫我小明，然后变作月野兔」切成两个独立子句。

    只按连接词和标点机械切分，**不做语义理解**——理解不了的部分原样留给大模型。
    切不出东西（单句/空串）时返回空列表，调用方用原文处理。
    """
    out: list[str] = []
    for p in _CLAUSE_SEP.split(text or ""):
        p = p.strip(" \u3000\t")
        if len(p) >= 2:
            out.append(p)
    return out


# ==================== 二、称呼 & 变身的意图抽取 ====================

_NICK_RE = re.compile(
    r"(?:以后|今后|就)?(?:叫我|喊我|称呼我|管我叫)\s*"
    r"([^\s，。,.！!？?、]{1,12}?)"
    r"(?:就可以了|就行|就好|好了|吧|啊|呀|哦|嘛|哈|啦|了|$)")
_NICK_BAD = ("什么", "怎么", "啥", "谁", "哪", "如何", "起来", "一下", "过来")

_COSPLAY_RE = re.compile(
    r"(?:变身为|变作|变身|变成|变为|扮演成|扮演|扮成|饰演|化身成|化身|"
    r"当一下|装成|cosplay|cos)"
    r"\s*(?:成|为|作|一下)?\s*"
    r"([^\s，。！？!?、；;：:]{1,20})", re.I)
_COSPLAY_TAIL = re.compile(
    r"(?:吧|呀|呗|哦|嘛|啊|呢|哈|啦|喔|噢|了|看看|试试|好吗|好不好)+$")
_COSPLAY_STOP = {"", "回来", "回去", "自己", "原样", "一下", "什么", "啥", "个人", "谁"}


def _nickname_target(text: str) -> str | None:
    """从「你就叫我超帅的阿福就可以了」里抠出称呼「超帅的阿福」。"""
    m = _NICK_RE.search(text or "")
    if not m:
        return None
    nick = m.group(1).strip()
    if not nick or any(b in nick for b in _NICK_BAD):
        return None
    return nick


def _cosplay_target(text: str) -> str | None:
    """从「你给我变作月野兔吧」里抠出角色名「月野兔」。

    覆盖 变身/变作/变成/变身为/扮演/扮成/饰演/化身/当一下/装成/cos 等说法，
    并顺手剥掉尾部的「吧/呀/哦」等语气词（旧版会把「为月野兔」「月野兔吧」整串喂给百科）。
    """
    m = _COSPLAY_RE.search(text or "")
    if not m:
        return None
    who = _COSPLAY_TAIL.sub("", m.group(1).strip()).strip()
    if who in _COSPLAY_STOP:
        return None
    return who


# ==================== 三、脚本快通道 ====================

def _scan_now(store: Store, user: dict) -> Iterator[str]:
    uid = user["id"]
    kws = store.get_keywords(uid)
    if not kws:
        yield "你还没设置关注关键词，发「关注 麦香鱼」先～"
        return
    yield "🔍 收到，这就去帮你蹲一圈……"
    client = McdMcpClient(user["mcp_token"])
    res = scan_user(client, kws)
    if res.error:
        yield f"😵 扫描失败了：{res.error}"
        return
    seen, hits = set(), []
    for h in res.hits:
        if (h.kind, h.title) not in seen:
            seen.add((h.kind, h.title))
            hits.append(h)
    if hits:
        yield format_push_text(hits)
    else:
        yield (f"蹲完了：当前可领券 {res.total_coupons} 张、本月活动 "
               f"{res.total_campaigns} 个，但你关注的"
               f"「{'、'.join(kws)}」暂无优惠。继续帮你盯着！")


def _claim_coupons(user: dict) -> Iterator[str]:
    client = McdMcpClient(user["mcp_token"])
    try:
        result = client.auto_bind_coupons()
        yield "🎟️ 一键领券结果：\n" + (result[:800] or "已提交领取。")
    except Exception as e:  # noqa: BLE001
        yield f"领券失败：{e}"


def _script_rule(store: Store, user: dict, text: str) -> list[str] | None:
    """跑一遍脚本快通道。

    命中 → 返回回复列表（秒回，零 token）；
    未命中 → 返回 None，表示"这句话脚本不敢自作主张"，交给 GLM。
    """
    uid = user["id"]

    # ---- 称呼：秒记，不等模型 ----
    nick = _nickname_target(text)
    if nick:
        store.upsert_fact(uid, "称呼", nick)
        return [f"📝 记住啦，以后我就叫你「{nick}」～"]

    # ---- 变身：短句走快通道，长句留给模型理解（说法千变万化正则追不上）----
    who = _cosplay_target(text)
    if who and len(text) <= 20:
        return list(_cosplay_start(store, uid, who))

    # ---- 变身流程待确认：对 / 换一个 / 序号 ----
    if user.get("persona_pending"):
        if text in ("对", "是", "确认", "对的", "是的", "对！", "嗯"):
            return list(_cosplay_confirm(store, uid, user["persona_pending"]))
        if text in ("换一个", "不是", "不对"):
            return list(_cosplay_next(store, uid, user["persona_pending"]))
        num = _pick_num(text)
        if num is not None:
            return list(_cosplay_pick(store, uid, user["persona_pending"], num))
        # 其他消息：先挂着候选，正常往下走

    if text in ("变回来", "做回自己", "卸下皮肤", "恢复原样"):
        store.set_persona(uid, "")
        return ["变回来啦～我是麦小薯，你的麦门蹲点搭子🍟 还是做自己最自在！"]

    # ---- 关键词 ----
    for prefix in ("关注", "添加", "监控"):
        if text.startswith(prefix) and len(text) > len(prefix):
            kw = text[len(prefix):].strip()
            if kw:
                return [_add_keyword(store, uid, kw)]

    for prefix in ("取消", "删除"):
        if text.startswith(prefix) and len(text) > len(prefix):
            kw = text[len(prefix):].strip()
            if kw:
                return [_remove_keyword(store, uid, kw)]

    if text in ("清单", "我的关注", "关注列表"):
        kws = store.get_keywords(uid)
        return [("📋 你正在关注：\n" + "\n".join(f"· {k}" for k in kws)) if kws
                else "你的关注清单是空的，发「关注 麦香鱼」试试～"]

    if text in ("查券", "看看", "现在", "扫一下", "查一下", "查查"):
        return list(_scan_now(store, user))

    if text in ("领券", "一键领券"):
        return list(_claim_coupons(user))

    if text in ("帮助", "help", "？", "?"):
        return [HELP]

    # ---- 头像切换：指令前缀 + 自然语言（"换一下图片/别发这个图了/太烦了"等）----
    _avatar_intent = re.search(
        r"换.*(头像|图片|造型|形象)|别.*发.*(图|头像)|(头像|图片).*(烦|腻|看腻)|"
        r"不要.*(头像|图片)|取消.*头像", text)
    if text.startswith("换头像") or text.startswith("切换头像"):
        return list(_avatar_command(store, uid, text))
    if _avatar_intent:
        return list(_avatar_command(store, uid, "换头像"))

    # ---- 记忆管理 ----
    if text in ("你记得我什么", "我的记忆", "你记住我什么了"):
        facts = store.get_facts(uid, limit=50)
        if facts:
            return ["我记得这些关于你的事呀：\n" + "\n".join(
                f"· {k}：{v}" for k, v in facts) + "\n\n想让我忘掉哪条，发「忘掉 xx」就好～"]
        return ["我们还没聊出啥共同回忆捏🍟 多跟我唠唠，我会记住的～"]

    for prefix in ("忘掉", "忘记"):
        if text.startswith(prefix) and len(text) > len(prefix):
            key = text[len(prefix):].strip()
            if key:
                return [("🧠 已经忘掉啦～" if store.delete_fact(uid, key)
                         else f"我本来就不记得「{key}」呀")]

    return None


# ==================== 四、大模型兜底 ====================

# 最近几轮上下文（内存态，重启即清）。原来 agent_chat 的 history 一直是
# None，模型每轮都是初次见面，接不住"那个""再来一次"这类指代。
_HIST: dict[int, list[dict]] = {}
_HIST_MAX = 8  # 4 轮


def _hist_recall(uid: int) -> list[dict]:
    return list(_HIST.get(uid, []))


def _hist_save(uid: int, user_text: str, bot_reply: str) -> None:
    h = _HIST.setdefault(uid, [])
    h.append({"role": "user", "content": user_text[:300]})
    h.append({"role": "assistant", "content": bot_reply[:600]})
    if len(h) > _HIST_MAX:
        del h[:-_HIST_MAX]


def _run_local(store: Store, user: dict, act: dict) -> Iterator[str]:
    """执行模型主动调用的本地能力（变身 / 换头像 / 记称呼）。"""
    uid = user["id"]
    name = act.get("name", "")
    args = act.get("args") or {}
    logger.info("local action from model: %s %s", name,
                {k: str(v)[:20] for k, v in args.items()})

    if name == "cosplay-become":
        who = str(args.get("character", "")).strip()
        if who:
            for m in _cosplay_start(store, uid, who):
                yield m
        return

    if name == "switch-avatar":
        tgt = str(args.get("target", "")).strip()
        for m in _avatar_command(store, uid, f"换头像 {tgt}".strip()):
            yield m
        return

    if name == "remember-nickname":
        nick = str(args.get("nickname", "")).strip()
        if nick:
            cur = dict(store.get_facts(uid)).get("称呼", "")
            store.upsert_fact(uid, "称呼", nick)
            # 模型自己那句话里通常已经喊过一遍了，同样的话就别再播报第二次
            if cur != nick:
                yield f"📝 好，以后我就叫你「{nick}」～"
        return


def _free_chat(store: Store, user: dict, text: str) -> Iterator[str]:
    """交给 GLM：它能看到自己的全部能力（变身/换头像/记称呼都已注册成工具），自主决定调哪个。"""
    import datetime
    import os
    import random

    uid = user["id"]
    day = datetime.date.today().isoformat()
    quota = user.get("chat_quota_daily", 100)

    if not os.environ.get("ZHIPU_API_KEY"):
        yield "我还不太会聊天啦，先学会盯优惠😄\n\n" + HELP
        return
    if not store.consume_chat_quota(uid, day, quota):
        yield f"今天已经聊了 {quota} 回合啦，我要攒钱买汉堡🍔 明天再聊～"
        return

    try:
        from .agent import agent_chat
        from .persona import maybe_extract_memories, memory_block

        # LLM 链路要 10~30 秒：先发一条秒回的铺垫，避免干等。
        # 变身状态下铺垫也要贴角色（傲娇通用池），不能本体卖萌穿帮
        if user.get("persona_card"):
            yield random.choice([
                "哼，催什么催，我亲自去查还不行吗😤 等着！",
                "吵死了，我在查啦！……给我老实等着。",
                "知道啦知道啦，这种小事还要我出手？马上好！",
            ])
        else:
            yield random.choice([
                "收到！我去后厨翻翻📋 稍等我一下下～",
                "在查啦在查啦，别催我嘛🍟 马上回来～",
                "让我瞅瞅……你先喝口水，马上就好😋",
            ])

        # 把进行中的流程状态告诉模型，接住"第三个"这类上下文相关的话
        state_hint = _flow_state_hint(store, uid, user)
        images: list[str] = []
        local_acts: list[dict] = []
        reply = agent_chat(user["mcp_token"], text,
                           history=_hist_recall(uid),
                           memory_text=memory_block(store, uid) + state_hint,
                           images_out=images,
                           persona_card=user.get("persona_card", ""),
                           local_out=local_acts)
        # 聊完顺手沉淀记忆（内部有预筛，大部分轮次零成本）
        maybe_extract_memories(store, uid, text, reply)
        yield reply
        _hist_save(uid, text, reply)

        # 活动相关配图：IMGURL 哨兵，通道下载裁剪后发出（最多 2 张）
        for url in images:
            yield f"IMGURL|{url}"

        # 模型主动调用的本地能力：先把话说完，再动手，像真人一样
        for act in local_acts:
            for m in _run_local(store, user, act):
                yield m

        # 打卡：聊满 2 轮算一天，连续 1/2/4/6/8 天解锁造型卡
        rounds, just_checked = store.record_chat_round(uid, day)
        if just_checked:
            for msg in _checkin_rewards(store, uid, day):
                yield msg
    except Exception:  # noqa: BLE001
        logger.exception("free chat failed uid=%s text=%r", uid, text[:60])
        yield "😵 脑子突然转不动了，稍后再问我一次～"


# ==================== 五、消息入口 ====================

async def handle_message(store: Store, user: dict, text: str) -> AsyncIterator[str]:
    """处理一条微信消息，yield 回复文本。

    流程：切句 → 每句先过脚本快通道（命中即秒回）→ 没被脚本吃掉的句子交给 GLM。
    """
    text = (text or "").strip()
    uid = user["id"]

    # 每轮对话先发头像缩略图（AVA 哨兵，daemon 通道转成图片消息），再发文字
    _ava = store.get_avatar(uid)
    if _ava != "NONE":
        yield f"AVA|{_ava}"

    if not text:
        return

    # 长任务拦截：按**原始整句**判断（切句后每句都变短了会漏判）
    if len(text) > 400:
        yield ("哇这么长的任务我看不过来啦😵 我只是个麦当劳蹲点小吃货，"
               "写代码写论文这种大工程真的帮不了～聊点吃的倒是很在行🍟")
        return

    clauses = _split_clauses(text)

    # 单句（或切不出东西）：老路径，行为完全不变
    if len(clauses) <= 1:
        got = _script_rule(store, user, text)
        if got is not None:
            for m in got:
                yield m
            return
        for m in _free_chat(store, user, text):
            yield m
        return

    # 多子句：逐句跑快通道，没命中的攒起来一起交给模型
    fed: list[str] = []
    hit_any = False
    for c in clauses:
        got = _script_rule(store, user, c)
        if got is None:
            fed.append(c)
            continue
        hit_any = True
        for m in got:
            yield m

    if fed:
        # 部分命中 → 只喂剩下的子句；一句都没命中 → 喂原文，语义最完整
        payload = text if not hit_any else "，".join(fed)
        logger.info("clauses=%d hit_any=%s -> llm feed=%r",
                    len(clauses), hit_any, payload[:60])
        for m in _free_chat(store, user, payload):
            yield m


# ==================== 六、辅助动作 ====================

def _avatar_command(store: Store, user_id: int, text: str):
    """「换头像」指令：无参数列出已解锁造型（带打卡进度引导）；带序号/关键词则切换。"""
    from .gacha import CARD_CATALOG, next_tier
    unlocked = [cid for cid in store.get_unlocks(user_id) if cid in CARD_CATALOG]
    arg = text.replace("切换头像", "").replace("换头像", "").strip()

    # 「关掉头像」跟有没有解锁过造型无关，必须先判
    if arg in ("关", "关闭", "不发", "不要"):
        store.set_avatar(user_id, "NONE")
        yield "好嘞，以后聊天我乖乖只发文字，不甩图了🍟 想我了发「换头像」随时叫我回来～"
        return

    if not unlocked:
        yield ("你还没有解锁任何造型啦～去抽卡屋抽第一张，"
               "然后每天跟我聊两轮就算打卡一天，累计 1 / 2 / 4 / 6 / 8 天"
               "都有新造型解锁哦🍟")
        return

    if not arg:
        cur = store.get_avatar(user_id)
        days = store.total_checkin_days(user_id)
        total = len([c for c in CARD_CATALOG if c != ""])
        lines = [f"你现在解锁了 {len(unlocked)}/{total} 个造型："]
        for i, cid in enumerate(unlocked, 1):
            mark = " 👈 当前头像" if cid == cur else ""
            lines.append(f"{i}. {CARD_CATALOG[cid]['name']}{mark}")
        nxt = next_tier(days)
        if nxt:
            lines.append(f"\n你已累计打卡 {days} 天，再坚持到 {nxt} 天"
                         f"（每天聊两轮就算一天）就能解锁下一个新造型啦✨")
        else:
            lines.append("\n目前的造型你都集齐啦，新的正在画，敬请期待✨")
        lines.append("发「换头像 2」或「换头像 麦香鱼」就能让我换造型～")
        lines.append("嫌头像烦的话，发「换头像 关」我就不发图啦（随时发「换头像」换回来）")
        yield "\n".join(lines)
        return
    # 按序号或名称关键词匹配
    target = None
    if arg.isdigit() and 1 <= int(arg) <= len(unlocked):
        target = unlocked[int(arg) - 1]
    else:
        for cid in unlocked:
            if arg in CARD_CATALOG[cid]["name"] or CARD_CATALOG[cid]["name"] in arg:
                target = cid
                break
    if not target:
        yield f"没找到「{arg}」这个造型哦，发「换头像」看看你都解锁了啥～"
        return
    store.set_avatar(user_id, target)
    yield f"AVA|{target}"
    yield (f"换好啦！从现在起我就是「{CARD_CATALOG[target]['name']}」造型✨ "
           f"每次聊天我都会带着这个头像出现，让你一眼认出我～")


def _cosplay_start(store: Store, user_id: int, query: str):
    """变身第一步：搜候选 → 报全名让用户核对，防止认错角色。"""
    import json
    from .cosplay import fetch_extract, is_disambiguation, search_candidates
    yield f"🔍 我去翻翻资料，找找「{query}」是谁……"
    candidates = search_candidates(query)
    if not candidates:
        yield (f"百科里没查到「{query}」这个角色诶😅 是不是打错字啦？"
               f"换个写法试试，比如带上作品名：「变身 EVA明日香」")
        return
    title = candidates[0]
    # "带作品名"的查询（如「美少女战士月野兔」「EVA明日香」）首位常命中作品页，
    # 优先取 query 的后缀候选——那才是角色本体。
    for c in candidates:
        if len(c) < len(query) and query.endswith(c):
            title = c
            break
    extract = fetch_extract(title)
    if extract and is_disambiguation(extract):
        # 撞上人名消歧页：直接列候选让用户报序号
        store.set_persona_pending(user_id, json.dumps(
            {"title": "", "extract": "", "candidates": candidates, "idx": -1},
            ensure_ascii=False))
        lines = [f"「{query}」这名字好多人用诶，你说的是哪一位？回序号告诉我～\n"]
        for i, t in enumerate(candidates, 1):
            lines.append(f"{i}. {t}")
        yield "\n".join(lines)
        return
    if not extract:
        yield "百科服务器打了个盹，等下再发一次「变身 " + query + "」试试～"
        return
    store.set_persona_pending(user_id, json.dumps(
        {"title": title, "extract": extract, "candidates": candidates, "idx": 0},
        ensure_ascii=False))
    intro = extract.split("\n\n")[0].replace("\n", "")[:80]
    yield (f"找到啦，你说的是这位吗——\n\n"
           f"📇 {title}\n{intro}……\n\n"
           f"确认是 TA 就回「对」，我马上变身；不是就回「换一个」，"
           f"还有 {len(candidates) - 1} 个候选～")


def _cosplay_confirm(store: Store, user_id: int, pending_json: str):
    """用户确认 → GLM 蒸馏人设卡 → 生效。"""
    import json
    from .cosplay import distill_persona
    pend = json.loads(pending_json)
    yield f"收到！开始研究 {pend['title']} 的说话方式……🎭"
    card = distill_persona(pend["title"], pend["extract"])
    if not card:
        store.set_persona_pending(user_id, "")
        yield "唔，研究失败了😵 等下再试一次「变身」吧～"
        return
    store.set_persona(user_id, card)
    yield (f"✨ 变身完成！从现在起我就是：\n\n{card}\n\n"
           f"想让我变回麦小薯，随时发「变回来」～")


def _pick_num(text: str) -> int | None:
    """从"3""第三个""第3个"里提取序号，中文数字也认。"""
    _CN = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
           "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
    t = text.strip().rstrip("个位。！!？?").lstrip("第")
    if t.isdigit():
        return int(t)
    return _CN.get(t)


def _cosplay_pick(store: Store, user_id: int, pending_json: str, num: int):
    """用户报序号选定候选（消歧列表场景）。"""
    import json
    from .cosplay import fetch_extract, is_disambiguation
    pend = json.loads(pending_json)
    cands = pend.get("candidates", [])
    if not (1 <= num <= len(cands)):
        yield f"序号 {num} 超出范围啦，1~{len(cands)} 之间选一个～"
        return
    title = cands[num - 1]
    extract = fetch_extract(title)
    if not extract:
        store.set_persona_pending(user_id, "")
        yield "百科服务器打了个盹，等下再试～"
        return
    if is_disambiguation(extract):
        yield f"「{title}」还是个名字列表页诶，换一个有具体角色的序号吧～"
        return
    pend.update({"title": title, "extract": extract, "idx": num - 1})
    store.set_persona_pending(user_id, json.dumps(pend, ensure_ascii=False))
    intro = extract.split("\n\n")[0].replace("\n", "")[:80]
    yield (f"锁定候选——\n\n📇 {title}\n{intro}……\n\n"
           f"确认是 TA 就回「对」，我马上变身～")


def _cosplay_next(store: Store, user_id: int, pending_json: str):
    """换下一个候选。"""
    import json
    from .cosplay import fetch_extract
    pend = json.loads(pending_json)
    idx = pend.get("idx", 0) + 1
    cands = pend.get("candidates", [])
    if idx >= len(cands):
        store.set_persona_pending(user_id, "")
        yield "候选都看完啦，没有你要找的那个吗？试试换个关键词再「变身」一次～"
        return
    title = cands[idx]
    extract = fetch_extract(title)
    if not extract:
        store.set_persona_pending(user_id, "")
        yield "百科服务器打了个盹，等下再试～"
        return
    pend.update({"title": title, "extract": extract, "idx": idx})
    store.set_persona_pending(user_id, json.dumps(pend, ensure_ascii=False))
    intro = extract.split("\n\n")[0].replace("\n", "")[:80]
    yield (f"那这位呢——\n\n📇 {title}\n{intro}……\n\n"
           f"回「对」确认，回「换一个」继续看～")


def _flow_state_hint(store: Store, user_id: int, user: dict) -> str:
    """拼装"进行中的流程状态"，注入 GLM 上下文，让它接住流程相关的话。"""
    import json
    parts = []
    pend = user.get("persona_pending")
    if pend:
        try:
            p = json.loads(pend)
            cands = p.get("candidates", [])
            if p.get("title"):
                parts.append(f"· 变身流程进行中：已向用户展示候选角色「{p['title']}」"
                             f"等待确认；候选清单共 {len(cands)} 个：{'、'.join(cands[:8])}。"
                             f"用户回序号或「对」都与这个流程有关。")
            else:
                parts.append(f"· 变身流程进行中：已向用户展示候选清单（{'、'.join(cands[:8])}），"
                             f"用户回「第X个/序号」是在选角色，请顺着引导确认。")
        except Exception:  # noqa: BLE001
            pass
    if user.get("persona_card"):
        first = user["persona_card"].split("\n")[0][:40]
        parts.append(f"· 你当前正在扮演：{first}，全程保持这个角色语气。")
    days = store.total_checkin_days(user_id)
    if days:
        parts.append(f"· 用户已累计打卡 {days} 天。")
    return ("\n\n【当前流程状态】\n" + "\n".join(parts)) if parts else ""


def _checkin_rewards(store: Store, user_id: int, day: str):
    """达成当天打卡后的奖励播报：新解锁卡 → 三连发（文字+图+预告）。"""
    from .gacha import check_unlocks, next_tier
    days = store.total_checkin_days(user_id)
    newly = check_unlocks(store, user_id, days)
    nxt = next_tier(days)
    if not newly:
        tip = f"累计打卡 {nxt} 天就能解锁新造型" if nxt else "明天也记得来呀"
        yield f"📮 打卡成功！今天聊满两轮啦（累计 {days} 天）～{tip}🍟"
        return
    for card in newly:
        yield (f"🎉 累计打卡 {card['tier_days']} 天达成！"
               f"解锁新造型「{card['name']}」★{card['rarity']}！")
        # IMG 哨兵：daemon 的微信通道会把它转成图片消息发出
        yield f"IMG|{card['id']}|{card['line']}"
        yield (f"对了对了，我每次聊天都会带着头像出现哦～想让我顶着"
               f"「{card['name']}」陪你的话，发「换头像 {card['name'].split('·')[0]}」就行😉")
    if nxt:
        yield (f"预告一下：累计打卡 {nxt} 天还有新造型等着你，"
               f"记得常来找我玩呀～")
    else:
        yield "目前的造型你都集齐啦，新的配饰正在画，敬请期待✨"
