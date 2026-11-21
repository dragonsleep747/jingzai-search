# -*- coding: utf-8 -*-
"""catalog.db 结构与打开/迁移。

设计要点（对应 NATIVE_DISCOVERY_PLAN.md 最小契约）：

- 四类核心记录：sources / objects / entries / checkpoints，外加 events 与
  failures 支撑可重放同步与错误追踪。
- 路径与对象身份分开：objects 以 (source_id, volume_serial, file_id) 识别
  同一对象；entries 保存每条路径（同一对象的多条硬链接路径各自保留）。
- 显示路径（path/name）与匹配键（match_path/match_name，Windows 下大小写
  不敏感）分开存储。
- 版本用 PRAGMA user_version 管理，独立于旧 index.db。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_VERSION = 3

DDL = """
CREATE TABLE IF NOT EXISTS sources (
    source_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    root TEXT NOT NULL,
    volume_serial INTEGER,
    volume_guid TEXT,
    filesystem TEXT,
    provider TEXT NOT NULL DEFAULT 'generic',
    state TEXT NOT NULL DEFAULT 'new',
    online INTEGER NOT NULL DEFAULT 1,
    discovered_count INTEGER NOT NULL DEFAULT 0,
    failed_count INTEGER NOT NULL DEFAULT 0,
    last_scan_at REAL,
    last_scan_generation INTEGER,
    needs_rescan INTEGER NOT NULL DEFAULT 0,
    rescan_reason TEXT,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS objects (
    object_id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id TEXT NOT NULL,
    volume_serial INTEGER,
    file_id INTEGER,
    is_dir INTEGER NOT NULL DEFAULT 0,
    UNIQUE(source_id, volume_serial, file_id)
);

CREATE TABLE IF NOT EXISTS entries (
    entry_id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id TEXT NOT NULL,
    object_id INTEGER,
    parent_object_id INTEGER,
    parent_path TEXT NOT NULL,
    name TEXT NOT NULL,
    path TEXT NOT NULL,
    match_name TEXT NOT NULL,
    match_path TEXT NOT NULL,
    ext TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL,
    size INTEGER,
    mtime_ns INTEGER,
    status TEXT NOT NULL DEFAULT 'present',
    scan_generation INTEGER,
    content_version INTEGER NOT NULL DEFAULT 0,
    UNIQUE(source_id, path)
);

CREATE INDEX IF NOT EXISTS idx_entries_src_path ON entries(source_id, path);
CREATE INDEX IF NOT EXISTS idx_entries_src_matchpath ON entries(source_id, match_path);
CREATE INDEX IF NOT EXISTS idx_entries_src_matchname ON entries(source_id, match_name);
CREATE INDEX IF NOT EXISTS idx_entries_src_parent ON entries(source_id, parent_path);
CREATE INDEX IF NOT EXISTS idx_entries_src_ext ON entries(source_id, ext);

CREATE TABLE IF NOT EXISTS events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id TEXT NOT NULL,
    seq INTEGER,
    change_type TEXT NOT NULL,
    object_id INTEGER,
    old_parent_path TEXT,
    new_parent_path TEXT,
    old_name TEXT,
    new_name TEXT,
    content_version INTEGER,
    applied INTEGER NOT NULL DEFAULT 0,
    applied_at REAL
);
CREATE INDEX IF NOT EXISTS idx_events_src_seq ON events(source_id, seq);

CREATE TABLE IF NOT EXISTS checkpoints (
    source_id TEXT PRIMARY KEY,
    provider TEXT,
    cursor TEXT,
    journal_id TEXT,
    generation INTEGER,
    committed_at REAL
);

CREATE TABLE IF NOT EXISTS failures (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id TEXT,
    path TEXT,
    stage TEXT,
    error TEXT,
    ts REAL
);
CREATE INDEX IF NOT EXISTS idx_failures_ts ON failures(ts);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""

