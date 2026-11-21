# -*- coding: utf-8 -*-
"""
镜仔搜索 · 文件变更监听模块
Copyright (c) 2026 SleepDragon
SPDX-License-Identifier: MIT
"""

"""
P1-11: 文件变更监听（watchdog）+ 增量更新

设计要点：
- watchdog 的 on_modified/on_created/on_deleted/on_moved 触发后只入队，
  实际索引由单独 worker 线程串行执行（避免并发写 SQLite + 防抖）
- 短时间内同一文件多次变更合并：入队时去重，worker 取最后版本
- IGNORE_DIRS 中的目录事件忽略
"""
import os
import time
import sqlite3
import threading
import queue
import traceback
from collections import deque

from indexer import index_single_file, IGNORE_DIRS

# 同一路径两次入队的最小间隔（秒），间隔内重复事件丢弃
_DEBOUNCE_SEC = 0.5
# 本模块所在目录（应用 config.json 的候选位置之一）
_APP_DIR = os.path.dirname(os.path.abspath(__file__))


class FileWatcher:
    """文件监听 + 增量索引"""

    def __init__(self, root, db_path, on_event=None):
        """
        root:     监听根目录
        db_path:  索引库路径
        on_event: callback(status, path)，事件处理完成时调用（用于刷新 UI）
        """
        self.root = root
        self.db_path = db_path
        self.on_event = on_event
        self._queue = queue.Queue()
        self._stop_event = threading.Event()
        self._worker = None
        self._observer = None
        self._lock = threading.Lock()
        # 防抖：记录最近入队的 (路径key, 时间戳)，0.5 秒内重复事件合并
        self._recent = deque(maxlen=200)

    def start(self):
        """启动 watcher（必须先扫描建索引，监听只处理后续变更）"""
        if self._worker and self._worker.is_alive():
            return
        from watchdog.observers import Observer

        self._stop_event.clear()
        self._worker = threading.Thread(target=self._worker_loop,
                                        daemon=True)
        self._worker.start()
        self._observer = Observer()
        self._observer.schedule(
            _Handler(self._enqueue, self._enqueue_dir), self.root,
            recursive=True)
        self._observer.start()

    def stop(self):
        """停止 watcher"""
        self._stop_event.set()
        if self._observer:
            self._observer.stop()
            self._observer.join(timeout=2)
            self._observer = None
        # 唤醒 worker 退出
        self._queue.put(None)
        if self._worker:
            self._worker.join(timeout=2)
            self._worker = None

    def is_running(self):
        """是否在运行"""
        return (self._worker is not None and self._worker.is_alive()
                and self._observer is not None and self._observer.is_alive())

    def _enqueue(self, path):
        """入队待处理的单个文件（带 0.5 秒防抖）"""
        if self._should_ignore(path):
            return
        self._debounced_put(path, path)

    def _enqueue_dir(self, path):
        """入队目录级事件（目录被删除/移动时无法逐文件精确定位）"""
        if self._should_ignore(path):
            return
        # key 与队列元素同为 ('dir', path)，worker 据此与文件事件分流
        self._debounced_put(('dir', path), ('dir', path))

    def _should_ignore(self, path):
        """该路径事件是否应整体忽略（黑名单目录 / 索引库伴随文件 / 应用配置）"""
        # 跳过 IGNORE_DIRS 中的事件
        lower_path = path.lower()
        for ig in IGNORE_DIRS:
            if '\\' + ig + '\\' in lower_path or '/' + ig + '/' in lower_path:
                return True
            # 路径以黑名单目录开头
            if lower_path.endswith('\\' + ig) or lower_path.endswith('/' + ig):
                return True
        # 跳过索引库本体及 SQLite 伴随文件（-wal / -shm / -journal）
        try:
            abs_path = os.path.abspath(path)
            db_abs = os.path.abspath(self.db_path)
            if abs_path in (db_abs, db_abs + '-wal', db_abs + '-shm',
                            db_abs + '-journal'):
                return True
        except Exception:
            pass
        # 只跳过应用自身的 config.json（与本模块或索引库同目录），
        # 用户资料目录中恰好同名的文件仍允许入库，避免过滤过宽
        if os.path.basename(path) == 'config.json':
            try:
                abs_path = os.path.abspath(path)
                for cfg_dir in (_APP_DIR,
                                os.path.dirname(
                                    os.path.abspath(self.db_path))):
                    if abs_path == os.path.join(cfg_dir, 'config.json'):
                        return True
            except Exception:
                pass
        return False

    def _debounced_put(self, key, item):
        """按 key 做防抖入队：同 key 距上次入队不足 0.5 秒则丢弃。
        时间戳在入队时记录、worker 处理完成后再刷新，因此处理期间
        （超过 0.5 秒）发生的修改不会被长期占位，仍会再入队一次。"""
        with self._lock:
            now = time.monotonic()
            old_ts = None
            for k, ts in self._recent:
                if k == key:
                    old_ts = ts
                    break
            if old_ts is not None:
                if now - old_ts < _DEBOUNCE_SEC:
                    return
                self._recent.remove((key, old_ts))
            self._recent.append((key, now))
        self._queue.put(item)

    def _mark_done(self, key):
        """处理完成后刷新该 key 的时间戳（处理前不长期占位）"""
        with self._lock:
            for k, ts in list(self._recent):
                if k == key:
                    self._recent.remove((k, ts))
                    break
            self._recent.append((key, time.monotonic()))

    def _worker_loop(self):
        """后台 worker：串行处理队列，串行写 SQLite"""
        while not self._stop_event.is_set():
            item = self._queue.get()
            if item is None:
                break
            # 目录事件与文件事件分流
            if isinstance(item, tuple) and item and item[0] == 'dir':
                key, path = item, item[1]
                try:
                    self._handle_dir(path)
                except Exception:
                    traceback.print_exc()
                self._mark_done(key)
                continue
            path = item
            try:
                status = index_single_file(path, self.db_path)
                if self.on_event:
                    try:
                        self.on_event(status, path)
                    except Exception:
                        pass
            except Exception:
                # 不再静默：完整异常堆栈打印到 stderr，便于排查入库失败
                traceback.print_exc()
            # 处理完成后再更新防抖时间戳（处理期间不占位）
            self._mark_done(path)

    def _handle_dir(self, path):
        """目录被删除/移动后的增量补扫：
        - 先以 'dir' 事件回调通知主程序；
        - 目录仍存在（移动/改名后的新位置）：遍历其下文件逐个入队；
        - 目录已删除：从索引库查出其下残留记录逐个入队，按删除处理。"""
        if self.on_event:
            try:
                self.on_event('dir', path)
            except Exception:
                pass
        if os.path.isdir(path):
            for dirpath, dirnames, files in os.walk(path):
                # 与 indexer 保持一致：剪枝黑名单目录
                dirnames[:] = [d for d in dirnames
                               if d.lower() not in IGNORE_DIRS]
                for fn in files:
                    self._enqueue(os.path.join(dirpath, fn))
            return
        # 目录已不存在：查出库内仍挂在该目录下的文件，逐个走删除
        try:
            conn = sqlite3.connect(self.db_path)
            try:
                rows = conn.execute(
                    'SELECT path FROM documents').fetchall()
            finally:
                conn.close()
        except Exception:
            traceback.print_exc()
            return
        abs_path = os.path.abspath(path)
        prefix = abs_path.rstrip('\\/') + os.sep
        for (p,) in rows:
            try:
                ap = os.path.abspath(p)
            except Exception:
                continue
            if ap == abs_path or ap.startswith(prefix):
                self._enqueue(p)


