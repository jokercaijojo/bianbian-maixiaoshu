"""每日数据库备份（幂等，可以反复调用）。

由 watchdog.bat 在每轮巡检时调用：当天已有备份就直接退出，几乎零开销。
用 sqlite3 的 backup API 而不是直接复制文件——库已开 WAL，
裸 copy 可能漏掉还没 checkpoint 的内容。

用法：.venv\\Scripts\\python.exe scripts\\backup_db.py
"""
from __future__ import annotations

import datetime
import os
import sqlite3
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SRC = os.path.join(ROOT, "watchdog.db")
OUT_DIR = os.path.abspath(os.path.join(ROOT, "..", "..", "backup", "daily"))
KEEP_DAYS = 7


def main() -> int:
    if not os.path.exists(SRC):
        print("source db not found:", SRC)
        return 1
    os.makedirs(OUT_DIR, exist_ok=True)
    today = datetime.date.today().strftime("%Y%m%d")
    dst = os.path.join(OUT_DIR, f"watchdog-{today}.db")
    if os.path.exists(dst) and os.path.getsize(dst) > 0:
        _prune()
        return 0
    src = sqlite3.connect(SRC, timeout=15)
    try:
        out = sqlite3.connect(dst)
        try:
            src.backup(out)
        finally:
            out.close()
    finally:
        src.close()
    _prune()
    print("backup written:", dst)
    return 0


def _prune() -> None:
    cutoff = (datetime.date.today()
              - datetime.timedelta(days=KEEP_DAYS)).strftime("%Y%m%d")
    try:
        for name in os.listdir(OUT_DIR):
            if not (name.startswith("watchdog-") and name.endswith(".db")):
                continue
            stamp = name[len("watchdog-"):-len(".db")]
            if stamp < cutoff:
                try:
                    os.remove(os.path.join(OUT_DIR, name))
                except OSError:
                    pass
    except OSError:
        pass


if __name__ == "__main__":
    sys.exit(main())
