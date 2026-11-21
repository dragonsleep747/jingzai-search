# -*- coding: utf-8 -*-
"""第二波动效冒烟测试：spinner/描边/柱状图/弹窗委托/行hover/滚动/呼吸等"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tkinter as tk

import main as appmod
import anim

appmod.DB_PATH = r'.\index.db'

root = (appmod.TkinterDnD.Tk() if appmod._DND_AVAILABLE else tk.Tk())
root.withdraw()
appmod.App._start_hotkeys = lambda self: None   # 避免热键被占用时弹模态
app = appmod.App(root)
app.cfg['inbox_auto_check'] = False   # 无人值守：禁止启动弹「待入库」模态
ok = fail = 0


def check(name, cond):
    global ok, fail
    print(('PASS ' if cond else 'FAIL ') + name)
    ok, fail = ok + (1 if cond else 0), fail + (1 if not cond else 0)


def _bw(i):
    """第 i 根柱子当前的实际渲染宽度（包围盒，不是控制点差值）"""
    b = app._chart_canvas.bbox(app._chart_bar_infos[i]['id'])
    return b[2] - b[0]


# 窗口可见（Map 事件 / Motion / Canvas 动画都需要真实映射）
root.deiconify()
root.update()

# 1. spinner
app._start_search_spinner()
check('spinner 已启动', app._spinner_on is True)
root.update()
after_text = app.search_btn.cget('text')
app._stop_search_spinner()
check('spinner 停止后恢复文案',
      app.search_btn.cget('text') == f'{app.IC_SEARCH} 搜索')
check('spinner 运行中标记清除', app._spinner_on is False)

# 2. 输入框 focus 描边样式
check('搜索框克隆样式存在',
      app.kw_entry.cget('style') == 'FocusSearch.TCombobox')
check('过滤框克隆样式存在', app.fe.cget('style') == 'Filter.TEntry')
st = tk.ttk.Style()
normal_bc = st.lookup('FocusSearch.TCombobox', 'bordercolor')
app.kw_entry.focus_set()
root.update()
root.after(350, None)


def _s2():
    focus_bc = st.lookup('FocusSearch.TCombobox', 'bordercolor')
    print(f'  DEBUG border: normal={normal_bc} focus={focus_bc}')
    check('focus 描边变品牌色', focus_bc.lower() in
          ('#0f6cbd', '#4fa3f0', '#8b6f47'))
    app.root.focus_set()
    root.after(300, _s3)


def _s3():
    leave_bc = st.lookup('FocusSearch.TCombobox', 'bordercolor')
    check('失焦描边回常态', leave_bc.lower() == normal_bc.lower())
    _s4()


# 3. 柱状图生长：构造假数据
def _s4():
    # hits 必须降序：真实链路上 aggregate_results 已按 hits 倒序，图表拿
    # groups[0] 当 max_hits 做归一化（第一根＝最长的那根）
    app.groups = [
        {'name': f'测试文件{i}.docx', 'hits': 40 - i * 7,
         'path': f'C:/fake/{i}.docx', 'first_line': 1 + i,
         'status': 'active', 'clusters': []}
        for i in range(6)]
    app.groups[5]['status'] = 'void'
    app._draw_distribution_chart()
    check('柱状图生成 6 根', len(app._chart_bar_infos) == 6)
    check('生长动画进行中标记', app._chart_grown is False)
    infos = app._chart_bar_infos
    mid_w = _bw(0)
    check('首帧柱宽接近 0', mid_w < 6)
    check('柱子用 smooth 多边形绘制（不是直角矩形）',
          app._chart_canvas.type(infos[0]['id']) == 'polygon'
          and str(app._chart_canvas.itemcget(infos[0]['id'],
                                             'smooth')).lower()
          in ('1', 'true'))
    # 等全部 stagger + 生长完成（6*26+240+180 ≈ 600ms）
    root.after(900, _s5)


def _s5():
    bi = app._chart_bar_infos[0]
    final_w = _bw(0)
    check('生长结束柱宽达标', final_w >= bi['bw'] - 2)
    # 圆角矩形：每个角点写两遍 → 12 个点 / 24 个坐标（生长到 0 宽时才退化）
    check('柱子是圆角矩形（smooth 多边形，12 点 24 坐标）',
          app._chart_canvas.type(bi['id']) == 'polygon'
          and len(app._chart_canvas.coords(bi['id'])) >= 24)
    # 实测 bbox 比控制点宽 2px（样条在圆角外凸约 1px/侧），留 3px 容差
    check('柱宽就是 bw（±3）、不越出自己的列，也不再长成 2*bw 钻隔壁',
          all(_bw(i) <= app._chart_bar_infos[i]['bw'] + 3
              and _bw(i) <= app._chart_items[i]['x1'] - app._chart_items[i]['x0']
              for i in range(6)))
    check('生长结束标记', app._chart_grown is True)
    # hover 放大
    app._chart_set_hover(2)
    hover_w = _bw(2)
    check('hover 柱子加宽（+6）', hover_w >= app._chart_bar_infos[2]['bw'] + 4)
    app._chart_set_hover(-1)
    rest_w = _bw(2)
    check('离开恢复原宽（±3，含样条外凸）',
          rest_w <= app._chart_bar_infos[2]['bw'] + 3)
    # 柱子与文件名/命中数字不得重叠（hover 向上长会啃到文件名）
    cv = app._chart_canvas

    def _gap(j):
        bi = cv_bi = app._chart_bar_infos[j]
        return (cv.bbox(cv_bi['id'])[1] - cv.bbox(cv_bi['lbl'])[3],
                cv.bbox(cv_bi['num'])[1] - cv.bbox(cv_bi['id'])[3])

    app._chart_set_hover(-1)
    g0 = _gap(0)
    check('常态：柱顶与文件名底留出安全间距', g0[0] >= 3)
    check('常态：柱底与命中数字不贴', g0[1] >= 5)
    app._chart_set_hover(1)
    g1 = _gap(1)
    check('hover：柱顶不向上长（仍不压文件名）',
          g1[0] >= 3 and cv.bbox(app._chart_bar_infos[1]['id'])[1]
          == cv.bbox(app._chart_bar_infos[0]['id'])[1])
    check('hover：加厚后仍不碰到命中数字', g1[1] >= 3)
    check('柱子厚度够（≥12px，不再是细线）',
          cv.bbox(app._chart_bar_infos[0]['id'])[3]
          - cv.bbox(app._chart_bar_infos[0]['id'])[1] >= 12)
    app._chart_set_hover(-1)
    _s6()


# 4. 弹窗自动弹入委托
def _s6():
    w = tk.Toplevel(root)
    w.geometry('200x120+100+100')
    root.update()
    root.after(400, lambda: _s7(w))


def _s7(w):
    try:
        a = w.attributes('-alpha')
    except Exception:
        a = 1.0
    check('弹窗淡入结束 alpha=1', abs(float(a) - 1.0) < 0.05)
    check('弹窗已登记委托', id(w) in app._popup_seen)
    w.destroy()
    _s8()


# 5. 结果树行 hover
def _s8():
    app.results = []
    app._ctx_map = {}
    # 用真实 _populate_tree 流程：构造 groups（带 clusters 才能插命中行）
    app.groups = [{
        'name': 'hover测试.docx', 'hits': 2, 'path': 'C:/fake/h.docx',
        'first_line': 1, 'status': 'active',
        'clusters': [
            {'line_start': 1, 'line_end': 1,
             'lines': [{'line_no': 1, 'line_text': '甲'}]},
            {'line_start': 2, 'line_end': 2,
             'lines': [{'line_no': 2, 'line_text': '乙'}]},
        ],
    }]
    app.keyword.set('测试')
    app._populate_tree()
    root.update()
    items = app.tree.get_children()
    check('结果树有内容', len(items) >= 1)
    iid = items[0]
    bbox = app.tree.bbox(iid)
    check('行 bbox 可取', bbox is not None and bbox != '')
    if bbox:
        x, y, w, h = bbox
        app.tree.event_generate('<Motion>', x=x + 5, y=y + h // 2,
                                warp=False)
        root.update()
        tags = app.tree.item(iid, 'tags')
        check('hover 行已挂 hoverrow 标签', 'hoverrow' in tags)
    _s9()


# 6. 数字滚动
def _s9():
    lbl = tk.Label(root, text='0')
    anim.count_up(lbl, lambda v: lbl.configure(text=str(int(v))), 0, 42, 250)
    root.after(420, lambda: (check('数字滚动到达 42',
                                   lbl.cget('text') == '42'), _s10()))


# 7. 平滑滚动：直接投递滚轮事件（event_generate 的 delta 在测试环境不可靠）
def _s10():
    ss = anim.SmoothScroller(app.tree, 'y', step=0.35)

    class _Wheel:
        delta = -120
    ret = ss._on_wheel(_Wheel())
    check('惯性滚动已启动', ss._job is not None and ret == 'break')
    root.after(700, lambda: (check('惯性滚动已停止', ss._job is None),
                             _s11()))


# 8. AI 呼吸：两帧之间颜色发生变化
def _s11():
    colors = []
    def _sample():
        colors.append(app.qa_btn.cget('bg'))
    root.after(0, _sample)
    root.after(700, _sample)
    root.after(720, lambda: (
        check('AI 呼吸颜色在变化', len(colors) == 2
              and colors[0].lower() != colors[1].lower()),
        _s12(colors)))


def _s12(colors):
    # hover 暂停呼吸
    app._qa_breathe_pause()
    check('呼吸暂停标记', app._qa_hovered)
    _s13()


# 9. 收藏弹跳 / 角标弹跳：不报错即可
def _s13():
    app._bm_was = False
    try:
        app._bm_pop()
        check('收藏弹跳调用正常', True)
    except Exception as ex:
        check(f'收藏弹跳调用正常 ({ex})', False)
    try:
        app._badge_pop(app.err_btn)
        check('角标弹跳调用正常', True)
    except Exception as ex:
        check(f'角标弹跳调用正常 ({ex})', False)
    root.after(300, _s14)


# 10. 减弱动效：呼吸不跑、count 直跳
def _s14():
    app.reduce_motion_var.set(True)
    app._toggle_reduce_motion()
    lbl = tk.Label(root, text='x')
    anim.count_up(lbl, lambda v: lbl.configure(text=str(int(v))), 0, 99, 300)
    root.update()
    check('减弱动效下数字直跳', lbl.cget('text') == '99')
    app.reduce_motion_var.set(False)
    app._toggle_reduce_motion()
    root.quit()


root.after(300, _s2)
root.mainloop()
sys.stdout.flush()
print(f'\n{ok} passed, {fail} failed')
os._exit(1 if fail else 0)
