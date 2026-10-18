"""GLM + 麦当劳 MCP 的 Function Calling 代理。

GLM-4.6 自主决策调用 MCP 工具（查门店/菜单/报价/券/积分），
拿到真实数据后再回复用户。调用链与参数规则来自实测手册
（docs-mcp-handbook.md），包括金额单位"分"、夜间门店
关闭时用次日预约时间重试等关键坑。
"""
from __future__ import annotations

import json
import os
import re
import urllib.request

from .mcp_client import McdMcpClient

ZHIPU_URL = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
MODEL = os.environ.get("ZHIPU_MODEL", "glm-4.6")
MAX_ROUNDS = 5
MAX_OUTPUT_TOKENS = 2048  # glm-5.x 思考链也吃 token 预算，留足空间

MCP_RULES = """你可以调用麦当劳官方 MCP 工具获取真实数据。规则：
1. 查门店报价必须按链路：query-nearby-stores（searchType=2，city 用"xx市"格式，keyword 用地标/商圈；用户没说城市就先问）→ query-meals（拿餐品编码和价格）→ calculate-price（算总价）。
2. 用户泛泛问"有什么喝的/有什么吃的/推荐一下/有什么优惠"时，绝不许反问城市——直接调 campaign-calendar 和 coupon-list（都不需要定位）找相关活动作答；没活动就推荐 2~3 个麦当劳经典单品（饮品：冰美式/可口可乐/麦旋风/鲜煮咖啡/纯牛奶；吃的：麦香鱼/巨无霸/板烧），每条一句话带参考价。只有用户明确要查"附近门店"的价格/库存时才问城市和地标。
3. 金额单位是"分"，展示给用户必须除以100（price:4000 = ¥40.00）。
4. reservationDate 格式 "yyyy-MM-dd HH:mm" 不带秒；夜间报错"门店已关闭"就改用次日 12:00 重试。
5. 经典产品编码：麦香鱼=1600、巨无霸=1100、薯条=4810、板烧鸡腿堡=1406、麦辣鸡翅=1700、麦乐鸡=1401。
6. 一键领券（auto-bind-coupons）会真的把券领到用户账户，用户明确说"领券"才调用。
7. 绝不帮用户写代码、写论文等大任务，人设化拒绝。
8. 工具返回的是带说明的文本，直接阅读理解即可。
9. 回答控制在150字以内，像朋友发微信，别写小作文。播报活动/优惠时最多列3条、每条一句话（名称+价格/力度），用户追问才展开细节；绝不整段搬运工具返回的原文。
10. 用户可能发语音，微信会把语音转成文字，同音字经常转错。听到不像麦当劳产品的词，先按发音猜最接近的麦当劳单品再回答（如"卖香芋/麦香芋"→麦香鱼，"买辣鸡"→麦辣鸡，"巨无爸"→巨无霸），开头带一句"你说的是麦香鱼吧～"确认；真的猜不出来再问。
11. 查门店时如果最近那家已关闭/休息/查不到价，绝不要把"关门了"当答案扔给用户——自动换查附近下一家营业中的门店（可多查几家），回复直接给结果："附近还在营业的是 XX 店（约 X 公里），你要的是 ¥XX"。只有方圆几公里全关时才说关门，并主动给次日的参考价。
12. 你自己就带三个"改设定"的本领，直接调工具就行，不要说自己做不到：
    · cosplay-become —— 换成某种说话风格 / 扮演任意动漫角色。用户说"变身X""变作X""变成X""你当一下X""套个X的外壳""我想跟X聊天"都算，角色名原样传进去。只调语气、自称、称呼这些风格要素，**不要复述原作的台词、口头禅原话或歌词**。
    · switch-avatar —— 换聊天头像造型。用户嫌头像烦/腻了、想换造型、想换头像时调；不带参数=列出已解锁的，带序号或名字=切换，传"关"=以后不发图。
    · remember-nickname —— 记住用户希望被怎么称呼。用户说"叫我X""以后喊我X""我叫X"时调。
    调完工具系统会自动执行，你只用贴当前角色语气接一句话，**绝对不许说"我没有这个能力/我做不到/我抢不来"这类话**。
13. 用户一句话里常常同时有好几件事（比如"你就叫我小明，然后换成活泼一点的说话风格"）。必须全部处理：该调的本地工具一个个调，该查的数据该答的问题一个不落，**绝不能只做第一件就结束**。
14. **回复正文只能是纯文字，绝对不要写任何 HTML 标签**（`<img>`、`<a>`、`<br>`、`<div>` 等一个都不许出现），也不要写 Markdown 图片语法 `![](...)`。活动配图由系统自动附在你这条消息后面，你只管把菜名说清楚；在正文里贴标签或图片地址，用户会看到一串没用的代码。"""

