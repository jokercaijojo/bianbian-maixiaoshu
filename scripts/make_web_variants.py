"""为网页端预生成 WebP 轻量变体（原 PNG 保留，作为老浏览器兜底）。

背景：原始卡图是 1024×1024 / 1024×1536 的 PNG，单张 1.1–1.5MB。
网页里实际显示尺寸远小于此（首页头像 132px、气泡头像 30px、
抽卡屋卡柱 176×262、放大图最大 360px），直接引用原图导致
一个页面要拖 5MB+ ，手机 4G/5G 下首屏要等好几秒。

本脚本按「显示尺寸 × 3 倍屏」生成四套变体：
  web/avatar/  400×400   首页 hero 头像（132px × 3）
  web/face/    120×120   聊天气泡头像（30px × 4）
  web/ring/    宽 540    抽卡屋 3D 卡柱（176×262 CSS × 3）
  web/zoom/    宽 900    抽卡放大揭示（≤360px CSS × 2.5，留余量）

用法（venv 里已装 Pillow）：
  .venv\\Scripts\\python.exe scripts\\make_web_variants.py
  .venv\\Scripts\\python.exe scripts\\make_web_variants.py --force   # 强制重建

新增卡图后跑一次即可；已存在且比源文件新的会自动跳过。
"""
from __future__ import annotations

import argparse
import glob
import os

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
CARDS = os.path.normpath(os.path.join(
    HERE, "..", "src", "maimai_watchdog", "static", "img", "cards"))

# 变体名 -> (源目录, 目标尺寸, 质量)
#   目标尺寸：int 表示等比缩到该宽度；tuple 表示居中裁成正方形后缩放
VARIANTS = {
    "avatar": ("", (400, 400), 82),          # 正方形卡图裁方
    "face":   ("", (120, 120), 85),
    "ring":   ("portrait", 540, 80),         # 竖版卡面等比缩宽
    "zoom":   ("portrait", 900, 82),
}


def _out_path(variant: str, card_id: str) -> str:
    return os.path.join(CARDS, "web", variant, f"{card_id}.webp")


def _make(src: str, dst: str, size, quality: int) -> int:
    im = Image.open(src).convert("RGB")
    if isinstance(size, tuple):
        # 居中裁成正方形再缩放（原图已经是正方形也走这条，保证一致）
        w, h = im.size
        side = min(w, h)
        im = im.crop(((w - side) // 2, (h - side) // 2,
                      (w + side) // 2, (h + side) // 2))
        im = im.resize(size, Image.LANCZOS)
    else:
        w, h = im.size
        nh = round(h * size / w)
        im = im.resize((size, nh), Image.LANCZOS)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    im.save(dst, "WEBP", quality=quality, method=6)
    return os.path.getsize(dst)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="忽略已存在的产物，全部重建")
    args = ap.parse_args()

    before = sum(os.path.getsize(p) for p in glob.glob(os.path.join(CARDS, "*", "*.png"))
                 ) + sum(os.path.getsize(p) for p in glob.glob(os.path.join(CARDS, "*.png")))
    after = 0
    made = 0
    for variant, (sub, size, quality) in VARIANTS.items():
        srcdir = os.path.join(CARDS, sub) if sub else CARDS
        for src in sorted(glob.glob(os.path.join(srcdir, "*.png"))):
            card_id = os.path.splitext(os.path.basename(src))[0]
            dst = _out_path(variant, card_id)
            if (not args.force and os.path.exists(dst)
                    and os.path.getmtime(dst) >= os.path.getmtime(src)):
                after += os.path.getsize(dst)
                continue
            kb = _make(src, dst, size, quality) / 1024
            after += kb * 1024
            made += 1
            print(f"  {variant:6s} {card_id:22s} {kb:7.1f} KB")
    total_before = where = len(glob.glob(os.path.join(CARDS, "web", "*", "*.webp")))
    print(f"\n本次生成 {made} 个文件，共 {where} 个 webp 变体")
    print(f"PNG 合计 {before/1024/1024:.1f} MB  →  webp 合计 {after/1024/1024:.2f} MB")


if __name__ == "__main__":
    main()
