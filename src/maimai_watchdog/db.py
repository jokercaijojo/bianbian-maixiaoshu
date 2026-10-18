"""SQLite 存储层：用户、监控规则、已见条目去重。

MVP 阶段单机 SQLite 足够；Token 用 Fernet 加密存储（密钥来自环境变量）。
"""
from __future__ import annotations

import base64
import hashlib
import os
import sqlite3
import time

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT DEFAULT '',
    mcp_token_enc TEXT NOT NULL,
    claw_account_id TEXT DEFAULT '',
    chat_quota_daily INTEGER DEFAULT 100,
    active INTEGER DEFAULT 1,
    created_at REAL,
    user_secret TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS watch_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    keyword TEXT NOT NULL,
    active INTEGER DEFAULT 1,
    UNIQUE(user_id, keyword)
);
CREATE TABLE IF NOT EXISTS seen_items (
    user_id INTEGER NOT NULL,
    item_key TEXT NOT NULL,
    first_seen REAL,
    PRIMARY KEY (user_id, item_key)
);
CREATE TABLE IF NOT EXISTS chat_usage (
    user_id INTEGER NOT NULL,
    day TEXT NOT NULL,
    count INTEGER DEFAULT 0,
    PRIMARY KEY (user_id, day)
);
CREATE TABLE IF NOT EXISTS binding_codes (
    code TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    created_at REAL NOT NULL,
    used INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS checkins (
    user_id INTEGER NOT NULL,
    day TEXT NOT NULL,
    rounds INTEGER DEFAULT 0,
    PRIMARY KEY (user_id, day)
);
CREATE TABLE IF NOT EXISTS unlocks (
    user_id INTEGER NOT NULL,
    card_id TEXT NOT NULL,
    source TEXT DEFAULT '',
    unlocked_at REAL,
    PRIMARY KEY (user_id, card_id)
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    day TEXT NOT NULL,
    type TEXT NOT NULL,
    user_id INTEGER,
    ts REAL
);
CREATE TABLE IF NOT EXISTS guest_usage (
    ip TEXT NOT NULL,
    day TEXT NOT NULL,
    count INTEGER DEFAULT 0,
    PRIMARY KEY (ip, day)
);
CREATE TABLE IF NOT EXISTS user_facts (
    user_id INTEGER NOT NULL,
    fact_key TEXT NOT NULL,
    fact_value TEXT NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY (user_id, fact_key)
);
CREATE TABLE IF NOT EXISTS bind_attempts (
    subject_id TEXT PRIMARY KEY,
    fails INTEGER DEFAULT 0,
    locked_until REAL DEFAULT 0,
    updated_at REAL
);
"""


def _fernet():
    """加密密钥三级获取：环境变量 → .env 文件 → 自动生成并写回 .env。

    绝不在代码里放固定种子——开源仓库里写死密钥等于没加密。
    """
    key = os.environ.get("WATCHDOG_FERNET_KEY", "")
    if not key:
        env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", ".env")
        if os.path.exists(env_path):
            with open(env_path, encoding="utf-8") as f:
                for line in f:
                    if line.strip().startswith("WATCHDOG_FERNET_KEY="):
                        key = line.split("=", 1)[1].strip()
                        break
        if not key:
            # 首次运行：生成随机密钥并写回 .env（.env 已 gitignore）
            from cryptography.fernet import Fernet
            key = Fernet.generate_key().decode()
            with open(env_path, "a", encoding="utf-8") as f:
                f.write(f"\n# 自动生成：user_facts/Token 加密密钥，勿外传\n"
                        f"WATCHDOG_FERNET_KEY={key}\n")
    os.environ.setdefault("WATCHDOG_FERNET_KEY", key)
    from cryptography.fernet import Fernet
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt_token(token: str) -> str:
    return _fernet().encrypt(token.encode()).decode()


def decrypt_token(enc: str) -> str:
    return _fernet().decrypt(enc.encode()).decode()


class Store:
    def __init__(self, path: str):
        # check_same_thread=False：FastAPI 线程池 / asyncio.to_thread 都会跨线程访问
        # timeout=15：webapp 与 daemon 两个进程共写同一个库，默认 5 秒偏短
        self.conn = sqlite3.connect(path, check_same_thread=False, timeout=15)
        # WAL：读写不互相阻塞，避免并发时 "database is locked" 被静默吞掉
        try:
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA busy_timeout=15000")
            self.conn.execute("PRAGMA synchronous=NORMAL")
        except sqlite3.Error:
            pass
        self.conn.executescript(_SCHEMA)
        self._migrate()
        self._purge_old()

    def _migrate(self) -> None:
        """轻量迁移：老库补列。"""
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(users)")}
        if "avatar_card" not in cols:
            self.conn.execute(
                "ALTER TABLE users ADD COLUMN avatar_card TEXT DEFAULT ''")
        if "persona_card" not in cols:
            # 变身皮肤：GLM 蒸馏后的角色人设卡（空=本体麦小薯）
            self.conn.execute(
                "ALTER TABLE users ADD COLUMN persona_card TEXT DEFAULT ''")
        if "persona_pending" not in cols:
            # 待确认的变身候选（JSON：角色全名+百科正文），用户回「对」才生效
            self.conn.execute(
                "ALTER TABLE users ADD COLUMN persona_pending TEXT DEFAULT ''")
        if "user_secret" not in cols:
            # 前端凭它读取自己的卡池，避免 /api/cards 裸 user_id 越权
            self.conn.execute(
                "ALTER TABLE users ADD COLUMN user_secret TEXT DEFAULT ''")
        self.conn.commit()

    def _purge_old(self, code_days: int = 7, event_days: int = 90) -> None:
        """清理过期数据：已用/超期绑定码、久远事件。失败不影响启动。"""
        import datetime
        try:
            cutoff_day = datetime.date.today() - datetime.timedelta(days=code_days)
            cutoff_ts = datetime.datetime.combine(
                cutoff_day, datetime.time()).timestamp()
            self.conn.execute(
                "DELETE FROM binding_codes WHERE created_at < ?", (cutoff_ts,))
            self.conn.execute(
                "DELETE FROM bind_attempts WHERE updated_at < ?", (cutoff_ts,))
            old_events = (datetime.date.today()
                          - datetime.timedelta(days=event_days)).isoformat()
            self.conn.execute("DELETE FROM events WHERE day < ?", (old_events,))
            self.conn.commit()
        except sqlite3.Error:
            pass

    # ---- 用户 ----
    def add_user(self, mcp_token: str, name: str = "") -> int:
        cur = self.conn.execute(
            "INSERT INTO users(name, mcp_token_enc, created_at) VALUES (?,?,?)",
            (name, encrypt_token(mcp_token), time.time()))
        self.conn.commit()
        return cur.lastrowid

    def get_user(self, user_id: int) -> dict | None:
        row = self.conn.execute(
            "SELECT id, name, mcp_token_enc, claw_account_id, chat_quota_daily,"
            " active, avatar_card, persona_card, persona_pending"
            " FROM users WHERE id=?", (user_id,)).fetchone()
        if not row:
            return None
        return {"id": row[0], "name": row[1], "mcp_token": decrypt_token(row[2]),
                "claw_account_id": row[3], "chat_quota_daily": row[4],
                "active": bool(row[5]), "avatar_card": row[6] or "",
                "persona_card": row[7] or "", "persona_pending": row[8] or ""}

    def active_users(self) -> list[dict]:
        rows = self.conn.execute("SELECT id FROM users WHERE active=1").fetchall()
        return [u for r in rows if (u := self.get_user(r[0]))]

    # ---- 规则 ----
    def set_keywords(self, user_id: int, keywords: list[str]) -> None:
        self.conn.execute("DELETE FROM watch_rules WHERE user_id=?", (user_id,))
        self.conn.executemany(
            "INSERT OR IGNORE INTO watch_rules(user_id, keyword) VALUES (?,?)",
            [(user_id, k.strip()) for k in keywords if k.strip()])
        self.conn.commit()

    def get_keywords(self, user_id: int) -> list[str]:
        return [r[0] for r in self.conn.execute(
            "SELECT keyword FROM watch_rules WHERE user_id=? AND active=1",
            (user_id,)).fetchall()]

    # ---- 去重 ----
    def filter_new(self, user_id: int, item_keys: list[str]) -> list[str]:
        new = []
        for k in item_keys:
            cur = self.conn.execute(
                "INSERT OR IGNORE INTO seen_items(user_id, item_key, first_seen)"
                " VALUES (?,?,?)", (user_id, k, time.time()))
            if cur.rowcount:
                new.append(k)
        self.conn.commit()
        return new

    # ---- 聊天配额 ----
    def consume_chat_quota(self, user_id: int, day: str, limit: int) -> bool:
        row = self.conn.execute(
            "SELECT count FROM chat_usage WHERE user_id=? AND day=?",
            (user_id, day)).fetchone()
        count = row[0] if row else 0
        if count >= limit:
            return False
        self.conn.execute(
            "INSERT INTO chat_usage(user_id, day, count) VALUES (?,?,1)"
            " ON CONFLICT(user_id, day) DO UPDATE SET count=count+1",
            (user_id, day))
        self.conn.commit()
        return True

    # ---- 绑定码（网站注册 → 微信关联）----
    CODE_DIGITS = 8          # 原 6 位空间仅 10^6，暴力枚举成本过低
    BIND_MAX_FAILS = 5       # 单微信 subject 连续猜错上限
    BIND_LOCK_SECONDS = 1800  # 触顶后锁定 30 分钟

    def create_binding_code(self, user_id: int) -> str:
        import secrets
        # 加密级随机：random.randint 是可预测的 Mersenne Twister
        space = 10 ** self.CODE_DIGITS
        for _ in range(20):
            code = f"{secrets.randbelow(space):0{self.CODE_DIGITS}d}"
            cur = self.conn.execute(
                "INSERT OR IGNORE INTO binding_codes(code, user_id, created_at)"
                " VALUES (?,?,?)", (code, user_id, time.time()))
            if cur.rowcount:
                self.conn.commit()
                return code
        raise RuntimeError("无法生成唯一绑定码")

    def consume_binding_code(self, code: str, ttl_seconds: int = 1800) -> int | None:
        """核销绑定码，成功返回 user_id；无效/过期/已用返回 None。"""
        row = self.conn.execute(
            "SELECT user_id, created_at, used FROM binding_codes WHERE code=?",
            (code,)).fetchone()
        if not row or row[2] or time.time() - row[1] > ttl_seconds:
            return None
        self.conn.execute("UPDATE binding_codes SET used=1 WHERE code=?", (code,))
        self.conn.commit()
        return row[0]

    # ---- 绑定码防爆破（按微信 subject 计数，不是按码）----
    def bind_lock_remaining(self, subject_id: str) -> int:
        """该 subject 还被锁定多少秒；0 表示可以继续尝试。"""
        row = self.conn.execute(
            "SELECT locked_until FROM bind_attempts WHERE subject_id=?",
            (subject_id,)).fetchone()
        if not row or not row[0]:
            return 0
        left = int(row[0] - time.time())
        return left if left > 0 else 0

    def record_bind_fail(self, subject_id: str) -> int:
        """记一次失败，返回当前连续失败次数（触顶时同时上锁）。"""
        now = time.time()
        row = self.conn.execute(
            "SELECT fails, updated_at FROM bind_attempts WHERE subject_id=?",
            (subject_id,)).fetchone()
        # 距上次失败超过锁定窗口，视为新一轮
        fails = 0
        if row and row[1] and now - float(row[1]) <= self.BIND_LOCK_SECONDS:
            fails = int(row[0] or 0)
        fails += 1
        locked_until = (now + self.BIND_LOCK_SECONDS
                        if fails >= self.BIND_MAX_FAILS else 0)
        self.conn.execute(
            "INSERT INTO bind_attempts(subject_id, fails, locked_until, updated_at)"
            " VALUES (?,?,?,?) ON CONFLICT(subject_id) DO UPDATE SET"
            " fails=excluded.fails, locked_until=excluded.locked_until,"
            " updated_at=excluded.updated_at",
            (subject_id, fails, locked_until, now))
        self.conn.commit()
        return fails

    def clear_bind_fails(self, subject_id: str) -> None:
        self.conn.execute("DELETE FROM bind_attempts WHERE subject_id=?",
                          (subject_id,))
        self.conn.commit()

    # ---- 前端读自己卡池用的口令（替代裸 user_id 越权）----
    def get_user_secret(self, user_id: int) -> str:
        row = self.conn.execute(
            "SELECT user_secret FROM users WHERE id=?", (user_id,)).fetchone()
        if not row:
            return ""
        return row[0] or ""

    def ensure_user_secret(self, user_id: int) -> str:
        """取用户的 user_secret，没有就生成一个（老用户自动补）。"""
        import secrets
        cur = self.get_user_secret(user_id)
        if cur:
            return cur
        cur = secrets.token_urlsafe(16)
        self.conn.execute("UPDATE users SET user_secret=? WHERE id=?",
                          (cur, user_id))
        self.conn.commit()
        return cur

    def verify_user_secret(self, user_id: int, secret: str) -> bool:
        import secrets as _secrets
        real = self.get_user_secret(user_id)
        if not real or not secret:
            return False
        return _secrets.compare_digest(real, secret)

    def bind_claw(self, user_id: int, subject_id: str) -> None:
        self.conn.execute("UPDATE users SET claw_account_id=? WHERE id=?",
                          (subject_id, user_id))
        self.conn.commit()

    # ---- 聊天头像 ----
    def set_avatar(self, user_id: int, card_id: str) -> None:
        self.conn.execute("UPDATE users SET avatar_card=? WHERE id=?",
                          (card_id, user_id))
        self.conn.commit()

    def get_avatar(self, user_id: int) -> str:
        """用户当前头像卡 ID，未设置回退到新手卡。"""
        row = self.conn.execute(
            "SELECT avatar_card FROM users WHERE id=?", (user_id,)).fetchone()
        return (row[0] if row else "") or "03_fries_hand"

    # ---- 变身皮肤 ----
    def set_persona(self, user_id: int, card: str) -> None:
        self.conn.execute(
            "UPDATE users SET persona_card=?, persona_pending='' WHERE id=?",
            (card, user_id))
        self.conn.commit()

    def set_persona_pending(self, user_id: int, pending_json: str) -> None:
        self.conn.execute("UPDATE users SET persona_pending=? WHERE id=?",
                          (pending_json, user_id))
        self.conn.commit()

    def find_user_by_token(self, mcp_token: str) -> dict | None:
        """按明文 Token 找已注册用户（逐行解密比对，小规模够用）。"""
        rows = self.conn.execute("SELECT id FROM users").fetchall()
        for r in rows:
            u = self.get_user(r[0])
            if u and u["mcp_token"] == mcp_token:
                return u
        return None

    # ---- 打卡与抽卡 ----
    CHECKIN_ROUNDS_NEEDED = 2  # 每天聊满 2 轮算打卡一天

    def record_chat_round(self, user_id: int, day: str) -> tuple[int, bool]:
        """记一轮聊天，返回 (今日轮次, 本轮是否刚好达成打卡)。"""
        self.conn.execute(
            "INSERT INTO checkins(user_id, day, rounds) VALUES (?,?,1)"
            " ON CONFLICT(user_id, day) DO UPDATE SET rounds=rounds+1",
            (user_id, day))
        self.conn.commit()
        row = self.conn.execute(
            "SELECT rounds FROM checkins WHERE user_id=? AND day=?",
            (user_id, day)).fetchone()
        rounds = row[0] if row else 0
        return rounds, rounds == self.CHECKIN_ROUNDS_NEEDED

    def rounds_today(self, user_id: int, day: str) -> int:
        row = self.conn.execute(
            "SELECT rounds FROM checkins WHERE user_id=? AND day=?",
            (user_id, day)).fetchone()
        return row[0] if row else 0

    def current_streak(self, user_id: int, today: str) -> int:
        """连续打卡天数（以 today 为终点往前数，仅 rounds 达标的日子算）。"""
        import datetime
        days = {r[0] for r in self.conn.execute(
            "SELECT day FROM checkins WHERE user_id=? AND rounds>=?",
            (user_id, self.CHECKIN_ROUNDS_NEEDED)).fetchall()}
        streak, d = 0, datetime.date.fromisoformat(today)
        while d.isoformat() in days:
            streak += 1
            d -= datetime.timedelta(days=1)
        return streak

    def total_checkin_days(self, user_id: int) -> int:
        """累计打卡天数（断签不清零，解锁档位按这个算）。"""
        row = self.conn.execute(
            "SELECT COUNT(*) FROM checkins WHERE user_id=? AND rounds>=?",
            (user_id, self.CHECKIN_ROUNDS_NEEDED)).fetchone()
        return row[0] if row else 0

    def unlock_card(self, user_id: int, card_id: str, source: str = "") -> bool:
        """解锁一张卡，返回是否新解锁（重复解锁返回 False）。"""
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO unlocks(user_id, card_id, source, unlocked_at)"
            " VALUES (?,?,?,?)", (user_id, card_id, source, time.time()))
        self.conn.commit()
        return cur.rowcount > 0

    def get_unlocks(self, user_id: int) -> list[str]:
        return [r[0] for r in self.conn.execute(
            "SELECT card_id FROM unlocks WHERE user_id=? ORDER BY unlocked_at",
            (user_id,)).fetchall()]

    # ---- 游客体验区（按 IP 限流）----
    GUEST_DAILY_LIMIT = 10

    def consume_guest_quota(self, ip: str, day: str) -> bool:
        row = self.conn.execute(
            "SELECT count FROM guest_usage WHERE ip=? AND day=?",
            (ip, day)).fetchone()
        count = row[0] if row else 0
        if count >= self.GUEST_DAILY_LIMIT:
            return False
        self.conn.execute(
            "INSERT INTO guest_usage(ip, day, count) VALUES (?,?,1)"
            " ON CONFLICT(ip, day) DO UPDATE SET count=count+1",
            (ip, day))
        self.conn.commit()
        return True

    # ---- 运营统计 ----
    def log_event(self, type_: str, user_id: int | None = None) -> None:
        import datetime
        self.conn.execute(
            "INSERT INTO events(day, type, user_id, ts) VALUES (?,?,?,?)",
            (datetime.date.today().isoformat(), type_, user_id, time.time()))
        self.conn.commit()

    def event_counts(self, day: str | None) -> dict:
        """各事件计数；day=None 统计全部，否则只算某天。"""
        out = {"qr_created": 0, "qr_scanned": 0, "register": 0, "bind": 0}
        sql = "SELECT type, COUNT(*) FROM events"
        args: tuple = ()
        if day:
            sql += " WHERE day=?"
            args = (day,)
        sql += " GROUP BY type"
        for t, n in self.conn.execute(sql, args):
            if t in out:
                out[t] = n
        return out

    def chat_rounds_on(self, day: str) -> int:
        row = self.conn.execute(
            "SELECT COALESCE(SUM(count),0) FROM chat_usage WHERE day=?",
            (day,)).fetchone()
        return row[0] if row else 0

    def rounds_on(self, user_id: int, day: str) -> int:
        row = self.conn.execute(
            "SELECT count FROM chat_usage WHERE user_id=? AND day=?",
            (user_id, day)).fetchone()
        return row[0] if row else 0

    def all_users(self) -> list[dict]:
        rows = self.conn.execute("SELECT id FROM users ORDER BY id").fetchall()
        return [u for r in rows if (u := self.get_user(r[0]))]

    # ---- 长期记忆（user_facts）----
    MAX_FACTS_PER_USER = 50

    def upsert_fact(self, user_id: int, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO user_facts(user_id, fact_key, fact_value, updated_at)"
            " VALUES (?,?,?,?)"
            " ON CONFLICT(user_id, fact_key) DO UPDATE SET"
            " fact_value=excluded.fact_value, updated_at=excluded.updated_at",
            (user_id, key.strip()[:40], value.strip()[:200], time.time()))
        # 超上限时淘汰最久未更新的
        self.conn.execute(
            "DELETE FROM user_facts WHERE user_id=? AND fact_key NOT IN"
            " (SELECT fact_key FROM user_facts WHERE user_id=?"
            "  ORDER BY updated_at DESC LIMIT ?)",
            (user_id, user_id, self.MAX_FACTS_PER_USER))
        self.conn.commit()

    def get_facts(self, user_id: int, limit: int = 20) -> list[tuple[str, str]]:
        return [(r[0], r[1]) for r in self.conn.execute(
            "SELECT fact_key, fact_value FROM user_facts"
            " WHERE user_id=? ORDER BY updated_at DESC LIMIT ?",
            (user_id, limit)).fetchall()]

    def delete_fact(self, user_id: int, key: str) -> bool:
        cur = self.conn.execute(
            "DELETE FROM user_facts WHERE user_id=?"
            " AND (fact_key LIKE ? OR fact_value LIKE ?)",
            (user_id, f"%{key}%", f"%{key}%"))
        self.conn.commit()
        return cur.rowcount > 0
