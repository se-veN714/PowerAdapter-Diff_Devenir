"""PAdif SQLite 存储层。

store.py 是 SQLite 的「唯一」出入口：其余模块（app.py / version.py / differ.py）
都不得直接 sqlite3.connect，统一经由本模块读写，确保存储语义集中、易迁移。

数据模型：
  Article(id, path, title, created_at, current_version_id)
  Version(id, article_id, content, commit_message, version, version_kind, created_at, diff_stats)
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "padif.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS articles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL,
    created_at TEXT NOT NULL,
    current_version_id INTEGER
);
CREATE TABLE IF NOT EXISTS versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id INTEGER NOT NULL,
    content TEXT NOT NULL,
    commit_message TEXT NOT NULL,
    version TEXT NOT NULL,
    version_kind TEXT NOT NULL,
    created_at TEXT NOT NULL,
    diff_stats TEXT,
    FOREIGN KEY (article_id) REFERENCES articles(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_versions_article ON versions(article_id, id);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    """初始化数据库与表结构（幂等）。"""
    with _connect() as conn:
        conn.executescript(_SCHEMA)


def save_article(path: str, title: str) -> int:
    """写入/复用一个 Article（按 path 去重），返回 article_id。"""
    with _connect() as conn:
        row = conn.execute("SELECT id FROM articles WHERE path = ?", (path,)).fetchone()
        if row:
            return row["id"]
        cur = conn.execute(
            "INSERT INTO articles (path, title, created_at) VALUES (?, ?, ?)",
            (path, title, _now()),
        )
        return int(cur.lastrowid)


def save_version(
    article_id: int,
    content: str,
    commit_message: str,
    version: str,
    version_kind: str,
    diff_stats: Optional[dict] = None,
) -> int:
    """保存一个版本，并更新 Article.current_version_id，返回 version_id。"""
    stats_json = json.dumps(diff_stats, ensure_ascii=False) if diff_stats else None
    with _connect() as conn:
        cur = conn.execute(
            """INSERT INTO versions
               (article_id, content, commit_message, version, version_kind, created_at, diff_stats)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (article_id, content, commit_message, version, version_kind, _now(), stats_json),
        )
        vid = int(cur.lastrowid)
        conn.execute(
            "UPDATE articles SET current_version_id = ? WHERE id = ?",
            (vid, article_id),
        )
        return vid


def get_articles() -> list:
    with _connect() as conn:
        rows = conn.execute(
            """SELECT a.id, a.path, a.title, a.current_version_id,
                      (SELECT version FROM versions v WHERE v.id = a.current_version_id) AS current_version
               FROM articles a ORDER BY a.id"""
        ).fetchall()
        return [dict(r) for r in rows]


def get_versions(article_id: int) -> list:
    with _connect() as conn:
        rows = conn.execute(
            """SELECT id, version, version_kind, commit_message, created_at
               FROM versions WHERE article_id = ? ORDER BY id""",
            (article_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_version(version_id: int) -> Optional[dict]:
    with _connect() as conn:
        row = conn.execute(
            "SELECT id, article_id, content, commit_message, version, version_kind, created_at, diff_stats FROM versions WHERE id = ?",
            (version_id,),
        ).fetchone()
        return dict(row) if row else None


def get_latest_version(article_id: int) -> Optional[dict]:
    with _connect() as conn:
        row = conn.execute(
            """SELECT id, article_id, content, commit_message, version, version_kind, created_at, diff_stats
               FROM versions WHERE article_id = ? ORDER BY id DESC LIMIT 1""",
            (article_id,),
        ).fetchone()
        return dict(row) if row else None
