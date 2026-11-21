"""核对候选构建来源，并只读比较实际旧资料库的检索结果。"""
import argparse
import hashlib
import json
from pathlib import Path
import sys


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest().upper()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--bundle', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / 'search_app'))
    import searcher
    from qt_frontend.backend import Store
    bundle = Path(args.bundle).resolve()
    manifest = json.loads((bundle / 'build-manifest.json').read_text(encoding='utf-8-sig'))
    checks = {'entry': digest(root / 'search_app' / 'qt_main.py') == manifest['entry_sha256']}
    sources=manifest['sources']
    if isinstance(sources,dict):
        for path,row in sources.items():
            checks[path]=digest(root/path)==row['sha256']
        checks['candidate_exe']=digest(bundle/'镜仔搜索新版.exe')==manifest['candidate_exe_sha256']
    else:
        for row in sources:
            checks[row['file']] = digest(root / 'search_app' / 'qt_frontend' / row['file']) == row['sha256']
    store = Store(root)
    comparisons = []
    for query in ('安全', '培训', '安全 -培训', '名称:制度'):
        expected = searcher.aggregate_results(searcher.search(store.db, query, only_active=True))
        actual = store.search(query, synonyms=False)
        comparisons.append({'query': query, 'equal': actual['groups'] == expected,
                            'files': len(actual['groups']), 'hits': actual['hits']})
    report = {'ok': all(checks.values()) and all(item['equal'] for item in comparisons),
              'source_checks': checks, 'comparisons': comparisons,
              'library_files': store.statistics()['files'],
              'old_exe_sha256': digest(root / '镜仔搜索.exe'),
              'new_exe_sha256': digest(bundle / '镜仔搜索新版.exe'),
              'legacy_bridge_main_sha256': digest(root / 'search_app' / 'main.py')}
    Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=True))
    return int(not report['ok'])


if __name__ == '__main__':
    sys.exit(main())
