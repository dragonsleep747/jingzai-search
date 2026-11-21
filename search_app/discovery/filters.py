# -*- coding: utf-8 -*-
"""排除规则与 LIKE 字面转义。

目录发现层与内容分析层使用**不同**的排除规则：

- 目录层（本模块）：只排除会导致循环/系统内部/写入监听循环的对象，尽量少漏
  文件名。绝不复用 indexer.IGNORE_DIRS（那是正文扫描策略，会排除 dist/build/
  虚拟环境等——目录层如果把 dist 排除，用户就再也找不到 dist 里的文件）。
- 内容层：沿用 indexer.IGNORE_DIRS，由后续分析队列消费。

默认忽略项及覆盖缺口在 ARCHITECTURE.md 中逐条解释。
"""
from __future__ import annotations

import os
import re


def io_path(path):
    """Windows 长路径仅在 IO 边界加前缀，数据库仍保存可读路径。"""
    path = os.fspath(path)
    if os.name != 'nt' or path.startswith('\\\\?\\'):
        return path
    absolute = os.path.abspath(path)
    if len(absolute) < 248:
        return path
    if absolute.startswith('\\\\'):
        return '\\\\?\\UNC\\' + absolute[2:]
    return '\\\\?\\' + absolute


def escape_like(text: str) -> str:
    """把用户输入转成 LIKE 字面量：\\ % _ 全部按字面处理。"""
    return (
        str(text)
        .replace('\\', '\\\\')
        .replace('%', '\\%')
        .replace('_', '\\_')
    )


# ---------------------------------------------------------------------------
# 目录层忽略（本模块使用）
# ---------------------------------------------------------------------------

# 任何深度都忽略的目录名（小写）。只放系统内部目录，避免误伤用户数据。
DIR_LAYER_ALWAYS_IGNORE_DIRS = frozenset({
    '$recycle.bin',
    'system volume information',
    '$windows.~bt',
    '$windows.~ws',
    'winreagent',
    'config.msi',
    'dumps',
})

# 任何深度都忽略的固定文件（小写）。系统页面/休眠/交换文件。
DIR_LAYER_ALWAYS_IGNORE_FILES = frozenset({
    'pagefile.sys',
    'hiberfil.sys',
    'swapfile.sys',
})

# 卷来源在卷根一级才忽略的目录（小写）。例如 C:\\Windows 只跳过卷根的那个。
# 用户显式添加 D:\\Windows 作为目录来源时**不会**命中本规则（kind != volume）。
DIR_LAYER_VOLUME_ROOT_IGNORE_DIRS = frozenset({
    'windows',
    'windows.old',
    'documents and settings',   # junction 指向 Users
    'programdata\\microsoft\\windows',  # 仅按完整相对段匹配，见 path 判断
})

# 目录来源也要在根一级忽略的 Windows 系统目录（相对根的完整段匹配）。
DIR_LAYER_ROOT_IGNORE_SUFFIXES = (
    '\\windows',
    '\\windows.old',
    '\\system volume information',
    '\\$recycle.bin',
)


def should_ignore_dir_name(name: str, is_volume_root: bool) -> bool:
    """按目录名判断是否忽略。is_volume_root=True 时额外套用卷根规则。"""
    lower = (name or '').lower().rstrip('\\/')
    if lower in DIR_LAYER_ALWAYS_IGNORE_DIRS:
        return True
    if is_volume_root and lower in DIR_LAYER_VOLUME_ROOT_IGNORE_DIRS:
        return True
    return False


def should_ignore_dir_root_path(path: str) -> bool:
    """目录来源：判断绝对路径是否命中根级系统目录段（如 D:\\Windows）。"""
    lowered = os.path.normcase(path).rstrip('\\/')
    for suffix in DIR_LAYER_ROOT_IGNORE_SUFFIXES:
        if lowered.endswith(suffix):
            return True
    return False


def should_ignore_file_name(name: str) -> bool:
    return (name or '').lower() in DIR_LAYER_ALWAYS_IGNORE_FILES


# ---------------------------------------------------------------------------
# 内容层忽略（供文档对照，不参与目录发现）
# ---------------------------------------------------------------------------

# 惰性引用 indexer.IGNORE_DIRS：导入本模块不 import indexer（避免拖入
# 文档解析依赖链），只在需要时读取。
def content_layer_ignore_dirs():
    try:
        from indexer import IGNORE_DIRS  # noqa: PLC0415
        return frozenset(IGNORE_DIRS)
    except Exception:
        return frozenset()


# ---------------------------------------------------------------------------
# 目录边界匹配
# ---------------------------------------------------------------------------

def path_within(root: str, path: str) -> bool:
    """path 是否等于 root 或在 root 之下（按组件边界，D:\\资料 ≠ D:\\资料备份）。"""
    root = os.path.normcase(os.path.normpath(root)).rstrip('\\/')
    path = os.path.normcase(os.path.normpath(path))
    if root == '':
        return True
    if path == root:
        return True
    return path.startswith(root + os.sep)


def parse_ext_filter(ext) -> list:
    """扩展名过滤参数归一化：'.txt'/'txt'/['txt','pdf']/'txt,pdf' → ['txt','pdf']。"""
    if ext is None or ext == '' or ext == []:
        return []
    if isinstance(ext, str):
        parts = [p for p in re.split(r'[,;，；]', ext) if p.strip()]
    else:
        parts = list(ext)
    out = []
    for p in parts:
        p = str(p).strip().lstrip('.').lower()
        if p or not isinstance(ext, str):
            out.append(p)
    return list(dict.fromkeys(out))
