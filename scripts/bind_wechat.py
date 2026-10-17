"""微信 ClawBot 扫码绑定：生成二维码 → 手机微信扫码 → 保存凭证。

用法：.venv/Scripts/python scripts/bind_wechat.py
扫码成功后凭证写入 claw_credentials.json（已 gitignore，勿外传）。
"""
from __future__ import annotations

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import segno  # noqa: E402
from octop_gateway.channels.weixin.login_qr import WeixinQRLogin  # noqa: E402

CRED_FILE = os.path.join(os.path.dirname(__file__), "..", "claw_credentials.json")
QR_PNG = os.path.join(os.path.dirname(__file__), "..", "claw_bind_qr.png")


async def main() -> int:
    login = WeixinQRLogin()
    print("正在向微信申请二维码……")
    qr = await login.fetch_qr_code()
    segno.make(qr.qrcode_img_content).save(QR_PNG, scale=8)
    print(f"二维码已保存: {os.path.abspath(QR_PNG)}")
    print("二维码链接:", qr.qrcode_img_content)
    try:
        os.startfile(QR_PNG)  # Windows 直接弹图
    except OSError:
        pass
    print("\n请用手机微信扫码并确认绑定（有效期约 8 分钟）……")

    result = await login.wait_for_login(qr.qrcode)
    if not result.connected:
        print("绑定失败:", result.message)
        return 1
    creds = {
        "account_id": result.account_id,
        "bot_token": result.bot_token,
        "base_url": result.base_url,
        "user_id": result.user_id,
    }
    with open(CRED_FILE, "w", encoding="utf-8") as f:
        json.dump(creds, f, ensure_ascii=False, indent=2)
    print(f"\n✅ 绑定成功！account_id={result.account_id}")
    print(f"凭证已保存: {os.path.abspath(CRED_FILE)}")
    print("现在你的微信通讯录里应该已经出现 ClawBot 联系人，")
    print("给它随便发一句话（比如「你好」），它才能回推消息给你。")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