class _Handler:
    """watchdog 事件处理器，统一入队（用 duck typing 避免 import 死循环）"""

    def __init__(self, enqueue, enqueue_dir):
        self._enqueue = enqueue
        # 目录级事件入口（deleted/moved 目录无法逐文件定位）
        self._enqueue_dir = enqueue_dir

    # FileSystemEventHandler 子类必须实现 dispatch（watchdog 框架约定）
    def dispatch(self, event):
        """统一入口，按事件类型分发"""
        if getattr(event, 'is_directory', False):
            # 目录事件不再全忽略：删除/移动时触发目录级增量补扫
            self.on_directory(event)
            return
        evt_type = event.event_type
        if evt_type == 'moved':
            self.on_moved(event)
        elif evt_type == 'created':
            self.on_created(event)
        elif evt_type == 'deleted':
            self.on_deleted(event)
        elif evt_type == 'modified':
            self.on_modified(event)

    def _push(self, src_path):
        try:
            self._enqueue(src_path)
        except Exception:
            pass

    def _push_dir(self, src_path):
        try:
            self._enqueue_dir(src_path)
        except Exception:
            pass

    def on_directory(self, evt):
        """目录删除/移动：旧位置按删除补扫，新位置按存在目录遍历补扫"""
        if evt.event_type == 'deleted':
            self._push_dir(evt.src_path)
        elif evt.event_type == 'moved':
            self._push_dir(evt.src_path)
            self._push_dir(evt.dest_path)

    def on_created(self, evt):
        if not evt.is_directory:
            self._push(evt.src_path)

    def on_modified(self, evt):
        if not evt.is_directory:
            self._push(evt.src_path)

    def on_deleted(self, evt):
        if not evt.is_directory:
            self._push(evt.src_path)

    def on_moved(self, evt):
        if not evt.is_directory:
            # 移动 = 旧路径删除 + 新路径创建
            self._push(evt.src_path)
            self._push(evt.dest_path)
