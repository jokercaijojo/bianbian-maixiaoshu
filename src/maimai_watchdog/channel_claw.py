"""微信 Claw 通道封装：基于 octop-gateway 的 weixin (iLink) 通道。

- 长轮询收消息（无需公网 IP）
- 用户首次发消息时自动登记为可推送对象（subject）
- 主动推送：push_text(subject_id, text)

凭证来自 scripts/bind_wechat.py 生成的 claw_credentials.json。
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import os
import socket
import urllib.parse
from collections.abc import AsyncIterator, Callable

from octop_gateway import ChannelManager, InboundMessage, MessageEvent
from octop_gateway.channels.weixin import WeixinAccountConfig, WeixinConfig
from octop_gateway.models import ChannelSubject

logger = logging.getLogger("maimai.claw")

SUBJECTS_FILE = os.path.join(os.path.dirname(__file__), "..", "..", "claw_subjects.json")

# ---- 远程配图下载的硬约束（防 SSRF / 内存放大）----
MAX_REMOTE_IMAGE_BYTES = 5 * 1024 * 1024   # 单图最大 5MB
MAX_IMAGE_PIXELS = 20_000_000              # 防解压炸弹（PIL 侧再兜一层）
_REMOTE_IMG_HOST_ALLOW = tuple(
    h.strip().lower() for h in
    os.environ.get("REMOTE_IMG_HOST_ALLOW", "").split(",") if h.strip()
)


def _is_public_ip(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return not (addr.is_private or addr.is_loopback or addr.is_link_local
                or addr.is_reserved or addr.is_multicast or addr.is_unspecified)


def _check_remote_url(url: str) -> str | None:
    """校验待下载的图片 URL，不通过返回错误原因，通过返回 None。

    只挡真正的风险：非 http(s) 协议、解析到内网/回环地址、不在可选白名单内。
    不做死域名白名单——麦当劳配图 CDN 域名会变，写死会把功能弄坏。
    """
    try:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            return f"bad scheme/host: {parsed.scheme}"
        host = parsed.hostname.lower()
        if _REMOTE_IMG_HOST_ALLOW and not any(
                host == h or host.endswith("." + h) for h in _REMOTE_IMG_HOST_ALLOW):
            return f"host not in allowlist: {host}"
        infos = socket.getaddrinfo(host, None)
        for info in infos:
            ip = info[4][0]
            if not _is_public_ip(ip):
                return f"host {host} resolves to non-public ip {ip}"
        return None
    except Exception as exc:  # noqa: BLE001
        return f"check failed: {exc}"


def _download_limited(url: str, timeout: int = 15) -> bytes:
    """带大小上限的下载，超限抛异常而不是把内存吃满。"""
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        # Content-Length 先判一次，避免明显超大时白下载
        try:
            declared = int(resp.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            declared = 0
        if declared > MAX_REMOTE_IMAGE_BYTES:
            raise ValueError(f"declared size {declared} exceeds limit")
        chunks, total = [], 0
        while True:
            chunk = resp.read(64 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_REMOTE_IMAGE_BYTES:
                raise ValueError(f"body exceeds {MAX_REMOTE_IMAGE_BYTES} bytes")
            chunks.append(chunk)
        return b"".join(chunks)



# ---- 猴补丁：媒体上传失败时，octop 默认会把 "[Image (local file)]" 这种
# 工程占位文本发给用户（体感很差）。替换为人设化提示，上传失败时用户看到
# 的是一句可爱的解释而不是报错。 ----
def _patch_media_fallback() -> None:
    """兼容多个 octop-gateway 版本的方法名。

    旧版：WeixinChannel._send_media_fallback(subject, media)
    1.0.3：WeixinChannel._send_media_fallback_unlocked(subject, media)
           且该版本在 _send_media 持锁状态下调用，回退发文字必须走
           _dispatch_text（不加锁），走 _send_text 会自己再抢同一把
           asyncio.Lock 造成死锁。
    """
    from octop_gateway.channels.weixin.channel import WeixinChannel

    target = None
    for name in ("_send_media_fallback_unlocked", "_send_media_fallback"):
        if hasattr(WeixinChannel, name):
            target = name
            break
    if target is None:
        logger.warning("octop_gateway 未找到媒体回退方法，跳过猴补丁")
        return
    _orig = getattr(WeixinChannel, target)

    async def _patched(self, subject, media):
        if getattr(media, "data", None) or getattr(media, "local_path", None):
            logger.warning("media upload failed, sending persona fallback text")
            try:
                text = "图片在路上堵车了😅 等下补发给你～"
                if hasattr(self, "_dispatch_text"):
                    # 1.0.3+：无锁底层发送，避免死锁
                    await self._dispatch_text(subject, text, prepaid_first=True)
                else:
                    await self._send_text(subject, text)
            except Exception:  # noqa: BLE001
                logger.exception("fallback text also failed")
        else:
            await _orig(self, subject, media)

    setattr(WeixinChannel, target, _patched)
    logger.info("media fallback 猴补丁已应用（%s）", target)


_patch_media_fallback()


class ClawGateway:
    """麦麦管家的微信通道。一个进程内常驻，收发一体。"""

    def __init__(self, cred_file: str,
                 on_message: Callable[[str, str], AsyncIterator[str]] | None = None):
        self.cred_file = cred_file
        self.on_message = on_message  # (subject_id, text) -> 异步迭代回复文本
        self.manager: ChannelManager | None = None
        self.channel_id: str = ""
        self.subjects: dict[str, dict] = self._load_subjects()

    def _load_subjects(self) -> dict[str, dict]:
        path = os.path.abspath(SUBJECTS_FILE)
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        return {}

    def _save_subjects(self) -> None:
        with open(os.path.abspath(SUBJECTS_FILE), "w", encoding="utf-8") as f:
            json.dump(self.subjects, f, ensure_ascii=False, indent=2)

    async def _processor(self, message: InboundMessage) -> AsyncIterator[MessageEvent]:
        """收到微信消息：登记 subject，并转交业务回调。"""
        subject = message.channel_subject
        if subject is None:
            logger.warning("inbound without channel_subject, dropped")
            return
        sid = subject.subject_id
        meta = dict(subject.metadata or {})
        is_new = sid not in self.subjects
        self.subjects[sid] = {"metadata": meta, "display_name": subject.display_name}
        self._save_subjects()
        logger.info(" inbound from %s: %s", sid[:12], message.text[:50])

        if is_new:
            yield MessageEvent.text("👋 嗨～我是麦小薯，你的麦门蹲点搭子！"
                                    "绑定成功啦，有优惠我会第一时间喊你🍟")
        if self.on_message:
            try:
                async for reply in self.on_message(sid, message.text or ""):
                    if reply.startswith("IMG|") or reply.startswith("AVA|"):
                        # 图片哨兵：IMG|卡ID|配文 发大卡；AVA|卡ID 发头像缩略图。
                        # 本地读图转 base64 直发，不走 URL，避免依赖 webapp 是否在线
                        await self._push_local_card(sid, reply)
                        continue
                    if reply.startswith("IMGURL|"):
                        # 远程活动配图：下载 → 缩到宽≤480 → base64 发送
                        await self._push_remote_image(sid, reply[7:].strip())
                        continue
                    logger.info("outbound to %s: %s", sid[:12], reply[:50].replace("\n", " "))
                    yield MessageEvent.text(reply)
            except Exception:  # noqa: BLE001
                logger.exception("on_message error")
                yield MessageEvent.text("😵 刚才出了点小状况，稍后再试试～")
        yield MessageEvent.completed()

    async def _push_remote_image(self, sid: str, url: str,
                                 max_width: int = 480) -> None:
        """下载远程活动图，等比缩到宽 ≤480（避免霸屏），base64 推送。"""
        import base64 as b64
        import io
        if not self.manager or sid not in self.subjects or not url:
            return
        # URL 来自麦当劳 MCP 富文本，虽非用户直接可控，仍按不可信处理
        reason = _check_remote_url(url)
        if reason:
            logger.warning("remote image blocked (%s): %s", reason, url[:120])
            return
        try:
            raw = await asyncio.to_thread(_download_limited, url)
            from PIL import Image
            Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS
            im = Image.open(io.BytesIO(raw))
            if im.width > max_width:
                im = im.resize((max_width, int(im.height * max_width / im.width)),
                               Image.LANCZOS)
            buf = io.BytesIO()
            im.convert("RGB").save(buf, format="JPEG", quality=82)
            data = b64.b64encode(buf.getvalue()).decode()
        except Exception:  # noqa: BLE001
            logger.exception("remote image fetch/resize failed: %s", url[:80])
            return
        from octop_gateway.models import ImageContent
        subject = ChannelSubject(
            subject_id=sid,
            metadata=dict(self.subjects[sid].get("metadata") or {}),
        )
        try:
            await self.manager.push_content(
                self.channel_id, subject,
                [ImageContent(data=data, mime_type="image/jpeg")])
        except Exception:  # noqa: BLE001
            logger.exception("remote image push failed")

    async def _push_local_card(self, sid: str, sentinel: str) -> None:
        """处理图片哨兵：
          IMG|card_id|caption —— 大卡 + 配文（解锁播报用）
          AVA|card_id          —— 纯头像缩略图（每轮对话的"头像"）
        """
        import base64 as b64
        from .gacha import avatar_thumb_file, card_file
        parts = sentinel.split("|", 2)
        if not self.manager or sid not in self.subjects:
            return
        is_avatar = parts[0] == "AVA"
        card_id = parts[1] if len(parts) >= 2 else ""
        caption = "" if is_avatar else (parts[2] if len(parts) >= 3 else "")
        path = avatar_thumb_file(card_id) if is_avatar else card_file(card_id)
        if not os.path.exists(path):
            logger.warning("card image missing: %s", path)
            if caption:
                await self.push_text(sid, caption)
            return
        from octop_gateway.models import ImageContent, TextContent
        with open(path, "rb") as f:
            data = b64.b64encode(f.read()).decode()
        subject = ChannelSubject(
            subject_id=sid,
            metadata=dict(self.subjects[sid].get("metadata") or {}),
        )
        content: list = [ImageContent(data=data, mime_type="image/png")]
        if caption:
            content.append(TextContent(text=caption))
        try:
            await self.manager.push_content(self.channel_id, subject, content)
        except Exception:  # noqa: BLE001
            logger.exception("card image push failed, fallback to text")
            if caption:
                await self.push_text(sid, caption)

    def _read_accounts(self) -> list[WeixinAccountConfig]:
        """读取凭证文件，兼容单账号（dict）与多账号（{"accounts": [...]}）格式。"""
        with open(self.cred_file, encoding="utf-8") as f:
            creds = json.load(f)
        entries = creds.get("accounts") if isinstance(creds, dict) else None
        if not entries:
            entries = [creds]
        return [WeixinAccountConfig(
            account_id=c["account_id"],
            token=c["bot_token"],
            base_url=c.get("base_url") or "https://ilinkai.weixin.qq.com",
        ) for c in entries]

    async def start(self) -> None:
        config = WeixinConfig(accounts=self._read_accounts())
        self.manager = ChannelManager(processor=self._processor)
        await self.manager.start()
        self.channel_id = await self.manager.add_weixin_channel(config)
        logger.info("ClawGateway started, channel_id=%s", self.channel_id)

    async def reload_accounts(self) -> None:
        """凭证文件变更后热重载（新用户扫码加入时）。"""
        logger.info("reloading weixin accounts…")
        await self.stop()
        await self.start()

    async def stop(self) -> None:
        if self.manager:
            await self.manager.stop()

    def known_subjects(self) -> list[str]:
        return list(self.subjects.keys())

    async def push_text(self, subject_id: str, text: str) -> bool:
        """主动向某个已绑定的微信用户推送。"""
        if not self.manager or subject_id not in self.subjects:
            logger.warning("push dropped: unknown subject %s", subject_id[:12])
            return False
        subject = ChannelSubject(
            subject_id=subject_id,
            metadata=dict(self.subjects[subject_id].get("metadata") or {}),
        )
        try:
            await self.manager.push_text(self.channel_id, subject, text)
            return True
        except Exception:  # noqa: BLE001
            logger.exception("push failed")
            return False

    async def push_parts(self, subject_id: str, text: str,
                         image_urls: list[str] | None = None) -> bool:
        """推送富内容：文字 + 官方配图（图片由通道自行下载上传）。"""
        if not self.manager or subject_id not in self.subjects:
            logger.warning("push dropped: unknown subject %s", subject_id[:12])
            return False
        from octop_gateway.models import ImageContent, TextContent
        subject = ChannelSubject(
            subject_id=subject_id,
            metadata=dict(self.subjects[subject_id].get("metadata") or {}),
        )
        parts: list = [TextContent(text=text)]
        for url in (image_urls or [])[:3]:  # 一次最多 3 张图，避免刷屏
            if url:
                parts.append(ImageContent(url=url))
        try:
            await self.manager.push_content(self.channel_id, subject, parts)
            return True
        except Exception:  # noqa: BLE001
            logger.exception("push_parts failed, fallback to text")
            try:
                await self.manager.push_text(self.channel_id, subject, text)
                return True
            except Exception:  # noqa: BLE001
                logger.exception("text fallback failed")
                return False


def push_text_sync(base_url: str, bot_token: str, account_id: str, text: str) -> bool:
    """供 Notifier 接口用的同步推送（独立短连接，无常驻进程时使用）。

    注意：微信 iLink 主动推送需要上下文凭证，仅在用户近期与机器人
    对话过后才可靠；常驻 daemon 模式（ClawGateway）是首选路径。
    """
    async def _go() -> bool:
        import aiohttp
        from octop_gateway.channels.weixin.api import WeixinAPIClient
        async with aiohttp.ClientSession() as session:
            api = WeixinAPIClient(base_url=base_url, token=bot_token, session=session)
            try:
                await api.send_message(to_user_id=account_id, text=text)
                return True
            except Exception:  # noqa: BLE001
                return False
    try:
        return asyncio.run(_go())
    except Exception:  # noqa: BLE001
        return False
