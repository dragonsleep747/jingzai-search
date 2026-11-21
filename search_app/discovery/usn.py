# -*- coding: utf-8 -*-
"""只读 NTFS USN 通道封装（ctypes）。

只做读操作：FSCTL_QUERY_USN_JOURNAL / FSCTL_ENUM_USN_DATA /
FSCTL_READ_USN_JOURNAL。不创建/删除日志、不安装服务/驱动、不自动提权。
显式声明 ctypes 参数与返回类型；句柄由调用方通过 close_handle 释放。

结构布局来源：winioctl.h（USN_RECORD_V2 / V3 已按 MS 文档核对）。
V2：FileReferenceNumber 为 8 字节；V3：为 FILE_ID_128（16 字节）。
V4 与更高 major version 列为不支持缺口。
"""
from __future__ import annotations

import ctypes
import os
import time
from ctypes import wintypes

from .errors import PermissionRequired, UnsupportedError, JournalExpired

# ---- 常量（CTL_CODE 推导见 ARCHITECTURE.md） ----
DWORDLONG = ctypes.c_uint64   # winioctl DWORDLONG / USN 均按 64 位处理
USN = ctypes.c_int64
FSCTL_QUERY_USN_JOURNAL = 0x000900F4
FSCTL_ENUM_USN_DATA = 0x000900B3
FSCTL_READ_USN_JOURNAL = 0x000900BB

GENERIC_READ = 0x80000000
FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
FILE_SHARE_DELETE = 0x00000004
OPEN_EXISTING = 3
INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value

FILE_ATTRIBUTE_DIRECTORY = 0x10
FILE_ATTRIBUTE_REPARSE_POINT = 0x400

# Reason 位（常用）
USN_REASON_DATA_OVERWRITE = 0x00000001
USN_REASON_DATA_EXTEND = 0x00000002
USN_REASON_DATA_TRUNCATION = 0x00000004
USN_REASON_FILE_CREATE = 0x00000100
USN_REASON_FILE_DELETE = 0x00000200
USN_REASON_RENAME_OLD_NAME = 0x00001000
USN_REASON_RENAME_NEW_NAME = 0x00002000
USN_REASON_BASIC_INFO_CHANGE = 0x00008000
USN_REASON_HARD_LINK_CHANGE = 0x00010000
USN_REASON_CLOSE = 0x80000000


def is_windows() -> bool:
    return os.name == 'nt'


class USN_JOURNAL_DATA_V2(ctypes.Structure):
    _fields_ = [
        ('UsnJournalID', DWORDLONG),
        ('FirstUsn', DWORDLONG),
        ('NextUsn', DWORDLONG),
        ('LowestValidUsn', DWORDLONG),
        ('MaxUsn', DWORDLONG),
        ('MaximumSize', DWORDLONG),
        ('AllocationDelta', DWORDLONG),
        ('MinSupportedMajorVersion', wintypes.WORD),
        ('MaxSupportedMajorVersion', wintypes.WORD),
    ]


class MFT_ENUM_DATA_V1(ctypes.Structure):
    _fields_ = [
        ('StartFileReferenceNumber', DWORDLONG),
        ('LowUsn', USN),
        ('HighUsn', USN),
        ('MinMajorVersion', wintypes.WORD),
        ('MaxMajorVersion', wintypes.WORD),
    ]


class READ_USN_JOURNAL_DATA_V1(ctypes.Structure):
    _fields_ = [
        ('StartUsn', USN),
        ('ReasonMask', wintypes.DWORD),
        ('ReturnOnlyOnClose', wintypes.DWORD),
        ('Timeout', DWORDLONG),
        ('BytesToWaitFor', DWORDLONG),
        ('UsnJournalID', DWORDLONG),
        ('MinMajorVersion', wintypes.WORD),
        ('MaxMajorVersion', wintypes.WORD),
    ]


# V2 头：8 字节文件引用。FileNameOffset 正常为 60。
_USN_V2_HEADER = 60
# V3 头：16 字节文件引用。FileNameOffset 正常为 76。
_USN_V3_HEADER = 76


def _require_windows():
    if not is_windows():
        raise UnsupportedError('USN journal requires Windows')


def open_volume_handle(volume: str):
    """打开卷的只读句柄。volume 形如 'C:'。失败返回 None（不抛权限错误）。"""
    _require_windows()
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
        wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    handle = kernel32.CreateFileW(
        f'\\\\.\\{volume.rstrip("\\/")}', GENERIC_READ,
        FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
        None, OPEN_EXISTING, 0, None)
    if handle == INVALID_HANDLE_VALUE or handle == 0:
        return None
    return handle


