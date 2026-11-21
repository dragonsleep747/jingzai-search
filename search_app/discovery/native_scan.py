"""Read-only MFT snapshot with a disk-backed parent queue and hardlink reconciliation."""
from __future__ import annotations
import os
import sqlite3
import tempfile
import time
from . import usn
from .catalog import normcase, casefold_name, signed64
from .errors import Cancelled, PermissionRequired, UnsupportedError
from .filters import io_path, path_within, should_ignore_dir_name, should_ignore_file_name
from .links import hardlink_paths, path_for_file_id
from .scan import _entry_row

_BATCH = 1000

def _ref_key(value):
    return f'{int(value):032x}'

def _root_id(root):
    return os.stat(io_path(root)).st_ino

def _metadata(path, expected):
    result = os.lstat(io_path(path))
    if result.st_ino and result.st_ino != expected:
        raise OSError('file identity changed during native snapshot')
    return result

class NativeIndexBuilder:
    def __init__(self, catalog, source):
        self.catalog, self.source = catalog, source

    def build(self, progress=None, cancel=None, limit_records=0):
        started = time.perf_counter()
        sid, root = self.source.source_id, os.path.abspath(self.source.root)
        generation = self.catalog.next_generation()
        boundary = self.catalog.event_boundary(sid)
        handle = spill = None
        found = dirs = written = failed = excluded = 0
        stopped = False
        reason = None
        journal = None
        def check():
            if cancel and cancel():
                raise Cancelled('native snapshot cancelled')
        def emit(stage):
            if progress:
                progress({'source': sid, 'stage': stage, 'found': found, 'dirs': dirs,
                          'written': written, 'failed': failed})
        self.catalog.set_source_state(sid, 'scanning', provider='usn', generation=generation)
        try:
            check()
            handle = usn.open_volume_handle(root)
            if handle is None:
                raise PermissionRequired('volume handle unavailable')
            journal = usn.query_usn_journal(handle)
            root_ref = _root_id(root)
            if not root_ref:
                raise UnsupportedError('root file identity unavailable')
            spill = _SpillDB()
            cursor = 0
            while True:
                check()
                if limit_records and found >= limit_records:
                    raise UnsupportedError(f'native enumeration bounded at {limit_records} records')
                raw = usn.enum_usn_chunk(handle, cursor, 65536)
                records, stats = usn.parse_usn_buffer(raw, start_offset=8)
                if any(stats.values()):
                    raise UnsupportedError('native enumeration gap: '+str(stats))
                if not records:
                    break
                if len(raw) < 8:
                    raise UnsupportedError('native cursor missing')
                following = int.from_bytes(raw[:8], 'little')
                if following <= cursor:
                    raise UnsupportedError('native cursor made no progress')
                for record in records:
                    check()
                    if limit_records and found >= limit_records:
                        raise UnsupportedError(f'native enumeration bounded at {limit_records} records')
                    spill.add(record)
                    found += 1
                    dirs += bool(record['attributes'] & usn.FILE_ATTRIBUTE_DIRECTORY)
                spill.conn.commit()
                cursor = following
                emit('enumerate')
            spill.seed(root_ref, root)
            spill.recover_missing_parents(root_ref,root,lambda ref:path_for_file_id(handle,ref))
            pending = []
            for item in spill.walk(root_ref, root, check):
                check()
                path, ref = item['path'], int(item['file_ref'], 16)
                if self.catalog.is_internal_path(path):
                    continue
                try:
                    st = _metadata(path, ref)
                    kind = 'link' if item['reparse'] else 'dir' if item['is_dir'] else 'file'
                    aliases = [path]
                    if kind == 'file' and st.st_nlink > 1:
                        aliases = list(hardlink_paths(path))
                        if len(set(normcase(p) for p in aliases)) < st.st_nlink:
                            raise OSError('incomplete hardlink enumeration')
                    for alias in aliases:
                        if not self._allowed_path(root, alias) or self.catalog.is_internal_path(alias):
                            continue
                        # Alias parents must not traverse a junction or symbolic link.
                        parent = os.path.dirname(alias)
                        while path_within(root, parent) and normcase(parent) != normcase(root):
                            if os.path.islink(io_path(parent)) or getattr(os.path, 'isjunction', lambda p:False)(io_path(parent)):
                                raise OSError('alias parent is a reparse point')
                            parent = os.path.dirname(parent)
                        pending.append(_entry_row(sid, alias,
                            os.path.basename(alias.rstrip('\\/')) or root, kind, st, kind=='dir', generation))
                        if len(pending) >= _BATCH:
                            written += self.catalog.upsert_entries(pending)
                            pending.clear()
                            emit('commit')
                except OSError as exc:
                    failed += 1
                    self.catalog.record_failure(sid, path, 'native-metadata', str(exc))
            if pending:
                check()
                written += self.catalog.upsert_entries(pending)
            unresolved = spill.unresolved()
            excluded = spill.conn.execute('SELECT COUNT(*) FROM rec WHERE processed=2').fetchone()[0]
            if unresolved:
                raise UnsupportedError(f'{unresolved} native paths unresolved (missing parent or cycle)')
            if failed:
                raise UnsupportedError(f'{failed} native metadata failures; use generic reconciliation')
            check()
            current = usn.query_usn_journal(handle)
            if (current['usn_journal_id'] != journal['usn_journal_id'] or
                journal['next_usn'] < max(current['first_usn'], current['lowest_valid_usn'])):
                raise UnsupportedError('journal changed or snapshot replay origin expired')
            self.catalog.finish_native_snapshot(sid, generation, journal, boundary)
            state = 'complete'
        except Cancelled as exc:
            stopped, reason, state = True, str(exc), 'partial'
        except (PermissionRequired, UnsupportedError, OSError) as exc:
            reason, state = str(exc), 'partial'
        except Exception:
            self.catalog.set_source_state(sid, 'partial', needs_rescan=True,
                rescan_reason='native build failed; previous entries retained')
            raise
        finally:
            if spill:
                spill.close()
            if handle is not None:
                usn.close_handle(handle)
        if state != 'complete':
            self.catalog.set_source_state(sid, state, needs_rescan=True, rescan_reason=reason,
                failed_count=failed, discovered_count=self.catalog.count_entries(sid),
                last_scan_at=time.time(), generation=generation)
        emit('finished')
        return {'source_id': sid, 'provider':'usn', 'state':state, 'found':found,
                'dirs':dirs, 'failed':failed, 'written':written, 'excluded':excluded, 'stopped':stopped,
                'generation':generation, 'reason':reason, 'elapsed_ms':(time.perf_counter()-started)*1000,
                'journal_id':journal['usn_journal_id'] if journal else None,
                'next_usn':journal['next_usn'] if journal else None}

    def _allowed_path(self, root, path):
        if not path_within(root, path):
            return False
        parts = os.path.relpath(path, root).replace('\\','/').split('/')
        if parts == ['.']:
            return True
        return not any(should_ignore_dir_name(p, self.source.kind=='volume' and i==0)
                       for i,p in enumerate(parts[:-1])) and not should_ignore_file_name(parts[-1])

