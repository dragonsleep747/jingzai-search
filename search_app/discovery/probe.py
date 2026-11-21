# -*- coding: utf-8 -*-
"""卷列表与能力探测（只读）。"""
from __future__ import annotations

import ctypes
import os
import hashlib
from ctypes import wintypes

from .errors import UnsupportedError, PermissionRequired
from .model import SourceCapabilities, SourceInfo
from . import usn


def list_volumes() -> list:
    """枚举逻辑驱动器（固定盘 + 可移动盘），返回 [SourceInfo, ...]。"""
    if not usn.is_windows():
        return []
    kernel32 = ctypes.windll.kernel32
    kernel32.GetLogicalDrives.restype = wintypes.DWORD
    kernel32.GetLogicalDrives.argtypes = []
    kernel32.GetDriveTypeW.argtypes = [wintypes.LPCWSTR]
    kernel32.GetDriveTypeW.restype = wintypes.UINT
    mask = kernel32.GetLogicalDrives()
    out = []
    for i in range(26):
        if not (mask & (1 << i)):
            continue
        letter = chr(ord('A') + i)
        root = f'{letter}:\\'
        drive_type = kernel32.GetDriveTypeW(root)
        if drive_type not in (3, 2):  # DRIVE_FIXED=3, DRIVE_REMOVABLE=2
            continue
        info = _volume_info(root)
        out.append(SourceInfo(
            source_id='vol:' + hashlib.sha256((info.get('guid') or
                f"{info.get('serial')}:{info.get('filesystem')}:{letter}").casefold().encode('utf-8')).hexdigest()[:24],
            kind='volume',
            root=root,
            volume_serial=info.get('serial'),
            volume_guid=info.get('guid'),
            filesystem=info.get('filesystem'),
            provider='generic',
            online=bool(info.get('filesystem')),
        ))
    return out


def _volume_info(root: str) -> dict:
    kernel32 = ctypes.windll.kernel32
    kernel32.GetVolumeInformationW.argtypes = [wintypes.LPCWSTR,wintypes.LPWSTR,wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),ctypes.POINTER(wintypes.DWORD),ctypes.POINTER(wintypes.DWORD),
        wintypes.LPWSTR,wintypes.DWORD]
    kernel32.GetVolumeInformationW.restype = wintypes.BOOL
    kernel32.GetVolumeNameForVolumeMountPointW.argtypes = [wintypes.LPCWSTR,wintypes.LPWSTR,wintypes.DWORD]
    kernel32.GetVolumeNameForVolumeMountPointW.restype = wintypes.BOOL
    name_buf = ctypes.create_unicode_buffer(64)
    fs_buf = ctypes.create_unicode_buffer(64)
    serial = wintypes.DWORD(0)
    max_len = wintypes.DWORD(0)
    flags = wintypes.DWORD(0)
    ok = kernel32.GetVolumeInformationW(
        root, name_buf, len(name_buf), ctypes.byref(serial),
        ctypes.byref(max_len), ctypes.byref(flags), fs_buf, len(fs_buf))
    guid = None
    try:
        guid_buf = ctypes.create_unicode_buffer(64)
        if kernel32.GetVolumeNameForVolumeMountPointW(root, guid_buf,
                                                      len(guid_buf)):
            guid = guid_buf.value
    except Exception:
        pass
    return {
        'serial': serial.value if ok else None,
        'filesystem': fs_buf.value if ok else None,
        'guid': guid,
    }


def probe_source(info: SourceInfo) -> SourceCapabilities:
    """探测单个来源能力。只读：绝不创建/删除 USN 日志、不自动提权。"""
    caps = SourceCapabilities(
        source_id=info.source_id, provider='generic',
        provider_available=True, native_usn_readable=False,
        filesystem=info.filesystem, volume_serial=info.volume_serial,
        volume_guid=info.volume_guid, fallback='generic',
    )
    if info.kind != 'volume' or not usn.is_windows():
        caps.reason = 'non-volume source or non-Windows: generic scan'
        return caps
    if (info.filesystem or '').lower() != 'ntfs':
        caps.reason = f'filesystem={info.filesystem}: USN requires NTFS'
        return caps
    handle = None
    try:
        handle = usn.open_volume_handle(info.root)
        if handle is None:
            caps.reason = 'cannot open volume handle (access denied?)'
            return caps
        journal = usn.query_usn_journal(handle)
        caps.provider = 'usn'
        caps.native_usn_readable = True
        caps.volume_serial = info.volume_serial
        caps.reason = f'USN readable; journal_id={journal["usn_journal_id"]} ' \
                      f'next_usn={journal["next_usn"]}'
        return caps
    except PermissionRequired as exc:
        caps.provider = 'generic'
        caps.reason = f'permission_required: {exc}'
        return caps
    except (UnsupportedError, OSError) as exc:
        caps.provider = 'generic'
        caps.reason = f'unsupported: {exc}'
        return caps
    finally:
        if handle is not None:
            usn.close_handle(handle)
