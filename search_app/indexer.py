# -*- coding: utf-8 -*-
"""
镜仔搜索 · 索引构建模块
Copyright (c) 2026 SleepDragon
SPDX-License-Identifier: MIT
"""

"""目录扫描 + SQLite 索引构建（FTS5 trigram 支持中文子串匹配）

P0-1: stop_event 支持取消扫描
P0-2: IGNORE_DIRS 自动跳过 .git/node_modules/__pycache__ 等
P0-5: errors 表持久化失败文件与完整堆栈
"""
import os
import re
import json
import sqlite3
import sys
import time
import shutil
import zipfile
import tempfile
import threading
import traceback as _tb

from detector import detect_type, detect_encoding
from parsers import (parse_text, parse_docx, parse_xlsx, parse_pptx,
                     parse_pdf, parse_doc, parse_xls, parse_ppt, parse_image)

# P0-2: 扫描时跳过的目录名（小写匹配，原地剪枝 os.walk）
IGNORE_DIRS = frozenset({
    '.git', '.hg', '.svn',
    'node_modules', '__pycache__', '.pytest_cache', '.mypy_cache',
    '.ruff-cache', '.tox', '.egg-info', '.eggs',
    '.venv', 'venv', 'env', '.env',
    '.idea', '.vscode',
    'dist', 'build', 'target', 'out',
    '.next', '.nuxt', '.sass-cache',
    'coverage', '.coverage', 'htmlcov',
    '.cache', '.parcel-cache',
    '$recycle.bin', 'system volume information',
})

SCHEMA = '''
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT UNIQUE NOT NULL,
    name TEXT,
    ext TEXT,
    ftype TEXT,
    encoding TEXT,
    mtime REAL,
    size INTEGER,
    line_count INTEGER DEFAULT 0,
    sha256 TEXT,
    doc_no TEXT,
    effective_date TEXT,
    status TEXT DEFAULT 'active',
    category TEXT DEFAULT '',
    cat_ignored INTEGER DEFAULT 0,
    indexed_at REAL
);
CREATE TABLE IF NOT EXISTS lines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id INTEGER NOT NULL,
    line_no INTEGER NOT NULL,
    line_text TEXT NOT NULL,
    FOREIGN KEY(doc_id) REFERENCES documents(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_lines_doc ON lines(doc_id);
CREATE TABLE IF NOT EXISTS chapters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id INTEGER NOT NULL,
    start_line INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    title TEXT NOT NULL,
    level INTEGER DEFAULT 0,
    FOREIGN KEY(doc_id) REFERENCES documents(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_chap_doc ON chapters(doc_id);
CREATE INDEX IF NOT EXISTS idx_chap_lines ON chapters(doc_id, start_line, end_line);
CREATE TABLE IF NOT EXISTS errors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT,
    stage TEXT,
    error TEXT,
    traceback TEXT,
    ts REAL
);
CREATE INDEX IF NOT EXISTS idx_errors_ts ON errors(ts);
-- lines 复合索引：bigram JOIN lines 时按 (doc_id, line_no) 点查
CREATE INDEX IF NOT EXISTS idx_lines_docno ON lines(doc_id, line_no);
CREATE TABLE IF NOT EXISTS bookmarks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL,
    line_no INTEGER NOT NULL,
    title TEXT,
    note TEXT DEFAULT '',
    created_at REAL,
    UNIQUE(path, line_no)
);
CREATE INDEX IF NOT EXISTS idx_bm_path ON bookmarks(path);
CREATE TABLE IF NOT EXISTS reading_positions (
    path TEXT PRIMARY KEY,
    line_no INTEGER NOT NULL,
    updated_at REAL
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS bigram (
    tok TEXT NOT NULL,
    doc_id INTEGER NOT NULL,
    line_no INTEGER NOT NULL,
    PRIMARY KEY (tok, doc_id, line_no)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS idx_bigram_doc ON bigram(doc_id);
'''

FTS_SCHEMA = '''
CREATE VIRTUAL TABLE IF NOT EXISTS lines_fts USING fts5(
    line_text, doc_id UNINDEXED, line_no UNINDEXED, tokenize='trigram'
);
'''

# ========== bigram 双字索引（中文两字词加速）==========
# trigram FTS 对 2 字词零命中，两字词只能 LIKE 全表扫（大库秒级）。
# bigram 把每行正文按相邻两字倒排，2 字词变索引点查，毫秒级。
# settings key 'bigram_ready' = '1' 就绪（查询可走 bigram）；缺失/'0' 未建。

# 可索引字符段（与查询端判断保持一致；lower 后的字母/数字/CJK汉字）
_BIGRAM_SEG_RE = re.compile(r'[0-9a-z\u4e00-\u9fff\u3400-\u4dbf]+')


def _bigram_toks(text):
    """一行文本 → 去重后的相邻两字 token 列表（全小写）"""
    if not text:
        return ()
    out = set()
    for seg in _BIGRAM_SEG_RE.findall(text.lower()):
        for i in range(len(seg) - 1):
            out.add(seg[i:i + 2])
    return out


def bigram_ready(db_path):
    """bigram 索引是否就绪（可安全走快路径）"""
    try:
        conn = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True, timeout=10)
        try:
            v = conn.execute(
                "SELECT value FROM settings WHERE key='bigram_ready'"
            ).fetchone()
        finally:
            conn.close()
        return v is not None and v[0] == '1'
    except sqlite3.Error:
        return False


def _set_bigram_ready(conn, ok):
    conn.execute('INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)',
                 ('bigram_ready', '1' if ok else '0'))


def _insert_bigrams(conn, doc_id, lines):
    """为一个文档的所有行写 bigram（upsert 前先清旧行）"""
    conn.execute('DELETE FROM bigram WHERE doc_id=?', (doc_id,))
    batch = []
    for ln, txt in lines:
        for tok in _bigram_toks(txt):
            batch.append((tok, doc_id, ln))
    if batch:
        conn.executemany(
            'INSERT OR IGNORE INTO bigram(tok,doc_id,line_no) VALUES(?,?,?)',
            batch)


def rebuild_bigram(db_path, progress=None, stop_event=None, batch_docs=20):
    """后台回填全库 bigram 索引（幂等：已有 bigram 的文档跳过）。
    完成后置 settings.bigram_ready=1，查询端自动切快路径。
    progress(done_docs, total_docs)；中途停止保持未就绪，下次续跑。
    返回 {'built': n, 'total': n, 'seconds': s}"""
    t0 = time.time()
    conn, _ = get_db(db_path)
    try:
        _set_bigram_ready(conn, False)
        conn.commit()
        done_docs = {r[0] for r in
                     conn.execute('SELECT DISTINCT doc_id FROM bigram')}
        all_docs = [r[0] for r in
                    conn.execute('SELECT id FROM documents ORDER BY id')]
        todo = [d for d in all_docs if d not in done_docs]
        total = len(all_docs)
        built = 0
        for i, doc_id in enumerate(todo, 1):
            if stop_event is not None and stop_event.is_set():
                return {'built': built, 'total': total,
                        'seconds': time.time() - t0, 'stopped': True}
            rows = conn.execute(
                'SELECT line_no, line_text FROM lines WHERE doc_id=? '
                'ORDER BY line_no', (doc_id,)).fetchall()
            _insert_bigrams(conn, doc_id, rows)
            built += 1
            if built % batch_docs == 0 or i == len(todo):
                conn.commit()   # 分批落盘，断电不整批回滚
                if progress:
                    progress(len(done_docs) + built, total)
        _set_bigram_ready(conn, True)
        conn.commit()
        return {'built': built, 'total': total,
                'seconds': time.time() - t0}
    finally:
        conn.close()


_DB_SCHEMA_READY = set()   # 已完成建表/迁移的库（abspath），进程内只做一次
_DB_FTS_OK = {}            # abspath -> bool（FTS 表是否可用）

# 线程局部只读连接池：预览渲染热路径反复读库，复用连接避免频繁
# open/close（WAL 下每次 close 可能触发同步）。autocommit + query_only：
# 每条 SELECT 独立读事务、总能看到最新已提交数据，且杜绝误写。
_ro_tls = threading.local()


def ro_conn(db_path):
    """返回当前线程对 db_path 的可复用只读连接（无需 close）"""
    store = getattr(_ro_tls, 'conns', None)
    if store is None:
        store = {}
        _ro_tls.conns = store
    key = os.path.abspath(db_path)
    conn = store.get(key)
    if conn is None:
        conn = sqlite3.connect(db_path, timeout=30, isolation_level=None)
        try:
            conn.execute('PRAGMA query_only=ON')
            conn.execute('PRAGMA busy_timeout=30000')
            conn.execute('PRAGMA synchronous=NORMAL')
            conn.execute('PRAGMA mmap_size=268435456')   # 256MB mmap 免拷贝读
            conn.execute('PRAGMA cache_size=-16384')     # 16MB 页缓存常驻
            conn.execute('PRAGMA temp_store=MEMORY')
        except sqlite3.DatabaseError:
            pass
        store[key] = conn
    return conn