def close_handle(handle) -> None:
    if handle is None:
        return
    api = ctypes.windll.kernel32.CloseHandle
    api.argtypes = [wintypes.HANDLE]
    api.restype = wintypes.BOOL
    api(wintypes.HANDLE(handle))


def _device_io_control(handle, code, inbuf, outbuf):
    kernel32 = ctypes.windll.kernel32
    kernel32.DeviceIoControl.restype = wintypes.BOOL
    kernel32.DeviceIoControl.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
        wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
        wintypes.LPVOID,
    ]
    kernel32.GetLastError.restype = wintypes.DWORD
    bytes_returned = wintypes.DWORD(0)
    in_ptr = ctypes.cast(ctypes.byref(inbuf), wintypes.LPVOID) \
        if inbuf is not None else None
    in_size = ctypes.sizeof(inbuf) if inbuf is not None else 0
    out_ptr = ctypes.cast(outbuf, wintypes.LPVOID) \
        if outbuf is not None else None
    out_size = len(outbuf) if outbuf is not None else 0
    ok = kernel32.DeviceIoControl(
        wintypes.HANDLE(handle), wintypes.DWORD(code),
        in_ptr, wintypes.DWORD(in_size),
        out_ptr, wintypes.DWORD(out_size),
        ctypes.byref(bytes_returned), None)
    if not ok:
        err = kernel32.GetLastError()
        if code == FSCTL_ENUM_USN_DATA and err == 38:  # ERROR_HANDLE_EOF
            return 0
        if err == 1181:  # ERROR_JOURNAL_ENTRY_DELETED
            raise JournalExpired('USN journal entries were discarded')
        if err in (5, 1300, 1314):  # ACCESS_DENIED / privilege / bad token
            raise PermissionRequired(f'USN ioctl denied (winerror {err})')
        raise UnsupportedError(f'DeviceIoControl failed (winerror {err})')
    return bytes_returned.value


def query_usn_journal(handle) -> dict:
    """FSCTL_QUERY_USN_JOURNAL：只读查询日志状态。返回字段字典。"""
    _require_windows()
    out = (ctypes.c_ubyte * ctypes.sizeof(USN_JOURNAL_DATA_V2))()
    _device_io_control(handle, FSCTL_QUERY_USN_JOURNAL, None, out)
    data = USN_JOURNAL_DATA_V2.from_buffer(out)
    return {
        'usn_journal_id': data.UsnJournalID,
        'first_usn': data.FirstUsn,
        'next_usn': data.NextUsn,
        'lowest_valid_usn': data.LowestValidUsn,
        'max_usn': data.MaxUsn,
        'maximum_size': data.MaximumSize,
        'allocation_delta': data.AllocationDelta,
        'min_supported_major': data.MinSupportedMajorVersion,
        'max_supported_major': data.MaxSupportedMajorVersion,
    }


def enum_usn_chunk(handle, start_ref: int = 0, buffer_size: int = 65536) -> bytes:
    """FSCTL_ENUM_USN_DATA：枚举 MFT 的一段记录，返回原始缓冲区。"""
    _require_windows()
    inp = MFT_ENUM_DATA_V1()
    inp.StartFileReferenceNumber = start_ref
    inp.LowUsn = 0
    inp.HighUsn = (1 << 63) - 1  # USN 为有符号值；全 1 在系统端被解释为 -1。
    inp.MinMajorVersion = 2
    inp.MaxMajorVersion = 3
    out = (ctypes.c_ubyte * buffer_size)()
    n = _device_io_control(handle, FSCTL_ENUM_USN_DATA, inp, out)
    return bytes(out[:n])


