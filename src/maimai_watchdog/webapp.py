"""麦麦管家网站后端：注册 / Token 引导 / 规则配置 / 微信扫码绑定。

页面流程：
  落地页 / → 引导页 /onboard
    第 1 步：图文指引用户去 open.mcd.cn 申请自己的 MCP Token（需本人短信验证，无法代办）
    第 2 步：粘贴 Token + 填关注关键词 → POST /api/register（服务端调 MCP 校验真伪）
    第 3 步：微信扫码添加 ClawBot（GET /api/qr 生成一次性二维码会话），
             再把 8 位绑定码发给 ClawBot 完成账号关联
"""
from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import mimetypes
import os
import secrets
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout

# Windows 的 mimetypes 注册表不全，.webp 不认识会退化成 application/octet-stream，
# 个别浏览器/微信 webview 会当成下载文件而不是图片。显式注册。
mimetypes.add_type("image/webp", ".webp")

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import DEFAULT_DB, load_env
from .db import Store
from .mcp_client import McdMcpClient
from .mcp_client import McdMcpClient

logger = logging.getLogger("maimai.web")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
CRED_FILE = os.path.join(BASE_DIR, "..", "..", "claw_credentials.json")


def _setup_file_log() -> None:
    """把 web 日志同时写进 logs/web.log。

    以前日志只打到 cmd 黑窗口，出事之后什么都查不到——
    「手机上报 Load failed」这种问题只能靠日志里对不上号来定位。
    """
    import logging.handlers
    logdir = os.path.normpath(os.path.join(BASE_DIR, "..", "..", "logs"))
    try:
        os.makedirs(logdir, exist_ok=True)
        h = logging.handlers.RotatingFileHandler(
            os.path.join(logdir, "web.log"),
            maxBytes=2_000_000, backupCount=3, encoding="utf-8")
        h.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)-7s %(message)s"))
        root = logging.getLogger()
        root.addHandler(h)
        if root.level > logging.INFO or root.level == logging.NOTSET:
            root.setLevel(logging.INFO)
    except Exception:  # noqa: BLE001  日志失败不能拖垮服务
        pass


_setup_file_log()

load_env()
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "")
store = Store(os.environ.get("WATCHDOG_DB", DEFAULT_DB))

app = FastAPI(title="麦麦管家", docs_url=None, redoc_url=None)

# ---- 微信扫码会话（内存态，重启即失效，用户重新生成即可）----
_qr_sessions: dict[str, dict] = {}
QR_SESSION_TTL = 600      # 10 分钟回收，避免会话对象永久驻留内存
QR_SESSION_GRACE = 90     # 已结束（done/expired）的会话再留 90 秒，好让前端把状态读走
QR_SESSION_MAX = 50       # 同时在册的会话上限

# ---- 外部调用超时预算（手机等不了太久，宁可快速失败让用户重试）----
MCP_CALL_TIMEOUT = 6      # 单次 MCP HTTP 超时（秒）
MCP_TOTAL_BUDGET = 15     # 探活整体预算（3 次串行 HTTP）
QR_FETCH_TIMEOUT = 15     # 向微信要二维码的超时
_MCP_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="mcp")

# ---- 简易限流（进程内滑动窗口，按真实客户端 IP）----
_RL_LOCK = threading.Lock()
_RL: dict[str, list[float]] = {}
_CRED_LOCK = threading.Lock()

# HTML 页面缓存策略：
# - 60 秒内浏览器直接用（配合 <link rel="prefetch">，点链接秒开）
# - 过期后先拿旧页面渲染、后台再刷新（用户永远不会为一次导航干等）
# 代价：改完文案最多晚一次导航生效，急的话强刷一下（Ctrl+F5）。
# 这几个页面都是纯静态壳，动态数据一律走 /api，缓存绝对安全。
_PAGE_CACHE = {"cache-control": "public, max-age=60, stale-while-revalidate=600"}
# 管理台不缓存，避免看到过期的统计
_NO_CACHE = {"cache-control": "no-cache"}