FTS_DDL = """
CREATE VIRTUAL TABLE IF NOT EXISTS entries_fts USING fts5(
    match_name, match_path,
    content='entries', content_rowid='entry_id',
    tokenize='trigram'
);
CREATE TRIGGER IF NOT EXISTS efts_ai AFTER INSERT ON entries BEGIN
    INSERT INTO entries_fts(rowid, match_name, match_path)
    VALUES (new.entry_id, new.match_name, new.match_path);
END;
CREATE TRIGGER IF NOT EXISTS efts_au AFTER UPDATE OF match_name, match_path ON entries BEGIN
    INSERT INTO entries_fts(entries_fts, rowid, match_name, match_path)
    VALUES('delete', old.entry_id, old.match_name, old.match_path);
    INSERT INTO entries_fts(rowid, match_name, match_path)
    VALUES(new.entry_id, new.match_name, new.match_path);
END;
CREATE TRIGGER IF NOT EXISTS efts_ad AFTER DELETE ON entries BEGIN
    INSERT INTO entries_fts(entries_fts, rowid, match_name, match_path)
    VALUES('delete', old.entry_id, old.match_name, old.match_path);
END;
"""


def open_catalog(db_path: str, read_only: bool = False) -> sqlite3.Connection:
    """打开 catalog.db；首次创建/迁移，返回连接（不启动任何扫描/监听）。"""
    if read_only:
        conn = sqlite3.connect(Path(db_path).resolve().as_uri() + '?mode=ro', uri=True, timeout=30)
    else:
        conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
    if tables and not {'sources', 'objects', 'entries', 'checkpoints'}.issubset(tables):
        conn.close()
        raise sqlite3.DatabaseError('not a discovery catalog; refusing to modify an unrelated database')
    if conn.execute('PRAGMA user_version').fetchone()[0] > SCHEMA_VERSION:
        conn.close()
        raise sqlite3.DatabaseError('discovery catalog is newer than this module')
    try:
        conn.execute('PRAGMA busy_timeout=30000')
    except sqlite3.DatabaseError:
        pass
    if not read_only:
        try:
            conn.execute('PRAGMA journal_mode=WAL')
        except sqlite3.DatabaseError:
            pass
        try:
            _migrate(conn)
        except Exception:
            conn.close()
            raise
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    version = conn.execute('PRAGMA user_version').fetchone()[0]
    if version > SCHEMA_VERSION:
        raise sqlite3.DatabaseError(f'catalog schema {version} is newer than supported {SCHEMA_VERSION}')
    if version < 1:
        conn.executescript(DDL)
        conn.commit()
    if version < 2:
        # 带前缀的十六进制 TEXT 避免 SQLite INTEGER/REAL 截断 128 位文件 ID。
        conn.execute('BEGIN IMMEDIATE')
        version = conn.execute('PRAGMA user_version').fetchone()[0]
        if version >= 2:
            conn.commit()
            return
        last = 0
        while True:
            rows = conn.execute('SELECT object_id,file_id FROM objects WHERE object_id>? '
                'AND file_id IS NOT NULL ORDER BY object_id LIMIT 500',(last,)).fetchall()
            if not rows:
                break
            for row in rows:
                value = row['file_id']
                if not isinstance(value, str) or not value.startswith('f:'):
                    number = int(value)
                    if number < 0:
                        number += 1 << 64
                    conn.execute('UPDATE objects SET file_id=? WHERE object_id=?',
                                 (f'f:{number:032x}', row['object_id']))
            last = rows[-1]['object_id']
        conn.execute('ALTER TABLE events ADD COLUMN dedupe_key TEXT')
        conn.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_events_dedupe ON events(source_id, dedupe_key)')
        conn.execute('PRAGMA user_version=2')
        conn.commit()
    if version < 3:
        conn.execute('BEGIN IMMEDIATE')
        version = conn.execute('PRAGMA user_version').fetchone()[0]
        if version >= 3:
            conn.commit()
            return
        conn.executescript(FTS_DDL)
        conn.execute("INSERT INTO entries_fts(entries_fts) VALUES('rebuild')")
        conn.execute('PRAGMA user_version=3')
        conn.commit()