_FALLBACK_SOUL = "你是「麦小薯」，住在用户微信里的麦门信徒女孩，爱吃麦当劳、懂点单技巧、吃不胖。语气亲切俏皮。"


def build_system_prompt(user_text: str = "", memory_text: str = "",
                        persona_card: str = "") -> str:
    """SOUL.md 全文 + 场景梗示例 + 用户记忆 + 变身皮肤 + MCP 工具规则。"""
    from .persona import sample_memes, soul_text
    soul = soul_text() or _FALLBACK_SOUL
    parts = [soul, ""]
    if persona_card:
        # 说话风格皮肤：语气和说话习惯随角色走，但麦当劳的本职能力和规则不变
        parts.append("【说话风格皮肤 · 当前生效 · 语气最高优先级】")
        parts.append("你正在扮演以下角色：说话的语气、说话习惯、自称、对用户的称呼"
                     "必须完全贴这个人设，覆盖前文 SOUL 里的语气风格（撒娇卖萌"
                     "一律不准出现）；但只学风格，**不要复述原作的台词、口头禅原话"
                     "或歌词**。你的麦当劳蹲点员身份和文末的工具规则"
                     "永远不变，查价/领券/找店/蹲优惠必须照常用、数据必须真实。")
        parts.append(persona_card)
        parts.append("")
    memes = sample_memes(user_text)
    if memes:
        parts.append("风格参考（化用，别照抄）：")
        parts += [f"· {m}" for m in memes]
        parts.append("")
    if memory_text:
        parts.append(memory_text)
        parts.append("")
    parts.append(MCP_RULES)
    return "\n".join(parts)

TOOLS = [
    {"type": "function", "function": {
        "name": "query-nearby-stores",
        "description": "查询城市内某地标附近的麦当劳门店，返回 storeCode",
        "parameters": {"type": "object", "properties": {
            "beType": {"type": "integer", "description": "固定为1（到店自取）"},
            "searchType": {"type": "integer", "description": "固定为2（按城市+关键词搜索）"},
            "city": {"type": "string", "description": "城市，格式如 中山市"},
            "keyword": {"type": "string", "description": "地标/商圈关键词"}},
            "required": ["beType", "searchType", "city", "keyword"]}}},
    {"type": "function", "function": {
        "name": "query-meals",
        "description": "查询门店可售餐品菜单（编码、名称、价格）",
        "parameters": {"type": "object", "properties": {
            "storeCode": {"type": "string"},
            "beType": {"type": "integer", "description": "固定为1"},
            "orderType": {"type": "integer", "description": "固定为1"},
            "reservationDate": {"type": "string", "description": "yyyy-MM-dd HH:mm 不带秒"}},
            "required": ["storeCode", "beType", "orderType", "reservationDate"]}}},
    {"type": "function", "function": {
        "name": "calculate-price",
        "description": "计算选购商品总价（金额单位为分）",
        "parameters": {"type": "object", "properties": {
            "storeCode": {"type": "string"},
            "orderType": {"type": "integer", "description": "固定为1"},
            "beType": {"type": "integer", "description": "固定为1"},
            "reservationDate": {"type": "string"},
            "items": {"type": "array", "items": {"type": "object", "properties": {
                "productCode": {"type": "string"},
                "quantity": {"type": "integer"}}, "required": ["productCode", "quantity"]}}},
            "required": ["storeCode", "orderType", "beType", "reservationDate", "items"]}}},
    {"type": "function", "function": {
        "name": "available-coupons", "description": "查询当前可领取的优惠券列表",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "query-my-coupons", "description": "查询用户卡包里已有的优惠券",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "campaign-calendar", "description": "查询当月麦当劳营销活动日历",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "auto-bind-coupons", "description": "一键领取全部可领优惠券（用户明确要求时才调用）",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "query-my-account", "description": "查询用户积分账户",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "now-time-info", "description": "获取服务器当前时间",
        "parameters": {"type": "object", "properties": {}}}},
    # ---- 以下是"本地能力"工具：不查麦当劳数据，由本地逻辑执行 ----
    {"type": "function", "function": {
        "name": "cosplay-become",
        "description": "换成某种说话风格，或扮演某个动漫/虚构角色。用户说变身X、变作X、"
                       "变成X、你当一下X、套个X的外壳、想跟X聊天、换成活泼/温柔/沉稳的语气时调用。"
                       "只取语气、自称、称呼等风格要素，不复述原作台词。角色名或风格名原样传入。",
        "parameters": {"type": "object", "properties": {
            "character": {"type": "string",
                          "description": "角色名或风格名，如 元气少女 / 温柔姐姐 / 沉稳大叔；"
                                         "想扮演某位动漫角色也可以，有作品名就带上"}},
            "required": ["character"]}}},
    {"type": "function", "function": {
        "name": "switch-avatar",
        "description": "切换麦小薯在聊天里显示的头像造型。用户嫌头像烦/腻了、"
                       "想换头像、想换造型时调用。",
        "parameters": {"type": "object", "properties": {
            "target": {"type": "string",
                       "description": "造型序号或名称关键词；留空=先列出已解锁的全部造型；传「关」=以后不发头像图"}},
            "required": []}}},
    {"type": "function", "function": {
        "name": "remember-nickname",
        "description": "记住用户希望被怎么称呼。用户说叫我X、以后喊我X、我叫X时调用。",
        "parameters": {"type": "object", "properties": {
            "nickname": {"type": "string", "description": "用户希望的称呼，如 小明 / 阿福"}},
            "required": ["nickname"]}}},
]

