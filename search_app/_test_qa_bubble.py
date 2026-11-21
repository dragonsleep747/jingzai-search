# -*- coding: utf-8 -*-
"""d4 冒烟：AI 问答气泡流（用户右气泡/AI 左气泡/光标/引用/复制/换色/重排）。"""
import ctypes, os, sys, tempfile, shutil
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import indexer, main, qa_win

tmp = tempfile.mkdtemp()
db = os.path.join(tmp, 't.db'); lib = os.path.join(tmp, 'lib')
os.makedirs(lib)
old_lib = indexer.LIBRARY_DIR
indexer.LIBRARY_DIR = lib
fp = os.path.join(lib, 'a.txt')
open(fp, 'w', encoding='utf-8').write('安全生产责任制\n')
indexer.index_single_file(fp, db)
indexer.LIBRARY_DIR = old_lib
main.DB_PATH = db

from tkinterdnd2 import TkinterDnD
root = TkinterDnD.Tk()
root.geometry('10x10-3000-3000'); root.deiconify()
main.App._start_hotkeys = lambda self: None
app = main.App(root)
app.dark.set(True); app._apply_ui_theme()
for _ in range(5):
    root.update()

qw = qa_win.QAWindow(app)
w = qw.win
qw.check_env = lambda: None   # 测试不探测/拉起本机 Ollama
for _ in range(20):
    root.update()
# 排空构造期检测线程可能已放入的 env 事件（会插引导说明），后续流程保持确定
qw._poll()
for _ in range(10):
    root.update()
qw.q.put(('ans_clear',))
qw._poll()

hits = [
    {'name': '安全生产管理制度', 'doc_no': 'AQ-001', 'effective_date': '2024-01-01',
     'chapter': '第二章 安全职责', 'status': 'active', 'score': 0.91,
     'text': '主要负责人是安全生产第一责任人，对本单位安全生产工作全面负责。',
     'path': fp, 'start_line': 1},
    {'name': '旧版制度', 'doc_no': 'AQ-000', 'effective_date': '2018-01-01',
     'chapter': '第一章', 'status': 'void', 'score': 0.62,
     'text': '已废止的旧条款内容。', 'path': fp, 'start_line': 1},
]
long_ans = ('根据库内检索到的条款 [1]，落实安全生产责任制应当做到：'
            '主要负责人作为第一责任人全面负责，安全管理部门负责日常检查与培训，'
            '从业人员发现隐患应立即报告；各部门每周至少开展一次现场检查，'
            '检查结果纳入月度绩效考核，重大隐患挂牌督办。'
            '历史版本 [2] 已废止，不应作为执行依据。')

qw.q.put(('ans_clear',))
qw.q.put(('user_q', '安全生产责任制怎么落实？'))
qw.q.put(('sys', '正在检索相关条款…'))
qw.q.put(('srcs', hits))
for ch in (long_ans[:20], long_ans[20:60], long_ans[60:120], long_ans[120:]):
    qw.q.put(('ans', ch))
    qw._poll()
    for _ in range(3):
        root.update()
qw.q.put(('link_cites',))
qw.q.put(('busy', False))
qw._poll()
for _ in range(30):
    root.update()

kinds = [it['kind'] for it in qw._chat_items]
assert 'user' in kinds and 'ai' in kinds and 'sys' in kinds, kinds
ai_it = next(it for it in qw._chat_items if it['kind'] == 'ai')
fr, t = ai_it['fr'], ai_it['t']

txt = qw._answer_text()
assert '▍' not in txt, '光标未移除'
assert '挂牌督办' in txt and txt.startswith('根据库内检索到的条款'), txt
assert t.tag_ranges('cite'), '引用 [n] 未打 cite 标签'
# 长回答必然换行：防抖重排后高度应与实际显示行数精确一致
import time
time.sleep(0.08)
for _ in range(10):
    root.update()
fr.update_idletasks()
n_lines = int(t.tk.call(t._w, 'count', '-displaylines', '-update',
                        '1.0', 'end'))
h_px = int(fr.cget('height'))
expect_h = n_lines * qw._qa_font_ls + 18
assert abs(h_px - expect_h) <= 2, f'气泡高 {h_px} != {expect_h}（{n_lines}行）'
assert n_lines >= 4, f'长回答应换行≥4，实际{n_lines}'
# 复制按钮取到纯文本
qw.copy_answer()
assert '安全生产责任制' in w.clipboard_get()

# 截图（深色）
import win32gui, win32ui
from ctypes import windll
from PIL import Image
def _shot(path):
    hw = windll.user32.GetParent(w.winfo_id())
    l, top, r, b = win32gui.GetWindowRect(hw)
    wd, ht = r - l, b - top
    hdc = win32gui.GetWindowDC(hw)
    mdc = win32ui.CreateDCFromHandle(hdc)
    bdc = mdc.CreateCompatibleDC()
    bmp = win32ui.CreateBitmap(); bmp.CreateCompatibleBitmap(mdc, wd, ht)
    bdc.SelectObject(bmp)
    windll.user32.PrintWindow(hw, bdc.GetSafeHdc(), 2)
    bi = bmp.GetInfo()
    Image.frombuffer('RGB', (bi['bmWidth'], bi['bmHeight']),
                     bmp.GetBitmapBits(True), 'raw', 'BGRX', 0, 1).save(path)
    win32gui.DeleteObject(bmp.GetHandle())
    bdc.DeleteDC(); mdc.DeleteDC(); win32gui.ReleaseDC(hw, hdc)
_shot(r'.\search_app\_qa_bubble_dark.png')

# 手动停止场景不崩
qw.q.put(('ans_clear',))
qw.q.put(('user_q', '测试停止'))
qw.q.put(('ans', '半截回答'))
qw._poll()
for _ in range(5):
    root.update()
qw.stop_ask()
for _ in range(5):
    root.update()

# 重新构建一轮，做换色 + 重排
qw.q.put(('ans_clear',))
qw.q.put(('user_q', '浅色主题看看？'))
qw.q.put(('ans', '这是浅色主题下的回答气泡，用来验证换色后气泡配色跟随。' * 3))
qw._poll()
for _ in range(10):
    root.update()
qw.q.put(('busy', False))
qw._poll()
for _ in range(10):
    root.update()
app.dark.set(False); app._apply_ui_theme(); qw.apply_theme()
for _ in range(10):
    root.update()
ai_it2 = next(it for it in qw._chat_items if it['kind'] == 'ai')
assert str(ai_it2['t'].cget('bg')).upper() == '#FFFFFF'
user_it2 = next(it for it in qw._chat_items if it['kind'] == 'user')
assert str(user_it2['w'].cget('bg')).upper() == '#0F6CBD', \
    '浅色下用户气泡应为品牌蓝'
qw._reflow_chat()
for _ in range(5):
    root.update()

# 截图（浅色）
_shot(r'.\search_app\_qa_bubble.png')

# 清空后状态归零
qw.q.put(('ans_clear',))
qw._poll()
for _ in range(5):
    root.update()
assert qw._chat_items == [] and qw._cur_ai is None

root.destroy()
shutil.rmtree(tmp, ignore_errors=True)
print('QA BUBBLE SMOKE OK, 末轮气泡高', h_px)
