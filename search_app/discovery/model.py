# -*- coding: utf-8 -*-
"""目录发现模块的数据契约（与 docs/NATIVE_DISCOVERY_PLAN.md 最小接口对齐）。

全部为纯 dataclass，不含 IO。模块导入无副作用。
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional, List, Callable


def _asdict_skip_none(obj):
    d = asdict(obj)
    return {k: v for k, v in d.items() if v is not None}


@dataclass
class SourceInfo:
    """目录/卷来源的静态身份与状态。"""
    source_id: str
    kind: str                 # 'volume' | 'directory'
    root: str                 # 绝对路径；卷来源形如 'C:\\'
    volume_serial: Optional[int] = None
    volume_guid: Optional[str] = None
    filesystem: Optional[str] = None
    provider: str = 'generic'
    state: str = 'new'        # new|scanning|partial|complete|needs_rescan|offline
    online: bool = True
    discovered_count: int = 0
    failed_count: int = 0
    last_scan_at: Optional[float] = None
    last_scan_generation: Optional[int] = None
    needs_rescan: bool = False
    rescan_reason: Optional[str] = None

    def to_dict(self):
        return _asdict_skip_none(self)


@dataclass
class SourceCapabilities:
    """probe_source 的能力结论。"""
    source_id: str
    provider: str                        # 探测到的 provider 名
    provider_available: bool             # 该 provider 是否可用
    native_usn_readable: bool            # USN 日志是否可读（真实探测，非模拟）
    filesystem: Optional[str] = None
    volume_serial: Optional[int] = None
    volume_guid: Optional[str] = None
    reason: Optional[str] = None         # 不可用/回退原因
    fallback: str = 'generic'

    def to_dict(self):
        return _asdict_skip_none(self)


@dataclass
class SearchRequest:
    """字面检索请求。默认按名称匹配，妥善处理 % _ \\ 与中文。"""
    query: str
    scope: str = 'name'                  # name|path|all
    ext: Optional[List[str]] = None      # 扩展名过滤（不带点，小写；空串表无扩展名）
    source_ids: Optional[List[str]] = None
    root: Optional[str] = None           # 根目录边界：只匹配 root 及其子项
    case_sensitive: bool = False
    sort: str = 'name'                   # name|path|size|mtime
    descending: bool = False
    limit: int = 50
    offset: int = 0
    cancel: Optional[Callable[[], bool]] = None


@dataclass
class FileHit:
    entry_id: int
    source_id: str
    path: str
    name: str
    kind: str                            # file|dir|link
    ext: Optional[str] = None
    size: Optional[int] = None
    mtime_ns: Optional[int] = None
    object_id: Optional[int] = None
    online: bool = True

    def to_dict(self):
        return _asdict_skip_none(self)


@dataclass
class SearchPage:
    results: List[dict]
    has_more: bool
    request_version: int
    elapsed_ms: float
    total_hint: Optional[int] = None     # 未完整计数时为空，不阻塞返回

    def to_dict(self):
        return _asdict_skip_none(self)


@dataclass
class ChangeEvent:
    """可重放变更事件：区分仅更名与内容变化。"""
    source_id: str
    seq: Optional[int] = None            # provider 游标（USN 号）或递增序号
    change_type: str = 'created'         # created|deleted|renamed|moved|modified
    object_id: Optional[int] = None
    old_parent_path: Optional[str] = None
    new_parent_path: Optional[str] = None
    old_name: Optional[str] = None
    new_name: Optional[str] = None
    content_version: Optional[int] = None  # 内容版本：modified 时递增，renamed 不变

    def to_dict(self):
        return _asdict_skip_none(self)


@dataclass
class ScanReport:
    source_id: str
    provider: str
    state: str
    found: int = 0
    dirs: int = 0
    failed: int = 0
    elapsed_ms: float = 0.0
    stopped: bool = False
    generation: Optional[int] = None

    def to_dict(self):
        return _asdict_skip_none(self)


@dataclass
class SyncReport:
    source_id: str
    provider: str
    applied: int = 0
    failed: int = 0
    cursor: Optional[str] = None
    needs_rescan: bool = False
    stopped: bool = False
    more_pending: bool = False

    def to_dict(self):
        return _asdict_skip_none(self)


@dataclass
class CoverageReport:
    sources: List[dict]
    generated_at: float = 0.0

    def to_dict(self):
        return _asdict_skip_none(self)
