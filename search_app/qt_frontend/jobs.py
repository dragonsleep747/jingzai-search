"""后台任务与主线程之间只通过队列信号传递数据。"""
from __future__ import annotations

from dataclasses import dataclass, field
import threading
import time
import traceback
import uuid
import logging
import os

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Qt, Signal, Slot


class Signals(QObject):
    result = Signal(str, object)
    error = Signal(str, str)
    progress = Signal(str, object)
    finished = Signal(str)


class Work(QRunnable):
    def __init__(self, key, fn, stop):
        super().__init__()
        self.key, self.fn, self.stop = key, fn, stop
        self.signals = Signals()

    def run(self):
        pending = [None]
        last = [0.0]
        def report(value):
            # 大批文件预筛时只保留最新进度，避免数万条信号淹没界面线程。
            if isinstance(value, dict) and ('text' in value or 'sources' in value):
                self.signals.progress.emit(self.key, value)
                return
            pending[0] = value
            now = time.monotonic()
            if now - last[0] >= .05:
                self.signals.progress.emit(self.key, value)
                pending[0] = None
                last[0] = now
        try:
            result = self.fn(report, self.stop)
            if pending[0] is not None:
                self.signals.progress.emit(self.key, pending[0])
            self.signals.result.emit(self.key, result)
        except Exception:
            self.signals.error.emit(self.key, traceback.format_exc())
        finally:
            # Qt pool threads outlive individual jobs. Legacy read connections are
            # thread-local; keeping them across jobs retains file handles through
            # closing/restoring a library. Close them in their owning worker.
            import sys
            for module_name,local_name in (('indexer','_ro_tls'),('searcher','_conn_tls')):
                module=sys.modules.get(module_name)
                local=getattr(module,local_name,None)
                connections=getattr(local,'conns',{})
                for conn in list(connections.values()):
                    try:
                        conn.close()
                    except Exception:
                        logging.exception('Failed to close worker read connection')
                connections.clear()
            self.signals.finished.emit(self.key)


@dataclass
class Job:
    key: str
    title: str
    worker: Work
    callback: object = None
    on_progress: object = None
    on_error: object = None
    visible: bool = True
    mutating: bool = False
    state: str = '进行中'
    progress: dict = field(default_factory=dict)
    result: object = None
    error: str = ''
    started: float = field(default_factory=time.time)


class Jobs(QObject):
    changed = Signal(object)
    settled = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(max(8, min(32, (os.cpu_count() or 4) * 2)))
        self.jobs = {}
        self.active = set()

    def submit(self, title, fn, callback=None, progress=None, error=None,
               visible=True, mutating=False):
        key = uuid.uuid4().hex
        worker = Work(key, fn, threading.Event())
        job = Job(key, title, worker, callback, progress, error, visible, mutating)
        self.jobs[key] = job
        self.active.add(key)
        for signal, handler in ((worker.signals.result, self._result),
                                (worker.signals.error, self._error),
                                (worker.signals.progress, self._progress),
                                (worker.signals.finished, self._finished)):
            signal.connect(handler, Qt.ConnectionType.QueuedConnection)
        self.changed.emit(job)
        self.pool.start(worker)
        return key

    def cancel(self, key):
        if key in self.active:
            job = self.jobs[key]
            job.worker.stop.set()
            job.state = '正在停止'
            self.changed.emit(job)

    @Slot(str, object)
    def _result(self, key, result):
        job = self.jobs[key]
        job.result = result
        job.state = '已停止' if job.worker.stop.is_set() or (isinstance(result, dict) and result.get('stopped')) else '已完成'
        if job.state=='已完成' and isinstance(result,dict) and 'source_id' in result:
            job.state={'partial':'待更新','offline':'离线','busy':'已在运行'}.get(result.get('state'),'已完成')
        if job.callback and not job.worker.stop.is_set():
            try:
                job.callback(result)
            except Exception:
                job.state = '失败'
                job.error = traceback.format_exc()
                logging.error('Job callback failed: %s', job.error)
        self.changed.emit(job)

    @Slot(str, str)
    def _error(self, key, detail):
        job = self.jobs[key]
        job.error = detail
        job.state = '已停止' if job.worker.stop.is_set() else '失败'
        if job.on_error and not job.worker.stop.is_set():
            try:
                job.on_error(detail)
            except Exception:
                job.error += '\n' + traceback.format_exc()
        self.changed.emit(job)

    @Slot(str, object)
    def _progress(self, key, value):
        job = self.jobs[key]
        if job.worker.stop.is_set():
            return
        job.progress = value if isinstance(value, dict) else {'status': str(value)}
        if job.on_progress:
            job.on_progress(value)
        self.changed.emit(job)

    @Slot(str)
    def _finished(self, key):
        self.active.discard(key)
        job = self.jobs[key]
        job.callback = job.on_progress = job.on_error = None
        job.worker.fn = None
        if not job.visible:
            job.result = None
            job.progress = {}
        # Typing and background checks create hidden jobs continuously. Keep a
        # bounded diagnostic history; visible task receipts remain available.
        hidden = [k for k, item in self.jobs.items() if not item.visible and k not in self.active]
        for old in hidden[:-32]:
            del self.jobs[old]
        self.settled.emit()