class _SpillDB:
    def __init__(self):
        descriptor, self.path = tempfile.mkstemp(prefix='jingzai-native-', suffix='.db')
        os.close(descriptor)
        self.conn = sqlite3.connect(self.path)
        # This private spool is rebuilt after any interruption. Its durability
        # is not the catalog's durability: avoid an fsync for every directory.
        # Catalog publication/checkpoints still use the normal atomic database.
        self.conn.execute('PRAGMA journal_mode=MEMORY')
        self.conn.execute('PRAGMA synchronous=OFF')
        self.conn.row_factory = sqlite3.Row
        self.external_roots=[]
        self.conn.execute('PRAGMA cache_size=-4096')
        self.conn.executescript('''
            CREATE TABLE rec (id INTEGER PRIMARY KEY, file_ref TEXT, parent_ref TEXT,
                name TEXT, is_dir INTEGER, reparse INTEGER, processed INTEGER DEFAULT 0);
            CREATE INDEX rec_parent ON rec(parent_ref);
            CREATE TABLE queue (id INTEGER PRIMARY KEY, file_ref TEXT, path TEXT,
                blocked INTEGER, depth INTEGER, done INTEGER DEFAULT 0);
            CREATE UNIQUE INDEX queue_ref ON queue(file_ref);
            CREATE INDEX queue_pending ON queue(done,id);
        ''')

    def add(self, record):
        attrs = record['attributes']
        self.conn.execute('INSERT INTO rec(file_ref,parent_ref,name,is_dir,reparse) VALUES(?,?,?,?,?)',
            (_ref_key(record['file_ref']), _ref_key(record['parent_ref']), record['name'],
             int(bool(attrs & usn.FILE_ATTRIBUTE_DIRECTORY)), int(bool(attrs & usn.FILE_ATTRIBUTE_REPARSE_POINT))))

    def seed(self, ref, root):
        self.conn.execute('INSERT INTO queue(file_ref,path,blocked,depth) VALUES(?,?,0,0)', (_ref_key(ref),root))
        self.conn.execute('UPDATE rec SET processed=1 WHERE file_ref=?',(_ref_key(ref),))
        self.conn.commit()

    def walk(self, root_ref, root, check):
        yield {'path':root, 'file_ref':_ref_key(root_ref), 'is_dir':True, 'reparse':False}
        yield from self.external_roots
        while True:
            check()
            parent = self.conn.execute('SELECT * FROM queue WHERE done=0 ORDER BY id LIMIT 1').fetchone()
            if parent is None:
                break
            last = 0
            while True:
                check()
                rows = self.conn.execute('SELECT * FROM rec WHERE parent_ref=? AND id>? AND processed=0 '
                    'ORDER BY id LIMIT ?', (parent['file_ref'],last,_BATCH)).fetchall()
                if not rows:
                    break
                for row in rows:
                    check()
                    name = row['name']
                    if not name or name in ('.','..') or os.sep in name or (os.altsep and os.altsep in name):
                        raise UnsupportedError('invalid native name')
                    path = os.path.join(parent['path'],name)
                    blocked = bool(parent['blocked']) or (should_ignore_dir_name(name,parent['depth']==0)
                              if row['is_dir'] else should_ignore_file_name(name))
                    if row['is_dir']:
                        self.conn.execute('INSERT OR IGNORE INTO queue(file_ref,path,blocked,depth) VALUES(?,?,?,?)',
                            (row['file_ref'],path,int(blocked or row['reparse']),parent['depth']+1))
                    self.conn.execute('UPDATE rec SET processed=? WHERE id=?',(2 if blocked else 1,row['id']))
                    last = row['id']
                    if not blocked:
                        yield dict(row, path=path)
                self.conn.commit()
            self.conn.execute('UPDATE queue SET done=1 WHERE id=?',(parent['id'],))
            self.conn.commit()

    def unresolved(self):
        return self.conn.execute('SELECT COUNT(*) FROM rec WHERE processed=0').fetchone()[0]

    def recover_missing_parents(self, root_ref, root, resolve):
        rows=self.conn.execute('SELECT DISTINCT r.parent_ref FROM rec r LEFT JOIN rec p '
            'ON p.file_ref=r.parent_ref WHERE p.id IS NULL AND r.parent_ref<>? LIMIT 1024',
            (_ref_key(root_ref),)).fetchall()
        for row in rows:
            ref=int(row[0],16)
            # NTFS MFT segment 11 is the reserved $Extend metadata directory.
            # Its own record is often omitted by ENUM_USN_DATA. Walk its children
            # as excluded metadata, never guess an ordinary missing user's path.
            metadata=(ref & ((1<<48)-1))==11
            try:
                path=os.path.join(root,'$Extend') if metadata else resolve(ref)
            except OSError:
                continue
            if not path_within(root,path):
                continue
            parts=os.path.relpath(path,root).replace('\\','/').split('/')
            blocked=metadata or any(should_ignore_dir_name(part,i==0) for i,part in enumerate(parts))
            parent=path
            while not blocked and normcase(parent)!=normcase(root) and path_within(root,parent):
                blocked=os.path.islink(io_path(parent)) or getattr(os.path,'isjunction',lambda p:False)(io_path(parent))
                parent=os.path.dirname(parent)
            self.conn.execute('INSERT OR IGNORE INTO queue(file_ref,path,blocked,depth) VALUES(?,?,?,?)',
                (row[0],path,int(blocked),len(parts)))
            if not blocked:
                self.external_roots.append({'path':path,'file_ref':row[0],'is_dir':True,'reparse':False})
        self.conn.commit()

    def close(self):
        self.conn.close()
        os.remove(self.path)
