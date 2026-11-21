# -*- coding: utf-8 -*-
"""冒烟测试：预览区隐藏行号/页码前缀后，渲染、标签定位、模糊高亮仍正常。"""
import os
import sys
import sqlite3
import tempfile
import shutil

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import indexer
from searcher import _PAGE_RE

# 1) _PAGE_RE 可从 main 导入且语义正确
import main  # noqa: E402
assert main._PAGE_RE is _PAGE_RE, '_PAGE_RE 导入失败'
assert _PAGE_RE.sub('', '[Page 12] 第三条', count=1) == '第三条'
assert _PAGE_RE.sub('', '[Slide 3] 要点', count=1) == '要点'
assert _PAGE_RE.sub('', '普通行 [Page 12]', count=1) == '普通行 [Page 12]'

# 2) 临时库 + 文档，走真实渲染
tmp = tempfile.mkdtemp()
db = os.path.join(tmp, 't.db')
lib = os.path.join(tmp, 'lib')
os.makedirs(lib)
old_lib = indexer.LIBRARY_DIR
try:
    indexer.LIBRARY_DIR = lib
    f = os.path.join(lib, '测试制度.txt')
    with open(f, 'w', encoding='utf-8') as fh:
        fh.write('第一条 总则\n本制度依据《安全生产法》制定\n\n第二条 适用范围\n含关键词 安全生产 责任制\n')
    indexer.index_single_file(f, db)
finally:
    indexer.LIBRARY_DIR = old_lib

# 模拟 PDF 行：页码前缀行
conn = sqlite3.connect(db)
doc_id = conn.execute('SELECT id FROM documents WHERE path=?', (f,)).fetchone()[0]
conn.execute('INSERT INTO lines(doc_id, line_no, line_text) VALUES (?,?,?)',
             (doc_id, 6, '[Page 3] 附则：本办法自发布之日起施行'))
conn.commit(); conn.close()

# 3) 构建 GUI 并渲染预览
import tkinter as tk
try:
    from tkinterdnd2 import TkinterDnD
    root = TkinterDnD.Tk()
except Exception:
    root = tk.Tk()
root.withdraw()
main.DB_PATH = db          # 预览查询全部指向临时库，避免碰真实索引
main.App._start_hotkeys = lambda self: None
app = main.App(root)
app.keyword.set('安全生产')   # 关键词高亮依赖搜索框词（真实流程预览均来自搜索后）
app._show_preview(f, 2, hit_lines=[2])
txt = app.preview.get('1.0', 'end-1c')
lines = txt.split('\n')
body = [l for l in lines]
# 行号/页码前缀已消失
assert not any('  2 │' in l or '  1 │' in l for l in body), '仍存在行号前缀'
assert not any('[Page 3]' in l for l in body), '页码前缀未剥离'
assert any('本制度依据' in l for l in body), '正文缺失'
assert any('安全生产法' in l for l in body), '引用链接文本缺失'
# 关键词高亮存在
assert app.preview.tag_ranges('hl'), '关键词高亮丢失'
# 行标签可定位（模糊定位机制）
rng = app.preview.tag_nextrange('ln2', '1.0')
assert rng, 'ln2 行标签未打上'
# 命中行底色
assert app.preview.tag_ranges('hit'), '命中行底色丢失'

# 3.5) 页码前缀剥离：直接预览 [Page 3] 行（整段模式覆盖 L4-L6）
app._show_preview(f, 6)
txt6 = app.preview.get('1.0', 'end-1c')
assert '[Page 3]' not in txt6, '页码前缀未剥离'
assert '附则：本办法自发布之日起施行' in txt6, '页码行正文缺失'
assert 'P3' in txt6, '顶部元信息页码缺失'

# 4) 模糊命中行定位渲染：模拟 fuzzy 场景（人工打 syn/调用 _highlight_fuzzy_row）
row = int(rng[0].split('.')[0])
app._highlight_fuzzy_row(row, '安全生产')
assert app.preview.tag_ranges('hl'), '模糊行内高亮失败'

# 5) 自适应重排：同一自然段内的 PDF 硬断行并成一条，
#    命中判定、行号锚定、字符级命中底色仍正确
_old_lib = indexer.LIBRARY_DIR
try:
    indexer.LIBRARY_DIR = lib
    f2 = os.path.join(lib, '硬断行.txt')
    with open(f2, 'w', encoding='utf-8') as fh:
        fh.write('第二条 适用范围\n'
                 '本制度适用于公司各部门、各单位的安全生产管理工作，各部门\n'
                 '应当严格执行并纳入年度考核，考核结果与绩效挂钩。\n'
                 '第三条 附则\n')
    indexer.index_single_file(f2, db)
finally:
    indexer.LIBRARY_DIR = _old_lib

app.keyword.set('年度考核')
app.preview_wrap_var.set(True)
app.preview.configure(wrap='word')
app._show_preview(f2, 3, hit_lines=[3])
txt = app.preview.get('1.0', 'end-1c')
assert '各部门应当严格执行' in txt, 'L2/L3 硬断行未合并（右侧仍留白）'
assert app.preview.tag_nextrange('ln2', '1.0'), '重排后段首 ln2 标签丢失'
assert not app.preview.tag_nextrange('ln3', '1.0'), '重排后 ln3 未并入段首'
assert app._ln_anchor(3) == 2, '被并行的行号未锚定到段首'
_rngs = app.preview.tag_ranges('hit')
assert _rngs, '重排后命中行底色丢失'
_htxt = app.preview.get(_rngs[0], _rngs[1])
assert '年度考核' in _htxt and '本制度适用于' not in _htxt, \
    '命中底色未按真实行上色（刷到了整段）'

# 关掉换行 → 回到逐行原样（核对原文用）
app.preview_wrap_var.set(False)
app.preview.configure(wrap='none')
app._show_preview(f2, 3, hit_lines=[3])
txt = app.preview.get('1.0', 'end-1c')
_ls = txt.split('\n')
assert '各部门应当严格执行' not in txt, '关换行后仍被合并'
assert any('本制度适用于' in l and l.rstrip().endswith('各部门') for l in _ls), \
    '关换行后首行未逐行显示'
assert any('应当严格执行' in l and '本制度适用于' not in l for l in _ls), \
    '关换行后次行未独立成行'
assert app.preview.tag_nextrange('ln3', '1.0'), '关换行后 ln3 行标签丢失'
assert app.preview.tag_ranges('hit'), '关换行后命中底色丢失'

root.destroy()
shutil.rmtree(tmp, ignore_errors=True)
print('SMOKE OK: 前缀已隐藏，标签定位/高亮全部正常')
