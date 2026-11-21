# -*- coding: utf-8 -*-
"""catalog.db 访问层：来源登记、批量写入、字面查询、checkpoint 与事件重放。

所有公开方法按需打开连接，不持有长连接；导入本模块无副作用。
事件应用在**单一连接事务**内完成（apply_pending / apply_usn_batch），
避免跨连接写库互相锁死。
"""
from __future__ import annotations

import os
import sqlite3
import time
import hashlib
import json
from pathlib import Path
from contextlib import contextmanager

from .filters import escape_like, parse_ext_filter, io_path
from .model import (
    ChangeEvent, FileHit, SearchPage, SearchRequest, SourceInfo, CoverageReport,
)
from .schema import open_catalog
from .errors import ReconciliationRequired, Cancelled

_UNSET = object()


def normcase(path: str) -> str:
    return os.path.normcase(os.path.normpath(path)).casefold()


def signed64(v):
    """把可能为无符号 64 位的文件 ID（如 NTFS st_ino / FRN）转成有符号
    64 位，以适配 SQLite 的 INTEGER（signed 64-bit）存储。"""
    if v is None:
        return None
    v = int(v)
    if v >= 1 << 63:
        v -= 1 << 64
    return v


def file_id_key(value):
    if value is None:
        return None
    if isinstance(value, str) and value.startswith('f:'):
        return value
    value = int(value)
    if value < 0:
        value += 1 << 64
    if not 0 <= value < 1 << 128:
        raise ValueError('file ID outside 128-bit range')
    return f'f:{value:032x}'


def casefold_name(name: str) -> str:
    return str(name).casefold()


def _fts_phrase(query: str) -> str:
    return '"' + query.replace('"', '""') + '"'


def _stat_entry_row(source_id: str, path: str, *, scan_generation=None):
    """对磁盘路径做轻量 stat，生成条目行（不打开文件内容）。失败返回 None。"""
    try:
        st = os.lstat(io_path(path))
    except OSError:
        return None
    kind = 'file'
    is_dir = False
    if os.path.isdir(io_path(path)):
        kind, is_dir = 'dir', True
    if os.path.islink(io_path(path)) or _is_junction(path):
        kind = 'link'
    name = os.path.basename(path.rstrip('\\/')) or path.rstrip('\\/')
    parent = os.path.dirname(path.rstrip('\\/'))
    ext = ''
    if kind == 'file':
        ext = os.path.splitext(name)[1].lstrip('.').lower()
    return {
        'source_id': source_id,
        'parent_path': parent,
        'name': name,
        'path': path,
        'match_name': casefold_name(name),
        'match_path': normcase(path),
        'ext': ext,
        'kind': kind,
        'size': st.st_size,
        'mtime_ns': st.st_mtime_ns,
        'volume_serial': signed64(getattr(st, 'st_dev', None)),
        'file_id': getattr(st, 'st_ino', None) or None,
        'is_dir': 1 if is_dir else 0,
        'scan_generation': scan_generation,
    }


def _is_junction(path: str) -> bool:
    try:
        st = os.lstat(io_path(path))
        if os.name == 'nt':
            # FILE_ATTRIBUTE_REPARSE_POINT = 0x400
            return bool(getattr(st, 'st_file_attributes', 0) & 0x400) and \
                os.path.isdir(io_path(path))
    except OSError:
        return False
    return False