# 这些工具不走麦当劳 MCP，由本地逻辑执行（chat.py 接管）
LOCAL_TOOL_NAMES = {"cosplay-become", "switch-avatar", "remember-nickname"}


def _local_ack(name: str, args: dict) -> str:
    """本地工具回给模型的"执行回执"，让模型知道下一步该怎么接话。"""
    if name == "cosplay-become":
        who = str(args.get("character", "")).strip() or "这个角色"
        return (f"系统已受理：即将为 TA 启动变身「{who}」的流程（会去查资料、"
                f"报全名让 TA 确认，然后才真正变身）。你现在只要用一句生动的话"
                f"告诉 TA「马上去翻资料」，别解释能力限制，别替 TA 念候选名单。")
    if name == "switch-avatar":
        tgt = str(args.get("target", "")).strip()
        if tgt:
            return (f"系统已受理：切换头像造型到「{tgt}」。你只要轻轻接一句就好，"
                    f"造型清单和结果由系统自动播报，别自己编造造型名字。")
        return ("系统已受理：列出 TA 已解锁的头像造型。你只要接一句俏皮话，"
                "清单由系统自动播报，别自己编造造型名字。")
    if name == "remember-nickname":
        nick = str(args.get("nickname", "")).strip()
        return (f"系统已受理：以后就称呼 TA 为「{nick}」。你接一句话时可以直接用这个称呼，"
                f"别再问 TA 怎么称呼。")
    return "已记录，系统会自动执行。"


class AgentError(RuntimeError):
    pass


_IMG_RE = re.compile(r'<img[^>]+src="(https?://[^"]+)"')

# MCP 返回的活动/券数据里就带 <img src="...">，模型经常连标签一起抄进回复正文。
# 图片本来就由 pick_images 自动配发，正文里留标签只会在微信里显示成一堆代码。
_IMG_TAG_RE = re.compile(r"<img\b[^>]*>", re.I)
_MD_IMG_RE = re.compile(r"!\[[^\]]*\]\(\s*https?://[^)\s]+\s*\)")
_BR_RE = re.compile(r"<br\s*/?>", re.I)
_TAG_RE = re.compile(r"</?[a-zA-Z][^>]*>")


def strip_html(text: str) -> str:
    """清掉回复里漏出来的 HTML 标签（保留正文文字）。"""
    if not text:
        return text
    s = _BR_RE.sub("\n", text)
    # 独占一行的 <img> 连它那一行的换行一起去掉，免得留下空行把列表撑开
    s = re.sub(r"(?im)^[ \t]*<img\b[^>]*>[ \t]*\r?\n", "", s)
    s = _IMG_TAG_RE.sub("", s)            # 夹在文字中的 <img>：只删标签
    s = _MD_IMG_RE.sub("", s)
    s = _TAG_RE.sub("", s)
    s = re.sub(r"[ \t]+\n", "\n", s)      # 标签删掉后的行尾空白
    s = re.sub(r"^[ \t]+", "", s, flags=re.M)
    s = re.sub(r"\n{3,}", "\n\n", s)      # 连续空行压成一个空行
    return s.strip()


