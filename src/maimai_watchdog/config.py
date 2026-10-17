"""配置与环境加载。"""
from __future__ import annotations

import os

DEFAULT_DB = os.path.join(os.path.dirname(__file__), "..", "..", "watchdog.db")


def load_env(env_path: str | None = None) -> None:
    """从 .env 读取配置（不覆盖已有环境变量）。"""
    if env_path is None:
        here = os.path.dirname(os.path.abspath(__file__))
        env_path = os.path.join(here, "..", "..", ".env")
    if not os.path.exists(env_path):
        return
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())
