# -*- coding: utf-8 -*-
"""合成语料压测：建库吞吐 + 检索延迟（stdlib，可重复运行）。

用法:
    python tools/bench.py [N]          # N = 文件数，默认 2000
"""
import os
import sys
import time
import random
import shutil
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'search_app'))

PHRASES = ('消防安全', '消防验收', '给排水设计', '电气设备', '通风空调',
           '建筑结构', '施工质量', '工程验收', '安全检测', '现场管理')
NOUNS = ('项目', '单位', '部门', '人员', '设备', '材料', '文件', '记录', '报告', '台账')
TEMPLATES = [
    '第{n}条 {ph}管理应满足现行规范的规定，相关{noun}由建设方负责落实。',
    '建设单位在施工中应组织{ph}专项检查，形成{ph}检测{noun}并存档备查。',
    '本{noun}适用于{ph}工程的设计、施工与验收各阶段。',
    '当{noun}不符合{ph}规定时，应立即整改，复查合格后方可继续施工。',
    'int build_{noun}_{n}() {{ return verify_{ph}({n}); }}  // 检测{noun}',
    'def test_{ph}_{n}: assert check("{ph}{noun}") == {n}',
]

RNG = random.Random(20261027)


def gen_line(idx):
    t = RNG.choice(TEMPLATES)
    return t.format(n=idx % 500, ph=RNG.choice(PHRASES), noun=RNG.choice(NOUNS))


def build_corpus(root, n_files, lines_per_file=60):
    for f in range(n_files):
        d = os.path.join(root, f'{f % 100:02d}')
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, f'doc_{f:04d}.txt')
        with open(p, 'w', encoding='utf-8') as fh:
            fh.write(f'# 文档 {f:04d} 安全技术资料\n\n')
            for ln in range(lines_per_file):
                fh.write(gen_line(ln) + '\n')
    return n_files


def time_ms(fn):
    t0 = time.perf_counter()
    r = fn()
    return (time.perf_counter() - t0) * 1000, r


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 2000
    root = tempfile.mkdtemp(prefix='bench_corpus_')
    db = os.path.join(root, 'bench.db')

    from indexer import index_directory
    from searcher import search, search_fuzzy

    build_corpus(root, n)
    ms, res = time_ms(lambda: index_directory(root, db))
    files_per_s = n / (ms / 1000)
    print(f'[build] files={res["total"]} processed={res["processed"]} {ms:.1f}ms -> {files_per_s:.0f} 文件/秒')

    queries = [
        ('二字词(精确)', 'search', '消防'),
        ('四字词(精确)', 'search', '消防安全'),
        ('AND 两词', 'search', '消防 验收'),
        ('OR 两词', 'search', '消防|给排水'),
        ('NOT 过滤', 'search', '验收 -电气'),
        ('模糊(错一字)', 'search_fuzzy', '消防验牧'),
        ('高级(状态)', 'search', '状态:现行 消防'),
    ]
    print('[search] 关键字 -> 命中行 / 耗时')
    for name, fn, kw in queries:
        f = search if fn == 'search' else search_fuzzy
        ms, hits = time_ms(lambda: f(db, kw))
        n_hits = len(hits) if hits else 0
        print(f'  {name:14s} {kw:20s} -> {n_hits:6d} 行   {ms:7.1f} ms')

    shutil.rmtree(root, ignore_errors=True)


if __name__ == '__main__':
    main()
