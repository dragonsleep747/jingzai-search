# -*- coding: utf-8 -*-
"""镜仔内置全盘发现模块（目录发现，无 GUI）。

公开最小接口（与 NATIVE_DISCOVERY_PLAN.md 对齐）：

    DiscoveryService.list_sources / probe_source / scan_source /
    sync_source / search_catalog / get_coverage

导入本包不启动扫描、监听或 GUI，不写入任何数据库。
"""
from __future__ import annotations

from .catalog import Catalog
from .errors import (
    Cancelled, DiscoveryError, JournalChanged, JournalExpired,
    PermissionRequired, UnsupportedError,
)
from .model import (
    ChangeEvent, CoverageReport, FileHit, ScanReport, SearchPage,
    SearchRequest, SourceCapabilities, SourceInfo, SyncReport,
)
from .provider import DiscoveryService

__all__ = [
    'Catalog', 'DiscoveryService',
    'ChangeEvent', 'CoverageReport', 'FileHit', 'ScanReport', 'SearchPage',
    'SearchRequest', 'SourceCapabilities', 'SourceInfo', 'SyncReport',
    'DiscoveryError', 'Cancelled', 'JournalChanged', 'JournalExpired',
    'PermissionRequired', 'UnsupportedError',
]

__version__ = '0.3.1'