def get_db(db_path):
    """打开/创建数据库，建表 + 旧库兼容迁移 + 尝试建 FTS。返回 (conn, fts_ok)。
    性能：建表脚本/列迁移/WAL 每个库进程内只执行一次（还原库会重启进程，
    天然失效缓存；换库路径按 abspath 各自独立）。"""
    conn = sqlite3.connect(db_path, timeout=30)
    # 连接级 pragma（便宜），每次连接都设
    try:
        conn.execute('PRAGMA synchronous=NORMAL')
    except sqlite3.DatabaseError:
        pass
    conn.execute('PRAGMA busy_timeout=30000')
    key = os.path.abspath(db_path)
    if key in _DB_SCHEMA_READY:
        return conn, _DB_FTS_OK.get(key, False)
    # —— 首次：WAL + 建表 + 旧库迁移 + FTS（仅一次）——
    # WAL 模式是数据库级持久设置，只需设一次；读写不互斥，断电不损坏主库
    try:
        conn.execute('PRAGMA journal_mode=WAL')
    except sqlite3.DatabaseError:
        pass
    conn.executescript(SCHEMA)
    # 旧库迁移：documents 补缺失列（sha256/doc_no/effective_date/status）
    cols = {r[1] for r in conn.execute('PRAGMA table_info(documents)')}
    for col, decl in [
        ('sha256', 'TEXT'),
        ('doc_no', 'TEXT'),
        ('effective_date', 'TEXT'),
        ('status', "TEXT DEFAULT 'active'"),
        ('category', "TEXT DEFAULT ''"),
        ('cat_ignored', 'INTEGER DEFAULT 0'),
        ('missing_count', 'INTEGER DEFAULT 0'),
    ]:
        if col not in cols:
            try:
                conn.execute(f'ALTER TABLE documents ADD COLUMN {col} {decl}')
            except sqlite3.OperationalError:
                pass
    # 旧库迁移：bookmarks 补 note 列（备注）
    bm_cols = {r[1] for r in conn.execute('PRAGMA table_info(bookmarks)')}
    if 'note' not in bm_cols:
        try:
            conn.execute("ALTER TABLE bookmarks ADD COLUMN note TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass
    fts_ok = False
    try:
        conn.executescript(FTS_SCHEMA)
        fts_ok = True
    except sqlite3.OperationalError:
        fts_ok = False
    # 全新库（从无 bigram_ready 标志且无历史文档）：导入时即双写 bigram，
    # 直接置就绪；旧库保持未就绪，由 rebuild_bigram 后台回填后置位
    try:
        v = conn.execute(
            "SELECT value FROM settings WHERE key='bigram_ready'").fetchone()
        if v is None and conn.execute(
                'SELECT 1 FROM documents LIMIT 1').fetchone() is None:
            conn.execute("INSERT OR REPLACE INTO settings(key,value) "
                         "VALUES('bigram_ready','1')")
    except sqlite3.OperationalError:
        pass
    conn.commit()
    _DB_SCHEMA_READY.add(key)
    _DB_FTS_OK[key] = fts_ok
    return conn, fts_ok


def _file_sha256(path, buf_size=1048576):
    """流式计算文件 sha256；读取失败返回 None"""
    import hashlib
    h = hashlib.sha256()
    try:
        with open(path, 'rb') as f:
            while True:
                b = f.read(buf_size)
                if not b:
                    break
                h.update(b)
        return h.hexdigest()
    except OSError:
        return None


def _quick_fingerprint(path):
    """快速指纹：文件大小 + 前 64KB 哈希。
    用于去重预筛——只有指纹完全相同的两个文件才可能是重复内容。
    不同 size 的文件一定不同；相同 size 但不同前 64KB 也一定不同。
    返回 (size, quick_hash_hex) 或 (size, None) 读取失败时。"""
    import hashlib
    try:
        size = os.path.getsize(path)
    except OSError:
        return (0, None)
    h = hashlib.md5()  # MD5 只用于快速指纹，安全不敏感
    try:
        with open(path, 'rb') as f:
            h.update(f.read(65536))  # 前 64KB
        return (size, h.hexdigest())
    except OSError:
        return (size, None)


def _check_fts(conn):
    """运行时检查 FTS 表是否存在"""
    try:
        conn.execute("SELECT 1 FROM lines_fts LIMIT 0")
        return True
    except sqlite3.OperationalError:
        return False


def parse_file(path):
    """
    按文件类型调用对应解析器，返回 (lines, encoding, ftype)

    P1-8:  ole 类型按扩展名分派 .doc/.xls/.ppt 解析器
    P1-9:  PDF 文本提取为空时按需走 OCR（扫描版 PDF）
    P1-10: 纯压缩包(zip/rar/7z)走 parse_archive，
           把内部文件内容带 [member 名] 前缀合并进 lines
    """
    from detector import detect_zip_kind
    ext = os.path.splitext(path)[1].lower()
    ftype = detect_type(path, ext)
    if ftype == 'text':
        enc, _ = detect_encoding(path)
        return list(parse_text(path, enc)), enc, 'text'
    if ftype == 'zip':
        # 区分 Office 容器 vs 纯压缩包
        kind = detect_zip_kind(path)
        if kind == 'docx':
            return list(parse_docx(path)), 'binary', 'docx'
        if kind == 'xlsx':
            return list(parse_xlsx(path)), 'binary', 'xlsx'
        if kind == 'pptx':
            return list(parse_pptx(path)), 'binary', 'pptx'
        # 纯 zip 压缩包
        return _parse_archive_into_lines(path), 'binary', 'archive'
    if ftype == 'rar':
        return _parse_archive_into_lines(path), 'binary', 'archive'
    if ftype == '7z':
        return _parse_archive_into_lines(path), 'binary', 'archive'
    if ftype == 'pdf':
        # 文本提取为空的页由 parse_pdf 内部逐页 OCR 兜底
        lines = list(parse_pdf(path))
        return lines, 'binary', 'pdf'
    if ftype == 'ole':
        # P1-8: 老 Office 二进制
        if ext == '.doc':
            return list(parse_doc(path)), 'binary', 'doc'
        if ext == '.xls':
            return list(parse_xls(path)), 'binary', 'xls'
        if ext == '.ppt':
            return list(parse_ppt(path)), 'binary', 'ppt'
        return [], 'binary', 'ole'
    # 图片文件：走 parsers.parse_image（含 OCR）
    if ext in ('.png', '.jpg', '.jpeg', '.bmp', '.tiff', '.tif', '.gif', '.webp'):
        try:
            from parsers import parse_image
            return list(parse_image(path)), 'binary', 'image_ocr'
        except Exception:
            return [], 'binary', 'image'
    return [], 'unknown', ftype


def _parse_archive_into_lines(path):
    """
    P1-10: 把压缩包内所有可解析文件合并成统一行列表。
    每行格式: '[archive 内 member 名] 原始行'
    行号全局递增，避免不同 member 行号冲突。
    """
    from parsers import parse_archive
    members = parse_archive(path)
    all_lines = []
    n = 0
    for member_name, lines in members:
        for _, text in lines:
            n += 1
            all_lines.append((n, f'[{member_name}] {text}'))
    return all_lines


def _record_error(conn, path, stage, exc):
    """P0-5: 把异常完整存入 errors 表（含堆栈）"""
    tb = ''.join(_tb.format_exception(type(exc), exc, exc.__traceback__))
    conn.execute(
        'INSERT INTO errors (path, stage, error, traceback, ts) VALUES (?,?,?,?,?)',
        (path, stage, str(exc), tb, time.time())
    )


def record_error(db_path, stage, exc, path=None):
    """对外接口：把关键路径（搜索/导出等）的异常落 errors 表，便于事后排查。
    失败时静默（记录错误本身不应再抛错影响主流程）。"""
    try:
        conn, _ = get_db(db_path)
        try:
            _record_error(conn, path, stage, exc)
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass


def index_directory(root, db_path, callback=None, stop_event=None):
    """
    遍历 root 建索引，按 mtime 增量更新；callback(i, total, path, status)

    P0-1: stop_event 为 threading.Event，is_set() 时停止扫描
    P0-2: 自动跳过 IGNORE_DIRS 中的目录
    P0-5: 解析异常写入 errors 表，不再静默吞
    返回 {'stopped': bool, 'total': int, 'processed': int}
    """
    conn, _ = get_db(db_path)
    fts_ok = _check_fts(conn)
    known = {r[0]: r[1] for r in conn.execute('SELECT path, mtime FROM documents')}
    # P0-2: 收集文件列表时跳过黑名单目录（原地剪枝 dirnames）
    all_files = []
    db_abs = os.path.abspath(db_path)
    for dirpath, dirnames, files in os.walk(root):
        dirnames[:] = [d for d in dirnames if d.lower() not in IGNORE_DIRS]
        for fn in files:
            p = os.path.join(dirpath, fn)
            # 跳过索引库自身及其 WAL/SHM/journal 旁车文件（否则会被当作文档入库）
            if os.path.abspath(p) == db_abs:
                continue
            if os.path.basename(p).startswith(os.path.basename(db_path) + '-'):
                continue
            all_files.append(p)
    total = len(all_files)
    stopped = False
    processed = 0
    for i, p in enumerate(all_files, 1):
        # P0-1: 检查停止信号
        if stop_event is not None and stop_event.is_set():
            stopped = True
            _cb(callback, i, total, p, 'stopped')
            break
        try:
            st = os.stat(p)
        except Exception as e:
            _record_error(conn, p, 'stat', e)
            _cb(callback, i, total, p, 'error: stat')
            continue
        if p in known and known[p] == st.st_mtime:
            _cb(callback, i, total, p, 'skip')
            continue
        try:
            lines, enc, ftype = parse_file(p)
            _upsert(conn, p, os.path.splitext(p)[1].lower(), ftype, enc,
                    st.st_mtime, st.st_size, lines, fts_ok)
            processed += 1
            _cb(callback, i, total, p, 'ok')
            # 分批落盘：OCR 大任务可能跑几小时，崩溃/断电时已完成部分不丢
            if processed % 25 == 0:
                conn.commit()
        except Exception as e:
            # P0-5: 完整记录异常，不再仅回字符串
            _record_error(conn, p, 'parse', e)
            _cb(callback, i, total, p, f'error: {e}')
    # 清理已删除的文件（停止时不清理，避免误删）
    if not stopped:
        # 只清理【本次扫描根目录之内】的消失条目，绝不能动库中其他目录；
        # 用完整删除（lines/章节/bigram/FTS/语义块），不依赖外键级联
        root_abs = os.path.abspath(root)
        scanned_abs = {os.path.abspath(p) for p in all_files}
        for (dbp,) in conn.execute('SELECT path FROM documents'):
            p_abs = os.path.abspath(dbp)
            # 不在本次扫描根目录内 → 跳过（跨盘符 commonpath 会抛异常）
            try:
                inside = os.path.commonpath([root_abs, p_abs]) == root_abs
            except ValueError:
                inside = False
            if not inside or p_abs in scanned_abs:
                continue
            _remove_doc_from_index(conn, dbp, fts_ok)
    conn.commit()
    conn.close()
    return {'stopped': stopped, 'total': total, 'processed': processed}


def _cb(callback, i, total, path, status):
    if callback:
        callback(i, total, path, status)


def refresh_index(db_path, callback=None):
    """
    启动时增量自检（无需选目录）：
    - 比对每个已索引文件的 mtime/size，变化的重索引
    - 磁盘上已消失的文件移出索引（不动磁盘）

    安全防护（防误删索引）：
    - 两段式：先全量预检收集失联清单；失联数 > max(50, 总数20%) 判定为
      存储未挂载（移动硬盘没插/网络盘掉线），本次一个都不删也不计数，
      返回 suspected_offline
    - 单文件需连续两次自检都缺失（missing_count>=2）才真正移出，
      防止杀软锁定/盘符抖动造成的一次性误判
    返回 {'updated', 'removed', 'total', 'suspected_offline'}
    """
    conn, _ = get_db(db_path)
    fts_ok = _check_fts(conn)
    updated = removed = 0
    try:
        rows = conn.execute(
            'SELECT path, mtime, size FROM documents').fetchall()
        total = len(rows)
        # —— 第一段：全量预检，收集失联清单 ——
        stats = {}
        missing = []
        for i, (p, mt, sz) in enumerate(rows, 1):
            _cb(callback, i, total, p, 'checking')
            try:
                stats[p] = os.stat(p)
            except OSError:
                missing.append(p)
        suspected = 0
        if missing and len(missing) > max(50, total * 0.2):
            # 失联比例异常高 → 大概率存储未挂载：不删、不计数，等下次自检
            suspected = len(missing)
            missing = []
        missing_set = set(missing)
        # —— 第二段：更新变动文件 + 失联计数处理 ——
        for i, (p, mt, sz) in enumerate(rows, 1):
            if p in missing_set:
                cur = conn.execute(
                    'SELECT COALESCE(missing_count,0) FROM documents '
                    'WHERE path=?', (p,)).fetchone()
                mc = (cur[0] if cur else 0) + 1
                if mc >= 2:
                    # 连续两次自检缺失 → 真正移出索引
                    _remove_doc_from_index(conn, p, fts_ok)
                    removed += 1
                    _cb(callback, i, total, p, 'removed')
                else:
                    conn.execute(
                        'UPDATE documents SET missing_count=? WHERE path=?',
                        (mc, p))
                continue
            st = stats.get(p)
            if st is None:
                continue   # suspected_offline 的失联文件：跳过不动
            # 文件存在：重置缺失计数（仅在有值时写库）
            conn.execute(
                'UPDATE documents SET missing_count=0 WHERE path=? '
                'AND COALESCE(missing_count,0)<>0', (p,))
            if st.st_mtime != mt or st.st_size != sz:
                try:
                    lines, enc, ftype = parse_file(p)
                    _upsert(conn, p, os.path.splitext(p)[1].lower(), ftype,
                            enc, st.st_mtime, st.st_size, lines, fts_ok)
                    updated += 1
                    _cb(callback, i, total, p, 'ok')
                except Exception as e:
                    _record_error(conn, p, 'refresh', e)
                    _cb(callback, i, total, p, f'error: {e}')
        conn.commit()
    finally:
        conn.close()
    return {'updated': updated, 'removed': removed, 'total': total,
            'suspected_offline': suspected}


def _upsert(conn, path, ext, ftype, enc, mtime, size, lines, fts_ok, sha=None):
    """新增或更新一个文档及其所有行。
    sha 可选：调用方已算好的全量 sha256，避免重复计算。"""
    if sha is None:
        sha = _file_sha256(path)
    name = os.path.basename(path)
    # 自动识别元数据
    meta = _extract_metadata(lines, name, path)
    # 自动归类：按当前分类规则推断（已手工设置的不覆盖）
    rules = get_category_rules(conn)
    head_text = '\n'.join(t for _, t in list(lines)[:200])
    auto_cat = infer_category(name, meta['doc_no'], head_text, rules)
    # 物理文件夹即归属：文件已在十大分类文件夹内时，以文件夹为准
    # （用户手工归档/监控到文件夹的落盘文件，优先级高于文件名推断）
    _parent = os.path.basename(os.path.dirname(path))
    if _parent in CATEGORY_FOLDERS:
        auto_cat = _parent
    row = conn.execute(
        'SELECT id, doc_no, effective_date, status, COALESCE(category,"") '
        'FROM documents WHERE path=?', (path,)).fetchone()
    if row:
        doc_id, old_no, old_eff, old_st, old_cat = row
        conn.execute('DELETE FROM lines WHERE doc_id=?', (doc_id,))
        if fts_ok:
            conn.execute('DELETE FROM lines_fts WHERE doc_id=?', (doc_id,))
        conn.execute('DELETE FROM chapters WHERE doc_id=?', (doc_id,))
        conn.execute('DELETE FROM bigram WHERE doc_id=?', (doc_id,))
        # 仅在原字段为空时自动填充，避免覆盖用户手动编辑的值
        new_no = old_no if old_no else meta['doc_no']
        new_eff = old_eff if old_eff else meta['effective_date']
        new_st = old_st
        # 仅在原状态为默认 active 时允许自动判定 void/replaced
        if old_st == 'active' and meta['status'] != 'active':
            new_st = meta['status']
        new_cat = old_cat if old_cat else auto_cat
        conn.execute('''UPDATE documents SET name=?, ext=?, ftype=?, encoding=?, mtime=?,
                        size=?, line_count=?, sha256=?, indexed_at=?,
                        doc_no=?, effective_date=?, status=?, category=?,
                        missing_count=0 WHERE id=?''',
                     (name, ext, ftype, enc, mtime, size,
                      len(lines), sha, time.time(),
                      new_no, new_eff, new_st, new_cat, doc_id))
    else:
        cur = conn.execute('''INSERT INTO documents
            (path, name, ext, ftype, encoding, mtime, size, line_count, sha256, indexed_at,
             doc_no, effective_date, status, category)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (path, name, ext, ftype, enc, mtime, size,
             len(lines), sha, time.time(),
             meta['doc_no'], meta['effective_date'], meta['status'], auto_cat))
        doc_id = cur.lastrowid
    conn.executemany('INSERT INTO lines (doc_id, line_no, line_text) VALUES (?,?,?)',
                     [(doc_id, ln, txt) for ln, txt in lines])
    if fts_ok:
        conn.executemany(
            'INSERT INTO lines_fts (line_text, doc_id, line_no) VALUES (?,?,?)',
            [(txt, doc_id, ln) for ln, txt in lines])
    # bigram 双字倒排：与 lines 同步维护（旧库未回填时写上也无妨，
    # rebuild_bigram 幂等跳过已有文档，最终一致性由回填保证）
    _insert_bigrams(conn, doc_id, lines)
    # P0-1: 章节识别
    chapters = _detect_chapters(lines)
    if chapters:
        conn.executemany(
            'INSERT INTO chapters (doc_id, start_line, end_line, title, level) VALUES (?,?,?,?,?)',
            [(doc_id, s, e, t, lv) for s, e, t, lv in chapters])


# ========== P0-1: 章节识别 ==========

# 中文制度编号模式（按级别从高到低）
_CHAPTER_PATTERNS = [
    # 第X篇 / 第X部
    (re.compile(r'^\s*第[一二三四五六七八九十百零\d]+[篇部]\s*(.+?)\s*$'), 1),
    # 第X章
    (re.compile(r'^\s*第[一二三四五六七八九十百零\d]+章\s*(.+?)\s*$'), 2),
    # 第X节
    (re.compile(r'^\s*第[一二三四五六七八九十百零\d]+节\s*(.+?)\s*$'), 3),
    # 第X条（条款制核心）
    (re.compile(r'^\s*第[一二三四五六七八九十百零\d]+条\s*(.+?)\s*$'), 4),
    # 一、 二、
    (re.compile(r'^\s*([一二三四五六七八九十]+)[、.]\s*(.+?)\s*$'), 5),
    # (一) （一）
    (re.compile(r'^\s*[\(（]([一二三四五六七八九十\d]+)[\)）]\s*(.+?)\s*$'), 6),
    # 1. 2. （后跟非数字，避免误抓 1.1.1）
    (re.compile(r'^\s*(\d+)[、.]\s*(.+?)\s*$'), 5),
    # 1.1 / 1.1.1
    (re.compile(r'^\s*(\d+(?:\.\d+)+)\s*(.+?)\s*$'), 6),
    # Markdown 标题
    (re.compile(r'^(#{1,6})\s+(.+?)\s*$'), 0),
]


# ========== 元数据自动识别（文档号 / 生效日期 / 状态）==========

import datetime as _dt

# 文档号正则（按优先级排序，第一个命中即用）
_DOC_NO_PATTERNS = [
    # 国务院公文：国发〔2024〕123号 / 国办发〔2024〕12号 / X字〔YYYY〕N号
    re.compile(r'([\u4e00-\u9fa5]{1,8})字?〔(\d{4})〕(\d+)号'),
    re.compile(r'([\u4e00-\u9fa5]{1,8})发〔(\d{4})〕(\d+)号'),
    # 旧式方括号/圆括号：XX[2024]123号 / XX(2024)123号
    re.compile(r'([\u4e00-\u9fa5]{1,8})[\[\(](\d{4})[\]\)](\d+)号'),
    # 国标/行标/地标/企标（直接用整个匹配串；允许 _ 或 / 分隔，文件名常把 / 转 _）
    # 不加结尾边界：年份后常紧跟中文标题，\b 会失配并导致回溯成残缺编号
    re.compile(r'\b(GB(?:[/_][A-Z])?\s*\d{2,6}-\d{4})'),
    re.compile(r'\b([A-Z]{2,4}[/_][A-Z]\s*\d{2,6}-\d{4})'),
    # 常见连字符写法优先于通用规则：
    # JGJ-T46-2024 / JGJT429-2018 / DB13-2934-2019 / DB13-J-T100-2022 /
    # DB13(J)/T 8362-2020 / DG-TJ08-2035-2014（上海 DGJ08 系连字符写法）
    re.compile(r'\b(JGJ\s*[\-/]?\s*T?\s*\d{2,5}-\d{4})'),
    re.compile(
        r'\b(DB\s*13\s*[\-/]?\s*\(?J\)?\s*[\-/]?\s*T?\s*\d{2,5}-\d{4})'),
    re.compile(r'\b(DG[\-/]?TJ?08\s*-\s*\d{2,5}-\d{4})'),
    # 无斜杠编号：JGJ 130-2011 / JGJT 188-2009 / DGTJ08-2035-2014 /
    # DGJ08-113-2017 / DB13/T 5048-2019 / DB13(J)/T 8362-2020
    re.compile(
        r'\b([A-Z]{2,5}\d{0,2}\s*(?:\([A-Z]\))?\s*'
        r'(?:[/_]\s?[A-Z])?\s*-?\s*\d{2,5}-\d{4})'),
    re.compile(r'\b(Q[/_][A-Z]{2,4}\s*\d{2,6}-\d{4})\b'),
    # QSY 类无分隔企标（中石油/中石化字号）
    re.compile(r'\b(QSY\s*\d{2,6}-\d{4})\b'),
    re.compile(r'\b(ISO\s*\d{2,5}(?:[-:]\d{1,2})?)\b', re.I),
]

# 生效日期正则（按优先级排序：强信号 → 标签类 → 弱信号；第一个命中即用）
_EFF_DATE_PATTERNS = [
    # === 强信号：自...起施行/实施 ===
    # 自YYYY年MM月DD日起施行/实施/执行/生效
    re.compile(r'自(\d{4})年(\d{1,2})月(\d{1,2})日起(?:施行|实施|执行|生效)'),
    # 自YYYY-MM-DD起施行/实施/执行/生效
    re.compile(r'自(\d{4})[-/](\d{1,2})[-/](\d{1,2})起(?:施行|实施|执行|生效)'),
    # 自YYYY年MM月DD日（可带"起"）实施/施行
    re.compile(r'自(\d{4})年(\d{1,2})月(\d{1,2})日(?:起)?(?:实施|施行)'),
    # YYYY年MM月DD日起实施/起施行/起生效（带"起"但无"自"）
    re.compile(r'(\d{4})年(\d{1,2})月(\d{1,2})日(?:起实施|起施行|起生效)'),
    # === 标签类：实施日期/生效日期/发布日期 ===
    # 实施日期：YYYY年MM月DD日
    re.compile(r'实施日期[：:]\s*(\d{4})年(\d{1,2})月(\d{1,2})日'),
    # 实施日期：YYYY-MM-DD（纯数字）
    re.compile(r'实施日期[：:]\s*(\d{4})[-/](\d{1,2})[-/](\d{1,2})'),
    # 生效日期：YYYY年MM月DD日
    re.compile(r'生效日期[：:]\s*(\d{4})年(\d{1,2})月(\d{1,2})日'),
    # 生效日期：YYYY-MM-DD（纯数字）
    re.compile(r'生效日期[：:]\s*(\d{4})[-/](\d{1,2})[-/](\d{1,2})'),
    # 发布日期：YYYY年MM月DD日
    re.compile(r'发布日期[：:]\s*(\d{4})年(\d{1,2})月(\d{1,2})日'),
    # === 弱信号：日期 + 实施关键词 ===
    # YYYY年MM月DD日施行/实施/执行/生效（无"自...起"前缀）
    re.compile(r'(\d{4})年(\d{1,2})月(\d{1,2})日(?:施行|实施|执行|生效)'),
    # YYYY-MM-DD实施/施行/生效（数字日期+实施）
    re.compile(r'(\d{4})[-/](\d{1,2})[-/](\d{1,2})(?:实施|施行|生效)'),
    # YYYY年MM月DD日发布/公布（兜底）
    re.compile(r'(\d{4})年(\d{1,2})月(\d{1,2})日(?:发布|公布)'),
]

# 失效日期/有效期正则
_EXPIRY_PATTERNS = [
    # 自X起至Y止（6 组：起y/m/d + 止y/m/d）
    re.compile(r'自(\d{4})年(\d{1,2})月(\d{1,2})日起至(\d{4})年(\d{1,2})月(\d{1,2})日止'),
    # 有效期至YYYY年MM月DD日 / 至YYYY年MM月DD日止
    re.compile(r'(?:有效期至|至)(\d{4})年(\d{1,2})月(\d{1,2})日(?:止)?'),
]

# 印发之日模式（公文常见："自印发之日起实施"——无具体日期，需从文件元数据兜底）
_IMPRINT_PATTERN = re.compile(r'自印发之日起(?:施行|实施|执行|生效)?')

# 印发日期模式（兜底，用于通知类文件："YYYY年MM月DD日印发/发布/公布/颁布/发布实施"）
_IMPRINT_DATE_PATTERN = re.compile(r'(\d{4})年(\d{1,2})月(\d{1,2})日(?:印发|发布|公布|颁布|发布实施)')

_VOID_KEYWORDS = ('已废止', '即日起废止', '失效日期', '本规定已失效')
_REPLACED_KEYWORDS = ('已被', '被替代', '被取代')


def _read_docx_created(path):
    """读取 docx 文件元数据的创建日期（core_properties.created）。
    成功返回 'YYYY-MM-DD' 字符串，失败返回 ''。"""
    try:
        from docx import Document
        doc = Document(path)
        cp = doc.core_properties
        if cp.created:
            # datetime 对象（可能带时区）
            return cp.created.strftime('%Y-%m-%d')
    except Exception:
        pass
    return ''


def _extract_metadata(lines, filename, path=None):
    """从前 200 行 + 文件名 + 文件元数据自动识别文档号/生效日期/状态。
    返回 {'doc_no', 'effective_date', 'status'}，未识别字段为 ''。
    生效日期格式：
      - 仅开始：'YYYY-MM-DD'
      - 起止：'YYYY-MM-DD~YYYY-MM-DD'
      - 仅失效：'~YYYY-MM-DD'
    status: 'active' / 'void' / 'replaced'
    """
    # 1. 文档号：先扫文件名（公文常以文档号命名），再扫正文
    doc_no = ''

    def _pick_doc_no(m):
        """从匹配对象拼出文档号：中文公文模式拼接，国标等直接 group(0)"""
        g = m.groups()
        if g and len(g) >= 3 and g[0] and '\u4e00' <= g[0][0] <= '\u9fa5':
            return f'{g[0]}〔{g[1]}〕{g[2]}号'
        return m.group(0).strip()

    for pat in _DOC_NO_PATTERNS:
        m = pat.search(filename)
        if m:
            doc_no = re.sub(r'\s+', ' ', _pick_doc_no(m))
            break

    # 前 200 行扫描（公文头 + 总则一般在前面）
    text = '\n'.join(t for _, t in list(lines)[:200])
    if not doc_no and text:
        for pat in _DOC_NO_PATTERNS:
            m = pat.search(text)
            if m:
                doc_no = re.sub(r'\s+', ' ', _pick_doc_no(m))
                break

    # 2. 生效日期 + 失效日期
    eff_start, eff_end = '', ''
    has_imprint = False  # 是否含"自印发之日起施行"模式（无具体日期）
    if text:
        for pat in _EFF_DATE_PATTERNS:
            m = pat.search(text)
            if m:
                y, mo, d = m.group(1), m.group(2), m.group(3)
                try:
                    eff_start = f'{int(y):04d}-{int(mo):02d}-{int(d):02d}'
                except Exception:
                    pass
                break
        # 未识别到具体生效日期 → 检查是否含"自印发之日起"兜底
        if not eff_start:
            if _IMPRINT_PATTERN.search(text):
                has_imprint = True
        # 兜底：通知类文件末尾"YYYY年MM月DD日印发"
        if not eff_start:
            m = _IMPRINT_DATE_PATTERN.search(text)
            if m:
                y, mo, d = m.group(1), m.group(2), m.group(3)
                try:
                    eff_start = f'{int(y):04d}-{int(mo):02d}-{int(d):02d}'
                except Exception:
                    pass
        for pat in _EXPIRY_PATTERNS:
            m = pat.search(text)
            if m:
                g = m.groups()
                # 起止模式取后 3 组（止日期）
                if len(g) >= 6 and g[3]:
                    y, mo, d = g[3], g[4], g[5]
                else:
                    y, mo, d = g[0], g[1], g[2]
                try:
                    eff_end = f'{int(y):04d}-{int(mo):02d}-{int(d):02d}'
                except Exception:
                    pass
                break

    # "自印发之日起"模式 → 从 docx 元数据读创建日期兜底
    if has_imprint and not eff_start and path:
        ext = os.path.splitext(path)[1].lower()
        if ext == '.docx':
            eff_start = _read_docx_created(path)

    effective_date = ''
    if eff_start and eff_end:
        effective_date = f'{eff_start}~{eff_end}'
    elif eff_start:
        effective_date = eff_start
    elif eff_end:
        effective_date = f'~{eff_end}'

    # 3. 状态：失效日期已过期 → void；关键词 → void/replaced
    status = 'active'
    if eff_end:
        try:
            if _dt.date.fromisoformat(eff_end) < _dt.date.today():
                status = 'void'
        except Exception:
            pass
    if status == 'active':
        if any(kw in text for kw in _VOID_KEYWORDS):
            status = 'void'
        elif any(kw in text for kw in _REPLACED_KEYWORDS):
            status = 'replaced'

    # 兜底：从文件名提取标准号中的年份（如 GB 50300-2013 → 2013）
    if not effective_date:
        fn = os.path.basename(path) if path else (filename or '')
        m = re.search(r'[-—](\d{4})(?:\s|\.)', fn)
        if m:
            year = m.group(1)
            # 只接受 1990~当前年份 范围
            try:
                y = int(year)
                if 1990 <= y <= _dt.date.today().year:
                    effective_date = f'{year}'
            except Exception:
                pass

    return {'doc_no': doc_no, 'effective_date': effective_date, 'status': status}


# ========== 分类（分类）管理：规则存储 / 自动归类 / 自查 ==========

_RULES_KEY = 'category_rules'

# 默认分类规则：[[分类名, 关键词...], ...]，从上到下先中先得；
# 关键词不区分大小写，依次匹配 文件名+文档号 → 正文开头 2000 字；
# 're:' 前缀 = 正则（对原始与去空格文本各试一次）
# ========== 十大分类（分类名 = library 下的物理文件夹名，不许改名） ==========
OTHER_CATEGORY = '10-其他资料'
CATEGORY_FOLDERS = [
    '01-国家法律', '02-行政法规', '03-部门规章', '04-规范性文件',
    '05-国家标准GB', '06-行业标准JGJ', '07-河北省法规规章',
    '08-河北省地方标准', '09-公司制度', OTHER_CATEGORY,
]

# 规则顺序即匹配优先级：国标/行标/地标编号是强信号，排在河北省法规之前，
# 避免「DB13… 河北省…」这类标题被“河北省”关键词抢先归入法规规章
DEFAULT_CATEGORY_RULES = [
    # 01 国家法律：全国人大及其常委会颁布，名称多为“中华人民共和国XX法”
    ['01-国家法律', '中华人民共和国', '主席令', '国家法律',
     r're:中华人民共和国.{0,25}法'],
    # 02 行政法规：国务院颁布的条例、规定、办法
    ['02-行政法规', '国务院令', '国务院关于', '条例', '行政法规'],
    # 03 部门规章：国务院各部委以部令/局长令发布
    ['03-部门规章', '部令', '局长令', '委员会令', '署令',
     '住建部令', '住房和城乡建设部令', '应急管理部令', '交通运输部令',
     '生态环境部令', '国家发改委令', '市场监管总局令', '部门规章'],
    # 04 规范性文件：各级部门发布的通知、意见、指南、导则、文号文件等
    ['04-规范性文件', '通知', '意见', '指南', '导则', '指引', '方案',
     '公告', '规范性文件', '建办质', '建质', '建市规', '冀建质安',
     '人社部发', '安委会', '指导手册', '安全手册', '实施办法', '暂行办法'],
    # 05 国家标准：GB / GB/T / GBZ / GBJ
    ['05-国家标准GB', 'GB/T', 'GBT', 'GBZ', 'GBJ', '国家标准',
     r're:GB[/T]?\s*\d{2,5}', r're:^GB\s*\d'],
    # 06 行业标准：JGJ/JTG/CJJ/DL 等行业编号
    ['06-行业标准JGJ', 'JGJ', 'JGJT', 'JTG', 'CJJ', 'DL/T', 'JB/T',
     'YD/T', 'HG/T', 'NB/T', 'JT/T', '行业标准',
     r're:JGJ[T]?\s*\d', r're:DL\s*/?\s*\d', r're:CJ[J]?\s*\d',
     r're:JTG\s*\d'],
    # 08 河北省地方标准：DB13 强制 / DB13/T 推荐 / DB13(J) 工程建设；
    # 兼容文件名写法 DB13-2934 / DB13-J-T100 / DB13(J)/T 8362
    ['08-河北省地方标准', 'DB13', 'DB13/T', 'DB13(J)', 'DB13(J)/T',
     '河北省地方标准', '河北省工程建设标准',
     r're:DB\s*13\s*[\-/]?\s*\(?J?\)?\s*[\-/]?\s*T?\s*\d'],
    # 07 河北省法规规章：省人大条例、省政府令、省政府规定/办法
    # 注意：不使用裸'河北省'关键词（会把河北省企业文件误归法规）
    ['07-河北省法规规章', '河北省人民政府令', '河北省人大',
     '河北省人民代表大会', '冀政令', '冀政办发',
     r're:河北省.{0,15}(?:条例|规定|办法|细则|决定)',
     r're:河北省.{0,10}(?:人民政府|人大常委会)'],
    # 09 公司制度：企业内部制度、办法、细则、规程等
    ['09-公司制度', '管理办法', '管理规定', '管理制度', '制度',
     '实施细则', '操作规程', '作业指导书', '公司文件', '内部文件',
     '惩戒', '负面行为', '责任田'],
]


def _default_rules():
    return [list(r) for r in DEFAULT_CATEGORY_RULES]


def get_category_rules(db_or_conn):
    """读分类规则 [[分类名, 关键词...], ...]。参数为 db 路径或已打开连接"""
    if isinstance(db_or_conn, sqlite3.Connection):
        row = db_or_conn.execute(
            'SELECT value FROM settings WHERE key=?', (_RULES_KEY,)).fetchone()
        val = row[0] if row else None
    else:
        conn, _ = get_db(db_or_conn)
        try:
            row = conn.execute(
                'SELECT value FROM settings WHERE key=?',
                (_RULES_KEY,)).fetchone()
            val = row[0] if row else None
        finally:
            conn.close()
    if val:
        try:
            rules = json.loads(val)
            if isinstance(rules, list) and rules:
                return rules
        except Exception:
            pass
    return _default_rules()


def save_category_rules(db_path, rules):
    """保存分类规则（列表 [[名, 关键词...], ...]）。True / 'error: ...'"""
    conn, _ = get_db(db_path)
    try:
        conn.execute(
            'INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)',
            (_RULES_KEY, json.dumps(rules, ensure_ascii=False)))
        conn.commit()
        return True
    except Exception as e:
        return f'error: {e}'
    finally:
        conn.close()


def infer_category(name, doc_no, text, rules):
    """按规则推断分类，未命中返回 ''。
    两轮匹配：文件名+文档号扫全部规则 → 正文开头 2000 字扫（默认九大类不扫正文，
    只信文件名/文档号；用户自建分类才扫正文）；每轮内从上到下先中先得。
    匹配忽略空格（兼容 GB 50160 这类带空格编号与 OCR 断行文本）"""
    hay_head = f'{name or ""}\n{doc_no or ""}'.upper()
    hay_body = (text or '')[:2000].upper()
    hay_head_ns = hay_head.replace(' ', '')
    hay_body_ns = hay_body.replace(' ', '')

    def _hit(kw, head_first):
        if not isinstance(kw, str):
            return False
        kw = kw.strip()
        if not kw:
            return False
        hay = hay_head if head_first else hay_body
        hay_ns = hay_head_ns if head_first else hay_body_ns
        if kw.startswith('re:'):
            pat = kw[3:].upper()
            for h in (hay, hay_ns):
                try:
                    if re.search(pat, h):
                        return True
                except re.error:
                    return False
            return False
        kw = kw.upper()
        kw_ns = kw.replace(' ', '')
        return kw in hay or kw_ns in hay_ns

    # 默认分类只信文件名/文档号里的信号，不扫正文：
    # 标准前言里普遍引用《中华人民共和国安全生产法》、住建部令、河北省条例等，
    # 正文弱词（通知/方案/制度/条例）更是高频出现，扫正文必误伤；
    # 文件名判不了的一律落 10-其他资料，由人工在“未分类”里指定。
    # 用户在分类管理里自建的新分类不受此限（仍可扫正文）。
    head_only_cats = {
        '01-国家法律', '02-行政法规', '03-部门规章', '04-规范性文件',
        '05-国家标准GB', '06-行业标准JGJ', '07-河北省法规规章',
        '08-河北省地方标准', '09-公司制度',
    }
    for head_first in (True, False):
        for rule in rules or []:
            if not isinstance(rule, (list, tuple)) or len(rule) < 2:
                continue
            cat = str(rule[0]).strip()
            if not cat:
                continue
            # 默认分类不扫正文（仅文件名+文档号）
            if not head_first and cat in head_only_cats:
                continue
            for kw in rule[1:]:
                if _hit(kw, head_first):
                    return cat
    return ''


def set_category_ignored(db_path, paths, ignored):
    """批量设置/取消分类忽略。ignored=True 时这些文件不再参与分类自查。
    返回成功条数"""
    if not paths:
        return 0
    conn, _ = get_db(db_path)
    try:
        n = 0
        for p in paths:
            conn.execute(
                'UPDATE documents SET cat_ignored=? WHERE path=?',
                (1 if ignored else 0, p))
            n += 1
        conn.commit()
        return n
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def category_counts(db_path):
    """{分类: 数量}；'' 键 = 未分类"""
    conn, _ = get_db(db_path)
    try:
        rows = conn.execute(
            "SELECT COALESCE(category,''), COUNT(*) FROM documents "
            "GROUP BY COALESCE(category,'')").fetchall()
        return {c or '': n for c, n in rows}
    finally:
        conn.close()


def category_audit(db_path):
    """分类自查：按当前规则重算每个文件的建议分类。
    返回 {'total', 'counts': {分类:n}, 'uncategorized': [item],
          'mismatch': [{'path','name','current','suggested'}]}"""
    conn, _ = get_db(db_path)
    try:
        rules = get_category_rules(conn)
        rows = conn.execute(
            "SELECT id, path, name, doc_no, COALESCE(category,''), "
            "COALESCE(cat_ignored,0) "
            'FROM documents WHERE COALESCE(cat_ignored,0)=0').fetchall()
        counts, uncategorized, mismatch = {}, [], []
        for doc_id, path, name, doc_no, cur, _ign in rows:
            text = '\n'.join(r[0] for r in conn.execute(
                'SELECT line_text FROM lines '
                'WHERE doc_id=? AND line_no<=200 ORDER BY line_no',
                (doc_id,)).fetchall())
            sug = infer_category(name, doc_no, text, rules)
            key = sug or ''
            counts[key] = counts.get(key, 0) + 1
            if not cur:
                # 未分类的文件始终列入 uncategorized（有建议则附建议）
                uncategorized.append({'path': path, 'name': name,
                                      'suggested': sug, 'need_manual': False})
            elif sug and sug != cur:
                # 当前有分类但建议不同 → mismatch
                is_likely_wrong = (cur == '09-公司制度' and
                                  sug in ('05-国家标准GB', '06-行业标准JGJ',
                                          '08-河北省地方标准', '03-部门规章',
                                          '01-国家法律', '02-行政法规',
                                          '07-河北省法规规章', '04-规范性文件'))
                mismatch.append({'path': path, 'name': name, 'current': cur,
                                 'suggested': sug, 'need_manual': is_likely_wrong})
            elif not sug and cur:
                # 规则无法判断但有分类 → 需人工确认
                uncategorized.append({'path': path, 'name': name,
                                      'suggested': '', 'need_manual': True})
        return {'total': len(rows), 'counts': counts,
                'uncategorized': uncategorized, 'mismatch': mismatch,
                'ignored_count': conn.execute(
                    'SELECT COUNT(*) FROM documents '
                    'WHERE COALESCE(cat_ignored,0)=1').fetchone()[0]}
    finally:
        conn.close()


def apply_categories(db_path, mapping):
    """批量写入分类 {path: category}（空串=未分类→10-其他资料文件夹）。
    分类变化时物理文件同步搬到对应文件夹。返回成功条数"""
    return len(apply_categories_ex(db_path, mapping))


def apply_categories_ex(db_path, mapping):
    """同 apply_categories，但返回 [{old_path, new_path, old_cat, new_cat}]
    供「撤销上次修正」用。"""
    if not mapping:
        return []
    conn, _ = get_db(db_path)
    try:
        recs = []
        for p, c in mapping.items():
            new_cat = c or ''
            row = conn.execute(
                "SELECT COALESCE(category,'') FROM documents WHERE path=?",
                (p,)).fetchone()
            old_cat = row[0] if row else ''
            conn.execute(
                'UPDATE documents SET category=? WHERE path=?',
                (new_cat, p))
            new_path = relocate_to_category(conn, p, new_cat)
            recs.append({'old_path': p, 'new_path': new_path,
                         'old_cat': old_cat, 'new_cat': new_cat})
        conn.commit()
        return recs
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def revert_categories(db_path, records):
    """撤销 apply_categories_ex 的批量修正：文件搬回原路径并恢复旧分类。
    目标位置被占/文件已不在 → 跳过记数。返回 (恢复条数, 跳过条数)"""
    conn, _ = get_db(db_path)
    try:
        ok = skip = 0
        for r in records:
            new_abs = os.path.abspath(r['new_path'])
            old_abs = os.path.abspath(r['old_path'])
            if os.path.normcase(new_abs) == os.path.normcase(old_abs):
                # 未发生物理搬迁（外部文件只改了分类）→ 只恢复分类
                conn.execute('UPDATE documents SET category=? WHERE path=?',
                             (r['old_cat'], new_abs))
                ok += 1
                continue
            if not os.path.exists(new_abs) or os.path.exists(old_abs):
                skip += 1
                continue
            try:
                os.makedirs(os.path.dirname(old_abs), exist_ok=True)
                os.replace(new_abs, old_abs)
            except OSError:
                skip += 1
                continue
            _update_path_everywhere(conn, new_abs, old_abs)
            conn.execute('UPDATE documents SET category=? WHERE path=?',
                         (r['old_cat'], old_abs))
            ok += 1
        conn.commit()
        return ok, skip
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _detect_chapters(lines):
    """
    识别制度文档的章节结构，返回 [(start_line, end_line, title, level), ...]

    end_line 算法：每个章节持续到**下一个同级或更高级别（level<=）章节起点 - 1**，
    或文档末尾。这样父章节（如第三条）能覆盖其下所有子项（1./2./3.），
    形成"第三章 > 第三条 > 2. 部门审批"的完整祖先链。

    level: 0=Markdown标题 1=篇/部 2=章 3=节 4=条 5=一级 6=二级
    """
    if not lines:
        return []
    # —— 先检测"目录"标记行号 ——
    _toc_markers = re.compile(r'^\s*目\s*录\s*[：:]?\s*$')
    toc_line = None
    for ln, text in lines:
        if _toc_markers.match(text):
            toc_line = ln
            break
    # —— 第一遍：识别所有章节起点 ——
    starts = []  # [(line_no, title, level)]
    for line_no, text in lines:
        for pat, lv in _CHAPTER_PATTERNS:
            m = pat.match(text)
            if m:
                if lv == 0:
                    hashes = m.group(1)
                    title = m.group(2).strip()
                    full_title = f'{hashes} {title}'
                else:
                    full_title = m.group(0).strip()
                starts.append((line_no, full_title[:200], lv))
                break
    if not starts:
        return []
    # —— 跳过目录页章节 ——
    # 目录页本质：toc_line 之后，连续的"第X章"条目，每条后面紧跟着的
    # 也是"第X章"（同为 level<=2），直到遇到正文里"章→条"的 level 跳变
    # 正文特征：level<=2 的章后面紧跟 level>2 的条
    if toc_line is not None:
        cut_idx = 0
        for i in range(len(starts)):
            ln, title, lv = starts[i]
            if ln <= toc_line:
                continue
            if lv > 2:
                # 遇到非章节条目（条/款等），说明已进入正文，停止跳过
                break
            # lv <= 2：检查后面第一个章节的 level
            # 如果后面第一个章节也是 lv<=2，说明还在目录页，继续跳过
            # 如果后面第一个章节 lv>2，说明这是正文第一章，保留
            next_lv = None
            for j in range(i + 1, len(starts)):
                nxt_ln, _, nxt_lv = starts[j]
                if nxt_ln - ln > 5:
                    break  # 太远了，不算紧邻
                next_lv = nxt_lv
                break
            if next_lv is not None and next_lv <= 2:
                # 目录页：章后面还是章
                cut_idx = i + 1
            else:
                # 正文第一章：章后面跟条（level>2），或者没有下一条
                break
        if cut_idx > 0:
            starts = starts[cut_idx:]
    last_line = lines[-1][0]
    # 第二遍：按级别规则算 end_line
    # 章节 i 的 end_line = 下一个 j 的 start_line - 1，其中 starts[j].level <= starts[i].level
    # 若无匹配，end_line = last_line
    chapters = []
    for i, (cur_start, cur_title, cur_level) in enumerate(starts):
        end = last_line
        for j in range(i + 1, len(starts)):
            nxt_start, _, nxt_level = starts[j]
            if nxt_level <= cur_level:
                end = nxt_start - 1
                break
        chapters.append((cur_start, end, cur_title, cur_level))
    return chapters


def get_errors(db_path):
    """P0-5: 读取错误日志，按时间倒序"""
    conn, _ = get_db(db_path)
    rows = conn.execute(
        'SELECT id, path, stage, error, ts FROM errors ORDER BY ts DESC'
    ).fetchall()
    conn.close()
    return [{'id': r[0], 'path': r[1], 'stage': r[2],
             'error': r[3], 'ts': r[4]} for r in rows]


def get_error_traceback(db_path, error_id):
    """P0-5: 读取单条错误的完整堆栈"""
    conn, _ = get_db(db_path)
    row = conn.execute(
        'SELECT traceback FROM errors WHERE id=?', (error_id,)
    ).fetchone()
    conn.close()
    return row[0] if row else ''


def clear_errors(db_path):
    """P0-5: 清空错误日志"""
    conn, _ = get_db(db_path)
    conn.execute('DELETE FROM errors')
    conn.commit()
    conn.close()


def count_errors(db_path):
    """P0-5: 错误总数"""
    conn, _ = get_db(db_path)
    n = conn.execute('SELECT COUNT(*) FROM errors').fetchone()[0]
    conn.close()
    return n


# ========== P1-11: 单文件增量更新（供 watcher 调用） ==========

def index_single_file(path, db_path, callback=None, parse_timeout=120):
    """
    增量更新单个文件：解析 + upsert。
    供 watchdog 事件回调使用，避免全量扫描。

    parse_timeout: parse_file 超时秒数，超时则跳过该文件继续下一个。
                   设为 None 则不超时（默认）。

    返回 'ok' / 'skip' / 'deleted' / 'error: ...' / 'timeout'
    """
    conn, _ = get_db(db_path)
    fts_ok = _check_fts(conn)
    try:
        if not os.path.exists(path):
            # 文件已删除
            _remove_doc_from_index(conn, path, fts_ok)
            conn.commit()
            _cb(callback, 1, 1, path, 'deleted')
            return 'deleted'
        st = os.stat(path)
        # 检查 mtime 是否变化
        known_mtime = conn.execute(
            'SELECT mtime FROM documents WHERE path=?', (path,)
        ).fetchone()
        if known_mtime and known_mtime[0] == st.st_mtime:
            _cb(callback, 1, 1, path, 'skip')
            return 'skip'
        # —— 解析文件（带超时保护）——
        lines = enc = ftype = None
        if parse_timeout is not None:
            result = _parse_with_timeout(path, parse_timeout)
            if result is None:
                _record_error(conn, path, 'parse_timeout',
                              TimeoutError(f'parse_file 超时 {parse_timeout}s'))
                conn.commit()
                _cb(callback, 1, 1, path, 'timeout')
                return 'timeout'
            lines, enc, ftype = result
        else:
            lines, enc, ftype = parse_file(path)
        try:
            _upsert(conn, path, os.path.splitext(path)[1].lower(), ftype, enc,
                    st.st_mtime, st.st_size, lines, fts_ok)
            conn.commit()
            _cb(callback, 1, 1, path, 'ok')
            return 'ok'
        except Exception as e:
            _record_error(conn, path, 'parse', e)
            conn.commit()
            _cb(callback, 1, 1, path, f'error: {e}')
            return f'error: {e}'
    except Exception as e:
        try:
            _record_error(conn, path, 'parse', e)
            conn.commit()
        except Exception:
            pass
        _cb(callback, 1, 1, path, f'error: {e}')
        return f'error: {e}'
    finally:
        conn.close()


def index_batch_files(files, db_path, stop_event=None, ui_cb=None,
                      parse_timeout=120, batch_commit=50, workers=None):
    """
    高性能批量入库：预筛（先算全量 sha256 去重）+ 并行解析（as_completed）+ 批量 DB 事务。

    关键改进（v2）：
    - 预筛先算 sha256 再提交：避免同文件被并发 parse 两次（v1 有 25 个并发 sha 重复）
    - parse_timeout 从 20s → 120s：200+ 页大 PDF 不会因太短超时被丢（v1 丢了 18 个）
    - as_completed 回收：哪个文件先 parse 完就先写 DB，不阻塞其他已完成结果
    - 去掉嵌套 ThreadPoolExecutor：_parse_with_timeout 改用子线程 + join，无额外线程池开销

    返回 {ok, dup, skip, failed, timeout, imported_ok, skipped_detail, total}
    """
    import concurrent.futures as _fut

    if workers is None:
        import os as _os
        workers = max(2, min(8, (_os.cpu_count() or 4) // 2))

    # ==========================================
    # 阶段 1：同 realpath 去重
    # ==========================================
    seen_rp, uniq = set(), []
    for p in files:
        try:
            rp = os.path.realpath(p)
        except Exception:
            rp = p
        if rp not in seen_rp:
            seen_rp.add(rp)
            uniq.append(p)
    files = uniq
    total = len(files)

    conn, _ = get_db(db_path)
    fts_ok = _check_fts(conn)

    # ==========================================
    # 阶段 2：拉 DB 已有 path / sha256 / mtime
    # ==========================================
    existing_paths = {}   # path -> (mtime, sha256)
    db_sha_set = set()    # DB 已有的 sha256
    try:
        for row in conn.execute(
                'SELECT path, mtime, sha256 FROM documents').fetchall():
            p, mt, sh = row[0], row[1], row[2]
            existing_paths[p] = (mt, sh)
            if sh:
                db_sha_set.add(sh)
    except Exception:
        pass

    # ==========================================
    # 阶段 3：全量预处理 —— 算 sha256 → 筛 dup / skip → 得到 to_parse
    # ==========================================
    ok = dup = skipped = failed = timeout = 0
    imported_ok = []
    skipped_detail = []
    batch_sha = set()    # 本批内已见过的 sha256
    to_parse = []        # [(path, sha256), ...] 真正要提交 parse 的

    for i, p in enumerate(files):
        if stop_event is not None and stop_event.is_set():
            break
        label = os.path.basename(p)

        # 3a) 同 path 已在库 → 看 mtime 是否变了
        if p in existing_paths:
            try:
                st = os.stat(p)
                known_mt = existing_paths[p][0]
                if known_mt is not None and known_mt == st.st_mtime:
                    skipped += 1
                    skipped_detail.append((p, '路径+时间戳未变'))
                    if ui_cb:
                        ui_cb(i + 1, total, label, 'skip', p)
                    continue
            except Exception:
                pass

        # 3b) 算 sha256（纯 I/O + hash，比 parse 快得多）
        try:
            sha = _file_sha256(p) or ''
        except Exception:
            sha = ''

        # 3c) 和 DB 已有 sha 对比
        if sha and sha in db_sha_set:
            # 找到 DB 里同 sha 的文件
            hit = None
            try:
                hit = conn.execute(
                    'SELECT path FROM documents WHERE sha256=? LIMIT 1',
                    (sha,)).fetchone()
            except Exception:
                pass
            dup_reason = f'内容与 {os.path.basename(hit[0])} 重复' if hit else '内容重复（DB）'
            dup += 1
            skipped_detail.append((p, dup_reason))
            if ui_cb:
                ui_cb(i + 1, total, label, 'dup_skip', p)
            continue

        # 3d) 和本批已收集的 sha 对比
        if sha and sha in batch_sha:
            dup += 1
            skipped_detail.append((p, '本批内容重复'))
            if ui_cb:
                ui_cb(i + 1, total, label, 'dup_skip', p)
            continue

        # 3e) 入队
        if sha:
            batch_sha.add(sha)
        to_parse.append((p, sha))
        if ui_cb:
            ui_cb(i + 1, total, label, 'pending', p)  # 表示已入队待 parse

    # ==========================================
    # 阶段 4：并行解析（as_completed）
    # ==========================================
    _fut_to_path = {}  # fut -> (path, sha)
    done_idx = 0

    pool = _fut.ThreadPoolExecutor(max_workers=workers)
    # 先全部 submit
    for p, sha in to_parse:
        if stop_event is not None and stop_event.is_set():
            break
        fut = pool.submit(_parse_with_timeout, p, parse_timeout)
        _fut_to_path[fut] = (p, sha)

    # 用 as_completed 回收：哪个先好先处理
    for fut in _fut.as_completed(_fut_to_path):
        if stop_event is not None and stop_event.is_set():
            # 立即响应停止：取消排队任务、不等待在跑的长 OCR
            try:
                pool.shutdown(wait=False, cancel_futures=True)
            except TypeError:  # Python <3.9 无 cancel_futures
                pool.shutdown(wait=False)
            break

        p, sha = _fut_to_path[fut]
        done_idx += 1
        label = os.path.basename(p)

        try:
            result = fut.result()
        except Exception as e:
            failed += 1
            skipped_detail.append((p, f'解析异常: {e}'))
            if ui_cb:
                ui_cb(done_idx, total, label, 'error', p)
            continue

        if result is None:
            timeout += 1
            skipped_detail.append((p, f'parse_timeout {parse_timeout}s'))
            if ui_cb:
                ui_cb(done_idx, total, label, 'timeout', p)
            continue

        lines, enc, ftype = result
        # 解析期间文件可能已被删除/移动：stat 失败整项跳过，不炸整批
        try:
            st = os.stat(p)
        except OSError as e:
            failed += 1
            skipped_detail.append((p, f'文件已消失: {e}'))
            if ui_cb:
                ui_cb(done_idx, total, label, 'error', p)
            continue

        # 最终 sha 校验（理论上不会命中，但防万一）
        final_sha = _file_sha256(p) or sha or ''
        if final_sha and final_sha not in batch_sha:
            batch_sha.add(final_sha)

        try:
            _upsert(conn, p, os.path.splitext(p)[1].lower(), ftype, enc,
                    st.st_mtime, st.st_size, lines, fts_ok, sha=final_sha)
            ok += 1
            imported_ok.append(p)
        except Exception as e:
            failed += 1
            skipped_detail.append((p, f'写入异常: {e}'))
            try:
                _record_error(conn, p, 'upsert', e)
            except Exception:
                pass

        # 批量 commit
        if (ok + dup + skipped + failed + timeout) % batch_commit == 0:
            try:
                conn.commit()
            except Exception:
                pass

        if ui_cb:
            ui_cb(done_idx, total, label, 'ok', p)

    else:
        # 正常处理完：等待线程池收尾
        pool.shutdown(wait=True)

    # 最终 commit
    try:
        conn.commit()
    except Exception:
        pass
    conn.close()

    return {
        'ok': ok, 'dup': dup, 'skip': skipped, 'failed': failed,
        'timeout': timeout,
        'imported_ok': imported_ok,
        'skipped_detail': skipped_detail,
        'total': total,
    }


def _parse_with_timeout(path, timeout):
    """带超时保护的 parse_file。超时返回 None，异常向上抛。
    用子线程 + join 实现超时（Python 线程无法硬杀，只能兜底）。"""
    import threading
    _result = [None]       # 容器（mutable）
    _exc = [None]

    def _run():
        try:
            _result[0] = parse_file(path)
        except Exception as e:
            _exc[0] = e

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        # 超时：放弃等待。线程仍会跑完（daemon 退出时自动清理）
        return None
    if _exc[0] is not None:
        raise _exc[0]
    return _result[0]


def _remove_doc_from_index(conn, path, fts_ok):
    """从索引中删除一个文件的所有记录（含语义索引块）。"""
    row = conn.execute('SELECT id FROM documents WHERE path=?', (path,)).fetchone()
    if not row:
        return
    doc_id = row[0]
    conn.execute('DELETE FROM lines WHERE doc_id=?', (doc_id,))
    if fts_ok:
        try:
            conn.execute('DELETE FROM lines_fts WHERE doc_id=?', (doc_id,))
        except Exception:
            pass
    conn.execute('DELETE FROM chapters WHERE doc_id=?', (doc_id,))
    conn.execute('DELETE FROM bigram WHERE doc_id=?', (doc_id,))
    try:
        conn.execute(
            'DELETE FROM qa_embeddings WHERE chunk_id IN '
            '(SELECT id FROM qa_chunks WHERE doc_id=?)', (doc_id,))
        conn.execute('DELETE FROM qa_chunks WHERE doc_id=?', (doc_id,))
    except Exception:
        pass
    conn.execute('DELETE FROM documents WHERE id=?', (doc_id,))


# ========== 库管理：拖拽入库 / 链接入库 / 列出 / 删除 ==========

def _get_app_dir():
    """exe 模式(PyInstaller)下数据放在 exe 同目录，源码模式放脚本目录"""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


LIBRARY_DIR = os.path.join(_get_app_dir(), 'library')


def _ensure_library():
    """确保 library 目录及十大分类文件夹存在"""
    os.makedirs(LIBRARY_DIR, exist_ok=True)
    for f in CATEGORY_FOLDERS:
        os.makedirs(os.path.join(LIBRARY_DIR, f), exist_ok=True)


def category_folder_name(category):
    """分类名 → 物理文件夹名；空串/未知分类 → 10-其他资料"""
    category = (category or '').strip()
    return category if category in CATEGORY_FOLDERS else OTHER_CATEGORY


def category_dir(category=''):
    """分类对应的物理目录（自动创建）"""
    d = os.path.join(LIBRARY_DIR, category_folder_name(category))
    os.makedirs(d, exist_ok=True)
    return d


def _is_in_library(path):
    """路径是否位于 library 目录内（含各分类子文件夹）。Windows 盘符大小写不敏感"""
    try:
        return os.path.normcase(os.path.commonpath(
            [os.path.abspath(path), LIBRARY_DIR])) == \
            os.path.normcase(LIBRARY_DIR)
    except ValueError:
        return False


def _unique_path_in(folder_dir, name):
    """指定文件夹内的不重名路径，重名加 _1/_2/_3"""
    dest = os.path.join(folder_dir, name)
    if not os.path.exists(dest):
        return dest
    stem, ext = os.path.splitext(name)
    for i in range(1, 10000):
        candidate = os.path.join(folder_dir, f'{stem}_{i}{ext}')
        if not os.path.exists(candidate):
            return candidate
    raise RuntimeError('库内文件重名超过 10000，无法入库')


def _unique_library_path(src_path):
    """兼容旧调用：先进 10-其他资料，建索引后再按分类归位"""
    _ensure_library()
    return _unique_path_in(category_dir(''), os.path.basename(src_path))


def _update_path_everywhere(conn, old_path, new_path):
    """文件搬家后同步所有存路径的表（chapters/lines 按 doc_id 关联，无需动）"""
    name = os.path.basename(new_path)
    conn.execute('UPDATE documents SET path=?, name=? WHERE path=?',
                 (new_path, name, old_path))
    conn.execute('UPDATE errors SET path=? WHERE path=?', (new_path, old_path))
    conn.execute('UPDATE bookmarks SET path=? WHERE path=?', (new_path, old_path))
    conn.execute(
        'UPDATE reading_positions SET path=? WHERE path=?', (new_path, old_path))


def relocate_to_category(conn, old_path, category):
    """把 library 内的物理文件搬到分类文件夹，并同步全部表路径。

    - 外部链接文件 / 磁盘缺失文件：只改分类，不动文件
    - 已在目标文件夹：原样返回
    - 目标重名：自动加 _1/_2
    返回最终路径。
    """
    old_abs = os.path.abspath(old_path)
    target_dir = os.path.join(LIBRARY_DIR, category_folder_name(category))
    cur_folder = os.path.basename(os.path.dirname(old_abs))
    if cur_folder == category_folder_name(category):
        return old_path
    if not _is_in_library(old_abs) or not os.path.exists(old_abs):
        return old_path
    os.makedirs(target_dir, exist_ok=True)
    new_path = _unique_path_in(target_dir, os.path.basename(old_abs))
    os.replace(old_abs, new_path)
    _update_path_everywhere(conn, old_abs, new_path)
    return new_path


def settle_to_category(path, db_path):
    """建索引后按最终分类把文件归位，返回最终路径。
    未识别的库内文件物理落入 10-其他资料，并显式记为该分类；
    外部链接文件（不在 library 内）保持 '' 未分类。"""
    conn, _ = get_db(db_path)
    try:
        row = conn.execute(
            "SELECT COALESCE(category,'') FROM documents WHERE path=?",
            (os.path.abspath(path),)).fetchone()
        cat = row[0] if row else ''
        final = relocate_to_category(conn, path, cat)
        if not cat and _is_in_library(final) and os.path.basename(
                os.path.dirname(os.path.abspath(final))) == OTHER_CATEGORY:
            conn.execute('UPDATE documents SET category=? WHERE path=?',
                         (OTHER_CATEGORY, os.path.abspath(final)))
        conn.commit()
        return final
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


ARCHIVE_EXTS = ('.zip', '.rar', '.7z')
# 解包防护：总解压 2GB / 单文件 512MB / 单包 3000 个成员（zip 炸弹防御）
_ARCHIVE_TOTAL_LIMIT = 2 * 1024 ** 3
_ARCHIVE_FILE_LIMIT = 512 * 1024 ** 2
_ARCHIVE_MEMBER_LIMIT = 3000


def _extract_archive(src_path, tmp_root):
    """解压压缩包到临时目录（一层，不递归内层压缩包）。
    防路径穿越 + 限总大小。返回 [(member_abs_path, member_name), ...]"""
    import zipfile
    ext = os.path.splitext(src_path)[1].lower()
    members = []
    total = 0
    if ext == '.zip':
        with zipfile.ZipFile(src_path) as zf:
            infos = [i for i in zf.infolist() if not i.is_dir()]
            if len(infos) > _ARCHIVE_MEMBER_LIMIT:
                raise RuntimeError('压缩包内文件过多（>3000），请手动分批导入')
            for info in infos:
                # 防 zip-slip：目标必须落在临时目录内
                dest = os.path.abspath(
                    os.path.join(tmp_root, info.filename))
                if os.path.normcase(os.path.commonpath([tmp_root, dest])) \
                        != os.path.normcase(tmp_root):
                    continue
                if info.file_size > _ARCHIVE_FILE_LIMIT:
                    continue
                total += info.file_size
                if total > _ARCHIVE_TOTAL_LIMIT:
                    raise RuntimeError('压缩包解压总量超 2GB，请分批导入')
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with zf.open(info) as fsrc, open(dest, 'wb') as fdst:
                    shutil.copyfileobj(fsrc, fdst, 1024 * 1024)
                members.append((dest, info.filename))
    elif ext == '.7z':
        import py7zr
        with py7zr.SevenZipFile(src_path, mode='r') as z:
            infos = z.list()
            total_u = sum(getattr(i, 'uncompressed', 0) or 0 for i in infos)
            if total_u > _ARCHIVE_TOTAL_LIMIT:
                raise RuntimeError('压缩包解压总量超 2GB，请分批导入')
            if len(infos) > _ARCHIVE_MEMBER_LIMIT:
                raise RuntimeError('压缩包内文件过多（>3000），请手动分批导入')
            z.extractall(tmp_root)
        for dirpath, dirnames, files in os.walk(tmp_root):
            for fn in files:
                members.append((os.path.join(dirpath, fn), fn))
    elif ext == '.rar':
        import rarfile   # 需要 unrar.exe；缺失时抛异常由调用方提示
        rf = rarfile.RarFile(src_path)
        for info in rf.infolist():
            if info.is_dir():
                continue
            dest = os.path.abspath(os.path.join(tmp_root, info.filename))
            if os.path.normcase(os.path.commonpath([tmp_root, dest])) \
                    != os.path.normcase(tmp_root):
                continue
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with rf.open(info) as fsrc, open(dest, 'wb') as fdst:
                shutil.copyfileobj(fsrc, fdst, 1024 * 1024)
            members.append((dest, info.filename))
    return members


def _sha_in_library(db_path, sha):
    """库内是否已有相同内容（SHA256）的文件"""
    if not sha:
        return False
    conn, _ = get_db(db_path)
    try:
        return conn.execute(
            'SELECT 1 FROM documents WHERE sha256=? LIMIT 1',
            (sha,)).fetchone() is not None
    finally:
        conn.close()


def import_archive_to_library(archive_path, db_path, callback=None):
    """压缩包拆包入库：把包内文件逐个提取、独立建索引、按各自内容分类归位。
    压缩包本身不进库（内容已全部拆入）。内层嵌套压缩包按普通文件入库。
    与库内 SHA256 相同的成员自动跳过。
    返回 {'imported': n, 'failed': n, 'skipped': n, 'total': n,
           'imported_paths': [path, ...]}"""
    t0 = time.time()
    tmp_root = tempfile.mkdtemp(prefix='jz_unzip_')
    try:
        members = _extract_archive(archive_path, tmp_root)
        if not members:
            raise RuntimeError('压缩包内没有可导入的文件')
        ok = fail = skipped = 0
        imported_paths = []
        total = len(members)
        for i, (m_path, m_name) in enumerate(members, 1):
            _cb(callback, i, total, m_name, 'extracting')
            try:
                if _sha_in_library(db_path, _file_sha256(m_path)):
                    skipped += 1
                    _cb(callback, i, total, m_name, 'dup_skip')
                    continue
            except OSError:
                fail += 1
                continue
            _, status = _import_one_file(m_path, db_path)
            if status.startswith('error'):
                fail += 1
            else:
                ok += 1
                imported_paths.append(m_path)
            _cb(callback, i, total, m_name, status)
        return {'imported': ok, 'failed': fail, 'skipped': skipped,
                'total': total, 'seconds': time.time() - t0,
                'imported_paths': imported_paths}
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


def import_file_to_library(src_path, db_path, callback=None):
    """入库入口分派：压缩包自动拆包入库，普通文件走单文件导入。
    返回 (path, status, extra) — extra 为压缩包时含 imported_paths 列表"""
    if os.path.splitext(src_path)[1].lower() in ARCHIVE_EXTS \
            and os.path.isfile(src_path):
        _cb(callback, 0, 1, src_path, 'extracting')
        try:
            r = import_archive_to_library(src_path, db_path, callback)
            _cb(callback, 1, 1, src_path,
                f'archive: +{r["imported"]}/{r["total"]}'
                + (f' 失败{r["failed"]}' if r['failed'] else ''))
            return src_path, f'archive: {r["imported"]}', \
                r.get('imported_paths', [])
        except Exception:
            return _import_one_file(src_path, db_path, callback) + (None,)
    return _import_one_file(src_path, db_path, callback) + (None,)


def _import_one_file(src_path, db_path, callback=None):
    """
    复制源文件到 library/<分类文件夹>/ + 立即解析建索引 + 按分类归位。
    返回 (final_path, status) — status: 'ok' / 'exists' / 'error: ...'

    用于拖拽入库/手机上传的默认模式：文件真正进库并物理归入所属分类，
    未识别的进 10-其他资料，library 根目录永不存散文件。
    """
    import shutil
    if not os.path.exists(src_path):
        if callback:
            callback(0, 1, src_path, 'error: source not found')
        return src_path, 'error: source not found'
    try:
        _ensure_library()
        name = os.path.basename(src_path)
        # 已在 library 内（含子文件夹）：不重复复制，直接建索引再归位
        if _is_in_library(src_path):
            status = index_single_file(src_path, db_path)
            final = settle_to_category(src_path, db_path)
            if callback:
                callback(1, 1, final, status)
            return final, status
        # 先按文件名+文档号快速预判分类，让文件直接落进目标文件夹
        rules = get_category_rules(db_path)
        quick_cat = infer_category(name, '', '', rules)
        dest = _unique_path_in(category_dir(quick_cat), name)
        shutil.copy2(src_path, dest)
        status = index_single_file(dest, db_path)
        # 正文扫描后分类可能变化，再归位一次
        final = settle_to_category(dest, db_path)
        if callback:
            callback(1, 1, final, status)
        return final, status
    except Exception as e:
        conn, _ = get_db(db_path)
        try:
            _record_error(conn, src_path, 'import', e)
        finally:
            conn.close()
        if callback:
            callback(0, 1, src_path, f'error: {e}')
        return src_path, f'error: {e}'


def link_file_to_library(src_path, db_path, callback=None, parse_timeout=60):
    """
    不复制文件，只把原路径注册并建索引。
    返回 status — 'ok' / 'skip' / 'error: ...' / 'timeout'

    用于"仅登记路径（不复制文件）"模式：省空间，但原文件删除后搜索会失效。
    """
    status = index_single_file(src_path, db_path, callback=None,
                               parse_timeout=parse_timeout)
    if callback:
        callback(1, 1, src_path, status)
    return status


def is_registered(db_path, path):
    """该路径是否已在库中注册（链接模式入库预检用）"""
    conn, _ = get_db(db_path)
    try:
        return conn.execute(
            'SELECT 1 FROM documents WHERE path=? LIMIT 1',
            (path,)).fetchone() is not None
    finally:
        conn.close()


def plan_import(src_path, db_path):
    """入库前冲突预判（不复制任何文件）。
    返回 (action, target, sha)：
      ('reindex', dest, sha)   源文件本身就在 library 内 → 仅重建索引
      ('dup_skip', existing, sha) 库内已有相同 SHA256 内容 → 建议跳过
      ('conflict', dest, sha)  library 内同名但内容不同 → 需用户决策
      ('new', dest, sha)       无冲突，可直接复制入库
    """
    dest_dir = category_dir(infer_category(
        os.path.basename(src_path), '', '', get_category_rules(db_path)))
    dest = os.path.join(dest_dir, os.path.basename(src_path))
    try:
        if os.path.exists(dest) and os.path.samefile(src_path, dest):
            return 'reindex', dest, _file_sha256(dest)
    except OSError:
        pass
    sha = _file_sha256(src_path)
    # 全库内容查重（含不同名的情况）
    if sha:
        conn, _ = get_db(db_path)
        try:
            row = conn.execute(
                'SELECT path FROM documents WHERE sha256=? LIMIT 1',
                (sha,)).fetchone()
        finally:
            conn.close()
        if row:
            return 'dup_skip', row[0], sha
    if os.path.exists(dest):
        return 'conflict', dest, sha
    return 'new', dest, sha


def replace_library_file(src_path, db_path, callback=None):
    """同名替换：旧版索引删除 + 旧文件移入回收站，新文件复制到同名路径并建索引。
    返回 (dest_path, status)"""
    import shutil
    if not os.path.exists(src_path):
        return src_path, 'error: source not found'
    _ensure_library()
    name = os.path.basename(src_path)
    # 同名旧文件现在都在分类子文件夹里：先查 DB，再扫十个文件夹
    conn, _ = get_db(db_path)
    dest = None
    row = conn.execute(
        'SELECT path FROM documents WHERE name=? LIMIT 1', (name,)).fetchone()
    if row and _is_in_library(row[0]):
        dest = row[0]
    conn.close()
    if not dest:
        for f in CATEGORY_FOLDERS:
            cand = os.path.join(LIBRARY_DIR, f, name)
            if os.path.exists(cand):
                dest = cand
                break
    if not dest:
        # 库内没有同名：按分类落位（不许落到 library 根目录）
        dest = os.path.join(category_dir(infer_category(
            name, '', '', get_category_rules(db_path))), name)
    conn, _ = get_db(db_path)
    fts_ok = _check_fts(conn)
    try:
        # 先删旧索引
        _remove_doc_from_index(conn, dest, fts_ok)
        conn.commit()
    finally:
        conn.close()
    # 旧文件移入回收站（不可用时兜底物理删除）
    if os.path.exists(dest):
        try:
            try:
                from send2trash import send2trash
                send2trash(dest)
            except Exception:
                os.remove(dest)
        except Exception:
            pass
    try:
        shutil.copy2(src_path, dest)
        status = index_single_file(dest, db_path)
        if callback:
            callback(1, 1, dest, status)
        return dest, status
    except Exception as e:
        _record_error(get_db(db_path)[0], src_path, 'import', e)
        return src_path, f'error: {e}'



def list_library(db_path, include_stats=True):
    """
    列出索引库内所有文件，按修改时间倒序。
    返回 [{path, name, ext, ftype, size, line_count, mtime,
           doc_no, effective_date, status, category}, ...]
    """
    conn, _ = get_db(db_path)
    sql = '''SELECT path, name, ext, ftype, size, line_count, mtime,
                    doc_no, effective_date, status, COALESCE(category,''),
                    indexed_at
             FROM documents ORDER BY mtime DESC'''
    rows = conn.execute(sql).fetchall()
    conn.close()
    items = []
    for r in rows:
        items.append({
            'path': r[0], 'name': r[1], 'ext': r[2], 'ftype': r[3],
            'size': r[4], 'line_count': r[5], 'mtime': r[6],
            'doc_no': r[7] or '', 'effective_date': r[8] or '',
            'status': r[9] or 'active', 'category': r[10] or '',
            'indexed_at': r[11],
        })
    return items


def remove_from_library(db_path, path, delete_file=True):
    """
    从库中删除一个文件。
    delete_file=True  → 磁盘文件先移入回收站，成功后才删索引；
                        回收站不可用/文件被占用时返回 error 且保留索引，
                        绝不物理删除兜底
    delete_file=False → 仅删索引，保留磁盘文件（链接模式常用）
    返回 'ok' / 'not_found' / 'error: ...'
    """
    conn, _ = get_db(db_path)
    fts_ok = _check_fts(conn)
    try:
        row = conn.execute(
            'SELECT id FROM documents WHERE path=?', (path,)).fetchone()
        if not row:
            conn.close()
            return 'not_found'
        if delete_file and os.path.exists(path):
            # 安全顺序：文件先入回收站成功，才允许删索引（失败可重试，不丢索引）
            try:
                from send2trash import send2trash
            except ImportError:
                return 'error: 回收站组件不可用，已跳过（文件与索引均保留）'
            try:
                send2trash(path)
            except Exception:
                return 'error: 该盘不支持回收站或文件被占用，已跳过（文件与索引均保留）'
        _remove_doc_from_index(conn, path, fts_ok)
        conn.commit()
        return 'ok'
    except Exception as e:
        return f'error: {e}'
    finally:
        conn.close()


def rename_in_library(db_path, old_path, new_name):
    """磁盘重命名 + 库内路径同步（不重建索引，lines/chapters 通过 doc_id 关联不受影响）。
    new_name 为新的完整文件名（含扩展名）。
    返回 'ok' / 'not_found' / 'exists' / 'invalid' / 'error: ...'
    """
    new_name = (new_name or '').strip()
    if not new_name or new_name in ('.', '..') or any(
            c in new_name for c in '\\/:*?"<>|') or new_name.startswith('.'):
        return 'invalid'
    d = os.path.dirname(old_path)
    new_path = os.path.join(d, new_name)
    if os.path.normcase(os.path.abspath(new_path)) == \
            os.path.normcase(os.path.abspath(old_path)):
        return 'ok'
    if os.path.exists(new_path):
        return 'exists'
    conn, _ = get_db(db_path)
    try:
        row = conn.execute(
            'SELECT id FROM documents WHERE path=?', (old_path,)).fetchone()
        if not row:
            return 'not_found'
        # 先改磁盘；库更新失败再改回去，保证两者一致
        os.rename(old_path, new_path)
        try:
            with conn:
                conn.execute('UPDATE documents SET path=?, name=? WHERE path=?',
                             (new_path, new_name, old_path))
                conn.execute('UPDATE bookmarks SET path=? WHERE path=?',
                             (new_path, old_path))
                conn.execute(
                    'UPDATE reading_positions SET path=? WHERE path=?',
                    (new_path, old_path))
        except Exception:
            try:
                os.rename(new_path, old_path)
            except OSError:
                pass
            raise
        return 'ok'
    except Exception as e:
        return f'error: {e}'
    finally:
        conn.close()


def library_stats(db_path):
    """返回库统计：{files, total_size, by_type: {ext: count}}"""
    conn, _ = get_db(db_path)
    try:
        n = conn.execute('SELECT COUNT(*) FROM documents').fetchone()[0]
        sz = conn.execute('SELECT COALESCE(SUM(size),0) FROM documents').fetchone()[0]
        by_type = {}
        for ext, cnt in conn.execute(
            'SELECT ext, COUNT(*) FROM documents GROUP BY ext ORDER BY COUNT(*) DESC'
        ).fetchall():
            if ext:
                by_type[ext] = cnt
        return {'files': n, 'total_size': sz, 'by_type': by_type}
    finally:
        conn.close()


def find_duplicates(db_path):
    """
    查重复文件，每组保留 mtime 最新的一个：
    - 内容重复：sha256 相同的不同路径文件
    - 同名旧版本：文件名相同但内容不同（新旧版本）
    一个文件只会出现在一个组里（内容重复优先）。
    返回 [{'keep': item, 'dups': [item...], 'reason': '内容重复'|'同名旧版本'}]
    item = {path, name, ext, mtime, size, line_count}
    """
    conn, _ = get_db(db_path)
    try:
        rows = conn.execute(
            '''SELECT path, name, ext, mtime, size, line_count, sha256
               FROM documents''').fetchall()
        cols = ('path', 'name', 'ext', 'mtime', 'size', 'line_count', 'sha256')
        items = []
        for r in rows:
            d = dict(zip(cols, r))
            # 旧库缺失 hash：现算并回写
            if not d['sha256']:
                d['sha256'] = _file_sha256(d['path'])
                if d['sha256']:
                    conn.execute('UPDATE documents SET sha256=? WHERE path=?',
                                 (d['sha256'], d['path']))
            items.append(d)
        conn.commit()
    finally:
        conn.close()

    used = set()      # 已进组的 path
    groups = []

    # 1) 内容重复（sha256 相同，排除读不到文件的 None）
    by_hash = {}
    for d in items:
        if d['sha256']:
            by_hash.setdefault(d['sha256'], []).append(d)
    for lst in by_hash.values():
        if len(lst) < 2:
            continue
        lst.sort(key=lambda x: x['mtime'] or 0, reverse=True)
        keep = lst[0]
        dups = [d for d in lst[1:] if d['path'] not in used]
        if dups:
            groups.append({'keep': keep, 'dups': dups, 'reason': '内容重复'})
            used.add(keep['path'])
            used.update(d['path'] for d in dups)

    # 2) 同名旧版本（内容不同，文件名相同）
    by_name = {}
    for d in items:
        if d['path'] in used:
            continue
        by_name.setdefault(d['name'], []).append(d)
    for lst in by_name.values():
        if len(lst) < 2:
            continue
        lst.sort(key=lambda x: x['mtime'] or 0, reverse=True)
        keep = lst[0]
        dups = [d for d in lst[1:] if d['path'] not in used]
        if dups:
            groups.append({'keep': keep, 'dups': dups, 'reason': '同名旧版本'})
            used.add(keep['path'])
            used.update(d['path'] for d in dups)

    return groups


# ========== 库整体打包 / 还原（换电脑迁移用）==========

PACKAGE_MANIFEST = 'JINGZAI_PACKAGE.json'
PACKAGE_FORMAT = 'jingzai-library'
PACKAGE_VERSION = 1
# 随包一起带走的数据（相对软件目录）；backup/ 不打包（纯历史备份，徒增体积）
_PACKAGE_DIRS = ('library', 'ocr_cache', 'inbox')
_PACKAGE_FILES = ('index.db', 'config.json')


def _walk_files(root):
    """列出目录下全部文件（相对路径用 / 分隔）"""
    out = []
    if not os.path.isdir(root):
        return out
    for dirpath, dirnames, files in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith('.'))
        for fn in sorted(files):
            ap = os.path.join(dirpath, fn)
            rp = os.path.relpath(ap, root).replace(os.sep, '/')
            out.append((ap, rp))
    return out


def _snapshot_db(db_path, tmp_path):
    """用 sqlite backup API 把正在使用的库热备份到临时文件（自动合并 WAL）"""
    src = sqlite3.connect(db_path, timeout=30)
    dst = sqlite3.connect(tmp_path)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


def package_library(zip_path, db_path=None, app_dir=None,
                    progress=None, stop_event=None, include_exe=False):
    """把整个库（library + index.db + ocr_cache + inbox + config.json）
    打成一个 zip。progress(done, total, name)；返回统计 dict。
    产出物绝不放在被打包目录内（防止自包含）。
    include_exe=True 时把 exe 本身也打入包内（用于分发给他人）。"""
    import tempfile
    app_dir = app_dir or _get_app_dir()
    db_path = db_path or os.path.join(app_dir, 'index.db')
    zip_path = os.path.abspath(zip_path)

    # 1. 收集待打包文件：(绝对路径, 包内相对路径)
    members = []
    for dname in _PACKAGE_DIRS:
        root = os.path.join(app_dir, dname)
        for ap, rp in _walk_files(root):
            members.append((ap, f'{dname}/{rp}'))
    for fn in _PACKAGE_FILES:
        ap = os.path.join(app_dir, fn)
        if os.path.isfile(ap):
            members.append((ap, fn))
    # 把 exe 本身加入包内（用于分发给他人）
    if include_exe:
        import sys as _sys
        exe_ap = os.path.abspath(_sys.executable)
        if os.path.isfile(exe_ap) and \
                os.path.normcase(exe_ap) != os.path.normcase(zip_path):
            members.append((exe_ap, os.path.basename(exe_ap)))
    # 输出文件若落在软件目录内，排除自身
    members = [(ap, rp) for ap, rp in members
               if os.path.normcase(ap) != os.path.normcase(zip_path)]
    if not any(rp == 'index.db' for _, rp in members):
        raise RuntimeError('未找到 index.db，库为空，无需打包')
    if not any(rp.startswith('library/') for _, rp in members):
        raise RuntimeError('未找到 library 文件夹，库为空，无需打包')

    total = len(members) + 2   # +快照 +校验
    doc_count = 0
    try:
        con = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True, timeout=10)
        doc_count = con.execute('SELECT COUNT(*) FROM documents').fetchone()[0]
        con.close()
    except Exception:
        pass
    total_size = sum(os.path.getsize(ap) for ap, _ in members
                     if os.path.exists(ap))

    # 2. 热备份 index.db 到临时文件（避免直接复制 WAL 状态）
    tmp_db = None
    if progress:
        progress(0, total, '正在整理索引库…')
    try:
        fd, tmp_db = tempfile.mkstemp(prefix='jz_pkg_', suffix='.db')
        os.close(fd)
        _snapshot_db(db_path, tmp_db)
        members = [(tmp_db, 'index.db') if rp == 'index.db' else (ap, rp)
                   for ap, rp in members]

        manifest = {
            'format': PACKAGE_FORMAT,
            'version': PACKAGE_VERSION,
            'created_at': time.strftime('%Y-%m-%d %H:%M:%S'),
            'app_dir': app_dir,
            'library_dir': os.path.join(app_dir, 'library'),
            'doc_count': doc_count,
            'file_count': total - 2,
            'total_size': total_size,
        }

        # 3. 写 zip（先清单，再文件；PDF 等已压缩数据用快速 deflate）
        done = 1
        skipped = []
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED,
                             compresslevel=3) as zf:
            zf.writestr(
                PACKAGE_MANIFEST,
                json.dumps(manifest, ensure_ascii=False, indent=1))
            for ap, rp in members:
                if stop_event is not None and stop_event.is_set():
                    raise RuntimeError('用户取消')
                if progress:
                    progress(done, total, os.path.basename(rp))
                try:
                    zf.write(ap, rp)
                except OSError:
                    # 被占用/无权限的个别文件跳过（如正在写入的上传临时文件）
                    skipped.append(rp)
                done += 1
        if skipped:
            manifest['skipped'] = skipped
            with zipfile.ZipFile(zip_path, 'a', zipfile.ZIP_DEFLATED,
                                 compresslevel=3) as zf:
                # 补写跳过清单，警告还原方
                zf.writestr('SKIPPED.txt',
                            '以下文件打包时被占用，未包含在包内：\n'
                            + '\n'.join(skipped))

        # 4. 完整性校验（逐个 CRC）
        if progress:
            progress(total - 1, total, '正在校验压缩包…')
        with zipfile.ZipFile(zip_path) as zf:
            bad = zf.testzip()
            if bad is not None:
                raise RuntimeError(f'压缩包校验失败：{bad}')
            names = set(zf.namelist())
        if 'index.db' not in names or PACKAGE_MANIFEST not in names:
            raise RuntimeError('压缩包内容不完整')
        if progress:
            progress(total, total, '完成')

        return {'zip_path': zip_path,
                'zip_size': os.path.getsize(zip_path),
                'doc_count': doc_count,
                'file_count': total - 2,
                'total_size': total_size,
                'skipped': skipped}
    finally:
        if tmp_db and os.path.exists(tmp_db):
            try:
                os.remove(tmp_db)
            except OSError:
                pass


def inspect_package(zip_path):
    """读压缩包清单；非镜仔库压缩包抛 RuntimeError。
    支持 zip / 7z / rar 三种格式。"""
    if not os.path.isfile(zip_path):
        raise RuntimeError('压缩包不存在')
    names = _archive_list_names(zip_path)
    if PACKAGE_MANIFEST not in names:
        raise RuntimeError('这不是镜仔搜索的库压缩包（缺少清单文件）')
    data = _archive_read_member(zip_path, PACKAGE_MANIFEST)
    try:
        mf = json.loads(data.decode('utf-8'))
    except Exception:
        raise RuntimeError('清单文件损坏')
    if mf.get('format') != PACKAGE_FORMAT:
        raise RuntimeError('这不是镜仔搜索的库压缩包')
    return mf


def _archive_list_names(path):
    """统一接口：列出压缩包内全部成员名（含目录项，以 / 结尾）"""
    ext = os.path.splitext(path)[1].lower()
    if ext == '.zip':
        with zipfile.ZipFile(path) as zf:
            return zf.namelist()
    if ext == '.7z':
        import py7zr
        with py7zr.SevenZipFile(path, mode='r') as z:
            return [i.filename for i in z.list()]
    if ext == '.rar':
        import rarfile
        rf = rarfile.RarFile(path)
        return [i.filename for i in rf.infolist()]
    raise RuntimeError(f'不支持的压缩格式：{ext}（请用 zip / 7z / rar）')


def _archive_read_member(path, member_name):
    """统一接口：读压缩包内单个成员的二进制内容"""
    ext = os.path.splitext(path)[1].lower()
    if ext == '.zip':
        with zipfile.ZipFile(path) as zf:
            return zf.read(member_name)
    if ext == '.7z':
        import py7zr
        with py7zr.SevenZipFile(path, mode='r') as z:
            # py7zr 的 read 返回 {name: BytesIO}，但部分版本不支持
            # 用 extract 到临时目录再读，兼容所有版本
            tmp = tempfile.mkdtemp(prefix='jz_7z_')
            try:
                z.extract(targets=[member_name], path=tmp)
                with open(os.path.join(tmp, member_name), 'rb') as f:
                    return f.read()
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
    if ext == '.rar':
        import rarfile
        rf = rarfile.RarFile(path)
        return rf.read(member_name)
    raise RuntimeError(f'不支持的压缩格式：{ext}')


def _archive_extract_all(path, dest_dir, progress=None, stop_event=None):
    """统一接口：安全解压全部成员到 dest_dir（防路径穿越）"""
    ext = os.path.splitext(path)[1].lower()
    if ext == '.zip':
        with zipfile.ZipFile(path) as zf:
            names = [n for n in zf.namelist()
                     if not n.startswith('__MACOSX/')]
            total = len(names)
            for i, name in enumerate(names, 1):
                if stop_event is not None and stop_event.is_set():
                    raise RuntimeError('用户取消')
                target = os.path.abspath(os.path.join(dest_dir, name))
                if os.path.normcase(
                        os.path.commonpath([dest_dir, target])) \
                        != os.path.normcase(dest_dir):
                    raise RuntimeError(f'压缩包含非法路径：{name}')
                if name.endswith('/'):
                    os.makedirs(target, exist_ok=True)
                    continue
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with zf.open(name) as src, open(target, 'wb') as dst:
                    shutil.copyfileobj(src, dst, 1024 * 1024)
                if progress and i % 5 == 0:
                    progress(i, total, os.path.basename(name))
    elif ext == '.7z':
        import py7zr
        with py7zr.SevenZipFile(path, mode='r') as z:
            z.extractall(dest_dir)
        if progress:
            progress(1, 1, '解压完成')
    elif ext == '.rar':
        import rarfile
        rf = rarfile.RarFile(path)
        infos = [i for i in rf.infolist() if not i.isdir()]
        total = len(infos)
        for i, info in enumerate(infos, 1):
            if stop_event is not None and stop_event.is_set():
                raise RuntimeError('用户取消')
            target = os.path.abspath(
                os.path.join(dest_dir, info.filename))
            if os.path.normcase(
                    os.path.commonpath([dest_dir, target])) \
                    != os.path.normcase(dest_dir):
                raise RuntimeError(f'压缩包含非法路径：{info.filename}')
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with rf.open(info) as src, open(target, 'wb') as dst:
                shutil.copyfileobj(src, dst, 1024 * 1024)
            if progress and i % 5 == 0:
                progress(i, total, os.path.basename(info.filename))
    else:
        raise RuntimeError(f'不支持的压缩格式：{ext}（请用 zip / 7z / rar）')


def _rewrite_db_paths(db_path, old_app_dir, new_app_dir):
    """还原后把四张表里的绝对路径从旧机器前缀改写到本机。
    大小写不敏感匹配（Windows 盘符大小写可能不同）。"""
    conn = sqlite3.connect(db_path, timeout=30)
    try:
        rewrites = 0
        pairs = [(os.path.join(old_app_dir, 'library'),
                  os.path.join(new_app_dir, 'library')),
                 (old_app_dir, new_app_dir)]
        for tbl, col in (('documents', 'path'), ('errors', 'path'),
                         ('bookmarks', 'path'), ('reading_positions', 'path')):
            try:
                rows = conn.execute(f'SELECT rowid, {col} FROM {tbl}').fetchall()
            except sqlite3.OperationalError:
                continue
            for rowid, p in rows:
                if not p:
                    continue
                low = os.path.normcase(p)
                for old, new in pairs:
                    if low.startswith(os.path.normcase(old)):
                        np = new + p[len(old):]
                        conn.execute(
                            f'UPDATE {tbl} SET {col}=? WHERE rowid=?',
                            (np, rowid))
                        rewrites += 1
                        break
        conn.commit()
        return rewrites
    finally:
        conn.close()


def restore_library(zip_path, app_dir=None, progress=None, stop_event=None):
    """【第一阶段·进程内】解压到软件目录旁的暂存目录、改写绝对路径、校验。
    不动任何在用数据。返回 staging 信息，交给 finish_restore 在重启时切换。
    放弃还原时调 discard_restore(staging) 清理。"""
    app_dir = app_dir or _get_app_dir()
    zip_path = os.path.abspath(zip_path)
    mf = inspect_package(zip_path)
    ts = time.strftime('%Y%m%d_%H%M%S')
    staging = os.path.join(app_dir, f'_restore_staging_{ts}')
    os.makedirs(staging, exist_ok=True)

    try:
        _archive_extract_all(zip_path, staging, progress=progress,
                              stop_event=stop_event)

        new_db = os.path.join(staging, 'index.db')
        if not os.path.isfile(new_db) or not os.path.isdir(
                os.path.join(staging, 'library')):
            raise RuntimeError('压缩包缺少 index.db 或 library 文件夹')

        # 在暂存库上改写绝对路径到本机（此时主程序没连这个文件，安全）
        old_app = mf.get('app_dir') or app_dir
        rewritten = _rewrite_db_paths(new_db, old_app, app_dir)

        # 校验暂存库可读
        con = sqlite3.connect(f'file:{new_db}?mode=ro', uri=True, timeout=10)
        n_docs = con.execute('SELECT COUNT(*) FROM documents').fetchone()[0]
        con.close()
        if progress:
            progress(n_docs, n_docs, '准备完成，等待重启切换')

        return {'staging': staging,
                'doc_count': n_docs,
                'rewritten_paths': rewritten,
                'created_at': mf.get('created_at', ''),
                'manifest': mf}
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def discard_restore(staging):
    """放弃还原：删除暂存目录"""
    import shutil
    if staging and os.path.basename(staging).startswith('_restore_staging_'):
        shutil.rmtree(staging, ignore_errors=True)


def finish_restore_bat(staging, app_dir=None, wait_pid=None):
    """【第二阶段】生成重启切换用的批处理：等本进程退出 →
    旧数据挪进 backup/restore_时间戳/ → 暂存数据瞬时改名就位 → 重启软件。
    返回 bat 路径。全部为同盘目录改名，秒级完成。"""
    app_dir = app_dir or _get_app_dir()
    staging = os.path.abspath(staging)
    ts = time.strftime('%Y%m%d_%H%M%S')
    keep_dir = os.path.join(app_dir, 'backup', f'restore_{ts}')
    exe = os.path.join(app_dir, os.path.basename(sys.executable)) \
        if getattr(sys, 'frozen', False) else ''
    bat = os.path.join(app_dir, f'_finish_restore_{ts}.bat')

    def q(p):
        return '"' + os.path.normpath(p) + '"'

    L = ['@echo off']
    if wait_pid:
        L += [f':wait',
              f'tasklist /FI "PID eq {wait_pid}" | find "{wait_pid}" >nul',
              'if not errorlevel 1 ( timeout /t 1 /nobreak >nul & goto wait )']
    L.append(f'md {q(keep_dir)} 2>nul')
    # 旧 DB 让位（WAL 临时文件直接删，正常退出时本就已合并）
    L.append(f'if exist {q(os.path.join(app_dir, "index.db"))} '
             f'move /Y {q(os.path.join(app_dir, "index.db"))} '
             f'{q(os.path.join(keep_dir, "index.db"))} >nul')
    for suffix in ('-wal', '-shm'):
        L.append(f'if exist {q(os.path.join(app_dir, "index.db" + suffix))} '
                 f'del /F /Q {q(os.path.join(app_dir, "index.db" + suffix))} >nul 2>nul')
    # 旧目录 → backup；暂存目录 → 软件目录（同盘改名，瞬时）
    for name in ('library', 'ocr_cache', 'inbox'):
        old_cur = os.path.join(app_dir, name)
        old_keep = os.path.join(keep_dir, name)
        new_cur = os.path.join(staging, name)
        L.append(f'if exist {q(old_cur)} move /Y {q(old_cur)} '
                 f'{q(old_keep)} >nul 2>nul')
        L.append(f'if exist {q(new_cur)} move /Y {q(new_cur)} '
                 f'{q(old_cur)} >nul 2>nul')
    # 暂存 index.db 就位（config.json 不覆盖本机设置，随暂存目录清掉）
    L.append(f'if exist {q(os.path.join(staging, "index.db"))} '
             f'move /Y {q(os.path.join(staging, "index.db"))} '
             f'{q(os.path.join(app_dir, "index.db"))} >nul')
    # 暂存 config 不覆盖本机设置，连同暂存目录一起清掉
    L.append(f'rd /s /q {q(staging)} 2>nul')
    if exe:
        L.append(f'start "" {q(exe)}')
    L += [f'echo 库已还原完成，旧数据保存在 {keep_dir}',
          '(goto) 2>nul & del "%~f0"']
    with open(bat, 'w', encoding='gbk', newline='\r\n') as f:
        f.write('\r\n'.join(L))
    return bat


# ========== 文件元数据（文档号 / 生效日期 / 状态）==========

def update_metadata(db_path, path, doc_no=None, effective_date=None,
                    status=None, category=None):
    """更新文件元数据。None 表示不修改该字段。
    status 取值：'active'（有效）/ 'replaced'（被替代）/ 'void'（废止）
    category：分类名，'' = 未分类
    返回 True 或 'error: ...'"""
    conn, _ = get_db(db_path)
    try:
        sets, params = [], []
        for col, val in [('doc_no', doc_no), ('effective_date', effective_date),
                         ('status', status), ('category', category)]:
            if val is not None:
                sets.append(f'{col}=?')
                params.append(val)
        if sets:
            params.append(path)
            conn.execute(
                f'UPDATE documents SET {", ".join(sets)} WHERE path=?', params)
            # 分类有变更时，物理文件同步搬到对应分类文件夹
            if category is not None:
                relocate_to_category(conn, path, category or '')
            conn.commit()
        return True
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        return f'error: {e}'
    finally:
        conn.close()


def get_metadata(db_path, path):
    """返回 {doc_no, effective_date, status, category}，无记录返回默认值"""
    conn = ro_conn(db_path)
    r = conn.execute(
        "SELECT doc_no, effective_date, status, COALESCE(category,''), "
        'COALESCE(line_count,0) '
        'FROM documents WHERE path=?', (path,)).fetchone()
    if r:
        return {'doc_no': r[0] or '', 'effective_date': r[1] or '',
                'status': r[2] or 'active', 'category': r[3] or '',
                'line_count': r[4]}
    return {'doc_no': '', 'effective_date': '', 'status': 'active',
            'category': '', 'line_count': 0}


def get_statuses(db_path, paths):
    """批量取文件状态：{path: status}，未记录默认 'active'"""
    if not paths:
        return {}
    conn, _ = get_db(db_path)
    try:
        out = {}
        placeholders = ','.join('?' for _ in paths)
        rows = conn.execute(
            f'SELECT path, COALESCE(status,"active") FROM documents '
            f'WHERE path IN ({placeholders})', tuple(paths)).fetchall()
        for p, s in rows:
            out[p] = s
        # 未命中的补默认值
        for p in paths:
            out.setdefault(p, 'active')
        return out
    finally:
        conn.close()


# ========== 书签 / 阅读位置 ==========

def add_bookmark(db_path, path, line_no, title=''):
    """收藏条款；已存在则忽略。返回 True=新增"""
    conn, _ = get_db(db_path)
    try:
        conn.execute(
            'INSERT OR IGNORE INTO bookmarks(path, line_no, title, created_at) '
            'VALUES(?,?,?,?)',
            (path, line_no, title, time.time()))
        conn.commit()
        cur = conn.execute(
            'SELECT changes()').fetchone()
        return bool(cur[0])
    finally:
        conn.close()


def remove_bookmark(db_path, bookmark_id=None, path=None, line_no=None):
    """按 id 或 (path,line_no) 删书签"""
    conn, _ = get_db(db_path)
    try:
        if bookmark_id is not None:
            conn.execute('DELETE FROM bookmarks WHERE id=?', (bookmark_id,))
        elif path is not None and line_no is not None:
            conn.execute('DELETE FROM bookmarks WHERE path=? AND line_no=?',
                         (path, line_no))
        conn.commit()
    finally:
        conn.close()


def restore_bookmark(db_path, row):
    """撤销取消收藏；恢复原备注与收藏时间，不覆盖后来重新创建的收藏。"""
    conn,_=get_db(db_path)
    try:
        cursor=conn.execute('INSERT OR IGNORE INTO bookmarks(path,line_no,title,note,created_at) VALUES(?,?,?,?,?)',
            (row['path'],row['line_no'],row.get('title') or '',row.get('note') or '',row['created_at']))
        conn.commit()
        return cursor.rowcount>0
    finally:
        conn.close()


def list_bookmarks(db_path):
    """全部书签（带文件名），按收藏时间倒序"""
    conn, _ = get_db(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            '''SELECT b.id, b.path, b.line_no, b.title, b.note, b.created_at,
                      d.name
               FROM bookmarks b
               LEFT JOIN documents d ON d.path = b.path
               ORDER BY b.created_at DESC''').fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def set_bookmark_note(db_path, bookmark_id, note):
    """更新书签备注"""
    conn, _ = get_db(db_path)
    try:
        conn.execute('UPDATE bookmarks SET note=? WHERE id=?',
                     (note or '', bookmark_id))
        conn.commit()
    finally:
        conn.close()


def find_missing_documents(db_path):
    """索引体检：返回磁盘上已不存在的文档（失联条目）"""
    conn, _ = get_db(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            'SELECT id, path, name, status, line_count, indexed_at '
            'FROM documents ORDER BY name').fetchall()
        return [dict(r) for r in rows
                if not os.path.exists(r['path'])]
    finally:
        conn.close()


def delete_documents(db_path, doc_ids):
    """按 id 批量删除文档（lines/chapters/QA 块级联删除）。返回删除数"""
    if not doc_ids:
        return 0
    conn, _ = get_db(db_path)
    try:
        conn.execute('PRAGMA foreign_keys=ON')
        qmarks = ','.join('?' * len(doc_ids))
        cur = conn.execute(
            f'DELETE FROM documents WHERE id IN ({qmarks})',
            list(doc_ids))
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def is_bookmarked(db_path, path, line_no):
    conn, _ = get_db(db_path)
    try:
        r = conn.execute(
            'SELECT id FROM bookmarks WHERE path=? AND line_no=?',
            (path, line_no)).fetchone()
        return r[0] if r else None
    finally:
        conn.close()


def save_reading_position(db_path, path, line_no):
    """记录/更新文件最后阅读行"""
    conn, _ = get_db(db_path)
    try:
        conn.execute(
            'INSERT INTO reading_positions(path, line_no, updated_at) '
            'VALUES(?,?,?) ON CONFLICT(path) DO UPDATE SET '
            'line_no=excluded.line_no, updated_at=excluded.updated_at',
            (path, line_no, time.time()))
        conn.commit()
    finally:
        conn.close()


def get_reading_position(db_path, path):
    """返回上次阅读行号，无记录返回 None"""
    conn, _ = get_db(db_path)
    try:
        r = conn.execute(
            'SELECT line_no FROM reading_positions WHERE path=?',
            (path,)).fetchone()
        return r[0] if r else None
    finally:
        conn.close()
