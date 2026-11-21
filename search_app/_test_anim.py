# -*- coding: utf-8 -*-
"""动效冒烟测试：按钮渐变/Toast/进度补间/清空按钮旋转/结果分批/网页端 CSS"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tkinter as tk

import main as appmod
import anim

appmod.DB_PATH = r'.\index.db'

if appmod._DND_AVAILABLE:
    root = appmod.TkinterDnD.Tk()
else:
    root = tk.Tk()
root.withdraw()
appmod.App._start_hotkeys = lambda self: None   # 避免热键被占用时弹模态
app = appmod.App(root)
app.cfg['inbox_auto_check'] = False   # 无人值守：禁止启动弹「待入库」模态
ok = fail = 0


def check(name, cond):
    global ok, fail
    print(('PASS ' if cond else 'FAIL ') + name)
    ok, fail = ok + (1 if cond else 0), fail + (1 if not cond else 0)


# 0. 动效模块 + 全局开关
check('anim 模块加载', anim is not None)
check('减弱动效默认关闭', not anim.reduced())

# 1. 主窗按钮已挂渐变样式
check('按钮渐变注册数量>5', len(app._anim_btns) > 5)
# 找一个初始可用的按钮（搜索按钮可能在初始化时被临时禁用）
info = None
for i in app._anim_btns:
    try:
        if str(i['btn']['state']) != 'disabled' and i['kind'] == 'accent':
            info = i
            break
    except Exception:
        pass
if info is None:
    info = next(i for i in app._anim_btns if i['btn'] is app.search_btn)
check('找到可测试的 Accent 按钮', info is not None)
st = tk.ttk.Style()
normal_bg = st.lookup(info['style'], 'background')
check('Accent 常态色',
      normal_bg.lower() == app._anim_palette('accent')['bg'].lower())

# 2. hover 渐变：进入后背景应向 hover 色变化（跑 mainloop 让补间走帧）
# 先确保按钮可用（搜索可能在后台跑完恢复）
if str(info['btn']['state']) == 'disabled':
    info['btn'].configure(state='normal')
app._btn_hover(info, True)
root.after(400, lambda: None)
t_end = [False]


def _check_hover():
    cur = st.lookup(info['style'], 'background')
    pal = app._anim_palette('accent')
    check('hover 后颜色已离开常态色', cur.lower() != pal['bg'].lower())
    check('hover 渐变到达目标色', cur.lower() == pal['hover'].lower())
    # 确保按钮可用再测 leave（初始化可能在后台禁用按钮）
    try:
        info['btn'].configure(state='normal')
    except Exception:
        pass
    app._btn_hover(info, False)
    root.after(400, _check_leave)


def _check_leave():
    cur = st.lookup(info['style'], 'background')
    pal = app._anim_palette('accent')
    print(f'  DEBUG leave: cur={cur!r} expect={pal["bg"]!r}')
    check('leave 后回到常态色', cur.lower() == pal['bg'].lower())
    _phase2()


def _phase2():
    # 3. 进度条补间
    app._set_progress(0, immediate=True)
    app._set_progress(60)
    root.after(450, _check_progress)


def _check_progress():
    v = float(app.progress['value'])
    check('进度条补间到达 60', abs(v - 60) < 1.5)
    app._set_progress(0, immediate=True)
    _phase3()


def _phase3():
    # 4. Toast 动画（创建即淡入，不报错）
    app._toast('动效测试提示')
    app._show_toast('动效测试通知', 'ok', ms=200)
    toasts = getattr(app, '_toasts', [])
    check('toast 已登记', len(toasts) >= 1)
    root.after(300, _phase4)


def _phase4():
    # 5. 清空按钮：Canvas + 两条线 + hover 旋转状态
    cb = app._clear_kw_btn
    check('清空按钮是 Canvas', cb.winfo_class() == 'Canvas')
    check('清空按钮两条线', len(cb.find_all()) == 2)
    stt = cb._cx_state
    check('清空按钮初始角度 0', stt['angle'] == 0.0)
    # 窗口需可见才能触发 Enter/Leave 事件
    root.deiconify()
    root.update()
    cb.event_generate('<Enter>')
    root.after(400, _phase5)


def _phase5():
    stt = app._clear_kw_btn._cx_state
    print(f'  DEBUG clear btn: angle={stt["angle"]} color={stt["color"]}')
    check('hover 旋转到 90°', abs(stt['angle'] - 90.0) < 1.0)
    check('hover 变红', stt['color'] == '#d04040')
    app._clear_kw_btn.event_generate('<Leave>')
    root.withdraw()
    _phase6()


def _phase6():
    # 6. 状态圆点渐变
    app._set_status('测试忙碌', 'busy')
    app._set_status('测试完成', 'ok')
    check('状态圆点变色无异常', True)
    # 7. 减弱动效：开关后补间直跳
    app.reduce_motion_var.set(True)
    app._toggle_reduce_motion()
    check('减弱动效已生效', anim.reduced())
    app._set_progress(30)
    check('减弱动效下进度直跳', abs(float(app.progress['value']) - 30) < 0.1)
    app.reduce_motion_var.set(False)
    app._toggle_reduce_motion()
    check('恢复动效', not anim.reduced())
    _phase7()


def _phase7():
    # 8. 网页端 CSS 动效存在
    import graph_html
    import inspect
    src = inspect.getsource(graph_html)
    check('关系图含 jzFadeUp', 'jzFadeUp' in src)
    check('关系图含 reduced-motion', 'prefers-reduced-motion' in src)
    root.quit()


root.after(350, _check_hover)
root.mainloop()

for w in list(root.winfo_children()):
    if isinstance(w, tk.Toplevel):
        try:
            w.destroy()
        except Exception:
            pass
sys.stdout.flush()
print(f'\n{ok} passed, {fail} failed')
os._exit(1 if fail else 0)
