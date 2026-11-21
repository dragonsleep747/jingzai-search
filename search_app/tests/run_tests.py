# -*- coding: utf-8 -*-
"""镜仔搜索 · 测试运行器（stdlib unittest discover，无需 pytest）

用法：
    python search_app/tests/run_tests.py            # 全部
    python search_app/tests/run_tests.py -q         # 精简
    python search_app/tests/run_tests.py test_searcher   # 单模块
"""
import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_APP = os.path.dirname(_HERE)
sys.path.insert(0, _APP)


def main():
    argv = sys.argv[1:]
    if argv and not argv[0].startswith('-'):
        pattern = argv[0] if argv[0].startswith('test_') \
            else 'test_' + argv[0]
        suite = unittest.defaultTestLoader.loadTestsFromName(
            'tests.' + pattern[:-3] if pattern.endswith('.py') else
            'tests.' + pattern)
    else:
        suite = unittest.defaultTestLoader.discover(_HERE, pattern='test_*.py')
    verbosity = 1
    if '-v' in argv or '--verbose' in argv:
        verbosity = 2
    runner = unittest.TextTestRunner(verbosity=verbosity)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)


if __name__ == '__main__':
    main()
