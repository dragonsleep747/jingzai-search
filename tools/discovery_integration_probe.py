# -*- coding: utf-8 -*-
"""当次重新探测本机卷与 USN 可读性，输出 JSON（不入库、不扫描全盘）。

用法：
    python tools/discovery_integration_probe.py --out .tmp/discovery-integration/native-probe.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_ROOT, 'search_app'))

from discovery import DiscoveryService  # noqa: E402
from discovery import probe  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', default=os.path.join(
        _ROOT, '.tmp', 'discovery-integration', 'native-probe.json'))
    parser.add_argument('--max-bounded', type=int, default=1000)
    args = parser.parse_args()

    out = {
        'probed_at': time.time(),
        'volumes': [],
        'bounded_enum': {},
        'note': 'read-only probe; no full-disk scan; no name export',
    }
    volumes = probe.list_volumes()
    for info in volumes:
        item = {
            'source_id': info.source_id,
            'root': info.root,
            'filesystem': info.filesystem,
            'volume_serial': info.volume_serial,
            'volume_guid': info.volume_guid,
        }
        caps = probe.probe_source(info)
        item.update({
            'provider': caps.provider,
            'native_usn_readable': caps.native_usn_readable,
            'reason': caps.reason,
        })
        out['volumes'].append(item)

    svc = DiscoveryService(os.path.join(
        os.path.dirname(args.out), 'probe-catalog.db'))
    for vid in svc.add_volume_sources():
        caps = svc.probe_source(vid)
        if caps.native_usn_readable:
            bounded = svc.bounded_usn_enum(vid, max_records=args.max_bounded)
            if bounded.get('supported'):
                out['bounded_enum'] = bounded
                # 不导出整盘文件名：仅统计。
                break

    Path = __import__('pathlib').Path
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, 'w', encoding='utf-8') as stream:
        json.dump(out, stream, ensure_ascii=False, indent=2)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
