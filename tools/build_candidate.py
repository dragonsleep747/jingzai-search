# -*- coding: utf-8 -*-
"""Build the discovery candidate EXE (onedir) into dist/discovery-candidate.

Pure-ASCII source (no Chinese literals in code paths): the spec filename is
spelled with unicode escapes so Windows PowerShell 5.1 never has to parse a
UTF-8 .ps1. WMI is bypassed exactly like tools/qt_package.py.
"""
import hashlib
import json
import os
import platform
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEARCH_APP = ROOT / 'search_app'
DIST = ROOT / 'dist' / 'discovery-candidate'
WORK = ROOT / 'build' / 'discovery-candidate'
SPEC_NAME = '\u955c\u4ed4\u641c\u7d22\u65b0\u7248.spec'  # 镜仔搜索新版.spec
EXE_NAME = '\u955c\u4ed4\u641c\u7d22\u65b0\u7248.exe'      # 镜仔搜索新版.exe
SUPPORT = ROOT / 'tools' / 'qt_build_support'


def _skip_wmi(*args):
    raise OSError('Packaging uses native Windows version APIs instead of WMI')


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest().upper()


def main():
    if hasattr(platform, '_wmi_query'):
        platform._wmi_query = _skip_wmi
    if '--manifest-only' in sys.argv:
        raise SystemExit('Refusing to attest current sources against an existing binary; rebuild the candidate.')
    if '--manifest-only' not in sys.argv:
        os.environ['PYTHONPATH'] = str(SUPPORT) + os.pathsep + os.environ.get('PYTHONPATH', '')
        os.chdir(str(SEARCH_APP))
        from PyInstaller.__main__ import run
        run(['--noconfirm', '--clean', '--distpath', str(DIST), '--workpath', str(WORK), SPEC_NAME])

    exe_folder = DIST / EXE_NAME[:-4]
    exe = exe_folder / EXE_NAME
    if not exe.is_file():
        print('BUILD_FAILED: candidate exe missing', file=sys.stderr)
        return 1

    manifest = {
        'built_at': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
        'candidate_exe': str(exe),
        'candidate_exe_sha256': sha256(exe),
        'candidate_exe_bytes': exe.stat().st_size,
        'entry_sha256': sha256(SEARCH_APP / 'qt_main.py'),
        'spec_sha256': sha256(SEARCH_APP / SPEC_NAME),
        'build_script_sha256': sha256(Path(__file__)),
        'sources': {},
    }
    for folder in ('discovery', 'qt_frontend'):
        for path in sorted((SEARCH_APP / folder).glob('*.py')):
            manifest['sources'][str(path.relative_to(ROOT))] = {
                'sha256': sha256(path), 'bytes': path.stat().st_size,
            }
    for rel in ('search_app/qt_main.py', 'search_app/main.py',
                'tools/discovery_benchmark.py', 'tools/discovery_demo.py',
                'tools/discovery_probe.py', 'tools/discovery_integration_e2e.py',
                'tools/discovery_integration_probe.py'):
        path = ROOT / rel
        if path.is_file():
            manifest['sources'][rel] = {'sha256': sha256(path), 'bytes': path.stat().st_size}

    out = exe_folder / 'build-manifest.json'
    out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    print('BUILD_OK')
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
