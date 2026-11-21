"""有界目录通知队列。观察线程只入队，后台元数据批次可缓冲到快照完成后。"""
from __future__ import annotations

import os
import queue
import threading

from .filters import path_within, should_ignore_dir_name, should_ignore_file_name
from .model import ChangeEvent


class DiscoveryWatcher:
    OVERFLOW_QUEUE = 5000

    def __init__(self,catalog,source_id,root):
        self.catalog,self.source_id,self.root = catalog,source_id,os.path.abspath(root)
        source = catalog.get_source(source_id)
        self._volume = bool(source and source['kind']=='volume')
        self._observer = self._worker = None
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._queue = queue.Queue(self.OVERFLOW_QUEUE)
        self._overflow = False

    def start(self,buffered=False):
        try:
            from watchdog.observers import Observer
            observer = Observer()
            observer.schedule(_Handler(self),self.root,recursive=True)
            observer.start()
            self._observer = observer
            if not buffered:
                self._ready.set()
            self._worker = threading.Thread(target=self._run,name='discovery-metadata',daemon=True)
            self._worker.start()
            return True
        except Exception:
            if self._observer:
                self._observer.stop()
                self._observer.join(timeout=2)
            self._observer = None
            self.mark_overflow()
            return False

    def activate(self):
        self._ready.set()

    def flush(self, timeout=5, cancel=None):
        import time
        deadline = time.monotonic() + timeout
        while self._queue.unfinished_tasks:
            if time.monotonic() >= deadline or (cancel and cancel()):
                self.mark_overflow()
                return False
            time.sleep(.01)
        return True

    def stop(self,drain=True):
        if self._observer is not None:
            self._observer.stop()
            self._observer.join(timeout=2)
            if self._observer.is_alive():
                self.mark_overflow()
            self._observer = None
        if not drain:
            while True:
                try:
                    self._queue.get_nowait()
                    self._queue.task_done()
                except queue.Empty:
                    break
            self.mark_overflow()
        self._stop.set()
        self._ready.set()
        if self._worker:
            self._worker.join(timeout=5)
            if self._worker.is_alive():
                self.mark_overflow()
        if not self._queue.empty():
            self.mark_overflow()
        if self._overflow:
            self.catalog.set_source_state(self.source_id,'needs_rescan',needs_rescan=True,
                rescan_reason='directory notifications incomplete or observer failed')
        return not self._overflow

    def is_running(self):
        return self._observer is not None and self._observer.is_alive()

    def allowed(self,path):
        if not path_within(self.root,path) or self.catalog.is_internal_path(path):
            return False
        relative = os.path.relpath(path,self.root)
        parts = relative.replace('\\','/').split('/')
        return not any(should_ignore_dir_name(part,self._volume and index==0)
                       for index,part in enumerate(parts[:-1])) and not should_ignore_file_name(parts[-1])

    def enqueue(self,kind,path,is_dir=False,dest=None):
        if not self.allowed(path) and not (dest and self.allowed(dest)):
            return
        try:
            self._queue.put_nowait((kind,path,is_dir,dest))
        except queue.Full:
            self.mark_overflow()

    def _event(self,kind,path,is_dir=False,dest=None):
        for candidate in (path,dest):
            if candidate and path_within(self.root,candidate):
                parent=os.path.dirname(candidate)
                while path_within(self.root,parent) and os.path.normcase(parent)!=os.path.normcase(self.root):
                    if os.path.islink(parent) or getattr(os.path,'isjunction',lambda p:False)(parent):
                        return None
                    next_parent=os.path.dirname(parent)
                    if parent==next_parent:
                        break
                    parent=next_parent
        inside = self.allowed(path)
        if kind == 'moved':
            destination_inside = self.allowed(dest)
            if inside and not destination_inside:
                kind = 'deleted'
            elif destination_inside and not inside:
                kind,path = 'created',dest
            elif not inside:
                return None
        elif not inside:
            return None
        if kind == 'modified' and is_dir:
            return None
        return ChangeEvent(source_id=self.source_id,change_type=kind,
            old_parent_path=os.path.dirname(path) if kind!='created' else None,
            old_name=os.path.basename(path) if kind!='created' else None,
            new_parent_path=os.path.dirname(dest if kind=='moved' else path) if kind in ('created','moved') else None,
            new_name=os.path.basename(dest if kind=='moved' else path) if kind in ('created','moved') else None)

    def _run(self):
        while True:
            self._ready.wait()
            try:
                first = self._queue.get(timeout=.1)
            except queue.Empty:
                if self._stop.is_set():
                    return
                continue
            batch = [first]
            while len(batch)<256:
                try:
                    batch.append(self._queue.get_nowait())
                except queue.Empty:
                    break
            try:
                events = [event for item in batch if (event:=self._event(*item)) is not None]
                self.catalog.apply_events(events)
            except Exception:
                self.mark_overflow()
            finally:
                for _ in batch:
                    self._queue.task_done()

    def _apply(self,kind,path,is_dir=False,dest=None):
        event = self._event(kind,path,is_dir,dest)
        if event is not None:
            self.catalog.record_event(event,apply_now=True)

    def on_created(self,path,is_dir=False):
        self._apply('created',path,is_dir)

    def on_deleted(self,path,is_dir=False):
        self._apply('deleted',path,is_dir)

    def on_modified(self,path,is_dir=False):
        self._apply('modified',path,is_dir)

    def on_moved(self,src,dest,is_dir=False):
        self._apply('moved',src,is_dir,dest)

    def check_overflow(self):
        return self._overflow

    def mark_overflow(self):
        self._overflow = True


class _Handler:
    def __init__(self,watcher):
        self.w = watcher

    def dispatch(self,event):
        if event.event_type in ('created','deleted','modified','moved'):
            try:
                self.w.enqueue(event.event_type,event.src_path,bool(event.is_directory),getattr(event,'dest_path',None))
            except Exception:
                self.w.mark_overflow()
