# -*- coding: utf-8 -*-
"""discovery_probe：查看卷与可用通道，不启动全盘扫描。

用法：
    python tools/discovery_probe.py
    python tools/discovery_probe.py --catalog .tmp/discovery-prework/probe.db
"""
from __future__ import annotations

import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_ROOT, 'search_app'))

from discovery import DiscoveryService  # noqa: E402
from discovery import probe  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description='镜仔目录发现：卷与通道探测')
    parser.add_argument('--catalog', default=os.path.join(
        _ROOT, '.tmp', 'discovery-prework', 'probe.db'))
    args = parser.parse_args()

    from pathlib import Path
    Path(args.catalog).resolve().parent.mkdir(parents=True, exist_ok=True)
    svc = DiscoveryService(args.catalog)

    volumes = probe.list_volumes()
    print(f'逻辑卷：{len(volumes)} 个（固定盘/可移动盘）')
    for info in volumes:
        print(f'  {info.source_id:8s} {info.root:4s} fs={info.filesystem} '
              f'serial={info.volume_serial} guid={info.volume_guid}')
    if not volumes:
        print('  （当前非 Windows，无卷列表）')

    # 登记卷来源（写入任务输出库，不触碰正式配置/索引）
    ids = svc.add_volume_sources()
    for sid in ids:
        caps = svc.probe_source(sid)
        print(f'\n[{sid}] provider={caps.provider} '
              f'available={caps.provider_available} '
              f'native_usn_readable={caps.native_usn_readable}')
        print(f'    reason={caps.reason}')

    print('\n说明：本工具只做只读探测，不创建/删除 USN 日志、不扫描全盘。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