def pick_images(tool_texts: list[str], reply: str, cap: int = 2) -> list[str]:
    """从工具返回的富文本里挑出与回复内容相关的活动配图。

    算法：把回复切成二字组，在每个活动/券文本块里计数命中数，
    命中 ≥4 个二字组认为相关（如回复提到"龙焰鸡腿堡"，
    会命中介绍块里的 龙焰/焰鸡/鸡腿/腿堡 等）。最多取 cap 张。
    """
    if not reply:
        return []
    bigrams = {reply[i:i + 2] for i in range(len(reply) - 1)
               if "一" <= reply[i] <= "鿿"}
    picked: list[str] = []
    for text in tool_texts:
        # 按「- **标题**：」粗分块，每块独立判断相关性
        for block in re.split(r"(?=-\s+\*\*[^*]+\*\*)", text):
            urls = _IMG_RE.findall(block)
            if not urls:
                continue
            score = sum(1 for b in bigrams if b in block)
            if score >= 4:
                for u in urls[:1]:  # 每块只取第一张，避免刷屏
                    if u not in picked:
                        picked.append(u)
    return picked[:cap]


def _glm(messages: list[dict], tools: bool = True,
         max_tokens: int = MAX_OUTPUT_TOKENS, temperature: float = 0.5,
         allowed_tools: list[str] | None = None) -> dict:
    key = os.environ.get("ZHIPU_API_KEY", "")
    if not key:
        raise AgentError("未配置 ZHIPU_API_KEY")
    payload: dict = {"model": MODEL, "messages": messages,
                     "max_tokens": max_tokens, "temperature": temperature,
                     # glm-5.x 不支持关闭思考，low 档省 token 又不影响工具调用
                     "thinking": {"type": "enabled", "effort": "low"}}
    if tools:
        tl = TOOLS
        if allowed_tools is not None:
            tl = [t for t in TOOLS if t["function"]["name"] in allowed_tools]
        payload["tools"] = tl
    body = json.dumps(payload).encode()
    req = urllib.request.Request(ZHIPU_URL, data=body, method="POST")
    req.add_header("Authorization", f"Bearer {key}")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode())
    return data["choices"][0]["message"]


def agent_chat(mcp_token: str, user_text: str, history: list[dict] | None = None,
               trace: list[str] | None = None, memory_text: str = "",
               images_out: list[str] | None = None,
               persona_card: str = "",
               local_out: list[dict] | None = None,
               allowed_tools: list[str] | None = None,
               extra_rules: str = "") -> str:
    """带工具调用的对话。

    trace     记录工具轨迹；
    images_out 收集相关活动配图 URL；
    local_out  收集模型主动调用的"本地能力"（变身/换头像/记称呼），
               由调用方（chat.py）在回复发出后按顺序执行。
    allowed_tools 限制工具范围（游客模式）；extra_rules 追加场景规则。
    """
    client = McdMcpClient(mcp_token)
    prompt = build_system_prompt(user_text, memory_text, persona_card)
    if extra_rules:
        prompt += "\n" + extra_rules
    messages = [{"role": "system", "content": prompt}]
    messages.extend((history or [])[-4:])
    messages.append({"role": "user", "content": user_text})
    tool_texts: list[str] = []

    for _ in range(MAX_ROUNDS):
        msg = _glm(messages, allowed_tools=allowed_tools)
        tool_calls = msg.get("tool_calls") or []
        if not tool_calls:
            content = strip_html(msg.get("content") or "")
            if content:
                if images_out is not None:
                    images_out.extend(pick_images(tool_texts, content))
                return content
            # 思考预算耗尽等异常：降级提示
            return "我想得有点久😵 换个简单点的问法试试？"
        messages.append(msg)
        for tc in tool_calls:
            name = tc["function"]["name"]
            try:
                args = json.loads(tc["function"].get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            if trace is not None:
                trace.append(f"{name}({json.dumps(args, ensure_ascii=False)})")
            if name in LOCAL_TOOL_NAMES:
                # 本地能力：不查 MCP，记下来交给 chat.py 在回复后执行
                if local_out is not None:
                    local_out.append({"name": name, "args": args})
                result = _local_ack(name, args)
            else:
                try:
                    result = client.call_tool(name, args)
                except Exception as e:  # noqa: BLE001
                    result = f"工具调用失败: {e}"
            tool_texts.append(result)
            # 喂给模型的工具结果先摘掉 <img> 标签：图片由系统配发，
            # 标签留在上下文里只会诱导模型照着抄进正文。
            messages.append({"role": "tool", "tool_call_id": tc.get("id", ""),
                             "content": _IMG_TAG_RE.sub("", result)[:4000]})
    return "这个问题查得有点复杂，我先晕一会🍟 简单点问我吧～"