class Catalog:
    """目录数据库访问层。连接按调用打开，跨线程安全（每连接独立）。"""

    def __init__(self, db_path: str):
        self.db_path = os.path.abspath(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        open_catalog(self.db_path).close()
        self._fts_ready = None

    # ------------------------------------------------------------------ util
    @contextmanager
    def _conn(self, read_only=False):
        conn = open_catalog(self.db_path, read_only=read_only)
        try:
            yield conn
        finally:
            conn.close()

    def is_internal_path(self, path):
        candidate = normcase(os.path.abspath(path))
        return candidate in {normcase(self.db_path + suffix) for suffix in ('', '-wal', '-shm', '-journal')}

    # --------------------------------------------------------------- sources
    def register_source(self, info: SourceInfo) -> None:
        with self._conn() as conn:
            previous = conn.execute('SELECT root FROM sources WHERE source_id=?',(info.source_id,)).fetchone()
            conn.execute(
                'INSERT INTO sources (source_id, kind, root, volume_serial, '
                'volume_guid, filesystem, provider, state, online, '
                'discovered_count, failed_count, last_scan_at, '
                'last_scan_generation, needs_rescan, rescan_reason, created_at) '
                'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) '
                'ON CONFLICT(source_id) DO UPDATE SET '
                'kind=excluded.kind, root=excluded.root, '
                'volume_serial=excluded.volume_serial, '
                'volume_guid=excluded.volume_guid, '
                'filesystem=excluded.filesystem, provider=excluded.provider, '
                'online=excluded.online',
                (info.source_id, info.kind, info.root, info.volume_serial,
                 info.volume_guid, info.filesystem, info.provider, info.state,
                 1 if info.online else 0, info.discovered_count,
                 info.failed_count, info.last_scan_at,
                 info.last_scan_generation,
                 1 if info.needs_rescan else 0, info.rescan_reason, time.time()),
            )
            conn.commit()
        if previous and normcase(previous['root']) != normcase(info.root):
            self.set_source_state(info.source_id,'needs_rescan',needs_rescan=True,online=False,
                                  rescan_reason='source mount path changed')

    def list_sources(self) -> list:
        with self._conn(read_only=True) as conn:
            rows = conn.execute(
                'SELECT * FROM sources ORDER BY source_id').fetchall()
        return [dict(r) for r in rows]

    def get_source(self, source_id: str):
        with self._conn(read_only=True) as conn:
            row = conn.execute(
                'SELECT * FROM sources WHERE source_id=?',
                (source_id,)).fetchone()
        return dict(row) if row else None

    def set_source_state(self, source_id: str, state: str, *,
                         discovered_count=None, failed_count=None,
                         needs_rescan=None, rescan_reason=_UNSET,
                         last_scan_at=None, generation=None, online=None,
                         provider=None) -> None:
        sets, args = [], []
        if state is not None:
            sets.append('state=?'); args.append(state)
        if discovered_count is not None:
            sets.append('discovered_count=?'); args.append(discovered_count)
        if failed_count is not None:
            sets.append('failed_count=?'); args.append(failed_count)
        if needs_rescan is not None:
            sets.append('needs_rescan=?'); args.append(1 if needs_rescan else 0)
        if rescan_reason is not _UNSET:
            sets.append('rescan_reason=?'); args.append(rescan_reason)
        if last_scan_at is not None:
            sets.append('last_scan_at=?'); args.append(last_scan_at)
        if generation is not None:
            sets.append('last_scan_generation=?'); args.append(generation)
        if online is not None:
            sets.append('online=?'); args.append(1 if online else 0)
        if provider is not None:
            sets.append('provider=?'); args.append(provider)
        if not sets:
            return
        args.append(source_id)
        with self._conn() as conn:
            conn.execute(
                f'UPDATE sources SET {", ".join(sets)} WHERE source_id=?', args)
            conn.commit()

    # ---------------------------------------------------------------- entries
    def _insert_rows(self, conn: sqlite3.Connection, rows: list) -> int:
        if not rows:
            return 0
        rows = [dict(row) for row in rows]
        obj_rows = []
        seen = set()
        for r in rows:
            r['file_id'] = file_id_key(r.get('file_id'))
            if r['file_id'] is not None and r.get('volume_serial') is None:
                r['volume_serial'] = 0
            key = (r['source_id'], r.get('volume_serial'), r.get('file_id'))
            if key in seen or key[2] is None:
                continue
            seen.add(key)
            obj_rows.append((r['source_id'], r.get('volume_serial'),
                             r.get('file_id'), r.get('is_dir', 0)))
        if obj_rows:
            conn.executemany(
                'INSERT OR IGNORE INTO objects '
                '(source_id, volume_serial, file_id, is_dir) '
                'VALUES (?,?,?,?)', obj_rows)
        oid_map = {}
        grouped = {}
        for sid, serial, fid, _ in obj_rows:
            grouped.setdefault((sid, serial), []).append(fid)
        for (sid, serial), ids in grouped.items():
            for offset in range(0, len(ids), 400):
                batch = ids[offset:offset + 400]
                for row in conn.execute(
                    'SELECT source_id, volume_serial, file_id, object_id FROM objects '
                    'WHERE source_id=? AND volume_serial IS ? AND file_id IN (' + ','.join('?' * len(batch)) + ')',
                    [sid, serial, *batch]):
                    oid_map[(row['source_id'], row['volume_serial'], row['file_id'])] = row['object_id']
        payload = []
        for r in rows:
            oid = None
            if r.get('file_id') is not None:
                oid = oid_map.get(
                    (r['source_id'], r.get('volume_serial'), r.get('file_id')))
            payload.append((
                r['source_id'], oid, r.get('parent_object_id'),
                r['parent_path'], r['name'], r['path'],
                r['match_name'], r['match_path'], r['ext'], r['kind'],
                r.get('size'), r.get('mtime_ns'),
                'present', r.get('scan_generation'),
            ))
        conn.executemany(
            'INSERT INTO entries (source_id, object_id, parent_object_id, '
            'parent_path, name, path, match_name, match_path, ext, kind, '
            'size, mtime_ns, status, scan_generation) '
            'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?) '
            'ON CONFLICT(source_id, path) DO UPDATE SET '
            'content_version=entries.content_version + CASE WHEN '
            'entries.object_id IS NOT excluded.object_id OR entries.size IS NOT excluded.size '
            'OR entries.mtime_ns IS NOT excluded.mtime_ns THEN 1 ELSE 0 END, '
            'object_id=excluded.object_id, '
            'parent_object_id=excluded.parent_object_id, '
            'parent_path=excluded.parent_path, name=excluded.name, '
            'match_name=excluded.match_name, '
            'match_path=excluded.match_path, ext=excluded.ext, '
            'kind=excluded.kind, size=excluded.size, '
            'mtime_ns=excluded.mtime_ns, status=excluded.status, '
            'scan_generation=excluded.scan_generation',
            payload)
        return len(payload)

    def upsert_entries(self, rows: list) -> int:
        """批量 upsert 条目。同一对象的多条硬链接路径各自保留。"""
        if not rows:
            return 0
        with self._conn() as conn:
            n = self._insert_rows(conn, rows)
            conn.commit()
            return n

    def upsert_path(self, source_id: str, path: str, *,
                    scan_generation=None, conn=None) -> bool:
        if self.is_internal_path(path):
            return True
        row = _stat_entry_row(source_id, path, scan_generation=scan_generation)
        if row is None:
            return False
        if conn is not None:
            self._insert_rows(conn, [row])
        else:
            self.upsert_entries([row])
        return True

    def delete_entry(self, source_id: str, path: str) -> bool:
        with self._conn() as conn:
            cur = conn.execute(
                'DELETE FROM entries WHERE source_id=? AND match_path=?',
                (source_id, normcase(path)))
            conn.commit()
            return cur.rowcount > 0

    def rename_entry(self, source_id: str, old_path: str, new_path: str,
                     is_dir: bool = False) -> int:
        with self._conn() as conn:
            n = self._rename_entry(conn, source_id, old_path, new_path, is_dir)
            conn.commit()
            return n

    def _rename_entry(self, conn, source_id, old_path, new_path, is_dir) -> int:
        old = os.path.normpath(old_path).rstrip('\\/')
        new = os.path.normpath(new_path).rstrip('\\/')
        new_parent = os.path.dirname(new)
        new_name = os.path.basename(new)
        changed = 0
        if normcase(old) != normcase(new):
            if normcase(new).startswith(normcase(old) + os.sep):
                raise ReconciliationRequired('cannot move directory into itself')
            conn.execute("DELETE FROM entries WHERE source_id=? AND (match_path=? OR match_path LIKE ? ESCAPE '\\')",
                (source_id,normcase(new),escape_like(normcase(new).rstrip('\\/')+os.sep)+'%'))
        if is_dir:
            rows = conn.execute(
                'SELECT entry_id, path FROM entries '
                'WHERE source_id=? AND (match_path=? OR '
                'match_path LIKE ? ESCAPE \'\\\')',
                (source_id, normcase(old),
                 escape_like(normcase(old).rstrip('\\/') + os.sep) + '%')).fetchall()
            for row in rows:
                if normcase(row['path']) == normcase(old):
                    new_path_i = new
                else:
                    new_path_i = os.path.join(new, row['path'][len(old) + 1:])
                conn.execute(
                    'UPDATE entries SET path=?, parent_path=?, name=?, '
                    'match_path=?, match_name=? WHERE entry_id=?',
                    (new_path_i, os.path.dirname(new_path_i),
                     os.path.basename(new_path_i), normcase(new_path_i),
                     casefold_name(os.path.basename(new_path_i)),
                     row['entry_id']))
                changed += 1
        else:
            cur = conn.execute(
                'UPDATE entries SET path=?, parent_path=?, name=?, ext=?, '
                'match_path=?, match_name=? WHERE source_id=? '
                'AND match_path=?',
                (new, new_parent, new_name, os.path.splitext(new_name)[1].lstrip('.').lower(), normcase(new),
                 casefold_name(new_name), source_id, normcase(old)))
            changed = cur.rowcount
        return changed

    def touch_modified(self, source_id: str, path: str) -> int:
        with self._conn() as conn:
            n = self._touch_modified(conn, source_id, path)
            conn.commit()
            return n

    def _touch_modified(self, conn, source_id, path, content_changed=True) -> int:
        known = conn.execute('SELECT object_id FROM entries WHERE source_id=? AND match_path=?',
                             (source_id,normcase(path))).fetchone()
        if known is None:
            raise ReconciliationRequired('modified path missing from snapshot')
        try:
            st = os.lstat(io_path(path))
        except OSError as exc:
            raise ReconciliationRequired('modified path unreadable') from exc
        condition = 'object_id=?' if known['object_id'] is not None else 'match_path=?'
        value = known['object_id'] if known['object_id'] is not None else normcase(path)
        return conn.execute('UPDATE entries SET size=?,mtime_ns=?,content_version=content_version+? '
            'WHERE source_id=? AND '+condition,
            (st.st_size,st.st_mtime_ns,int(content_changed),source_id,value)).rowcount

    # ----------------------------------------------------------------- events
    def _next_seq(self, conn, source_id: str) -> int:
        row = conn.execute(
            "SELECT value FROM settings WHERE key=?", (f'seq:{source_id}',)
        ).fetchone()
        nxt = (int(row['value']) if row else 0) + 1
        conn.execute(
            'INSERT OR REPLACE INTO settings(key, value) VALUES(?,?)',
            (f'seq:{source_id}', str(nxt)))
        return nxt

    def _insert_event(self, conn, event, namespace='local'):
        values = event.to_dict()
        seq = event.seq if event.seq is not None else self._next_seq(conn, event.source_id)
        key = None
        if event.seq is not None:
            key = namespace + ':' + hashlib.sha256(json.dumps(values, sort_keys=True,
                ensure_ascii=True).encode('utf-8')).hexdigest()
        conn.execute('INSERT OR IGNORE INTO events '
            '(source_id,seq,change_type,object_id,old_parent_path,new_parent_path,old_name,new_name,content_version,dedupe_key) '
            'VALUES (?,?,?,?,?,?,?,?,?,?)', (event.source_id, seq, event.change_type, event.object_id,
            event.old_parent_path, event.new_parent_path, event.old_name, event.new_name, event.content_version, key))
        if key is not None:
            return conn.execute('SELECT * FROM events WHERE source_id=? AND dedupe_key=?',
                                (event.source_id,key)).fetchone()
        return conn.execute('SELECT * FROM events WHERE event_id=last_insert_rowid()').fetchone()

    def record_event(self, event: ChangeEvent, apply_now: bool = True) -> int:
        with self._conn() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row = self._insert_event(conn, event)
            event_id = row['event_id']
            conn.commit()
        if apply_now:
            self.apply_pending(event.source_id)
        return event_id

    def apply_pending(self, source_id: str) -> int:
        with self._conn() as conn:
            conn.execute('BEGIN IMMEDIATE')
            rows = conn.execute('SELECT * FROM events WHERE source_id=? AND applied=0 '
                'ORDER BY COALESCE(seq,0),event_id LIMIT 1000', (source_id,)).fetchall()
            applied = 0
            for row in rows:
                try:
                    if not self._apply_one(conn, row):
                        raise ReconciliationRequired('event could not be applied')
                except (OSError, ReconciliationRequired) as exc:
                    conn.execute("UPDATE sources SET state='needs_rescan',needs_rescan=1,rescan_reason=? WHERE source_id=?",
                                 (str(exc),source_id))
                    break
                conn.execute('UPDATE events SET applied=1,applied_at=? WHERE event_id=?',
                             (time.time(),row['event_id']))
                applied += 1
            conn.commit()
            return applied

    def _apply_one(self, conn, row) -> bool:
        source_id, ct = row['source_id'], row['change_type']
        old = os.path.join(row['old_parent_path'] or '', row['old_name'] or '')
        new = os.path.join(row['new_parent_path'] or '', row['new_name'] or '')
        if self.is_internal_path(new if ct == 'created' else old):
            return True
        if ct == 'created':
            if not self.upsert_path(source_id, new, conn=conn):
                raise ReconciliationRequired('created path no longer readable; reconcile source')
            if os.path.isdir(io_path(new)):
                conn.execute("UPDATE sources SET state='needs_rescan',needs_rescan=1,rescan_reason=? WHERE source_id=?",
                             ('directory arrival requires subtree reconciliation', source_id))
            return True
        if ct == 'deleted':
            key = normcase(old)
            conn.execute("DELETE FROM entries WHERE source_id=? AND (match_path=? OR match_path LIKE ? ESCAPE '\\')",
                         (source_id,key,escape_like(key.rstrip('\\/')+os.sep)+'%'))
            return True
        if ct in ('renamed','moved'):
            known = conn.execute('SELECT kind FROM entries WHERE source_id=? AND match_path=?',
                                 (source_id,normcase(old))).fetchone()
            if known:
                self._rename_entry(conn, source_id, old, new, is_dir=known['kind']=='dir')
            elif not self.upsert_path(source_id, new, conn=conn):
                raise ReconciliationRequired('rename path missing from snapshot')
            return True
        if ct in ('modified','metadata'):
            self._touch_modified(conn, source_id, old, content_changed=ct=='modified')
            return True
        raise ReconciliationRequired('unsupported change type: '+str(ct))

    def replay_all(self) -> dict:
        with self._conn(read_only=True) as conn:
            sources = [r['source_id'] for r in conn.execute(
                'SELECT DISTINCT source_id FROM events WHERE applied=0')]
        out = {}
        for sid in sources:
            out[sid] = self.apply_pending(sid)
        return out

    def apply_events(self, events):
        if not events:
            return 0
        with self._conn() as conn:
            conn.execute('BEGIN IMMEDIATE')
            applied = 0
            for event in events:
                row = self._insert_event(conn,event)
                if row['applied']:
                    continue
                try:
                    if not self._apply_one(conn,row):
                        raise ReconciliationRequired('directory event incomplete')
                except (OSError,ReconciliationRequired) as exc:
                    conn.execute("UPDATE sources SET state='needs_rescan',needs_rescan=1,rescan_reason=? WHERE source_id=?",
                                 (str(exc),event.source_id))
                    continue
                conn.execute('UPDATE events SET applied=1,applied_at=? WHERE event_id=?',
                             (time.time(),row['event_id']))
                applied += 1
            conn.commit()
            return applied

    # ------------------------------------------------------------ checkpoints
    def apply_usn_batch(self, source_id: str, provider: str, journal_id: str,
                        next_usn: str, events: list) -> int:
        """批次应用失败回滚；已提交同日志事件可重复投递但不会二次应用。"""
        with self._conn() as conn:
            conn.execute('BEGIN IMMEDIATE')
            previous = conn.execute('SELECT * FROM checkpoints WHERE source_id=?',(source_id,)).fetchone()
            if previous and (previous['journal_id'] != journal_id or int(next_usn) < int(previous['cursor'])):
                raise ReconciliationRequired('checkpoint journal changed or cursor moved backwards')
            applied = 0
            for ev in events:
                if ev.source_id != source_id:
                    raise ValueError('event source differs from batch source')
                row = self._insert_event(conn, ev, namespace='usn:'+str(journal_id))
                if row['applied']:
                    continue
                if not self._apply_one(conn, row):
                    raise ReconciliationRequired('event batch incomplete')
                conn.execute('UPDATE events SET applied=1,applied_at=? WHERE event_id=?',
                             (time.time(), row['event_id']))
                applied += 1
            conn.execute('INSERT OR REPLACE INTO checkpoints '
                '(source_id,provider,cursor,journal_id,generation,committed_at) VALUES (?,?,?,?,?,?)',
                (source_id,provider,next_usn,journal_id,previous['generation'] if previous else None,time.time()))
            conn.commit()
            return applied

    def save_checkpoint(self, source_id: str, provider: str, cursor: str,
                        journal_id: str = None, generation: int = None) -> None:
        with self._conn() as conn:
            conn.execute(
                'INSERT OR REPLACE INTO checkpoints '
                '(source_id, provider, cursor, journal_id, generation, '
                'committed_at) VALUES (?,?,?,?,?,?)',
                (source_id, provider, cursor, journal_id, generation,
                 time.time()))
            conn.commit()

    def get_checkpoint(self, source_id: str):
        with self._conn(read_only=True) as conn:
            row = conn.execute(
                'SELECT * FROM checkpoints WHERE source_id=?',
                (source_id,)).fetchone()
        return dict(row) if row else None

    # ----------------------------------------------------------------- search
    def search(self, request: SearchRequest, dedupe_paths: bool = False) -> SearchPage:
        started = time.perf_counter()
        req_version = time.monotonic_ns()
        if request.cancel and request.cancel():
            raise Cancelled('catalog query cancelled')
        limit = max(1, min(1000, request.limit))
        offset = max(0, request.offset)

        scope = request.scope if request.scope in ('name', 'path', 'all') else 'name'
        if request.case_sensitive:
            name_col, path_col = 'e.name', 'e.path'
            norm = os.path.normpath
        else:
            name_col, path_col = 'e.match_name', 'e.match_path'
            norm = normcase

        wheres, args = [], []
        query = request.query if request.case_sensitive else request.query.casefold()
        like = f'%{escape_like(query)}%'
        def match(column):
            return f'instr({column}, ?) > 0' if request.case_sensitive else f"{column} LIKE ? ESCAPE '\\'"
        needle = query if request.case_sensitive else like

        fts_expr = None
        if not request.case_sensitive and len(query) >= 3:
            phrase = _fts_phrase(query)
            if scope == 'name':
                fts_expr = f'match_name : {phrase}'
            elif scope == 'path':
                fts_expr = f'match_path : {phrase}'
            else:
                fts_expr = f'match_name : {phrase} OR match_path : {phrase}'

        if fts_expr is not None and self._fts_ready is not False:
            if self._fts_ready is None:
                try:
                    with self._conn(read_only=True) as probe:
                        probe.execute('SELECT rowid FROM entries_fts LIMIT 1')
                    self._fts_ready = True
                except sqlite3.OperationalError:
                    self._fts_ready = False
        if fts_expr is not None and self._fts_ready:
            wheres.append('e.entry_id IN (SELECT rowid FROM entries_fts WHERE entries_fts MATCH ?)')
            args.append(fts_expr)
        elif scope == 'name':
            wheres.append(match(name_col))
            args.append(needle)
        elif scope == 'path':
            wheres.append(match(path_col))
            args.append(needle)
        else:
            wheres.append(
                f'({match(name_col)} OR {match(path_col)})')
            args.extend([needle, needle])

        wheres.append("e.status != 'missing'")

        exts = parse_ext_filter(request.ext)
        if exts:
            wheres.append('e.ext IN (%s)' % ','.join('?' * len(exts)))
            args.extend(exts)
        if request.source_ids:
            wheres.append('e.source_id IN (%s)' % ','.join(
                '?' * len(request.source_ids)))
            args.extend(request.source_ids)
        if request.root:
            root = norm(request.root)
            prefix = root.rstrip('\\/') + os.sep
            if request.case_sensitive:
                wheres.append('(e.path=? OR instr(e.path,?)=1)')
                args.extend([root, prefix])
            else:
                wheres.append("(e.match_path=? OR e.match_path LIKE ? ESCAPE '\\')")
                args.extend([root, escape_like(prefix)+'%'])

        sort_map = {'name': 'name', 'path': 'path', 'size': 'size', 'mtime': 'mtime_ns'}
        base_order = sort_map.get(request.sort, 'name')
        direction = 'DESC' if request.descending else 'ASC'
        where_sql = ' AND '.join(wheres)

        select_cols = ('e.entry_id, e.source_id, e.path, e.name, e.kind, e.ext, '
                       'e.size, e.mtime_ns, e.object_id, '
                       "(CASE WHEN e.status='present' THEN COALESCE(s.online,1) ELSE 0 END) AS online")

        if dedupe_paths:
            # 重叠根目录按同一真实路径去重：优先根更具体的来源，其次在线来源。
            sql = (
                'WITH candidates AS ('
                ' SELECT e.entry_id, e.source_id, e.path, e.name, e.match_name, '
                '        e.match_path, e.kind, e.ext, e.size, e.mtime_ns, e.object_id, '
                '        (CASE WHEN e.status=\'present\' THEN COALESCE(s.online,1) ELSE 0 END) AS online, '
                '        s.root AS src_root '
                ' FROM entries e LEFT JOIN sources s ON s.source_id=e.source_id '
                ' WHERE ' + where_sql + '), '
                'ranked AS ('
                ' SELECT c.*, ROW_NUMBER() OVER ('
                '   PARTITION BY c.match_path '
                '   ORDER BY LENGTH(c.src_root) DESC, c.online DESC, c.entry_id ASC) AS rn '
                ' FROM candidates c) '
                'SELECT entry_id, source_id, path, name, kind, ext, size, mtime_ns, object_id, online '
                'FROM ranked WHERE rn=1 '
                'ORDER BY ' + base_order + ' ' + direction + ', entry_id ' + direction + ' '
                'LIMIT ? OFFSET ?')
        else:
            order_col = {'name': name_col, 'path': path_col,
                         'size': 'e.size', 'mtime': 'e.mtime_ns'}.get(request.sort, name_col)
            sql = (
                'SELECT e.entry_id, e.source_id, e.path, e.name, e.kind, e.ext, '
                'e.size, e.mtime_ns, e.object_id, '
                "(CASE WHEN e.status='present' THEN COALESCE(s.online,1) ELSE 0 END) AS online "
                'FROM entries e LEFT JOIN sources s ON s.source_id=e.source_id WHERE ' + where_sql +
                f' ORDER BY {order_col} {direction}, e.entry_id {direction} '
                'LIMIT ? OFFSET ?')
        args.extend([limit + 1, offset])

        with self._conn(read_only=True) as conn:
            interrupted = [False]
            if request.cancel:
                def should_stop():
                    interrupted[0] = bool(request.cancel())
                    return int(interrupted[0])
                conn.set_progress_handler(should_stop, 2000)
            try:
                rows = conn.execute(sql, args).fetchall()
            except sqlite3.OperationalError as exc:
                if interrupted[0]:
                    raise Cancelled('catalog query cancelled') from exc
                raise
        has_more = len(rows) > limit
        rows = rows[:limit]
        results = [FileHit(
            entry_id=r['entry_id'], source_id=r['source_id'], path=r['path'],
            name=r['name'], kind=r['kind'], ext=r['ext'], size=r['size'],
            mtime_ns=r['mtime_ns'], object_id=r['object_id'],
            online=bool(r['online'])).to_dict() for r in rows]
        return SearchPage(
            results=results, has_more=has_more,
            request_version=req_version,
            elapsed_ms=(time.perf_counter() - started) * 1000,
        )

    # ---------------------------------------------------------------- coverage
    def get_coverage(self) -> CoverageReport:
        with self._conn(read_only=True) as conn:
            rows = conn.execute(
                'SELECT source_id, provider, state, online, '
                '(SELECT COUNT(*) FROM entries e WHERE e.source_id=sources.source_id) AS discovered_count, '
                'failed_count, needs_rescan, rescan_reason, last_scan_at '
                'FROM sources ORDER BY source_id').fetchall()
        return CoverageReport(sources=[dict(r) for r in rows],
                              generated_at=time.time())

    # ---------------------------------------------------------------- misc
    def frn_path_map(self, source_id: str) -> dict:
        with self._conn(read_only=True) as conn:
            rows = conn.execute(
                'SELECT o.file_id, e.path FROM entries e '
                'JOIN objects o ON o.object_id=e.object_id '
                'WHERE e.source_id=? AND o.file_id IS NOT NULL',
                (source_id,)).fetchall()
        paths = {}
        for row in rows:
            key = row['file_id']
            key = int(key[2:],16) if isinstance(key,str) and key.startswith('f:') else int(key)
            if key < 0:
                key += 1 << 64
            if key in paths and paths[key] != row['path']:
                paths[key] = None  # 多条硬链接路径不能任选一条当成真实唯一位置。
            else:
                paths[key] = row['path']
        return paths

    def record_failure(self, source_id: str, path: str, stage: str,
                       error: str) -> None:
        with self._conn() as conn:
            conn.execute(
                'INSERT INTO failures (source_id, path, stage, error, ts) '
                'VALUES (?,?,?,?,?)',
                (source_id, path, stage, str(error)[:1000], time.time()))
            conn.commit()

    def next_generation(self) -> int:
        with self._conn() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute(
                "SELECT value FROM settings WHERE key='generation'").fetchone()
            nxt = (int(row['value']) if row else 0) + 1
            conn.execute(
                "INSERT OR REPLACE INTO settings(key, value) VALUES('generation',?)",
                (str(nxt),))
            conn.commit()
            return nxt

    def remove_source(self, source_id: str) -> int:
        """移除一个范围：只删除目录索引与来源登记，绝不触碰原文件。"""
        with self._conn() as conn:
            conn.execute('DELETE FROM entries WHERE source_id=?', (source_id,))
            conn.execute('DELETE FROM events WHERE source_id=?', (source_id,))
            conn.execute('DELETE FROM checkpoints WHERE source_id=?', (source_id,))
            conn.execute('DELETE FROM failures WHERE source_id=?', (source_id,))
            conn.execute('DELETE FROM objects WHERE source_id=?', (source_id,))
            cur = conn.execute('DELETE FROM sources WHERE source_id=?', (source_id,))
            conn.commit()
            return cur.rowcount

    def count_entries(self, source_id: str = None) -> int:
        with self._conn(read_only=True) as conn:
            if source_id:
                row = conn.execute(
                    'SELECT COUNT(*) AS n FROM entries WHERE source_id=?',
                    (source_id,)).fetchone()
            else:
                row = conn.execute(
                    'SELECT COUNT(*) AS n FROM entries').fetchone()
            return row['n']

    def event_boundary(self, source_id):
        with self._conn(read_only=True) as conn:
            return conn.execute('SELECT COALESCE(MAX(event_id),0) FROM events WHERE source_id=?',
                                (source_id,)).fetchone()[0]

    def prune_stale(self, source_id: str, generation: int, reconciled_event_id=None) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                'DELETE FROM entries WHERE source_id=? AND status=\'present\' '
                'AND (scan_generation IS NULL OR scan_generation<>?)',
                (source_id, generation))
            if reconciled_event_id is not None:
                conn.execute('UPDATE events SET applied=1,applied_at=? '
                    'WHERE source_id=? AND applied=0 AND event_id<=?',
                    (time.time(),source_id,reconciled_event_id))
            conn.commit()
            return cur.rowcount

    def finish_native_snapshot(self, source_id, generation, journal, event_boundary):
        """Publish a complete native generation and its replay origin atomically."""
        with self._conn() as conn:
            conn.execute('BEGIN IMMEDIATE')
            conn.execute('DELETE FROM entries WHERE source_id=? AND '
                         '(scan_generation IS NULL OR scan_generation<>?)', (source_id, generation))
            conn.execute('UPDATE events SET applied=1,applied_at=? WHERE '
                         'source_id=? AND applied=0 AND event_id<=?', (time.time(), source_id, event_boundary))
            conn.execute('INSERT OR REPLACE INTO checkpoints '
                         '(source_id,provider,cursor,journal_id,generation,committed_at) VALUES (?,?,?,?,?,?)',
                         (source_id, 'usn', str(journal['next_usn']), str(journal['usn_journal_id']), generation, time.time()))
            conn.execute("UPDATE sources SET state='complete', provider='usn', needs_rescan=0, "
                         'rescan_reason=NULL, online=1, last_scan_at=?, last_scan_generation=?, '
                         'failed_count=0, discovered_count=(SELECT COUNT(*) FROM entries WHERE source_id=?) '
                         'WHERE source_id=?', (time.time(), generation, source_id, source_id))
            conn.commit()
