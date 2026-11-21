"""Compare the actual bundled Python code with current sources, ignoring paths."""
import argparse
import json
from pathlib import Path
import sys
import types

from PyInstaller.archive.readers import CArchiveReader


def normalized(code):
    return code.replace(co_filename='', co_consts=tuple(
        normalized(value) if isinstance(value, types.CodeType) else value for value in code.co_consts))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--bundle', required=True)
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    bundle = Path(args.bundle)
    archive = CArchiveReader(str(bundle / '镜仔搜索新版.exe'))
    pyz_name = next(name for name in archive.toc if name.lower().endswith('.pyz'))
    pyz = archive.open_embedded_archive(pyz_name)
    checks, absent = {}, []
    for path in sorted((root / 'search_app').rglob('*.py')):
        relative = path.relative_to(root / 'search_app')
        if len(relative.parts) > 1 and relative.parts[0] not in ('qt_frontend', 'discovery'):
            continue
        parts = list(relative.with_suffix('').parts)
        if parts[-1] == '__init__':
            parts.pop()
        name = '.'.join(parts)
        if not name or name not in pyz.toc:
            absent.append(name)
            continue
        current = compile(path.read_bytes(), str(path), 'exec', dont_inherit=True, optimize=0)
        frozen = pyz.extract(name)
        checks[name] = normalized(current) == normalized(frozen)
    report = dict(ok=bool(checks) and all(checks.values()) and checks.get('export', False),
                  checked=len(checks), checks=checks, absent_from_pyz=absent)
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=True))
    return int(not report['ok'])


if __name__ == '__main__':
    sys.exit(main())