def _client_ip(request: Request) -> str:
    """取真实客户端 IP。经 Cloudflare 隧道进来时对端永远是 127.0.0.1，
    必须读 CF-Connecting-IP，否则全站共用一个计数桶、一人打满其他人全挂。"""
    for header in ("cf-connecting-ip", "x-forwarded-for"):
        raw = request.headers.get(header)
        if raw:
            return raw.split(",")[0].strip() or "unknown"
    return request.client.host if request.client else "unknown"


def _rate_limited(bucket: str, limit: int, window: int = 60) -> bool:
    """超限返回 True。"""
    now = time.time()
    with _RL_LOCK:
        if len(_RL) > 5000:      # 防御性上限，避免字典无限增长
            _RL.clear()
        hits = [t for t in _RL.get(bucket, []) if now - t < window]
        if len(hits) >= limit:
            _RL[bucket] = hits
            return True
        hits.append(now)
        _RL[bucket] = hits
        return False


def _prune_qr_sessions() -> None:
    """回收会话。

    用户点「换一张」会不停新建会话，如果只按创建时间回收，
    同一个人连点十几次就能把上限占满、把别人挡在门外。
    所以已结束的会话（done / expired）只留 90 秒给前端读状态，之后立刻回收。
    """
    now = time.time()
    for sid, v in list(_qr_sessions.items()):
        finished = v.get("finished")
        if finished and now - finished > QR_SESSION_GRACE:
            _qr_sessions.pop(sid, None)
        elif now - v.get("created", now) > QR_SESSION_TTL:
            _qr_sessions.pop(sid, None)


@app.middleware("http")
async def _log_api(request: Request, call_next):
    """记下每一次 /api 调用的结果和耗时（绝不记请求体，里面可能带 Token）。"""
    if not request.url.path.startswith("/api/"):
        return await call_next(request)
    t0 = time.perf_counter()
    ip = _client_ip(request)
    try:
        resp = await call_next(request)
    except Exception:
        logger.exception("API 异常 %s %s ip=%s",
                         request.method, request.url.path, ip)
        raise
    logger.info("API %s %s -> %d %.0fms ip=%s",
                request.method, request.url.path, resp.status_code,
                (time.perf_counter() - t0) * 1000, ip)
    return resp


class RegisterIn(BaseModel):
    mcp_token: str = Field(min_length=8, max_length=200)
    keywords: list[str] = Field(default_factory=list, max_length=10)


def _validate_mcp_token(token: str) -> bool:
    """用 now-time-info 探活校验 Token 真伪。

    注意：探活本身要发 3 次串行 HTTP（initialize / initialized / tools/call），
    单次 30 秒超时的话最坏能拖到 90 秒 —— 手机早就把连接掐了，
    但服务端还在傻等，最后照样把用户写进库，于是就出现
    「页面报 Load failed，可数据库里账号已经建好了」这种怪事。

    所以这里给整体设一个总预算，超了就明确告诉前端"是网络超时，再点一次"，
    而不是让浏览器干等到断连。
    """
    try:
        McdMcpClient(token, timeout=MCP_CALL_TIMEOUT).call_tool("now-time-info", {})
        return True
    except Exception:  # noqa: BLE001
        return False


def _validate_mcp_token_bounded(token: str) -> bool:
    """带总时限的校验；超时抛 504，令牌无效返回 False。"""
    fut = _MCP_POOL.submit(_validate_mcp_token, token)
    try:
        return fut.result(timeout=MCP_TOTAL_BUDGET)
    except FuturesTimeout as e:
        raise HTTPException(
            504, "连接麦当劳 MCP 超时了（网络抖动）。直接再点一次就行，不会重复注册。") from e


