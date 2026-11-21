# -*- coding: utf-8 -*-
"""导入面冒烟测试：锁定全应用模块图。

每个模块在 import 时可能跑模块级初始化（路径解析、全局变量、
类定义、可选依赖探测）。本测试逐模块导入，保证后续拆分/重构
不会引入循环导入或 import 期崩溃。
"""
import importlib
import unittest

_ALL_MODULES = [
    # 基础层
    'detector', 'parsers', 'indexer', 'searcher', 'ipc',
    'core', 'service', 'export', 'synonyms', 'graph_html',
    # 依赖探测与后台层
    'anim', 'ai_qa', 'websearch', 'winchrome',
    # 界面 / 服务 / 入口
    'tray', 'quickwin', 'qa_win', 'main',
]


class TestImportSurface(unittest.TestCase):
    def test_every_module_imports(self):
        failed = []
        for name in _ALL_MODULES:
            try:
                importlib.import_module(name)
            except Exception as exc:  # noqa: BLE001 —— 冒烟测试如实报告
                failed.append(f'{name}: {type(exc).__name__}: {exc}')
        self.assertEqual(failed, [], msg='\n'.join(failed))

    def test_main_is_the_entry(self):
        """main 必须能被导入（GUI 在 __main__ 分支里才启动）。"""
        import main
        self.assertTrue(hasattr(main, 'App'))


if __name__ == '__main__':
    unittest.main()
