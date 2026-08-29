"""SQLite 持久化（架构 §5.1）。

向量与语料正文在 Qdrant；这里只存业务元数据与管理态字段，
管理后台列表查询走本表，避免拉取向量。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from server.models import CorpusEntry, InterviewConfig, Message, Report, utc_now

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    config JSON NOT NULL,
    state TEXT NOT NULL,
    turns INTEGER DEFAULT 0,
    asked_corpus_ids JSON DEFAULT '[]',
    created_at TEXT NOT NULL,
    ended_at TEXT
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT REFERENCES sessions(id),
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reports (
    session_id TEXT PRIMARY KEY REFERENCES sessions(id),
    report JSON NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS corpus_meta (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    role TEXT NOT NULL,
    tags JSON DEFAULT '[]',
    source TEXT NOT NULL,
    status TEXT NOT NULL,
    version INTEGER DEFAULT 1,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id);
CREATE INDEX IF NOT EXISTS idx_corpus_role_kind ON corpus_meta(role, kind, status);
"""


class Storage:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # -- 会话 --------------------------------------------------------------

    def upsert_session(
        self,
        *,
        session_id: str,
        config: InterviewConfig,
        state: str,
        turns: int,
        asked_corpus_ids: list[str],
        created_at: str,
        ended_at: str | None = None,
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO sessions (id, config, state, turns, asked_corpus_ids, created_at, ended_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                state = excluded.state,
                turns = excluded.turns,
                asked_corpus_ids = excluded.asked_corpus_ids,
                ended_at = excluded.ended_at
            """,
            (
                session_id,
                config.model_dump_json(),
                state,
                turns,
                json.dumps(asked_corpus_ids),
                created_at,
                ended_at,
            ),
        )
        self._conn.commit()

    def append_message(self, session_id: str, message: Message) -> None:
        self._conn.execute(
            "INSERT INTO messages (session_id, role, content, created_at) VALUES (?, ?, ?, ?)",
            (session_id, message.role, message.content, message.created_at),
        )
        self._conn.commit()

    def save_report(self, session_id: str, report: Report) -> None:
        self._conn.execute(
            """
            INSERT INTO reports (session_id, report, created_at) VALUES (?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET report = excluded.report
            """,
            (session_id, report.model_dump_json(), utc_now()),
        )
        self._conn.commit()

    # -- 语料元数据 --------------------------------------------------------

    def upsert_corpus_meta(self, entries: list[CorpusEntry]) -> None:
        self._conn.executemany(
            """
            INSERT INTO corpus_meta (id, kind, role, tags, source, status, version, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                kind = excluded.kind,
                role = excluded.role,
                tags = excluded.tags,
                source = excluded.source,
                status = excluded.status,
                version = excluded.version,
                updated_at = excluded.updated_at
            """,
            [
                (e.id, e.kind, e.role, json.dumps(e.tags), e.source, e.status, e.version, e.updated_at)
                for e in entries
            ],
        )
        self._conn.commit()

    def set_corpus_status(self, ids: list[str], status: str) -> None:
        self._conn.executemany(
            "UPDATE corpus_meta SET status = ?, updated_at = ? WHERE id = ?",
            [(status, utc_now(), cid) for cid in ids],
        )
        self._conn.commit()

    def list_corpus_meta(
        self,
        *,
        role: str | None = None,
        kind: str | None = None,
        status: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        for column, value in (("role", role), ("kind", kind), ("status", status)):
            if value:
                clauses.append(f"{column} = ?")
                params.append(value)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        rows = self._conn.execute(
            f"SELECT * FROM corpus_meta {where} ORDER BY updated_at DESC LIMIT ?", params
        ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["tags"] = json.loads(item.get("tags") or "[]")
            result.append(item)
        return result

    def corpus_stats(self) -> dict[str, int]:
        rows = self._conn.execute(
            "SELECT status, COUNT(*) AS n FROM corpus_meta GROUP BY status"
        ).fetchall()
        return {row["status"]: row["n"] for row in rows}