@app.post("/api/register")
def register(body: RegisterIn, request: Request) -> dict:
    if _rate_limited(f"register:{_client_ip(request)}", limit=10, window=60):
        raise HTTPException(429, "操作太频繁啦，歇一分钟再试～")
    token = body.mcp_token.strip()
    keywords = [k.strip() for k in body.keywords if k.strip()][:10]

    user = store.find_user_by_token(token)
    if user:
        # 老用户：这个 Token 早就校验过了，重试时不必再跑一遍 MCP 探活，
        # 否则"网络抖一下重试"还要再等十几秒。
        uid = user["id"]
        if keywords:
            store.set_keywords(uid, keywords)
    else:
        if not _validate_mcp_token_bounded(token):
            raise HTTPException(400, "Token 校验失败：无法连接麦当劳 MCP，请检查是否复制完整")
        uid = store.add_user(token)
        store.set_keywords(uid, keywords)
    # 注册即拥有新手卡「薯条·手捧」（与网站首抽对应）
    from .gacha import FIRST_CARD_ID
    store.unlock_card(uid, FIRST_CARD_ID, source="register")
    code = store.create_binding_code(uid)
    store.log_event("register", uid)
    return {"user_id": uid, "binding_code": code,
            "user_secret": store.ensure_user_secret(uid),
            "keywords": store.get_keywords(uid)}


@app.get("/api/qr")
async def create_qr(request: Request) -> dict:
    """生成一次性微信绑定二维码（约 8 分钟有效）。"""
    import segno
    from octop_gateway.channels.weixin.login_qr import WeixinQRLogin

    if _rate_limited(f"qr:{_client_ip(request)}", limit=10, window=60):
        raise HTTPException(429, "二维码生成太频繁，歇一分钟再试～")
    _prune_qr_sessions()
    if len(_qr_sessions) >= QR_SESSION_MAX:
        raise HTTPException(429, "同时等待扫码的人太多了，稍等片刻再试～")

    login = WeixinQRLogin()
    try:
        qr = await asyncio.wait_for(login.fetch_qr_code(), timeout=QR_FETCH_TIMEOUT)
    except asyncio.TimeoutError as e:
        logger.warning("fetch_qr_code 超时 %ss", QR_FETCH_TIMEOUT)
        raise HTTPException(
            504, "向微信申请二维码超时了（网络抖动）。点下面「重新申请二维码」再试一次。") from e
    except Exception as e:  # noqa: BLE001
        logger.exception("fetch_qr_code 失败")
        raise HTTPException(502, f"向微信申请二维码失败：{e}") from e

    sid = uuid.uuid4().hex[:12]
    store.log_event("qr_created")
    buf = io.BytesIO()
    segno.make(qr.qrcode_img_content).save(buf, kind="png", scale=8)
    data_url = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    _qr_sessions[sid] = {"login": login, "qrcode": qr.qrcode,
                         "status": "pending", "task": None,
                         "created": time.time()}
    return {"session_id": sid, "qr_data_url": data_url}


def _append_account(result) -> None:
    """把扫码得到的新账号写入凭证文件（保持多账号格式）。

    并发安全：加进程内锁 + 临时文件原子替换，避免两个用户同时扫码
    时互相覆盖或写坏 JSON（写坏了微信通道会整体瘫痪）。

    注意「解绑后重新扫码」：同一个微信号 account_id 不变，但 bot_token 换了。
    旧逻辑遇到重复 account_id 直接 return，结果文件里留着的是已经失效的旧
    token —— 用户看页面提示"绑定成功"，实际发消息没反应。所以这里必须覆盖更新。
    """
    path = os.path.abspath(CRED_FILE)
    with _CRED_LOCK:
        creds = {}
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                creds = json.load(f)
        accounts = creds.get("accounts")
        if not accounts:  # 兼容旧的单账号格式
            accounts = [creds] if creds.get("account_id") else []
        fresh = {
            "account_id": result.account_id,
            "bot_token": result.bot_token,
            "base_url": result.base_url,
            "user_id": getattr(result, "user_id", ""),
        }
        for i, a in enumerate(accounts):
            if a.get("account_id") == result.account_id:
                if all(a.get(k) == v for k, v in fresh.items()):
                    logger.info("weixin account unchanged: %s", result.account_id)
                    return
                accounts[i] = {**a, **fresh}   # 保留其它字段，刷新凭证
                logger.info("weixin account refreshed (re-bind): %s",
                            result.account_id)
                break
        else:
            accounts.append(fresh)
            logger.info("new weixin account appended: %s", result.account_id)
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"accounts": accounts}, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)   # 原子替换，读方永远看到完整文件


