# -*- coding: utf-8 -*-
"""通用目录通道：流式 scandir 发现 + 分批 SQLite 写入。

- 不把整盘路径收集到一个列表：显式栈 DFS，每弹出一个目录才 scandir 它。
- 元数据发现不打开文件内容、不做全文件哈希、不调文档解析器/OCR。
- 支持取消 / 进度 / 错误隔离 / 普通用户权限 / 重启恢复（幂等重扫）。
- 重解析点（符号链接 / 联接点）不递归，避免循环；按 'link' 记录。
- 同一对象的多条硬链接路径各自保留（object_id 相同、entry 不同）。
"""
from __future__ import annotations

import os
import time
import threading

from .catalog import Catalog, normcase, casefold_name, signed64
from .errors import Cancelled
from .filters import should_ignore_dir_name, should_ignore_file_name, io_path
from .model import ScanReport, SourceInfo


SCAN_BACKOFF = threading.Event()


def _is_reparse(entry) -> bool:
    """scandir 条目是否为重解析点（符号链接或目录联接点）。"""
    try:
        if entry.is_symlink():
            return True
    except OSError:
        pass
    is_junction = getattr(entry, 'is_junction', None)
    if is_junction is not None:
        try:
            return bool(is_junction())
        except OSError:
            return False
    return False


def _stat_safe(entry):
    try:
        return entry.stat(follow_symlinks=False)
    except OSError:
        return None


def _entry_row(source_id, path, name, kind, st, is_dir, generation):
    ext = ''
    if kind == 'file':
        ext = os.path.splitext(name)[1].lstrip('.').lower()
    return {
        'source_id': source_id,
        'parent_path': os.path.dirname(path.rstrip('\\/')),
        'name': name,
        'path': path,
        'match_name': casefold_name(name),
        'match_path': normcase(path),
        'ext': ext,
        'kind': kind,
        'size': st.st_size if st is not None else None,
        'mtime_ns': st.st_mtime_ns if st is not None else None,
        'volume_serial': signed64(getattr(st, 'st_dev', None)) if st is not None else None,
        'file_id': (getattr(st, 'st_ino', None) or None) if st is not None else None,
        'is_dir': 1 if is_dir else 0,
        'scan_generation': generation,
    }


def scan_source(catalog: Catalog, source: SourceInfo, *,
                progress=None, cancel=None, batch_size=1000) -> ScanReport:
    """流式元数据扫描。任何未完成区域都保留旧条目，不推断批量删除。"""
    started = time.perf_counter()
    root = os.path.abspath(source.root)
    batch_size = max(1, int(batch_size))
    generation = catalog.next_generation()
    event_boundary = catalog.event_boundary(source.source_id)
    found = dirs = failed = 0
    stopped = False
    pending = []
    error = None
    offline = False

    def flush():
        if pending:
            catalog.upsert_entries(pending)
            pending.clear()
            time.sleep(0.03 if SCAN_BACKOFF.is_set() else 0.008)

    def report():
        if progress:
            progress({'source': source.source_id, 'found': found, 'dirs': dirs, 'failed': failed})

    def cancelled():
        return cancel is not None and cancel()

    def failure(path, stage, exc):
        nonlocal failed
        failed += 1
        catalog.record_failure(source.source_id, path, stage, str(exc))

    def put(row):
        nonlocal found, dirs
        pending.append(row)
        found += 1
        dirs += row['kind'] == 'dir'
        if len(pending) >= batch_size:
            flush()
        if found % 100 == 0:
            report()

    catalog.set_source_state(source.source_id, 'scanning', provider='generic', generation=generation)
    try:
        check_root = os.stat(io_path(root))
        if not os.path.isdir(io_path(root)):
            raise NotADirectoryError(root)
        catalog.set_source_state(source.source_id, None, online=True)
        name = os.path.basename(root.rstrip('\\/')) or root.rstrip('\\/')
        put(_entry_row(source.source_id, root, name, 'dir', check_root, True, generation))
        stack = [(root, 0)]
        visited = {(check_root.st_dev, check_root.st_ino)}
        while stack:
            if cancelled():
                stopped = True
                break
            cur, depth = stack.pop()
            try:
                with os.scandir(io_path(cur)) as entries:
                    for entry in entries:
                        if cancelled():
                            stopped = True
                            break
                        path, name = os.path.join(cur,entry.name), entry.name
                        if catalog.is_internal_path(path):
                            continue
                        try:
                            native_path = io_path(path)
                            # Windows DirEntry.stat 可将 st_ino/st_dev 返回为 0；
                            # 不可用它建立文件身份或判断目录循环。
                            st = os.lstat(native_path) if os.name == 'nt' else entry.stat(follow_symlinks=False)
                            is_dir = entry.is_dir(follow_symlinks=False)
                            if getattr(st,'st_file_attributes',0) & 0x400 or _is_reparse(entry):
                                put(_entry_row(source.source_id, path, name, 'link', st, False, generation))
                                continue
                            if is_dir:
                                if should_ignore_dir_name(name, source.kind == 'volume' and depth == 0):
                                    continue
                                put(_entry_row(source.source_id, path, name, 'dir', st, True, generation))
                                key = st.st_dev, st.st_ino
                                if st.st_ino and key in visited:
                                    failure(path, 'cycle', 'directory identity already visited')
                                else:
                                    if st.st_ino:
                                        visited.add(key)
                                    stack.append((path, depth + 1))
                            elif not should_ignore_file_name(name):
                                put(_entry_row(source.source_id, path, name, 'file', st, False, generation))
                        except OSError as exc:
                            failure(path, 'entry', exc)
            except OSError as exc:
                failure(cur, 'scandir', exc)
            if stopped:
                break
        stopped = stopped or cancelled()
    except (FileNotFoundError, NotADirectoryError) as exc:
        offline = True
        failure(root, 'root', exc)
    except OSError as exc:
        failure(root, 'root', exc)
    except Exception as exc:
        error = exc
        failure(root, 'scan', exc)
    finally:
        try:
            flush()
        except Exception as exc:
            failed += 1
            error = exc
        complete = not stopped and not failed and error is None
        if complete:
            catalog.prune_stale(source.source_id, generation, reconciled_event_id=event_boundary)
        state = 'offline' if offline else 'complete' if complete else 'partial'
        reason = None if complete else ('source unavailable' if offline else
            'scan cancelled' if stopped else 'scan incomplete; old records retained')
        catalog.set_source_state(source.source_id, state, online=not offline,
            discovered_count=catalog.count_entries(source.source_id), failed_count=failed,
            last_scan_at=time.time(), generation=generation, needs_rescan=not complete,
            rescan_reason=reason)
    report()
    if error is not None:
        raise error
    return ScanReport(source_id=source.source_id, provider='generic', state=state,
        found=found, dirs=dirs, failed=failed, elapsed_ms=(time.perf_counter()-started)*1000,
        stopped=stopped, generation=generation)
