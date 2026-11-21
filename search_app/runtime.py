# -*- coding: utf-8 -*-
"""运行时定位：数据根目录 / 数据库路径 / 备份目录，并同步 indexer、parsers 目录常量。

本模块集中了原先散在 main.py 顶部的「启动期定位」逻辑，main.py 通过
`from runtime import ...` 引用并保持对外接口不变。任何模块只要 import
本模块，就会完成一次定位与目录常量同步（幂等：模块只执行一次）。
"""
import os
import re

from core import APP_DIR, load_config, _resolve_data_root

# 启动时执行定位（必须在 indexer / parsers 模块属性覆盖之前完成）
_STARTUP_CFG = load_config()
DATA_ROOT = _resolve_data_root(APP_DIR, _STARTUP_CFG)
DB_PATH = os.path.join(DATA_ROOT, 'index.db')
BACKUP_DIR = os.path.join(DATA_ROOT, 'backup')   # index.db 自动备份目录
MAX_BACKUPS = 3                                  # 保留最近 3 份
# 正文里的 http(s) 链接（预览区渲染成可点击，单击用默认浏览器打开）
_URL_RE = re.compile(r"https?://[^\s\u3000，。；、：）】〕》」'\"<>《【〔（]+")
# 自动覆盖 indexer / parsers 的数据目录常量（这些模块在 import 时已
# 用 _get_app_dir() 赋过默认值，这里同步到我们定位到的 DATA_ROOT）
try:
    import indexer as _indexer_mod
    _indexer_mod.LIBRARY_DIR = os.path.join(DATA_ROOT, 'library')
    # main 模块顶部 from indexer import LIBRARY_DIR 已拷贝旧值，这里同步
    LIBRARY_DIR = _indexer_mod.LIBRARY_DIR
except Exception:
    from indexer import LIBRARY_DIR
try:
    import parsers as _parsers_mod
    _parsers_mod._OCR_CACHE_DIR = os.path.join(DATA_ROOT, 'ocr_cache')
except Exception:
    pass
# 标记：本次启动是否走了「自动定位」（非默认位置）
_DB_RELOCATED = (DATA_ROOT != APP_DIR)
