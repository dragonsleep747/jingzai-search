# -*- coding: utf-8 -*-
"""
镜仔搜索 · 文件类型与编码识别模块
Copyright (c) 2026 SleepDragon
SPDX-License-Identifier: MIT
"""

"""编码与文件类型自识别"""
import os
import zipfile
import chardet

# 文件头 magic bytes → 类型
MAGIC = {
    b'\x50\x4b\x03\x04': 'zip',       # Office 新格式 / zip
    b'\x25\x50\x44\x46': 'pdf',       # %PDF
    b'\xd0\xcf\x11\xe0': 'ole',       # 老 Office 二进制
    b'\x1f\x8b': 'gzip',
    b'\x52\x61\x72\x21': 'rar',
    b'\x37\x7a\xbc\xaf\x27\x1c': '7z',
}

# 可直接按文本读取的扩展名
TEXT_EXTS = {
    '.txt', '.md', '.log', '.csv', '.tsv', '.json', '.xml', '.html', '.htm',
    '.ini', '.yaml', '.yml', '.toml', '.py', '.js', '.ts', '.java', '.c',
    '.cpp', '.h', '.hpp', '.cs', '.go', '.rs', '.rb', '.php', '.sh', '.bat',
    '.ps1', '.sql', '.vue', '.jsx', '.tsx', '.conf', '.cfg', '.properties',
}

# Office 新格式容器内特征文件 → 真实类型
OFFICE_MARKER = {
    'word/document.xml': 'docx',
    'xl/workbook.xml':   'xlsx',
    'ppt/presentation.xml': 'pptx',
}


def detect_type(path, ext):
    """优先按文件头识别，回落到扩展名"""
    try:
        with open(path, 'rb') as f:
            head = f.read(8)
    except Exception:
        return 'unknown'
    for sig, t in MAGIC.items():
        if head.startswith(sig):
            return t
    if ext.lower() in TEXT_EXTS:
        return 'text'
    return 'unknown'


def detect_zip_kind(path):
    """
    对 zip 容器进一步识别真实类型：
    返回 'docx' / 'xlsx' / 'pptx' / 'zip'（纯压缩包）
    通过查看 zip 内特征文件路径判断。
    """
    try:
        with zipfile.ZipFile(path, 'r') as zf:
            names = set(zf.namelist())
            for marker, kind in OFFICE_MARKER.items():
                if any(n.startswith(marker.split('/')[0] + '/') and n == marker
                       for n in names):
                    return kind
            # 兜底：按扩展名
            ext = os.path.splitext(path)[1].lower()
            if ext == '.docx':
                return 'docx'
            if ext == '.xlsx':
                return 'xlsx'
            if ext == '.pptx':
                return 'pptx'
            return 'zip'
    except Exception:
        # 损坏的 zip：按扩展名兜底
        ext = os.path.splitext(path)[1].lower()
        if ext == '.docx':
            return 'docx'
        if ext == '.xlsx':
            return 'xlsx'
        if ext == '.pptx':
            return 'pptx'
        return 'zip'


def detect_encoding(path):
    """返回 (encoding, confidence)"""
    try:
        with open(path, 'rb') as f:
            raw = f.read(65536)
    except Exception:
        return 'utf-8', 0.0
    # BOM 链式 fallback
    if raw.startswith(b'\xef\xbb\xbf'):
        return 'utf-8-sig', 1.0
    if raw[:2] in (b'\xff\xfe', b'\xfe\xff'):
        return 'utf-16', 1.0
    try:
        raw.decode('utf-8')
        return 'utf-8', 1.0
    except UnicodeDecodeError:
        pass
    result = chardet.detect(raw) or {}
    enc = (result.get('encoding') or 'utf-8').lower()
    return enc, result.get('confidence') or 0.0