def read_usn_journal_chunk(handle, start_usn: int, journal_id: int, *,
                           reason_mask: int = 0xFFFFFFFF,
                           timeout_ms: int = 0, bytes_to_wait: int = 0,
                           buffer_size: int = 65536, max_major_version: int = 3):
    """FSCTL_READ_USN_JOURNAL：从游标读一段。返回 (raw_bytes, next_usn)。"""
    _require_windows()
    inp = READ_USN_JOURNAL_DATA_V1()
    inp.StartUsn = start_usn
    inp.ReasonMask = reason_mask
    inp.ReturnOnlyOnClose = 0
    inp.Timeout = max(0, (int(timeout_ms) + 999) // 1000)  # Windows 接口单位为秒。
    inp.BytesToWaitFor = bytes_to_wait
    inp.UsnJournalID = journal_id
    inp.MinMajorVersion = 2
    inp.MaxMajorVersion = int(max_major_version)
    out = (ctypes.c_ubyte * buffer_size)()
    n = _device_io_control(handle, FSCTL_READ_USN_JOURNAL, inp, out)
    raw = bytes(out[:n])
    # 输出缓冲区前 8 字节是下一个 USN；空返回时回退到输入结构回写的 StartUsn
    if len(raw) >= 8:
        next_usn = int.from_bytes(raw[0:8], 'little')
    else:
        next_usn = inp.StartUsn
    return raw, next_usn


def parse_usn_buffer(buf: bytes, max_records: int = 100000, start_offset: int = 0):
    """解析 USN 记录缓冲区。返回 (records, stats)。

    设备输出缓冲区前 8 字节是下一个 USN（见 MS「Walking a Buffer of Change
    Journal Records」），调用方应传 start_offset=8；合成记录测试可传 0。
    按 RecordLength 推进；校验截断、长度边界与 UTF-16 字节对齐；
    未知 major version（如 V4 range-tracking 记录）计入 unsupported 并跳过。
    """
    records = []
    stats = {'truncated': 0, 'unsupported': 0, 'bad_length': 0}
    offset = start_offset
    n = len(buf)
    while offset + 8 <= n:
        record_length = int.from_bytes(buf[offset:offset + 4], 'little')
        if record_length < 8 or offset + record_length > n:
            stats['truncated'] += 1
            break
        major = int.from_bytes(buf[offset + 4:offset + 6], 'little')
        if major == 2:
            header = _USN_V2_HEADER
        elif major == 3:
            header = _USN_V3_HEADER
        else:
            stats['unsupported'] += 1
            offset += record_length
            continue
        if record_length < header:
            stats['bad_length'] += 1
            offset += record_length
            continue
        fname_len = int.from_bytes(
            buf[offset + header - 4:offset + header - 2], 'little')
        fname_off = int.from_bytes(
            buf[offset + header - 2:offset + header], 'little')
        if fname_len % 2 != 0 or fname_off % 2 or fname_off < header or fname_off + fname_len > record_length:
            stats['bad_length'] += 1
            offset += record_length
            continue
        if major == 2:
            file_ref = int.from_bytes(buf[offset + 8:offset + 16], 'little')
            parent_ref = int.from_bytes(buf[offset + 16:offset + 24], 'little')
        else:
            file_ref = int.from_bytes(buf[offset + 8:offset + 24], 'little')
            parent_ref = int.from_bytes(buf[offset + 24:offset + 40], 'little')
        usn = int.from_bytes(buf[offset + header - 36:offset + header - 28], 'little')
        timestamp = int.from_bytes(buf[offset + header - 28:offset + header - 20], 'little')
        reason = int.from_bytes(buf[offset + header - 20:offset + header - 16], 'little')
        attributes = int.from_bytes(buf[offset + header - 8:offset + header - 4], 'little')
        raw_name = buf[offset + fname_off:offset + fname_off + fname_len]
        name = raw_name.decode('utf-16-le', errors='surrogatepass')
        records.append({
            'usn': usn,
            'file_ref': file_ref,
            'parent_ref': parent_ref,
            'timestamp': timestamp,
            'reason': reason,
            'attributes': attributes,
            'name': name,
            'is_dir': bool(attributes & FILE_ATTRIBUTE_DIRECTORY),
            'major_version': major,
        })
        offset += record_length
        if len(records) >= max_records:
            if offset < n:
                stats['truncated'] = max(stats['truncated'], 1)
            break
    if offset < n and n - offset < 8:
        stats['truncated'] += 1
    return records, stats


def fileref_to_str(ref) -> str:
    """文件引用转稳定字符串（V2 为 64 位整数，V3 为 128 位整数）。"""
    return f'{ref:032x}'


def error_hint(exc) -> str:
    """把权限/异常归一到可读原因。"""
    if isinstance(exc, PermissionRequired):
        return 'permission_required'
    if isinstance(exc, UnsupportedError):
        return 'unsupported'
    if isinstance(exc, JournalExpired):
        return 'journal_expired'
    return 'error'
