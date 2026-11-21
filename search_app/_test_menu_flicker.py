# -*- coding: utf-8 -*-
"""菜单悬停闪烁回归：
1) 菜单刷新后高频 <<MenuSelect>>（鼠标悬停）不得再 configure 菜单；
2) EnumWindows 全窗口枚举必须节流（悬停风暴 ≤1 次）；
3) 同主题重刷被脏标记拦截；主题切换后 selectcolor 必须正确重刷。
不真实 post 菜单（系统菜单循环会让自动化挂起）；刷新逻辑与弹出路径
（_popup/_post/_on_mb_press → _refresh_menu_tree）是同一份函数。"""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
tmp = tempfile.mkdtemp()
import main
main.DB_PATH = os.path.join(tmp, 't.db')
# 测试期间禁止写产品 config.json（主题切换会触发保存）
main.save_config = lambda *a, **k: None
from tkinterdnd2 import TkinterDnD
import tkinter as tk
import winchrome

root = TkinterDnD.Tk(); root.geometry('900x600+80+60')
app = main.App(root)
# 无论产品 config 当前是什么主题，测试统一起始于浅色
app.dark.set(False)
app._apply_ui_theme()
for _ in range(10):
    root.update()

calls = {'cfg': 0, 'enum': 0}
_orig_cfg = tk.Menu.configure


def counting_cfg(self, cnf=None, **kw):
    if (isinstance(cnf, dict) and 'selectcolor' in cnf) \
            or 'selectcolor' in kw:
        calls['cfg'] += 1
    return _orig_cfg(self, cnf, **kw)


tk.Menu.configure = counting_cfg
winchrome.dark_owned_menus = lambda dark: calls.__setitem__('enum',
                                                            calls['enum'] + 1)

mm = root.nametowidget(app.more_btn.cget('menu'))
# 模拟「菜单已弹出过」：弹出钩子会把菜单收进 known WeakSet
winchrome.install._known_menus.add(mm)

# 1) 首次刷新（浅色）：configure selectcolor
winchrome.install.refresh_all_menus()
n1 = calls['cfg']
assert n1 >= 1, '首次刷新应 configure selectcolor'
sel = str(mm.cget('selectcolor'))
assert sel.upper() == '#1C1C1C', f'浅色勾号色错误: {sel}'
print(f'首次刷新: configure {n1} 次, selectcolor={sel}')

# 2) 悬停风暴：30 个 MenuSelect（bind_all 路径，与真实悬停一致）
root.update()
e1 = calls['enum']
for _ in range(30):
    root.event_generate('<<MenuSelect>>')
    root.update_idletasks()
root.update()
d_cfg = calls['cfg'] - n1
d_enum = calls['enum'] - e1
assert d_cfg == 0, f'悬停期间 configure 菜单 {d_cfg} 次 → 必然闪烁'
assert d_enum <= 1, f'悬停风暴触发 {d_enum} 次全窗口枚举（应节流到 ≤1）'
print(f'悬停 30 事件: configure +{d_cfg}, enum +{d_enum}')

# 3) 同主题再次刷新 → 脏标记命中，零 configure
winchrome.install.refresh_all_menus()
assert calls['cfg'] == n1, '同主题重复刷新应被脏标记拦截'
print('同主题重刷: 脏标记拦截零 configure OK')

# 4) 切深色（真实入口 _toggle_dark_animated 内含菜单刷新）→ 白色勾号
app.dark.set(True)
app._toggle_dark_animated()
for _ in range(6):
    root.update()
assert str(mm.cget('selectcolor')).upper() == '#FFFFFF', '深色勾号色错误'
print('深色主题: selectcolor =', mm.cget('selectcolor'))

# 5) 切回浅色 → 深灰勾号
app.dark.set(False)
app._toggle_dark_animated()
for _ in range(6):
    root.update()
assert str(mm.cget('selectcolor')).upper() == '#1C1C1C', '浅色勾号色错误'
print('浅色主题: selectcolor =', mm.cget('selectcolor'))

root.destroy()
print('RESULT: PASS')

