# -*- coding: utf-8 -*-
"""镜仔搜索 · 异常治理审计工具（只读分析，不改代码）

统计每个生产模块的 `except Exception` / 裸 `except` 数量与处理模式，
输出按风险分类的报告：重新抛出(OK) / 记日志(OK) / 吞掉(pass/return，需人工复核)。
用法：python tools/exception_audit.py [模块...]
"""
import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(ROOT, 'search_app')

# 非生产代码（测试/示例/开发脚本）默认排除
SKIP_PREFIX = ('_test', '_smoke', '_gen')


def analyze(path):
    src = open(path, 'r', encoding='utf-8', errors='replace').read()
    tree = ast.parse(src)
    lines = src.splitlines()
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        # 判定是否“宽泛”捕获
        t = node.type
        broad = t is None
        if isinstance(t, ast.Name) and t.id in ('Exception', 'BaseException'):
            broad = True
        elif isinstance(t, ast.Attribute) and t.attr in ('Exception', 'BaseException'):
            broad = True
        elif isinstance(t, ast.Tuple):
            broad = any(
                isinstance(e, ast.Name) and e.id in ('Exception', 'BaseException')
                for e in t.elts)
        if not broad:
            continue
        body = node.body
        kind = 'unknown'
        if body:
            first = body[0]
            if isinstance(first, ast.Raise):
                kind = 're-raise'
            elif isinstance(first, ast.Pass):
                kind = 'pass'
            elif isinstance(first, ast.Return):
                kind = 'return'
            elif isinstance(first, ast.Continue) or isinstance(first, ast.Break):
                kind = 'flow'
            elif isinstance(first, ast.Expr) and isinstance(
                    first.value, ast.Call):
                fn = first.value.func
                fnname = ''
                if isinstance(fn, ast.Name):
                    fnname = fn.id
                elif isinstance(fn, ast.Attribute):
                    fnname = fn.attr
                if any(k in fnname.lower() for k in ('log', 'record', 'trace',
                                                      'report', 'error')):
                    kind = 'log:' + fnname
                else:
                    kind = 'call:' + fnname
            elif isinstance(first, ast.Assign):
                kind = 'assign'
        out.append((node.lineno, kind))
    return out, lines


def main():
    targets = sys.argv[1:] or [f for f in sorted(os.listdir(APP))
                               if f.endswith('.py')
                               and not f.startswith(SKIP_PREFIX)]
    # --out path：直接把报告写为 UTF-8 文件（绕开 PowerShell 重定向转码）
    out_path = None
    if '--out' in targets:
        i = targets.index('--out')
        out_path = targets[i + 1]
        targets = targets[:i] + targets[i + 2:] or targets[:i]
    if not targets:
        targets = [f for f in sorted(os.listdir(APP))
                   if f.endswith('.py') and not f.startswith(SKIP_PREFIX)]
    buf = []
    emit = buf.append
    total = 0
    by_kind = {}
    problem_files = []
    for name in targets:
        path = os.path.join(APP, name) if not os.path.isabs(name) else name
        if not os.path.exists(path):
            emit(f'[跳过] {name} 不存在')
            continue
        if os.path.basename(path).startswith(SKIP_PREFIX):
            continue
        hits, lines = analyze(path)
        if not hits:
            continue
        total += len(hits)
        kinds = {}
        for ln, kind in hits:
            kinds[kind] = kinds.get(kind, 0) + 1
            by_kind[kind] = by_kind.get(kind, 0) + 1
        # 吞掉式（pass/return/flow/assign/unknown）按风险列出
        risky = [ln for ln, k in hits
                 if k in ('pass', 'return', 'flow', 'assign', 'unknown')]
        emit(f'\n## {name}: 宽泛 except 共 {len(hits)} 处')
        for k, c in sorted(kinds.items()):
            emit(f'   - {k}: {c}')
        if risky:
            problem_files.append((name, risky, lines))
    emit('\n' + '=' * 70)
    emit(f'合计宽泛 except：{total} 处；按处理模式分布：')
    for k, c in sorted(by_kind.items(), key=lambda x: -x[1]):
        emit(f'   {k}: {c}')
    emit(f'\n吞掉式（pass/return/flow/assign/unknown）文件数：{len(problem_files)}')
    for name, risky, lines in problem_files:
        emit(f'\n--- {name}：{len(risky)} 处可疑吞掉 ---')
        for ln in risky[:40]:
            code = lines[ln - 1].strip() if ln - 1 < len(lines) else '?'
            emit(f'   L{ln}: {code}')
    text = '\n'.join(buf)
    if out_path:
        with open(out_path, 'w', encoding='utf-8') as f:
            f.write(text)
        sys.stdout.write(f'报告已写入 {out_path}（{len(buf)} 行）\n')
    else:
        sys.stdout.write(text + '\n')


if __name__ == '__main__':
    main()
