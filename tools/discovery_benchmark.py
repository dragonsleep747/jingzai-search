# -*- coding: utf-8 -*-
"""discovery_benchmark：在独立数据库生成 ≥10 万条合成目录记录并输出统计。

用法：
    python tools/discovery_benchmark.py
    python tools/discovery_benchmark.py --records 150000
"""
from __future__ import annotations

import argparse
import gc
import os
import platform
import sqlite3
import sys
import tempfile
import time
import tracemalloc

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_ROOT, 'search_app'))

from discovery import DiscoveryService, SearchRequest  # noqa: E402
from discovery.catalog import Catalog, casefold_name, normcase  # noqa: E402

_BATCH = 5000


def _fmt(ms):
    return f'{ms:.2f} ms' if ms < 1000 else f'{ms / 1000:.2f} s'


def build_synthetic(catalog: Catalog, source_id: str, total: int):
    """生成合成目录记录：dirs + files，分批写入，模拟扫描批量提交。"""
    files_per_dir = 20
    dirs_per_slice = 200
    t0 = time.perf_counter()
    batch = []
    generated = 0
    idx = 0
    while generated < total:
        a = idx // dirs_per_slice
        b = idx % dirs_per_slice
        d = f'D:\\合成\\目录{a:03d}\\子{b:03d}'
        batch.append({
            'source_id': source_id, 'parent_path': f'D:\\合成\\目录{a:03d}',
            'name': f'子{b:03d}', 'path': d, 'match_name': f'子{b:03d}',
            'match_path': normcase(d), 'ext': '', 'kind': 'dir',
            'size': None, 'mtime_ns': 1700000000000000000 + generated,
            'volume_serial': 0x1000, 'file_id': generated + 1,
            'is_dir': 1, 'scan_generation': 1,
        })
        generated += 1
        if generated >= total:
            break
        for c in range(files_per_dir):
            if generated >= total:
                break
            name = f'文件{c:03d}_{a:03d}_{b:03d}.txt'
            p = f'{d}\\{name}'
            batch.append({
                'source_id': source_id, 'parent_path': d, 'name': name,
                'path': p, 'match_name': casefold_name(name),
                'match_path': normcase(p), 'ext': 'txt', 'kind': 'file',
                'size': 1024 + c, 'mtime_ns': 1700000000000000000 + generated,
                'volume_serial': 0x1000, 'file_id': generated + 1,
                'is_dir': 0, 'scan_generation': 1,
            })
            generated += 1
        idx += 1
        if len(batch) >= _BATCH:
            catalog.upsert_entries(batch)
            batch = []
    if batch:
        catalog.upsert_entries(batch)
    return time.perf_counter() - t0


def main() -> int:
    parser = argparse.ArgumentParser(description='镜仔目录发现：合成基准')
    parser.add_argument('--records', type=int, default=100000)
    parser.add_argument('--catalog', default=os.path.join(
        _ROOT, '.tmp', 'discovery-prework', 'benchmark.db'))
    args = parser.parse_args()

    from pathlib import Path
    if os.path.exists(args.catalog):
        parser.error('catalog already exists; choose a fresh output path')
    Path(args.catalog).resolve().parent.mkdir(parents=True, exist_ok=True)
    if hasattr(platform, '_wmi_query'):
        def native_version(*args):
            raise OSError('Use native Windows version APIs')
        platform._wmi_query = native_version

    svc = DiscoveryService(args.catalog)
    from discovery.model import SourceInfo
    svc.catalog.register_source(SourceInfo(
        source_id='bench:syn', kind='directory', root='D:\\合成',
        provider='generic', state='complete'))

    print('=' * 64)
    print('镜仔目录发现 · 合成记录基准')
    print('=' * 64)
    print(f'硬件平台：{platform.platform()}')
    print(f'Python：{sys.version.split()[0]}  '
          f'SQLite：{sqlite3.sqlite_version}')
    try:
        conn = sqlite3.connect(':memory:')
        fts = bool(conn.execute(
            'SELECT sqlite_compileoption_used("ENABLE_FTS5")').fetchone()[0])
        conn.close()
    except sqlite3.Error:
        fts = False
    print(f'FTS5 编译选项：{"已启用" if fts else "未启用"}（本目录库不使用 FTS）')
    print(f'目标记录数：{args.records}')

    tracemalloc.start()
    gc.collect()
    mem_before = tracemalloc.get_traced_memory()[0]

    t0 = time.perf_counter()
    build_synthetic(svc.catalog, 'bench:syn', args.records)
    build_sec = time.perf_counter() - t0

    gc.collect()
    mem_peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()

    count = svc.catalog.count_entries('bench:syn')
    db_size = os.path.getsize(args.catalog)
    print('-' * 64)
    print(f'实际写入条目：{count}')
    print(f'数据库大小：{db_size / 1024 / 1024:.2f} MiB')
    print(f'批量写入耗时：{_fmt(build_sec * 1000)}（批次 {_BATCH} 条）')
    print(f'写入吞吐：{count / build_sec:,.0f} 条/秒')
    print(f'Python 内存峰值（tracemalloc）：{mem_peak / 1024 / 1024:.1f} MiB')

    # 首批结果 / 冷热查询 / 分页
    def query(q, **kw):
        started = time.perf_counter()
        page = svc.search_catalog(SearchRequest(query=q, **kw))
        return page, (time.perf_counter() - started) * 1000

    page, cold1 = query('文件000_000_000')
    print('-' * 64)
    print(f'名称查询（冷，首次连接）：{_fmt(cold1)}  命中 {len(page.results)}')
    _, cold2 = query('不存在的中文关键词xyz')
    print(f'名称查询（冷，未命中）：{_fmt(cold2)}  命中 0')
    hot = []
    for _ in range(5):
        _, ms = query('文件000_000_000')
        hot.append(ms)
    print(f'名称查询（热，5 次）：min {_fmt(min(hot))} / '
          f'avg {_fmt(sum(hot) / len(hot))}')

    _, pag = query('文件', limit=20, offset=0)
    print(f'分页（首页 limit=20）：{_fmt(pag)}')
    _, pag2 = query('文件', limit=20, offset=90000)
    print(f'分页（深页 offset=90000）：{_fmt(pag2)}')

    page_ext, _ = query('', ext=['txt'], limit=20)
    print(f'扩展名筛选（txt 首页）：{len(page_ext.results)} 条')

    # 取消响应（真实目录扫描，与合成库分开）
    print('-' * 64)
    tmp = tempfile.mkdtemp(prefix='discan-cancel-')
    try:
        for i in range(3000):
            with open(os.path.join(tmp, f'f{i:05d}.txt'), 'w') as f:
                f.write('x')
        import threading
        from discovery.model import SourceInfo as SI
        from discovery.scan import scan_source
        stop = threading.Event()
        calls = []

        def prog(p):
            calls.append(p)
            if len(calls) >= 3:
                stop.set()

        t0 = time.perf_counter()
        rep = scan_source(svc.catalog, SI(source_id='bench:real', kind='directory',
                                          root=tmp, provider='generic'),
                          cancel=stop.is_set, progress=prog)
        cancel_sec = time.perf_counter() - t0
        print(f'真实扫描取消响应：{_fmt(cancel_sec * 1000)} '
              f'(stopped={rep.stopped}, found={rep.found})')
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

    print('=' * 64)
    print('注意：以上为合成记录结果，不代表真实全盘扫描速度；'
          '真实扫描见 demo 与 VALIDATION.md。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