async def _wait_login(sid: str) -> None:
    sess = _qr_sessions.get(sid)
    if not sess:
        return
    try:
        result = await sess["login"].wait_for_login(sess["qrcode"])
        if result.connected:
            _append_account(result)
            store.log_event("qr_scanned")
            sess["status"] = "done"
            logger.info("qr session %s scanned ok", sid)
        else:
            sess["status"] = "expired"
            logger.info("qr session %s expired", sid)
    except Exception:  # noqa: BLE001
        logger.exception("qr wait_for_login failed")
        sess["status"] = "expired"
    finally:
        sess["finished"] = time.time()   # 打上结束时间，供回收用


@app.get("/api/qr/{sid}")
async def qr_status(sid: str) -> dict:
    _prune_qr_sessions()
    sess = _qr_sessions.get(sid)
    if not sess:
        raise HTTPException(404, "二维码会话不存在或已过期，请重新生成")
    if sess["status"] == "pending" and sess["task"] is None:
        sess["task"] = asyncio.create_task(_wait_login(sid))
    return {"status": sess["status"]}


# ---- 游客体验区 ----
GUEST_TOOLS = ["campaign-calendar", "available-coupons", "query-nearby-stores",
               "query-meals", "calculate-price", "now-time-info"]
GUEST_RULES = """【游客体验模式】
1. 对方是没注册的网页访客，只能查不能领：不要调用领券/账户类工具（也没有）。
2. 回答活泼简短，两三句内带一个"开通钩子"（如"开通我以后天天帮你盯"）。
3. 绝不答应帮游客领券、下单、查账户——那些是开通后的专属服务；
   游客说"帮我领券"时，正面回答：领券是开通后的专属功能，引导开通。"""


class GuestChatIn(BaseModel):
    text: str = Field(min_length=1, max_length=400)
    history: list[dict] = Field(default_factory=list, max_length=6)


@app.post("/api/guest-chat")
def guest_chat(body: GuestChatIn, request: Request) -> dict:
    import datetime
    ip = request.client.host if request.client else "unknown"
    day = datetime.date.today().isoformat()
    if not store.consume_guest_quota(ip, day):
        return {"reply": "今天聊得够多啦😋 开通我的话，每天能聊 100 轮，"
                         "还能帮你蹲券领券～点上面「开始开通」就行！", "limited": True}
    from .agent import agent_chat
    images: list[str] = []
    reply = agent_chat(os.environ["MCD_MCP_TOKEN"], body.text,
                       history=body.history[-6:], images_out=images,
                       allowed_tools=GUEST_TOOLS, extra_rules=GUEST_RULES)
    return {"reply": reply, "images": images[:1], "limited": False}


