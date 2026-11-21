"""Bounded discovery lifecycle; IO methods are called by workers, never Qt slots."""
from __future__ import annotations
from dataclasses import replace
import json
import os
import threading
import time
from .model import SearchPage, SearchRequest, SourceInfo
from .provider import DiscoveryService
from .scan import scan_source
from .native_scan import NativeIndexBuilder

CONCURRENCY_LIMIT = 2

class _Stop:
    def __init__(self, *events):
        self.events = [event for event in events if event is not None]
    def is_set(self):
        return any(event.is_set() for event in self.events)

class DiscoveryController:
    def __init__(self, db_path, config_path, concurrency=CONCURRENCY_LIMIT):
        self.service = DiscoveryService(db_path)
        self.catalog = self.service.catalog
        self.config_path = os.path.abspath(config_path)
        self.concurrency = max(1, int(concurrency))
        self._lock = threading.RLock()
        self._semaphore = threading.BoundedSemaphore(self.concurrency)
        self._workers = {}
        self._watchers = {}
        self._probe_cache = {}
        self._fallback = {}
        self._removing = set()
        self._shutdown = threading.Event()
        self._config = {'selected': [], 'auto_sync_seconds': 15, 'auto_scan': False}
        self.load()

    def load(self):
        try:
            with open(self.config_path, encoding='utf-8-sig') as stream:
                value = json.load(stream)
            if isinstance(value, dict):
                selected = value.get('selected', [])
                if isinstance(selected, list):
                    self._config['selected'] = list(dict.fromkeys(s for s in selected if isinstance(s, str)))
                self._config['auto_scan'] = value.get('auto_scan') is True
                self._config['auto_sync_seconds'] = max(5, min(3600, int(value.get('auto_sync_seconds', 15))))
        except (OSError, ValueError, TypeError):
            pass

    def save(self):
        with self._lock:
            os.makedirs(os.path.dirname(self.config_path) or '.', exist_ok=True)
            with open(self.config_path + '.tmp', 'w', encoding='utf-8') as stream:
                json.dump(self._config, stream, ensure_ascii=False, indent=2)
            os.replace(self.config_path + '.tmp', self.config_path)

    def set_auto_scan(self, enabled):
        with self._lock:
            self._config['auto_scan'] = bool(enabled)
            self.save()

    def ensure_volume_sources(self):
        return self.service.add_volume_sources()

    def add_directory(self, path, select=True):
        source_id = self.service.add_directory_source(os.path.abspath(path))
        if select:
            self.select(source_id, True)
        return source_id

    def select(self, source_id, selected):
        with self._lock:
            ids = self._config['selected']
            if selected and source_id not in ids:
                ids.append(source_id)
            elif not selected:
                self._config['selected'] = [sid for sid in ids if sid != source_id]
            self.save()
        if not selected:
            self.cancel(source_id)
            self.stop_watch(source_id)

    def selected_ids(self):
        with self._lock:
            return list(self._config['selected'])

    def selected_sources(self):
        selected = set(self.selected_ids())
        return [s for s in self.service.list_sources() if s['source_id'] in selected]

    def _cached_probe(self, source_id):
        with self._lock:
            cached = self._probe_cache.get(source_id)
        now = time.monotonic()
        if cached and now - cached[0] < 15:
            return cached[1]
        caps = self.service.probe_source(source_id)
        with self._lock:
            self._probe_cache[source_id] = now, caps
        return caps

    def list_ranges(self):
        selected = set(self.selected_ids())
        result = []
        for source in self.service.list_sources():
            caps = self._cached_probe(source['source_id'])
            row = dict(source)
            row.update(selected=source['source_id'] in selected,
                       native_usn_readable=caps.native_usn_readable,
                       cap_reason=self._fallback.get(source['source_id']) or caps.reason,
                       running=self.is_running(source['source_id']))
            result.append(row)
        return sorted(result, key=lambda row: (not row['selected'], row['root'].casefold()))

    def is_running(self, source_id):
        with self._lock:
            return source_id in self._workers

    def _reserve(self, source_id):
        with self._lock:
            if self._shutdown.is_set() or source_id in self._workers or source_id in self._removing:
                return None
            stop = threading.Event()
            self._workers[source_id] = stop
            return stop

    def _execute(self, source_id, fn, internal, external=None):
        stop = _Stop(internal, external, self._shutdown)
        acquired = False
        try:
            while not stop.is_set():
                acquired = self._semaphore.acquire(timeout=.1)
                if acquired:
                    return fn(stop)
            return {'source_id': source_id, 'state': 'partial', 'stopped': True}
        finally:
            if acquired:
                self._semaphore.release()
            with self._lock:
                if self._workers.get(source_id) is internal:
                    self._workers.pop(source_id, None)

    def _operation(self, source_id, fn, stop=None):
        internal = self._reserve(source_id)
        if internal is None:
            return {'source_id': source_id, 'state': 'busy', 'busy': True}
        return self._execute(source_id, fn, internal, stop)

    def cancel(self, source_id):
        with self._lock:
            event = self._workers.get(source_id)
        if event:
            event.set()

    def remove_range(self, source_id):
        with self._lock:
            self._removing.add(source_id)
        try:
            self.cancel(source_id)
            while self.is_running(source_id):
                time.sleep(.03)
            return self._remove(source_id)
        finally:
            with self._lock:
                self._removing.discard(source_id)

    def _remove(self, source_id):
        self.stop_watch(source_id)
        self.catalog.remove_source(source_id)
        with self._lock:
            self._config['selected'] = [sid for sid in self._config['selected'] if sid != source_id]
            self._probe_cache.pop(source_id, None)
            self.save()
        return {'source_id': source_id, 'state': 'removed'}

    def start_scan(self, source_id, progress=None, full_native=True, stop=None):
        return self._operation(source_id, lambda token: self._scan(source_id, progress, token, full_native), stop)

    def _scan(self, source_id, progress, stop, full_native=True):
        source = self.catalog.get_source(source_id)
        if source is None:
            raise KeyError(source_id)
        self.stop_watch(source_id)
        info = SourceInfo(**{key: source[key] for key in
            ('source_id', 'kind', 'root', 'volume_serial', 'volume_guid', 'filesystem', 'provider')})
        caps = self._cached_probe(source_id)
        if full_native and info.kind == 'volume' and caps.native_usn_readable:
            report = NativeIndexBuilder(self.catalog, info).build(progress=progress, cancel=stop.is_set)
            if report.get('state') == 'complete':
                replay = self.service.sync_source(source_id, progress=progress, cancel=stop.is_set)
                if not (replay.needs_rescan or replay.stopped):
                    report['state'] = 'partial' if replay.more_pending else 'complete'
                    return report
            if report.get('stopped') or stop.is_set():
                return report
            self._fallback[source_id] = report.get('reason') or 'native snapshot requires reconciliation'
        return self._generic_snapshot(info, progress, stop)

    def _generic_snapshot(self, info, progress, stop):
        from .watch import DiscoveryWatcher
        watcher = DiscoveryWatcher(self.catalog, info.source_id, info.root)
        watching = watcher.start(buffered=True)
        if watching:
            with self._lock:
                self._watchers[info.source_id] = watcher
        try:
            report = scan_source(self.catalog, info, progress=progress, cancel=stop.is_set)
        finally:
            watcher.activate()
        watcher.flush(cancel=stop.is_set)
        if not watching or watcher.check_overflow():
            self.catalog.set_source_state(info.source_id, 'needs_rescan', needs_rescan=True,
                rescan_reason='directory notifications unavailable or incomplete')
        state = self.catalog.get_source(info.source_id)
        if report.state == 'complete' and state['needs_rescan']:
            report.state = 'partial'
        return report.to_dict()

    def start_scan_async(self, source_id, callback=None, progress=None, full_native=True):
        internal = self._reserve(source_id)
        if internal is None:
            return None
        def run():
            try:
                result = self._execute(source_id,
                    lambda token: self._scan(source_id, progress, token, full_native), internal)
            except Exception as exc:
                result = {'source_id': source_id, 'state': 'partial', 'error': str(exc)}
            if callback:
                callback(result)
        thread = threading.Thread(target=run, name='discovery-scan', daemon=True)
        thread.start()
        return thread

    def sync(self, source_id, progress=None, stop=None):
        return self._operation(source_id, lambda token: self._sync(source_id, progress, token), stop)

    def _sync(self, source_id, progress, stop):
        source = self.catalog.get_source(source_id)
        if source is None:
            raise KeyError(source_id)
        if source['provider'] == 'usn' and not source['needs_rescan']:
            report = self.service.sync_source(source_id, progress=progress, cancel=stop.is_set)
            if not report.needs_rescan:
                return report.to_dict()
        with self._lock:
            watcher = self._watchers.get(source_id)
        if (source['state'] == 'complete' and not source['needs_rescan'] and
                watcher and watcher.is_running() and not watcher.check_overflow() and
                os.path.isdir(source['root'])):
            return {'source_id': source_id, 'state': 'complete', 'applied': 0}
        return self._scan(source_id, progress, stop)

    def start_watch(self, source_id):
        source = self.catalog.get_source(source_id)
        if source is None or source['provider'] == 'usn':
            return False
        with self._lock:
            if source_id in self._watchers:
                return False
        from .watch import DiscoveryWatcher
        watcher = DiscoveryWatcher(self.catalog, source_id, source['root'])
        if not watcher.start():
            return False
        with self._lock:
            self._watchers[source_id] = watcher
        return True

    def stop_watch(self, source_id):
        with self._lock:
            watcher = self._watchers.pop(source_id, None)
        if watcher:
            watcher.stop(drain=True)
        return watcher is not None

    def resume_watchers(self):
        count = 0
        for source in self.selected_sources():
            with self._lock:
                watching=self._watchers.get(source['source_id'])
            if watching and watching.is_running():
                continue
            if source['provider'] != 'usn' and source['state'] in ('complete', 'partial', 'scanning'):
                self.catalog.set_source_state(source['source_id'], 'needs_rescan', needs_rescan=True,
                    rescan_reason='reconcile changes while application was closed')
                count += 1
        return count

    def search(self, request: SearchRequest, dedupe_paths=True) -> SearchPage:
        ids = self.selected_ids()
        if request.source_ids is not None:
            ids = [sid for sid in ids if sid in request.source_ids]
        if not ids:
            return SearchPage([], False, time.monotonic_ns(), 0)
        return self.catalog.search(replace(request, source_ids=ids), dedupe_paths=dedupe_paths)

    def get_coverage(self):
        selected = set(self.selected_ids())
        report = self.catalog.get_coverage().to_dict()
        report['sources'] = [s for s in report['sources'] if s['source_id'] in selected]
        return report

    def shutdown(self):
        self._shutdown.set()
        with self._lock:
            for stop in self._workers.values():
                stop.set()
        while True:
            with self._lock:
                if not self._workers:
                    break
            time.sleep(.03)
        for source_id in list(self._watchers):
            self.stop_watch(source_id)
        self.save()
