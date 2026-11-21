# -*- coding: utf-8 -*-
"""发现模块异常类型。

导入本模块不产生任何副作用：不启动扫描、不打开监听、不连接数据库。
"""


class DiscoveryError(Exception):
    """发现模块统一基类。"""


class UnsupportedError(DiscoveryError):
    """通道在当前环境不可用（如无权限读取 USN 日志）。"""


class PermissionRequired(DiscoveryError):
    """需要更高权限才能执行的操作（仅报告，不自动提权）。"""


class JournalExpired(DiscoveryError):
    """USN 日志范围已过期，游标不再有效。"""


class JournalChanged(DiscoveryError):
    """USN Journal ID 变化（日志被重建/清空），游标失效。"""


class Cancelled(DiscoveryError):
    """扫描/同步被用户取消。"""


class ReconciliationRequired(DiscoveryError):
    """本批无法可靠应用；保留游标，等待补扫，不能伪装为同步完成。"""
