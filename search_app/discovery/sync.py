# -*- coding: utf-8 -*-
"""同步正确性相关的纯函数：FRN→路径重建、USN 记录→可重放事件。

纯函数便于用合成记录单测；真实 USN 读取由 provider.py 调用。
"""
from __future__ import annotations

import os
from collections import deque

def reference(value):
    value = int(value)
    return value + (1 << 64) if value < 0 else value
from .model import ChangeEvent

# 根文件引用（NTFS MFT 根目录固定为 5）
_NTFS_ROOT_FRN = 5


def build_path_map(records, root='', root_ref=None) -> dict:
    """由 USN 枚举记录重建 FRN→全路径映射（目录与文件）。

    records: 每项含 file_ref, parent_ref, name, is_dir。
    返回 {file_ref: full_path}。无法解析的 FRN 不进入映射（调用方计入缺口）。
    """
    records = list(records)
    children = {}
    names = {}
    is_dir = {}
    for r in records:
        fr, pr, name = r['file_ref'], r['parent_ref'], r['name']
        names[fr] = name
        is_dir[fr] = r['is_dir']
        children.setdefault(pr, []).append(fr)

    paths = {}
    # 根：file_ref==parent_ref 或 file_ref==NTFS 根
    queue = deque()
    if root_ref is not None:
        paths[root_ref] = root
        queue.append(root_ref)
    for fr, pr in set((r['file_ref'], r['parent_ref']) for r in records):
        if fr == pr or (fr & ((1 << 48) - 1)) == _NTFS_ROOT_FRN:
            paths[fr] = root
            queue.append(fr)
    # BFS：只有父路径已知才可解析子路径
    while queue:
        parent = queue.popleft()
        base = paths.get(parent, '')
        for fr in children.get(parent, []):
            if fr in paths:
                continue
            name = names.get(fr)
            if name is None:
                continue
            paths[fr] = os.path.join(base, name) if base else name
            if is_dir.get(fr):
                queue.append(fr)
    return paths


def convert_usn_records_to_events(records, frn_to_path):
    """把一段 USN 记录转成 ChangeEvent 序列（可重放）。

    frn_to_path：FRN→路径映射（含文件与目录），随事件更新以支持同批内
    新建目录后的子项定位。返回 (events, stats)。
    """
    from .usn import (
        USN_REASON_FILE_CREATE, USN_REASON_FILE_DELETE,
        USN_REASON_RENAME_NEW_NAME, USN_REASON_RENAME_OLD_NAME,
        USN_REASON_DATA_OVERWRITE, USN_REASON_DATA_EXTEND,
        USN_REASON_DATA_TRUNCATION, USN_REASON_BASIC_INFO_CHANGE,
        USN_REASON_HARD_LINK_CHANGE,
    )
    frn_map = {reference(key): value for key, value in frn_to_path.items()}
    events = []
    stats = {'rename_old_skipped': 0, 'unresolved_path': 0, 'hard_link': 0}

    def parent_path(pr):
        pr = reference(pr)
        return frn_map.get(pr)

    for r in records:
        reason = r['reason']
        fr = reference(r['file_ref'])
        name = r['name']
        cur = frn_map.get(fr)
        parent = parent_path(r['parent_ref'])

        if reason & USN_REASON_FILE_DELETE:
            if cur is not None:
                events.append(ChangeEvent(
                    source_id='', seq=r['usn'], change_type='deleted',
                    old_parent_path=os.path.dirname(cur),
                    old_name=os.path.basename(cur)))
                frn_map.pop(fr, None)
            else:
                stats['unresolved_path'] += 1
            continue

        if reason & USN_REASON_RENAME_NEW_NAME:
            if cur is not None and parent is not None:
                new_path = os.path.join(parent, name) if parent else name
                events.append(ChangeEvent(
                    source_id='', seq=r['usn'], change_type='renamed',
                    old_parent_path=os.path.dirname(cur),
                    new_parent_path=parent,
                    old_name=os.path.basename(cur), new_name=name))
                frn_map[fr] = new_path
                if r['is_dir']:
                    prefix = cur.rstrip('\\/') + os.sep
                    for key, path in list(frn_map.items()):
                        if path and path.startswith(prefix):
                            frn_map[key] = os.path.join(new_path, path[len(prefix):])
                if reason & (USN_REASON_DATA_OVERWRITE | USN_REASON_DATA_EXTEND | USN_REASON_DATA_TRUNCATION):
                    events.append(ChangeEvent(source_id='', seq=r['usn'], change_type='modified',
                        old_parent_path=os.path.dirname(new_path), old_name=os.path.basename(new_path), content_version=1))
            else:
                stats['unresolved_path'] += 1
            continue
        if reason & USN_REASON_RENAME_OLD_NAME:
            stats['rename_old_skipped'] += 1
            continue

        if reason & USN_REASON_FILE_CREATE:
            if parent is not None:
                new_path = os.path.join(parent, name) if parent else name
                events.append(ChangeEvent(
                    source_id='', seq=r['usn'], change_type='created',
                    new_parent_path=parent, new_name=name))
                frn_map[fr] = new_path
            else:
                stats['unresolved_path'] += 1
            continue

        if reason & USN_REASON_HARD_LINK_CHANGE:
            stats['hard_link'] += 1
            # 硬链接增删需要重扫才能可靠跟踪多路径，列为缺口
            continue

        if reason & (USN_REASON_DATA_OVERWRITE | USN_REASON_DATA_EXTEND
                     | USN_REASON_DATA_TRUNCATION):
            if cur is not None:
                events.append(ChangeEvent(
                    source_id='', seq=r['usn'], change_type='modified',
                    old_parent_path=os.path.dirname(cur),
                    old_name=os.path.basename(cur), content_version=1))
            else:
                stats['unresolved_path'] += 1
            continue

        if reason & USN_REASON_BASIC_INFO_CHANGE:
            if cur is not None:
                events.append(ChangeEvent(
                    source_id='', seq=r['usn'], change_type='metadata',
                    old_parent_path=os.path.dirname(cur),
                    old_name=os.path.basename(cur)))
            continue
    frn_to_path.clear()
    frn_to_path.update(frn_map)
    return events, stats
