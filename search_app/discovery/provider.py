# -*- coding: utf-8 -*-
"""通道提供方与 DiscoveryService（最小对外接口实现）。

对应 NATIVE_DISCOVERY_PLAN.md：

    list_sources / probe_source / scan_source / sync_source /
    search_catalog / get_coverage

导入本模块不启动扫描/监听/GUI。
"""
from __future__ import annotations

import os
import time
import hashlib
import threading

from . import probe, scan, sync, usn
from .catalog import Catalog
from .errors import JournalChanged, JournalExpired, PermissionRequired, UnsupportedError, ReconciliationRequired
from .model import (
    ChangeEvent, CoverageReport, ScanReport, SearchPage, SearchRequest,
    SourceCapabilities, SourceInfo, SyncReport,
)

# 每次 sync 最多处理的事件数（有界：预研阶段不做无界回放）
SYNC_MAX_RECORDS = 20000
# 每次 sync 最多读取的枚举记录数（有界只读实验）
ENUM_MAX_RECORDS = 20000


class DiscoveryService:
    def __init__(self, db_path: str):
        self.catalog = Catalog(db_path)
        self._scan_lock = threading.RLock()

    # ------------------------------------------------------------- sources
    def list_sources(self) -> list:
        return self.catalog.list_sources()

    def add_directory_source(self, path: str) -> str:
        path = os.path.abspath(path)
        source_id = 'dir:' + hashlib.sha256(os.path.normcase(path).encode('utf-8')).hexdigest()[:24]
        for source in self.catalog.list_sources():
            if source['kind']=='directory' and os.path.normcase(os.path.normpath(source['root']))==os.path.normcase(path):
                source_id=source['source_id']
                break
        info = SourceInfo(source_id=source_id, kind='directory', root=path,
                          provider='generic', online=os.path.isdir(path))
        self.catalog.register_source(info)
        return source_id

    def add_volume_sources(self) -> list:
        """登记系统所有固定/可移动卷来源。返回 source_id 列表。"""
        ids = []
        for info in probe.list_volumes():
            for old in self.catalog.list_sources():
                if info.volume_guid and old['kind']=='volume' and old['volume_guid']==info.volume_guid:
                    info.source_id=old['source_id']
                    info.provider=old['provider']
                    break
            self.catalog.register_source(info)
            ids.append(info.source_id)
        return ids

    # --------------------------------------------------------------- probe
    def probe_source(self, source_id: str) -> SourceCapabilities:
        row = self.catalog.get_source(source_id)
        if row is None:
            return SourceCapabilities(source_id=source_id, provider='generic',
                                      provider_available=False,
                                      native_usn_readable=False,
                                      reason='unknown source')
        return probe.probe_source(SourceInfo(**{k: row[k] for k in (
            'source_id', 'kind', 'root', 'volume_serial', 'volume_guid',
            'filesystem', 'provider')}))

    # ---------------------------------------------------------------- scan
    def scan_source(self, source_id: str, progress=None, cancel=None) -> ScanReport:
        with self._scan_lock:
            row = self.catalog.get_source(source_id)
            if row is None:
                raise KeyError(source_id)
            info = SourceInfo(**{key: row[key] for key in ('source_id','kind','root',
                'volume_serial','volume_guid','filesystem','provider')})
            # 先记录日志游标；没有可用日志时在枚举前开始缓冲目录通知。
            caps = self.probe_source(source_id)
            journal = None
            if caps.native_usn_readable:
                handle = usn.open_volume_handle(info.root)
                if handle is not None:
                    try:
                        journal = usn.query_usn_journal(handle)
                    except (PermissionRequired,UnsupportedError,OSError):
                        pass
                    finally:
                        usn.close_handle(handle)
            watcher = None
            if journal is None:
                from .watch import DiscoveryWatcher
                watcher = DiscoveryWatcher(self.catalog,source_id,info.root)
                watching = watcher.start(buffered=True)
            try:
                report = scan.scan_source(self.catalog,info,progress=progress,cancel=cancel)
            finally:
                if watcher is not None:
                    watcher.stop(drain=True)
            if report.state != 'complete':
                return report
            if journal is not None:
                self.catalog.save_checkpoint(source_id,'usn',str(journal['next_usn']),
                    str(journal['usn_journal_id']),report.generation)
                result = self.sync_source(source_id,progress=progress,cancel=cancel)
                if result.needs_rescan or result.stopped or result.more_pending:
                    report.state = 'partial'
                    report.stopped = result.stopped
            else:
                current = self.catalog.get_source(source_id)
                if not watching or watcher.check_overflow() or current['needs_rescan']:
                    self.catalog.set_source_state(source_id,'needs_rescan',needs_rescan=True,
                        rescan_reason='directory notification gap or subtree reconciliation required')
                    report.state = 'partial'
            return report

    # ---------------------------------------------------------------- sync
    def sync_source(self, source_id: str, cursor: str = None,
                    progress=None, cancel=None) -> SyncReport:
        row = self.catalog.get_source(source_id)
        if row is None:
            raise KeyError(source_id)
        start = 0
        applied = processed = 0
        handle = None
        def require_rescan(reason, provider='usn'):
            self.catalog.set_source_state(source_id,'needs_rescan',needs_rescan=True,rescan_reason=reason)
            return SyncReport(source_id=source_id,provider=provider,applied=applied,
                              failed=1,cursor=str(start),needs_rescan=True)
        if not self.probe_source(source_id).native_usn_readable:
            return require_rescan('generic channel requires reconciliation','generic')
        if row['needs_rescan'] or row['state'] not in ('complete','partial'):
            return require_rescan('snapshot requires reconciliation before journal replay')
        try:
            handle = usn.open_volume_handle(row['root'])
            if handle is None:
                return require_rescan('volume unavailable','generic')
            journal = usn.query_usn_journal(handle)
            cp = self.catalog.get_checkpoint(source_id)
            journal_id = str(journal['usn_journal_id'])
            if cp is None or cp['journal_id'] != journal_id:
                return require_rescan('USN journal id changed or snapshot missing')
            start = int(cp['cursor'])
            if cursor is not None and int(cursor) != start:
                return require_rescan('caller cursor differs from committed checkpoint')
            if start < max(journal['lowest_valid_usn'],journal['first_usn']) or start > journal['next_usn']:
                return require_rescan('USN cursor outside retained journal')
            frn_map = None
            target = journal['next_usn']
            while start < target and processed < SYNC_MAX_RECORDS:
                if cancel is not None and cancel():
                    self.catalog.set_source_state(source_id,'partial',needs_rescan=False,rescan_reason='sync cancelled')
                    return SyncReport(source_id=source_id,provider='usn',applied=applied,cursor=str(start),stopped=True)
                maximum = int(journal.get('max_supported_major',3) or 3)
                if maximum>4:
                    return require_rescan('future USN record version requires reconciliation')
                raw,next_usn = usn.read_usn_journal_chunk(handle,start,journal['usn_journal_id'],timeout_ms=0,
                    max_major_version=max(3,maximum))
                if len(raw) < 8 or next_usn <= start:
                    return require_rescan('USN buffer missing cursor or made no progress')
                records,stats = usn.parse_usn_buffer(raw,start_offset=8)
                if any(stats.values()):
                    return require_rescan('USN record gap: '+str(stats))
                if frn_map is None:
                    frn_map = self.catalog.frn_path_map(source_id)
                staged = dict(frn_map)
                events,event_stats = sync.convert_usn_records_to_events(records,staged)
                if event_stats['unresolved_path'] or event_stats['hard_link']:
                    return require_rescan('USN paths require reconciliation: '+str(event_stats))
                for event in events:
                    event.source_id = source_id
                applied += self.catalog.apply_usn_batch(source_id,'usn',journal_id,str(next_usn),events)
                frn_map = staged
                start = next_usn
                processed += max(1,len(records))
                current = self.catalog.get_source(source_id)
                if current['needs_rescan']:
                    return require_rescan(current['rescan_reason'] or 'subtree reconciliation required')
                if progress:
                    progress({'source':source_id,'applied':applied,'records':processed,'cursor':str(start)})
            more = start < target
            state = 'partial' if more else 'complete'
            self.catalog.set_source_state(source_id,state,needs_rescan=False,
                rescan_reason='USN backlog remains' if more else None,last_scan_at=time.time())
            return SyncReport(source_id=source_id,provider='usn',applied=applied,
                              cursor=str(start),more_pending=more)
        except (PermissionRequired,UnsupportedError,OSError,JournalChanged,JournalExpired,ReconciliationRequired) as exc:
            return require_rescan(type(exc).__name__+': '+str(exc))
        finally:
            if handle is not None:
                usn.close_handle(handle)

    # -------------------------------------------------------------- search
    def search_catalog(self, request: SearchRequest) -> SearchPage:
        return self.catalog.search(request)

    def get_coverage(self) -> CoverageReport:
        return self.catalog.get_coverage()

    # ------------------------------------------------------- 有界原生只读实验
    def bounded_usn_enum(self, source_id: str, max_records=ENUM_MAX_RECORDS) -> dict:
        """FSCTL_ENUM_USN_DATA 有界只读实验：只解析记录、重建路径映射并返回统计。

        不用于构建完整 catalog（ENUM 全量无界，预研阶段列为缺口）。
        """
        row = self.catalog.get_source(source_id)
        if row is None:
            raise KeyError(source_id)
        caps = self.probe_source(source_id)
        if not caps.native_usn_readable:
            return {'supported': False, 'reason': caps.reason}
        handle = None
        try:
            handle = usn.open_volume_handle(row['root'])
            journal = usn.query_usn_journal(handle)
            records = []
            start_ref = 0
            buffer_size = 65536
            rounds = 0
            while len(records) < max_records and rounds < 200:
                buf = usn.enum_usn_chunk(handle, start_ref, buffer_size)
                recs, _stats = usn.parse_usn_buffer(buf, start_offset=8)
                if any(_stats.values()):
                    raise UnsupportedError('enumeration contains unsupported or malformed records')
                if not recs:
                    break
                records.extend(recs)
                next_ref = int.from_bytes(buf[:8], 'little')
                if next_ref <= start_ref:
                    raise UnsupportedError('enumeration cursor made no progress')
                start_ref = next_ref
                rounds += 1
            records = records[:max_records]
            try:
                root_ref = os.stat(row['root']).st_ino
            except OSError:
                root_ref = None
            paths = sync.build_path_map(records, root=row['root'], root_ref=root_ref)
            resolved = sum(record['file_ref'] in paths for record in records)
            return {
                'supported': True,
                'journal_id': journal['usn_journal_id'],
                'next_usn': journal['next_usn'],
                'first_usn': journal['first_usn'],
                'lowest_valid_usn': journal['lowest_valid_usn'],
                'records_read': len(records),
                'paths_resolved': resolved,
                'unresolved': len(records) - resolved,
                'truncated': len(records) >= max_records,
            }
        except (PermissionRequired, UnsupportedError) as exc:
            return {'supported': False, 'reason': f'{type(exc).__name__}: {exc}'}
        finally:
            if handle is not None:
                usn.close_handle(handle)
