# -*- coding: utf-8 -*-
"""镜仔搜索 · 核心基础模块（配置 / 路径 / 工具函数，不依赖 tkinter）。

从 main.py 抽出：路径与打包资源定位、config.json 读写、数据目录自动定位、
访问口令生成、搜索框高亮词解析、规范引用文本。GUI 层（main.py）与其它
子模块（qa_win / quickwin）均可安全 import，不产生循环依赖。
"""
import os
import json
import shutil
import sys

from searcher import (
    parse_advanced_query,
    get_chapter_chain_str,
    get_line_text,
)
from indexer import get_metadata


def _get_app_dir():
    """exe 模式(PyInstaller)下数据放在 exe 同目录，源码模式放脚本目录"""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


APP_DIR = _get_app_dir()


def _resource_path(name):
    """打包内置资源（ico/png）在 _MEIPASS；源码运行在脚本目录"""
    return os.path.join(getattr(sys, '_MEIPASS', APP_DIR), name)


# 配置文件跟随 exe（用户设置不应被数据目录迁移影响）
CONFIG_PATH = os.path.join(APP_DIR, 'config.json')

# 在线意见反馈（WPS 在线表格分享链接）。发布给他人前把链接写死在这里，
# 重新打包后所有用户 ⚙→「意见反馈」 即可直达；本机可用 config.json
# 的 feedback_url 键临时覆盖（不重新打包就能换链接）
DEFAULT_FEEDBACK_URL = 'https://www.kdocs.cn/l/ctRzVCysc36V'

# 类型下拉选项
TYPE_OPTIONS = [
    ('all', '全部'), ('text', '文本'), ('office', 'Office'),
    ('pdf', 'PDF'), ('code', '代码'),
]

# 预览「上下文」模式固定显示命中行前后各 N 行（原 ± 调节控件已移除）
CTX_LINES = 5


def load_config():
    """P1-15: 读取配置"""
    default = {'root_dir': '', 'recent_keywords': [],
               'window': '1200x760',
               'quickwin_geo': '', 'qa_geo': '',
               'quick_hotkey': 'ctrl+alt+j', 'clip_hotkey': 'ctrl+alt+m',
               'url_protocol': False,
               'ollama_base': 'http://127.0.0.1:11434',
               'ollama_chat': 'qwen2.5:7b-instruct',
               'ollama_embed': 'bge-m3', 'qa_topk': 8,
               'autowatch': True, 'only_active': True, 'qa_auto': False,
               'frameless': True, 'appbar_flat': True}
    # utf-8-sig：容忍 BOM 头（外部工具编辑配置可能带 BOM，utf-8 会解析失败）
    for path in (CONFIG_PATH, CONFIG_PATH + '.bak'):
        try:
            if not os.path.exists(path):
                continue
            with open(path, 'r', encoding='utf-8-sig') as f:
                cfg = json.load(f)
                # 合并默认值，避免老配置缺字段
                for k, v in default.items():
                    cfg.setdefault(k, v)
                return cfg
        except json.JSONDecodeError:
            # 主配置损坏：坏文件改名保留现场（可手工找回），继续尝试 .bak
            if path == CONFIG_PATH:
                try:
                    os.replace(CONFIG_PATH, CONFIG_PATH + '.bad')
                except OSError:
                    pass
        except Exception:
            pass
    return default


def save_config(cfg):
    """P1-15: 写入配置（临时文件 + 原子替换，断电/崩溃不损坏配置）"""
    try:
        tmp = CONFIG_PATH + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        os.replace(tmp, CONFIG_PATH)
        try:
            # 滚动备份：主配置万一再损坏，下次启动自动从 .bak 恢复
            shutil.copyfile(CONFIG_PATH, CONFIG_PATH + '.bak')
        except Exception:
            pass
    except Exception:
        pass


# ---------- 数据目录自动定位 ----------
# 用户搬动 index.db 后，软件能自动在候选目录中找回数据，避免「库打不开」
_DB_MIN_SIZE = 102400   # 100KB，避免误识别空库


def _resolve_data_root(app_dir, cfg):
    """启动时定位数据根目录（index.db / library / ocr_cache / backup 所在处）。

    候选顺序（首个含可用 index.db 的目录胜出）：
      1. cfg['last_db_path'] 所在目录（用户主动指定的）
      2. app_dir（默认：exe 同目录）
      3. app_dir 父目录（dist 上一级 = 镜仔搜索根目录等）
      4. app_dir 父目录的兄弟子目录（深度 1，避免扫太远）
    都找不到则返回 app_dir（让后续逻辑创建新库）。
    """
    candidates = []

    # 1. 用户主动指定的位置
    last = cfg.get('last_db_path')
    if last and os.path.isabs(last):
        candidates.append(os.path.dirname(last))

    # 2. 默认：exe 同目录
    candidates.append(app_dir)

    # 3/4. 自动搜索：父目录 + 兄弟子目录（深度 1）
    parent = os.path.dirname(app_dir.rstrip(os.sep)) if app_dir else ''
    if parent and os.path.isdir(parent):
        candidates.append(parent)
        try:
            for name in os.listdir(parent):
                if name.startswith('.'):
                    continue
                # 跳过明显的非数据目录
                if name.lower() in ('backup', 'build', '__pycache__',
                                     '.git', '.trae', 'templates'):
                    continue
                full = os.path.join(parent, name)
                if os.path.isdir(full) and full not in candidates:
                    candidates.append(full)
        except OSError:
            pass

    # 对每个候选，检查 index.db 是否存在且足够大
    for d in candidates:
        if not d or not os.path.isdir(d):
            continue
        cand_db = os.path.join(d, 'index.db')
        try:
            if os.path.isfile(cand_db) and \
                    os.path.getsize(cand_db) >= _DB_MIN_SIZE:
                return d
        except OSError:
            continue

    # 都没找到 → 回到默认，让 indexer 创建空库
    return app_dir


def _gen_token(n=6):
    """生成随机访问口令（剔除易混淆字符 0O1lIi）"""
    import secrets
    alphabet = 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghjkmnpqrstuvwxyz23456789'
    return ''.join(secrets.choice(alphabet) for _ in range(n))


def kw_terms(keyword):
    """解析搜索框输入为需要高亮的正向词列表。
    语法与 searcher 一致：空格=AND，| 或 OR=OR，-词=排除（不高亮排除词）；
    文号:/状态:/日期:/名称: 前缀是元数据过滤，不作为正文高亮词"""
    expr = parse_advanced_query(keyword)[0].replace('|', ' OR ')
    terms = []
    for tok in expr.split():
        if tok in ('OR', 'NOT'):
            continue
        if tok.startswith('-') and len(tok) > 1:
            continue
        if tok.startswith('+') and len(tok) > 1:
            tok = tok[1:]
        if tok and tok not in terms:
            terms.append(tok)
    return terms


# 搜索框占位提示（聚焦自动消失；内容等于它时一律按空查询处理）
KW_HINT = '输入关键词搜索，回车执行'


def build_citation(db_path, path, line_no):
    """规范引用文本：《文件名》章节链（文号）: 命中行原文（迷你窗/主窗共用）"""
    name = os.path.splitext(os.path.basename(path))[0]
    chain = get_chapter_chain_str(db_path, path, line_no)
    line_text = get_line_text(db_path, path, line_no).strip()
    meta = get_metadata(db_path, path)
    parts = [f'《{name}》']
    if chain:
        parts.append(chain)
    if meta.get('doc_no'):
        parts.append(f'（{meta["doc_no"]}）')
    return ''.join(parts) + f': {line_text}'
