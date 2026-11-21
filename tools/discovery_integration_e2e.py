# -*- coding: utf-8 -*-
"""discovery_integration_e2e：真实样本端到端增量 + F: 原生有界能力验证。

全部输出写入隔离目录；不触碰正式 index.db/配置；不复制/删除用户文件。
用法：
    python tools/discovery_integration_e2e.py --out .tmp/discovery-integration/e2e.json
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
import uuid

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_ROOT, 'search_app'))

from discovery.scheduler import DiscoveryController  # noqa: E402
from discovery.model import SearchRequest  # noqa: E402
from discovery.native_scan import NativeIndexBuilder  # noqa: E402
from discovery.model import SourceInfo  # noqa: E402


def generic_e2e(workdir: str) -> dict:
    sample = os.path.join(workdir, 'sample')
    os.makedirs(os.path.join(sample, '资料'), exist_ok=True)
    os.makedirs(os.path.join(sample, '资料备份'), exist_ok=True)
    write = lambda p, t: open(p, 'w', encoding='utf-8').write(t)
    write(os.path.join(sample, '资料', '安全生产法.txt'), '样本正文，不参与目录发现')
    write(os.path.join(sample, '资料', 'JGJ130-2011 脚手架规范.pdf'), '%PDF-1.4 样本')
    write(os.path.join(sample, '资料备份', '边界.txt'), '不应出现在 资料 检索')

    controller = DiscoveryController(os.path.join(workdir, 'catalog.db'),
                                     os.path.join(workdir, 'discovery.json'))
    sid = controller.add_directory(os.path.join(sample, '资料'))
    report = controller.start_scan(sid)
    assert report['state'] == 'complete', report

    def names(query, **kw):
        page = controller.search(SearchRequest(query=query, limit=50, **kw))
        return [r['name'] for r in page.results]

    first = {q: names(q) for q in ('安全', '脚手架', 'JGJ')}
    boundary = names('', root=os.path.join(sample, '资料'))
    assert all('资料备份' not in p for p in boundary), '根目录边界失效'

    # 更名 + 增量（真实重扫）
    old = os.path.join(sample, '资料', '安全生产法.txt')
    new = os.path.join(sample, '资料', '安全生产管理办法.txt')
    os.rename(old, new)
    controller.start_scan(sid)
    after_rename = names('管理办法')
    old_left = names('安全生产法.txt')
    assert after_rename and not old_left, ('更名增量失败', after_rename, old_left)

    # 删除 + 增量
    gone = os.path.join(sample, '资料', 'JGJ130-2011 脚手架规范.pdf')
    os.unlink(gone)
    controller.start_scan(sid)
    deleted_left = names('JGJ130')
    assert not deleted_left, '删除增量失败'

    # 移入 + 增量
    moved_in = os.path.join(sample, '资料', '新移入.txt')
    write(moved_in, 'x')
    controller.start_scan(sid)
    move_in_found = '新移入.txt' in names('新移入')
    assert move_in_found, '移入增量失败'

    coverage = controller.get_coverage()
    controller.shutdown()
    return {
        'first_queries': first,
        'rename': {'old_gone': not old_left, 'new_found': bool(after_rename)},
        'delete': {'gone': not deleted_left},
        'move_in': {'found': move_in_found},
        'coverage': coverage,
        'catalog_bytes': os.path.getsize(os.path.join(workdir, 'catalog.db')),
    }


def native_bounded(workdir: str) -> dict:
    controller = DiscoveryController(os.path.join(workdir, 'native-catalog.db'),
                                     os.path.join(workdir, 'native.json'))
    ids = controller.ensure_volume_sources()
    for source in controller.service.list_sources():
        if source['kind'] == 'volume' and source['root'].upper().startswith('F:'):
            caps = controller.service.probe_source(source['source_id'])
            if not caps.native_usn_readable:
                return {'root': source['root'], 'native_usn_readable': False, 'reason': caps.reason}
            info = SourceInfo(**{k: source[k] for k in
                                 ('source_id', 'kind', 'root', 'volume_serial',
                                  'volume_guid', 'filesystem', 'provider')})
            builder = NativeIndexBuilder(controller.catalog, info)
            started = time.perf_counter()
            report = builder.build(limit_records=1000)
            report['elapsed_ms'] = (time.perf_counter() - started) * 1000
            report['root'] = source['root']
            controller.shutdown()
            return report
    controller.shutdown()
    return {'error': 'F: volume not found'}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', default=os.path.join(
        _ROOT, '.tmp', 'discovery-integration', 'e2e.json'))
    parser.add_argument('--skip-native', action='store_true')
    args = parser.parse_args()
    workdir = os.path.join(os.path.dirname(args.out), 'e2e-' + uuid.uuid4().hex[:8])
    os.makedirs(workdir, exist_ok=True)
    result = {'workdir': workdir, 'generic_e2e': None, 'native_bounded': None}
    try:
        result['generic_e2e'] = generic_e2e(workdir)
    except Exception as exc:  # noqa: BLE001
        result['generic_e2e'] = {'error': repr(exc)}
    if not args.skip_native:
        try:
            result['native_bounded'] = native_bounded(workdir)
        except Exception as exc:  # noqa: BLE001
            result['native_bounded'] = {'error': repr(exc)}
    with open(args.out, 'w', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
