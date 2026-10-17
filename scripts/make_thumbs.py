"""为新卡图预生成 480px 聊天头像缩略图。

注意：项目 venv 没装 Pillow，请用系统/托管 Python 跑：
  python scripts/make_thumbs.py
新配饰卡图放进 static/img/cards/ 后跑一次即可（已存在的会跳过）。
"""
from __future__ import annotations

import glob
import os

from PIL import Image

CARDS = os.path.join(os.path.dirname(__file__), "..",
                     "src", "maimai_watchdog", "static", "img", "cards")
THUMBS = os.path.join(CARDS, "thumbs")
os.makedirs(THUMBS, exist_ok=True)

for p in sorted(glob.glob(os.path.join(CARDS, "*.png"))):
    out = os.path.join(THUMBS, os.path.basename(p))
    if os.path.exists(out):
        continue
    Image.open(p).resize((480, 480), Image.LANCZOS).save(out, optimize=True)
    print("生成", os.path.basename(out))
print("完成")
