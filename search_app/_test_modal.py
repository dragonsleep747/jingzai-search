# -*- coding: utf-8 -*-
"""临时专项：弹窗等级模态（遮罩/响铃/禁越级关闭/解锁恢复）"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tkinter as tk

import main as appmod

appmod.DB_PATH = r'.tmp\legacy\index.db'

if appmod._DND_AVAILABLE:
    root = appmod.TkinterDnD.Tk()
else:
    root = tk.Tk()
root.title('主界面')
root.geometry('300x200+10+10')
root.update()
app = appmod.App(root)
h0 = root.protocol('WM_DELETE_WINDOW')   # 主界面原关闭处理器
ok = fail = 0


def check(name, cond):
    global ok, fail
    print(('PASS ' if cond else 'FAIL ') + name)
    ok, fail = ok + (1 if cond else 0), fail + (1 if not cond else 0)


# 1) 操作型弹窗（modal 默认 True）→ 锁定主界面
d1 = tk.Toplevel(root)
app._center_win(d1, 400, 300)
root.update()
ovs = app._modal_overlays.get(id(d1))
check('弹窗登记遮罩', bool(ovs))
check('遮罩覆盖主界面', bool(ovs) and ovs[0][0] is root
      and ovs[0][1].winfo_exists() and ovs[0][1].winfo_ismapped())
h1 = root.protocol('WM_DELETE_WINDOW')
check('主界面关闭已被拦截', h1 != h0)
# 模拟越级点击遮罩（应触发响铃绑定且不报错）
ovs[0][1].event_generate('<Button-1>')
root.update()
check('越级点击已处理', True)

# 2) 关闭弹窗 → 自动解锁，恢复原关闭处理器
d1.destroy()
root.update()
check('遮罩登记已清除', id(d1) not in app._modal_overlays)
h2 = root.protocol('WM_DELETE_WINDOW')
check('主界面关闭处理器已恢复', h2 == h0)

# 3) 浏览型窗口（modal=False）→ 不锁定
lib = tk.Toplevel(root)
app._center_win(lib, 1250, 620, modal=False)
root.update()
check('工作台窗口未登记遮罩', id(lib) not in app._modal_overlays)

# 4) 操作型弹窗开在工作台之上 → 逐级锁定（工作台 + 主界面）
d2 = tk.Toplevel(lib)
app._center_win(d2, 600, 460)
root.update()
ovs2 = app._modal_overlays.get(id(d2))
parents2 = [p for p, _ in ovs2] if ovs2 else []
check('逐级锁定 工作台+主界面',
      any(p is lib for p in parents2) and any(p is root for p in parents2))
hl = lib.protocol('WM_DELETE_WINDOW')
check('工作台关闭已被拦截', bool(hl))
d2.destroy()
root.update()
check('工作台关闭处理器已恢复', lib.protocol('WM_DELETE_WINDOW') != hl
      and id(d2) not in app._modal_overlays)
lib.destroy()
root.update()

# 5) 嵌套两层弹窗逐个关闭，主界面逐级恢复
da = tk.Toplevel(root)
app._center_win(da, 400, 300)
db = tk.Toplevel(root)
app._center_win(db, 400, 300)
root.update()
check('两弹窗同锁主界面',
      root.protocol('WM_DELETE_WINDOW') != h0)
da.destroy()
root.update()
check('关一个仍锁定', root.protocol('WM_DELETE_WINDOW') != h0)
db.destroy()
root.update()
check('全关后恢复', root.protocol('WM_DELETE_WINDOW') == h0)

root.destroy()
print(f'\n{ok} passed, {fail} failed')
sys.exit(1 if fail else 0)
