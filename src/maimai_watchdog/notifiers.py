"""通知层：可插拔 Notifier 接口。

- ServerChanNotifier：Server酱 Turbo，5 分钟接入，兜底通道
- ClawNotifier：octop-gateway 微信通道（ClawBot 联系人主动推送），主打通道
"""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request


class Notifier:
    def send(self, user: dict, text: str) -> bool:  # pragma: no cover - 接口定义
        raise NotImplementedError


class ServerChanNotifier(Notifier):
    """Server酱 Turbo：https://sctapi.ftqq.com/<SENDKEY>.send"""

    def __init__(self, sendkey: str | None = None):
        self.sendkey = sendkey or os.environ.get("SERVERCHAN_SENDKEY", "")

    def send(self, user: dict, text: str) -> bool:
        if not self.sendkey:
            return False
        url = f"https://sctapi.ftqq.com/{self.sendkey}.send"
        body = urllib.parse.urlencode({
            "title": "麦麦管家优惠提醒",
            "desp": text,
        }).encode()
        req = urllib.request.Request(url, data=body, method="POST")
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
        return data.get("code") == 0


class ClawNotifier(Notifier):
    """微信 Claw 通道（octop-gateway weixin）。

    扫码绑定后获得 account_id + bot_token + base_url，即可向该用户
    主动推送微信消息。实现见 channel_claw.py（异步通道常驻在网关进程中）。
    """

    def __init__(self):
        self.base_url = os.environ.get("CLAW_BASE_URL", "")
        self.bot_token = os.environ.get("CLAW_BOT_TOKEN", "")

    def send(self, user: dict, text: str) -> bool:
        account_id = user.get("claw_account_id")
        if not (self.base_url and self.bot_token and account_id):
            return False
        from .channel_claw import push_text_sync  # 延迟导入，避免无网关环境报错
        return push_text_sync(self.base_url, self.bot_token, account_id, text)


def default_notifier() -> Notifier:
    """按环境变量自动选择可用通道（优先微信 Claw，兜底 Server酱）。"""
    if os.environ.get("CLAW_BOT_TOKEN"):
        return ClawNotifier()
    return ServerChanNotifier()
