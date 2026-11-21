# -*- coding: utf-8 -*-
"""discovery_demo：对任务生成的样本目录建 catalog.db，查询中文名，演示更名与增量。

用法：
    python tools/discovery_demo.py
    python tools/discovery_demo.py --sample .tmp/discovery-prework/sample --catalog .tmp/discovery-prework/demo.db
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_ROOT, 'search_app'))

from discovery import DiscoveryService, SearchRequest  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description='镜仔目录发现：demo')
    parser.add_argument('--sample', default=os.path.join(
        _ROOT, '.tmp', 'discovery-prework', 'sample'))
    parser.add_argument('--catalog', default=os.path.join(
        _ROOT, '.tmp', 'discovery-prework', 'demo.db'))
    args = parser.parse_args()

    # 每次使用独立样本子目录；永不删除调用方传入的目录或数据库。
    from pathlib import Path
    import uuid
    sample = os.path.join(os.path.abspath(args.sample), 'run-' + uuid.uuid4().hex[:12])
    if os.path.exists(args.catalog):
        parser.error('catalog already exists; choose a fresh output path')
    Path(args.catalog).resolve().parent.mkdir(parents=True, exist_ok=True)
    os.makedirs(os.path.join(sample, '资料'), exist_ok=True)
    os.makedirs(os.path.join(sample, '资料备份'), exist_ok=True)
    with open(os.path.join(sample, '资料', '安全生产法.txt'), 'w',
              encoding='utf-8') as f:
        f.write('样本正文（不参与目录发现的内容解析）')
    with open(os.path.join(sample, '资料', 'JGJ 130-2011 脚手架规范.pdf'), 'w',
              encoding='utf-8') as f:
        f.write('%PDF-1.4 样本')
    with open(os.path.join(sample, '资料备份', '无关.txt'), 'w',
              encoding='utf-8') as f:
        f.write('边界测试：不应出现在 资料 的检索里')

    svc = DiscoveryService(args.catalog)
    sid = svc.add_directory_source(os.path.join(sample, '资料'))
    report = svc.scan_source(sid)
    print(f'扫描：{report.state} found={report.found} dirs={report.dirs} '
          f'failed={report.failed} gen={report.generation}')

    for q in ['安全', '脚手架', 'JGJ']:
        page = svc.search_catalog(SearchRequest(query=q, limit=20))
        print(f'查询[{q}] -> {len(page.results)} 条：'
              f'{[r["name"] for r in page.results]}')

    page = svc.search_catalog(SearchRequest(query='', root=os.path.join(
        sample, '资料'), limit=20))
    print(f'根目录边界 {os.path.join(sample, "资料")} -> '
          f'{[r["name"] for r in page.results]}')
    assert all('资料备份' not in r['path'] for r in page.results), \
        '根目录边界失效：匹配到了 资料备份'

    # 更名 + 增量
    old = os.path.join(sample, '资料', '安全生产法.txt')
    new = os.path.join(sample, '资料', '安全生产管理办法.txt')
    os.rename(old, new)
    svc.scan_source(sid)
    page = svc.search_catalog(SearchRequest(query='管理办法', limit=20))
    print(f'更名后查询[管理办法] -> {[r["name"] for r in page.results]}')
    page_old = svc.search_catalog(SearchRequest(query='安全生产法.txt', limit=20))
    print(f'旧名残留 -> {len(page_old.results)} 条')
    assert len(page.results) and len(page_old.results) == 0, '更名增量更新不正确'

    cov = svc.get_coverage()
    print(f'coverage -> {cov.to_dict()}')
    print(f'catalog.db -> {args.catalog}（{os.path.getsize(args.catalog)} 字节）')
    return 0


if __name__ == '__main__':
    sys.exit(main())