# ---- 静态页面 ----
@app.get("/api/cards")
def user_cards(user_id: int | None = None, s: str = "") -> dict:
    """返回卡池：已解锁的卡 + 未解锁卡的解锁条件。

    不带 user_id（游客）时：只有新手卡可见，其余全是 ? 卡背——
    对应落地 funnel：先抽一张 → 引导开通。

    带 user_id 时必须同时带注册时下发的 user_secret（参数 s），
    否则一律按游客返回——user_id 是自增整数可枚举，不能裸信。
    """
    import datetime
    from .gacha import (CARD_CATALOG, FIRST_CARD_ID, STREAK_TIERS,
                        card_exists)
    unlocked: set[str] = set()
    days, rounds = 0, 0
    if user_id and store.verify_user_secret(user_id, s):
        store.unlock_card(user_id, FIRST_CARD_ID, source="register")  # 老用户自愈补发
        unlocked = set(store.get_unlocks(user_id))
        today = datetime.date.today().isoformat()
        days = store.total_checkin_days(user_id)
        rounds = store.rounds_today(user_id, today)
    else:
        unlocked = set()  # 游客或口令不匹配：卡柱全锁，不泄露他人数据

    tier_of = {cid: d for d, cid in STREAK_TIERS.items()}
    cards = []
    for cid, c in CARD_CATALOG.items():
        if not card_exists(cid):
            continue
        entry = {"id": cid, "unlocked": cid in unlocked}
        if cid in unlocked:
            entry.update(name=c["name"], rarity=c["rarity"],
                         img=c["img"], line=c["line"])
        else:
            need = tier_of.get(cid)
            entry["condition"] = (f"累计打卡 {need} 天解锁" if need
                                  else "节日限定 · 敬请期待")
        cards.append(entry)
    return {"cards": cards, "first_card": FIRST_CARD_ID,
            "days": days, "rounds_today": rounds,
            "checkin_rounds_needed": store.CHECKIN_ROUNDS_NEEDED}


# ---- 管理台（需 ADMIN_TOKEN）----
@app.get("/admin")
def admin_page() -> FileResponse:
    return FileResponse(os.path.join(STATIC_DIR, "admin.html"),
                        headers=_NO_CACHE)


@app.get("/api/admin/stats")
def admin_stats(request: Request, key: str = "") -> dict:
    import datetime
    # 优先读请求头 X-Admin-Token（不进 URL、不落浏览器历史/网关日志）；
    # key 查询参数保留兼容老链接。比较用 compare_digest 消除时序差异。
    provided = request.headers.get("x-admin-token") or key or ""
    ok = False
    if ADMIN_TOKEN and provided:
        try:
            ok = secrets.compare_digest(provided, ADMIN_TOKEN)
        except TypeError:
            ok = False
    if not ok:
        raise HTTPException(403, "无权访问")
    today = datetime.date.today()
    days = []
    for i in range(13, -1, -1):
        d = (today - datetime.timedelta(days=i)).isoformat()
        days.append({"day": d[5:], "chat_rounds": store.chat_rounds_on(d),
                     **store.event_counts(d)})
    users = [{
        "id": u["id"], "name": u["name"], "bound": bool(u["claw_account_id"]),
        "today_rounds": store.rounds_on(u["id"], today.isoformat()),
        "checkin_days": store.total_checkin_days(u["id"]),
        "unlocks": len(store.get_unlocks(u["id"])),
        "facts": len(store.get_facts(u["id"], limit=50)),
        "quota": u["chat_quota_daily"],
    } for u in store.all_users()]
    return {"now": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "totals": store.event_counts(None), "days": days, "users": users,
            "today": {"chat_rounds": store.chat_rounds_on(today.isoformat())}}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(os.path.join(STATIC_DIR, "index.html"),
                        headers=_PAGE_CACHE)


@app.get("/onboard")
def onboard() -> FileResponse:
    return FileResponse(os.path.join(STATIC_DIR, "onboard.html"),
                        headers=_PAGE_CACHE)


@app.get("/gacha")
def gacha() -> FileResponse:
    return FileResponse(os.path.join(STATIC_DIR, "gacha.html"),
                        headers=_PAGE_CACHE)


class _CachedStatic(StaticFiles):
    """静态资源带长缓存头。

    图片内容一旦发布就不会原地改（改图就换文件名/web 变体），
    所以可以让浏览器和 Cloudflare 放心缓存 30 天，避免每次进页面重拉。
    没有缓存头时 Cloudflare 对 png/webp 也缓存，但浏览器每次都要回源确认，
    手机端会明显感觉"图一直在转圈"。
    """

    _LONG = "public, max-age=2592000, immutable"      # 30 天

    async def get_response(self, path: str, scope):  # type: ignore[override]
        resp = await super().get_response(path, scope)
        if resp.status_code == 200:
            resp.headers.setdefault("cache-control", self._LONG)
        return resp


app.mount("/static", _CachedStatic(directory=STATIC_DIR), name="static")
