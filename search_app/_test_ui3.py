# -*- coding: utf-8 -*-
"""第三波 UI 美化回归：winchrome / 细滚动条 / 品牌进度条 / 分栏线 / MDL2 图标。
无头可跑（窗口 withdraw，不进 mainloop）。"""
import os
import sys
import tempfile
import ctypes
import time
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import indexer
import main
import searcher
import winchrome

# ---------- 临时库，不碰真实索引 ----------
main.App._start_hotkeys = lambda self: None     # 避免热键被占用时弹模态
db = os.path.join(tempfile.mkdtemp(prefix='jzl_'), 't.db')
lib = tempfile.mkdtemp(prefix='jzl_lib_')
_old_lib = indexer.LIBRARY_DIR
try:
    indexer.LIBRARY_DIR = lib
    f = os.path.join(lib, 'x.txt')
    with open(f, 'w', encoding='utf-8') as fh:
        fh.write('第一条 总则\n本制度依据《安全生产法》制定\n')
    indexer.index_single_file(f, db)
finally:
    indexer.LIBRARY_DIR = _old_lib
main.DB_PATH = db

try:
    from tkinterdnd2 import TkinterDnD
    root = TkinterDnD.Tk()
except Exception:
    root = tk.Tk()
root.withdraw()

_passed = 0
def check(name, cond):
    global _passed
    assert cond, f'FAIL: {name}'
    _passed += 1
    print(f'  ok {_passed:02d}  {name}')

# ---------- 1. winchrome：幂等安装 / 不抛异常 ----------
_orig_popup = tk.Menu.tk_popup
app = main.App(root)
root.update_idletasks()
# 固定从暗色起步（本机 config 可能为浅色，断言按暗色基准）
app.dark.set(True)
app._apply_ui_theme()
root.update_idletasks()
check('install 已替换 Menu.tk_popup', tk.Menu.tk_popup is not _orig_popup)
winchrome.install(root, lambda: app.dark.get())
winchrome.install(root, lambda: app.dark.get())
check('install 重复调用不抛异常且只包一层', tk.Menu.tk_popup is not _orig_popup)
winchrome.dark_owned_menus(True)
winchrome.dark_owned_menus(False)
check('dark_owned_menus 无菜单时不抛异常', True)
winchrome.apply_window(root, True)
winchrome.apply_window(root, False)
check('apply_window 任意调用不抛异常', True)

# ---------- 2. 深色标题栏 DWM 属性实写 ----------
IS_WIN = sys.platform.startswith('win')
if IS_WIN:
    hwnd = winchrome._caption_hwnd(root)
    check('_caption_hwnd 返回正整数句柄', isinstance(hwnd, int) and hwnd > 0)

    def _dark_attr(h):
        v = ctypes.c_int(-1)
        hr = ctypes.windll.dwmapi.DwmGetWindowAttribute(
            h, 20, ctypes.byref(v), ctypes.sizeof(v))
        return hr, v.value

    winchrome.apply_window(root, True)
    root.update_idletasks()
    hr, v = _dark_attr(hwnd)
    check('深色标题栏属性写入 (attr20=1)', hr == 0 and v == 1)
    winchrome.apply_window(root, False)
    root.update_idletasks()
    hr, v = _dark_attr(hwnd)
    check('浅色标题栏属性回写 (attr20=0)', hr == 0 and v == 0)
    winchrome.apply_window(root, app.dark.get())  # 还原

# ---------- 3. 细滚动条 / 品牌进度条 / 分栏线（暗色） ----------
st = ttk.Style()
check('竖向滚动条 width=6',
      str(st.lookup('Vertical.TScrollbar', 'width')) == '6')
check('横向滚动条 height=6',
      str(st.lookup('Horizontal.TScrollbar', 'height')) == '6')
check('暗滚动条槽=#202020',
      st.lookup('Vertical.TScrollbar', 'troughcolor').lower() == '#202020')
check('暗滚动条滑块=#5A5C61',
      st.lookup('Vertical.TScrollbar', 'background').lower() == '#5a5c61')
check('Brand 进度条 thickness=6',
      str(st.lookup('Brand.Horizontal.TProgressbar', 'thickness')) == '6')
check('Brand 进度条品牌色',
      st.lookup('Brand.Horizontal.TProgressbar', 'background').upper()
      in ('#2889DE', '#0F6CBD', '#2F5E8C'))
check('分栏线 sashwidth=4',
      str(st.lookup('TPanedwindow', 'sashwidth')) == '4')
check('分栏线常态暗色 #3A3B3F',
      st.lookup('TPanedwindow', 'background').lower() == '#3a3b3f')
check('_sash_hover 品牌色', app._sash_hover in ('#0F6CBD', '#4FA3F0', '#2F5E8C'))

# ---------- 4. 亮色主题重算后跟随 ----------
# 必须走 _apply_ui_theme（它先 sv_ttk.set_theme 再 configure）：只调
# _configure_control_styles 会把浅色 TFrame 写进「当前 ttk 主题」的样式库，
# 若此刻仍是 sun-valley-dark，暗色主题的 TFrame 就被污染成米白，
# 之后切深色会漏白框（§11b 会抓到）。
app.dark.set(False)
app._apply_ui_theme()
check('亮滚动条槽=#F4F6F9',
      st.lookup('Vertical.TScrollbar', 'troughcolor').lower() == '#fdfbf6')
check('亮滚动条滑块=#C6C9CE',
      st.lookup('Vertical.TScrollbar', 'background').lower() == '#c6c9ce')
check('亮分栏线 #E3E5E8',
      st.lookup('TPanedwindow', 'background').lower() == '#e3e5e8')
check('浅色主按钮底色=档案蓝 #2F5E8C',
      st.lookup('Accent.TButton', 'background').lower() == '#8b6f47')
check('主按钮 layout 已换自建圆角元素 RndAccent',
      'RndAccentL.button' in str(st.layout('Accent.TButton'))
      and 'RndAccentL.button' in st.element_names())
check('图标主按钮同样换 RndAccent layout',
      'RndAccentL.button' in str(st.layout('Icon.Accent.TButton')))
app.dark.set(True)
app._apply_ui_theme()
check('切回暗色滚动条色',
      st.lookup('Vertical.TScrollbar', 'background').lower() == '#5a5c61')

# ---------- 5. MDL2 图标字体 ----------
fam = set(tkfont.families())
check('系统装了 Segoe MDL2 Assets', 'Segoe MDL2 Assets' in fam)
f_icon = tkfont.Font(root, font=app.FONT_ICON)
check('Icon 样式字体族为 MDL2', f_icon.actual('family') == 'Segoe MDL2 Assets')
g_w = f_icon.measure(app.IC_GEAR)
check('齿轮字形可测量（非 tofu 宽度为 0）', g_w > 0)
f_mix = tkfont.Font(root, font=app.FONT_ICON)
mix_w = f_mix.measure(app.IC_GEAR + ' 库管理')
check('MDL2 串可携带中文 fallback（更宽）', mix_w > g_w)
check('Icon.TButton 字体族',
      st.lookup('Icon.TButton', 'font') and 'MDL2' in str(st.lookup(
          'Icon.TButton', 'font')))
check('Icon.TMenubutton 字体族',
      'MDL2' in str(st.lookup('Icon.TMenubutton', 'font')))
check('顶栏齿轮按钮文本/样式',
      app.more_btn.cget('text') == app.IC_GEAR
      and str(app.more_btn.cget('style')) == 'Icon.TMenubutton')
_sb_style = str(app.search_btn.cget('style'))
import re as _re
check('搜索按钮已挂动画克隆样式 HA*.TButton',
      bool(_re.fullmatch(r'HA\d+\.TButton', _sb_style)))
check('搜索克隆样式保留 MDL2 图标字体',
      'MDL2' in str(st.lookup(_sb_style, 'font')))
check('搜索克隆样式底色为当前主题强调色',
      str(st.lookup(_sb_style, 'background')).upper() in
      ('#2889DE', '#0F6CBD', '#2F5E8C'))
check('搜索克隆样式 layout 为自建圆角元素（非 sv_ttk 精灵）',
      'RndAccent{}.button'.format('D' if app.dark.get() else 'L') in str(
          app.root.tk.call('ttk::style', 'layout', _sb_style)))

# ---------- 6. ttk sash 的 Tcl 层 identify 通路（Python 层有 bug） ----------
p = app.main_pane
ret = str(p.tk.call(str(p), 'identify', 1, 1))
check('Tcl identify 调用安全（返回索引或空串）', ret in ('', '0', '1'))

# ---------- 7. 图标常量齐全且为私用区字形 ----------
for name in ('IC_SEARCH', 'IC_GEAR', 'IC_WARN', 'IC_STAR', 'IC_STAR_FILL',
             'IC_DOWNLOAD', 'IC_CHAT', 'IC_FOLDER',
             'IC_FOLDER_ADD', 'IC_DOC', 'IC_BOOKS', 'IC_SWAP'):
    ch = getattr(app, name)
    check(f'{name} 为 MDL2 私用区字形',
          isinstance(ch, str) and len(ch) == 1 and 0xE000 <= ord(ch) <= 0xF8FF)

# ---------- 8. d5 预览正文排版 ----------
check('预览区内边距 padx=16', int(app.preview.cget('padx')) == 16)
check('预览区内边距 pady=12', int(app.preview.cget('pady')) == 12)
check('body 行距 spacing1=4',
      str(app.preview.tag_cget('body', 'spacing1')) == '4')
check('body 行距 spacing2=1',
      str(app.preview.tag_cget('body', 'spacing2')) == '1')
check('body 行距 spacing3=3',
      str(app.preview.tag_cget('body', 'spacing3')) == '3')

# ---------- 9. d6 对话框统一头部 ----------
_d = tk.Toplevel(root)   # 根窗 withdraw，子窗不映射，仅校验构造/配色
app._dialog_header(_d, app.IC_BOOKS, '测试标题', '测试副标题')
recs = [r for r in app._dlg_headers if r[0] is _d]
check('头部构造并登记', len(recs) == 1 and recs[0][5] is not None)
_band = recs[0][1]
_hair = recs[0][2]
check('暗色头部 band=#2B2D30',
      str(_band.cget('bg')).upper() == '#2B2D30')
check('暗色发丝线=#3A3B3F',
      str(_hair.cget('bg')).upper() == '#3A3B3F')
app.dark.set(False)
app._refresh_dialog_headers()
check('切浅色后 band=#FFFFFF',
      str(_band.cget('bg')).upper() == '#FFFFFF')
check('切浅色后发丝线=#E5E7EB',
      str(_hair.cget('bg')).upper() == '#E5E7EB')
app.dark.set(True)
app._refresh_dialog_headers()
_d.destroy()
root.update_idletasks()
app._refresh_dialog_headers()
check('销毁的头部记录被自动清理',
          all(r[0] is not _d for r in app._dlg_headers))

# ---------- 10. 搜索框：? 语法按钮不再压在历史下拉箭头上 ----------
check('? 挂在输入域外侧右缘（place 参照 kw_entry）',
      app._syntax_btn.winfo_manager() == 'place'
      and str(app._syntax_btn.place_info().get('in', '')) == str(app.kw_entry)
      and app._syntax_btn.place_info().get('anchor') == 'w')
check('? 与输入域之间正好隔一个空格',
      int(app._syntax_btn.place_info().get('x')) == app._sp_syntax
      and app._sp_syntax > 0)
check('右侧留了「空格+字形+空格」的占位（窗口变窄不挤没 ?）',
      app._syntax_slot.winfo_manager() == 'pack'
      and app._syntax_slot.pack_info()['side'] == 'right'
      and app._syntax_slot.winfo_reqwidth()
      == app._sp_syntax * 2 + app._syntax_btn.winfo_reqwidth())
check('? 底色跟随顶栏',
      str(app._syntax_btn.cget('bg')).upper()
      == str(ttk.Style().lookup('TFrame', 'background') or '#F4F6F9').upper())

# 清空 ✕ 现在嵌在输入域内侧右端（原挂在输入域外面，离得远）
_pi = app._clear_kw_btn.place_info()
check('清空叉用 place 放进输入域内',
      app._clear_kw_btn.winfo_manager() == 'place'
      and str(_pi.get('in', '')) == str(app.kw_entry))
check('清空叉靠输入域右端、右侧留出下拉箭头的位置',
      _pi.get('anchor') == 'e' and str(_pi.get('relx')) in ('1', '1.0')
      and int(_pi.get('x')) == -26)
app.dark.set(False)
app._apply_ui_theme()
root.update_idletasks()
check('亮色清空叉底色 = 输入域内底色（与放大镜一致，不再是顶栏色）',
      str(app._clear_kw_btn.cget('bg')).upper() == '#FFFFFF'
      and str(app._search_icon.cget('bg')).upper() == '#FFFFFF'
      and str(app._syntax_btn.cget('bg')).upper() != '#FFFFFF')
app.dark.set(True)
app._apply_ui_theme()
root.update_idletasks()
check('暗色清空叉底色跟随输入域 #2D2E32',
      str(app._clear_kw_btn.cget('bg')).upper() == '#2D2E32')
app.dark.set(False)
app._apply_ui_theme()
root.update_idletasks()
check('清空叉不再与 ? 抢同一排（? 在输入域外）',
      str(_pi.get('in', '')) != str(app._syntax_btn))
# 两者都以输入域为参照、都取 rely=0.5 → 纵向必然落在同一条水平中线上；
# ✕ 贴右缘内侧（anchor=e）、? 贴右缘外侧（anchor=w），互不重叠
_qi = app._syntax_btn.place_info()
check('✕ 与 ? 同以输入域为参照且都取中线 rely=0.5（水平线对齐）',
      str(_pi.get('in', '')) == str(app.kw_entry)
      and str(_qi.get('in', '')) == str(app.kw_entry)
      and str(_pi.get('rely')) in ('0.5', '.5')
      and str(_qi.get('rely')) in ('0.5', '.5'))
check('✕ 居输入域内侧右端、? 居外侧（一内一外，互不遮盖）',
      _pi.get('anchor') == 'e' and _qi.get('anchor') == 'w')

# ---------- 11. 分栏条双箭头光标必须复位 ----------
app.main_pane.configure(cursor='sb_h_double_arrow')
app.main_pane.event_generate('<Leave>')
root.update_idletasks()
check('离开竖直分栏条后双箭头光标复位',
      app.main_pane.cget('cursor') == '')
app.combined_pane.configure(cursor='sb_v_double_arrow')
app.combined_pane.event_generate('<Leave>')
root.update_idletasks()
check('离开水平分栏条后双箭头光标复位',
      app.combined_pane.cget('cursor') == '')

# ---------- 11b. 深色模式不得残留浅色（白框根因：闭包捕获浅色 + 根窗底没刷）----
_LIGHT_TONES = ('#DFE4EA', '#2F5E8C', '#F4F6F9')
_st = ttk.Style()
app.dark.set(True)
app._apply_ui_theme()
root.update_idletasks()
check('深色：分栏线 / 经典分栏底 / 根窗底 / 标题栏都不是浅色',
      str(_st.lookup('TPanedwindow', 'background')).upper() == '#3A3B3F'
      and str(app.combined_pane.cget('bg')).upper() == '#3A3B3F'
      and str(root.cget('bg')).upper() not in _LIGHT_TONES
      and str(app._cap['bar'].cget('bg')).upper() not in _LIGHT_TONES)
_sx = None
# identify 需要真实布局：临时映射窗口拿分栏坐标（跑完再 withdraw）
root.deiconify()
root.geometry('1180x760+40+40')
root.update()
for _a in range(2, app.main_pane.winfo_width()):
    try:
        if str(app.main_pane.tk.call(str(app.main_pane), 'identify', _a, 5)):
            _sx = _a
            break
    except Exception:
        pass
check('深色：分栏条能在画布坐标里被命中（悬停验证的前提）', _sx is not None)
if _sx is not None:
    app.main_pane.event_generate('<Motion>', x=_sx, y=5, warp=False)
    root.update()
    check('深色：悬停分栏条变深色品牌蓝 #0F6CBD（不是浅金 #2F5E8C）',
          str(_st.lookup('TPanedwindow', 'background')).upper() == '#0F6CBD')
    app.main_pane.event_generate('<Motion>', x=2, y=2, warp=False)
    root.update()
    check('深色：移开分栏条后回深色 #3A3B3F（不刷回浅灰 #E3E5E8）',
          str(_st.lookup('TPanedwindow', 'background')).upper() == '#3A3B3F')
root.withdraw()
app.dark.set(False)
app._apply_ui_theme()
root.update_idletasks()

# ---------- 12. 搜索记录下拉：鼠标移出即收回 ----------
check('已挂上下拉自动收回绑定', getattr(app, '_hist_bound', False) is True)
_pop = getattr(app, '_hist_pop', '')
check('拿到 Combobox 弹出列表的 Tcl 路径',
      str(app.kw_entry).split('!')[-1] in _pop and _pop.endswith('popdown'))
# 弹出列表不是 tkinter 控件，只能用 Tcl 层 bind 查证
_bind_leave = [app.kw_entry.tk.call('bind', w, '<Leave>')
               for w in (_pop, _pop + '.f', _pop + '.f.l')]
_bind_enter = [app.kw_entry.tk.call('bind', w, '<Enter>')
               for w in (_pop, _pop + '.f', _pop + '.f.l')]
check('外壳/内容框/listbox 三层都绑了 Enter+Leave',
      all(str(s).strip() for s in _bind_leave)
      and all(str(s).strip() for s in _bind_enter))

app._hist_on_enter()
app._hist_on_leave()
check('Leave 后挂了待收回定时器', app._hist_timer is not None)
app._hist_on_enter()
check('Enter 回来即取消待收回', app._hist_timer is None)

# 分支真值：还在范围内不收，离开了才 Unpost
class _SpyEntry:
    def __init__(self, real):
        self._real, self.tk, self.calls = real, self, []

    def call(self, *a):
        self.calls.append(a)


_orig_entry = app.kw_entry
_orig_pip = app._pointer_in_path
_spy = _SpyEntry(_orig_entry)
app.kw_entry = _spy
try:
    app._pointer_in_path = lambda *p: True
    app._hist_close_if_out()
    check('指针还在搜索记录范围内 → 不收回', _spy.calls == [])
    app._pointer_in_path = lambda *p: False
    app._hist_close_if_out()
    check('指针移出搜索记录范围 → Unpost 收回',
          len(_spy.calls) == 1
          and _spy.calls[0][0] == 'ttk::combobox::Unpost')
finally:
    app.kw_entry = _orig_entry
    app._pointer_in_path = _orig_pip

# _pointer_in_path 自身：自身 / 子孙 / 无关控件
_orig_cont = app.root.winfo_containing
try:
    app.root.winfo_containing = lambda x, y: app.kw_entry
    check('指针停在目标控件上 → 命中', app._pointer_in_path(str(app.kw_entry)))
    _child = app.kw_entry.winfo_children()
    app.root.winfo_containing = lambda x, y: app.search_btn
    check('指针在无关控件上 → 不命中',
          not app._pointer_in_path(str(app.kw_entry)))
    app.root.winfo_containing = lambda x, y: None
    check('指针不在本程序窗口上 → 不命中',
          not app._pointer_in_path(str(app.kw_entry)))
finally:
    app.root.winfo_containing = _orig_cont

# ---------- 13. 语法速查卡：点击显现 / 再点收回 / 移出即收回 ----------
app._show_syntax_card()
root.update_idletasks()
_sw = app._wins.get('syntax')
check('点 ? 即显现速查卡（不再是每次只 lift）',
      _sw is not None and _sw.winfo_exists())
# 窗体本身 + 每个后代都要绑，漏一层就会出现「鼠标滑到字上没收」
_stacked, _need = [], [_sw]
while _need:
    _w = _need.pop()
    _stacked.append(_w)
    _need.extend(_w.winfo_children())
check('速查卡及其所有子控件都绑了 Enter/Leave',
      all(_w.bind('<Enter>') and _w.bind('<Leave>') for _w in _stacked)
      and len(_stacked) > 5)
check('速查卡定位在 ? 按钮正下方',
      _sw.winfo_rootx() >= app._syntax_btn.winfo_rootx()
      and _sw.winfo_y() >= 0)
app._show_syntax_card()          # 再点一次
root.update_idletasks()
check('再点 ? 收回速查卡', 'syntax' not in app._wins)
app._show_syntax_card()          # 还能再打开
root.update_idletasks()
check('收回后仍可重新打开', (app._wins.get('syntax') or None) is not None
      and app._wins['syntax'].winfo_exists())

# 移出即收回：模拟 Leave → 指针落点判定为窗外 → 销毁
_swn = app._wins['syntax']
_orig_pip2 = app._pointer_in_path
app._pointer_in_path = lambda *p: False
try:
    _swn.event_generate('<Leave>')
    root.update()
    _t0 = time.time()
    while time.time() - _t0 < 1.0 and 'syntax' in app._wins:
        time.sleep(0.03)
        root.update()
finally:
    app._pointer_in_path = _orig_pip2
check('鼠标移出速查卡即收回',
      ('syntax' not in app._wins)
      or not app._wins['syntax'].winfo_exists())

# ---------- 14. 流畅度三项固化回归（分批 / 取消 / 过滤缓存） ----------
app.filter_var.set('')


def _mk_groups(n):
    return [dict(name=f'文件{i:02d}.txt', path=f'C:/x/文件{i:02d}.txt',
                 hits=1, status='active',
                 clusters=[dict(line_start=i + 1, line_end=i + 1,
                                lines=[dict(line_no=i + 1,
                                            line_text='命中行内容',
                                            cross_text='', score=1.0)])])
            for i in range(n)]


# 14a 结果树按行预算分批入场：首帧只铺一屏，其余分帧补满
app.groups = _mk_groups(60)
app.keyword.set('命中')
app._populate_tree()
_first_files = app.tree.get_children()
_first_rows = sum(1 + len(app.tree.get_children(f)) for f in _first_files)
check('分批：首帧未一次铺满（60 组只插一部分）', 0 < len(_first_files) < 60)
check('分批：首帧行数受 _BATCH_FIRST 约束（≤30 行）',
      _first_rows <= app._BATCH_FIRST)
check('分批：行预算常量满足 首帧 < 常规帧',
      app._BATCH_FIRST < app._BATCH_ROWS <= 200)
_t0 = time.time()
while time.time() - _t0 < 5.0 and len(app.tree.get_children()) < 60:
    root.update()
    time.sleep(0.005)
check('分批：分帧把 60 个文件全部补满', len(app.tree.get_children()) == 60)

# 14b 取消令牌：SQLite 进度钩子能毫秒级中断长查询，_ck 抛 SearchCancelled
import sqlite3
import inspect
_cx = sqlite3.connect(':memory:')
_cx.execute('create table t(a text)')
_cx.executemany('insert into t values(?)', [('abcdefghij',)] * 200000)
_cx.commit()
searcher._install_cancel(_cx, lambda: True, every=5000)
_interrupted = False
try:
    _cx.execute("select count(*) from t where a like '%xyz%'").fetchone()
except sqlite3.OperationalError:
    _interrupted = True
_cx.close()
check('取消：进度钩子在长查询中断（interrupted）', _interrupted)
try:
    searcher._ck(lambda: True)
    _raised = False
except searcher.SearchCancelled:
    _raised = True
check('取消：_ck 在取消置位时抛 SearchCancelled', _raised)
check('取消：search / search_fuzzy 均暴露 cancel 参数',
      'cancel' in inspect.signature(searcher.search).parameters
      and 'cancel' in inspect.signature(searcher.search_fuzzy).parameters)

# 14c 结果内过滤：小写文本预缓存，过滤只做包含判定、不再现场 .lower()
_g = [dict(name='安全生产制度.txt', path='C:/x/a.txt', hits=1, status='active',
           clusters=[dict(line_start=1, line_end=1,
                          lines=[dict(line_no=1, line_text='本制度依据安全生产法',
                                      cross_text='', score=1.0)])])]
check('过滤缓存：预热前无 _low_name / _low',
      '_low_name' not in _g[0] and '_low' not in _g[0]['clusters'][0])
main.App._prepare_filter_cache(_g)
check('过滤缓存：_low_name = 文件名小写',
      _g[0]['_low_name'] == '安全生产制度.txt')
check('过滤缓存：_low = 行文本小写元组',
      _g[0]['clusters'][0]['_low'] == [('本制度依据安全生产法', '')])
_g[0]['_low_name'] = '哨兵'
main.App._prepare_filter_cache(_g)
check('过滤缓存：幂等（已缓存的不重算）', _g[0]['_low_name'] == '哨兵')
# 把缓存换成原文里没有的哨兵词：仍能命中 → 证明判定走的是缓存而非现场 .lower()
_g[0]['_low_name'] = ''
_g[0]['clusters'][0]['_low'] = [('哨兵词', '')]
app.groups = _g
app.filter_var.set('哨兵词')
app._populate_tree()
check('过滤缓存：命中判定只读 _low 缓存（原文不含该词也命中）',
      len(app.tree.get_children()) == 1)
app.filter_var.set('')

# 14d 自绘标题按钮悬停：指针挪进正文（客户区）也必须清掉高亮
_cap_st = {'inner': False, 'root': 0, 'hover': winchrome._HTCLOSE,
           'tracked': True, 'track_hwnd': 0, 'maxed': False, 'widget': root}
check('标题高亮：初始为已高亮', _cap_st['hover'] == winchrome._HTCLOSE)
winchrome._clear_cap_hover_on_move(_cap_st, 0)   # hwnd=0 → 命中测试必失败
check('标题高亮：客户区移动后高亮被清掉', not _cap_st['hover'])
check('标题高亮：无高亮时补判不改状态（幂等、零副作用）',
      winchrome._clear_cap_hover_on_move(_cap_st, 0) is None
      and not _cap_st['hover'])
winchrome.pump_events(root)      # 派发积压的 <<WcBtnLeave>>，不留脏队列
root.update()

# 15a. 关掉 DWM 过渡（黑帧修复的前提）:幂等、静默降级、不动透明度
_A0 = root.attributes('-alpha')
winchrome.disable_transitions(root)
winchrome.disable_transitions(root)
check('disable_transitions 不改窗口透明度',
      float(root.attributes('-alpha')) == float(_A0))
try:
    winchrome.disable_transitions(None)
    _soft = True
except Exception:
    _soft = False
check('disable_transitions 幂等且异常入参静默降级', _soft)

# 15b. 还原防黑帧：任务栏最小化**不发 <Unmap>**、Tk state() 也恒报 'normal'
#      （实测），所以只能常驻轮询 IsIconic。还原当刻会露 47~150ms 纯黑，且
#      纯抢画无效（窗口表面已画好、屏幕仍黑，黑帧来自 DWM 过渡动画）——
#      改用「冻结帧遮罩」：最小化前抓一张干净画面，还原当刻贴上去盖住。
check('winchrome 不再保留压透明的窗口过程钩子',
      not hasattr(winchrome, '_go_transparent'))
check('旧的「还原遮罩」已彻底移除（不再动窗口透明度）',
      not hasattr(app, '_restore_cover') and not hasattr(app, '_reveal_main')
      and not hasattr(app, '_on_main_map'))
check('还原防黑帧就绪（常驻轮询 + 冻结帧遮罩）',
      all(hasattr(app, n) for n in
          ('_watch_restore', '_poll_restore', '_build_cover', '_show_cover',
           '_hide_cover', '_grab_restore_cover')))

# 遮罩窗必须常驻映射在屏幕外：撤掉的窗重新映射时首帧未初始化（纯黑），
# 实测正是残留的那 1 帧黑，所以不 withdraw、只挪位置。
_cv = app._cover
check('遮罩窗常驻映射在屏幕外 + 无边框 + 置顶',
      _cv is not None and _cv.winfo_exists() and _cv.winfo_x() < -10000
      and bool(_cv.overrideredirect()) and bool(_cv.attributes('-topmost')))

# 最小化守卫：窗口过程拦下 SC_MINIMIZE 只打标记（在里面做 ctypes 调用会因
# 嵌套回调丢线程状态 → GIL 崩），抓帧与投递最小化都回到主线程正常上下文。
_h = winchrome._caption_hwnd(root)
check('最小化守卫已装上（SC_MINIMIZE 先被拦下以便抓帧）',
      bool(winchrome._MIN_GUARDS.get(_h)))
winchrome.set_minimize_guard(root, False)
check('守卫可关（投递最小化前要先撤掉）',
      not winchrome._MIN_GUARDS.get(_h))
winchrome.set_minimize_guard(root, True)
check('守卫可再装回', bool(winchrome._MIN_GUARDS.get(_h)))

# 轮询状态机：隐藏 → 还原 → 贴遮罩 → 连续抢画 → 到点撤遮罩
_calls = []
_orig_update = root.update
_orig_state = root.state
root.update = lambda *a, **k: _calls.append(1)
app._restore_hw = 0                    # 强制走 root.state() 分支
root.state = lambda *a: 'iconic'
app._restore_hidden = False
app._restore_keep = 0
app._poll_restore()
check('仍在隐藏：只记状态，不抢画', app._restore_hidden is True
      and app._restore_keep == 0 and not _calls)
root.state = lambda *a: 'normal'
app._poll_restore()
check('一发现还原就开始连续抢画（同时已贴遮罩）',
      app._restore_hidden is False
      and app._restore_keep == main.RESTORE_KEEP_TICKS - 1
      and len(_calls) == 1)
for _ in range(main.RESTORE_KEEP_TICKS):
    app._poll_restore()
check('抢画窗口结束后停手 + 遮罩挪回屏幕外',
      app._restore_keep == 0 and _cv.winfo_x() < -10000)
root.state = _orig_state
root.update = _orig_update
app._restore_hw = None
check('轮询自身永不抛异常', app._poll_restore() is None)

# 15c. 点叉语义：会话级记忆（首次询问 → 本次沿用 → 进程结束即失效）
check('旧的持久化开关已彻底移除',
      not hasattr(app, 'tray_close_var')
      and not hasattr(app, '_toggle_close_to_tray'))
_orig_save = main.save_config
main.save_config = lambda *a, **k: None      # 别动真实 config.json
app.cfg.pop('close_to_tray', None)
app._save_config()
check('_save_config 不再落盘 close_to_tray',
      'close_to_tray' not in app.cfg)
main.save_config = _orig_save
check('新实例未选择过（下次启动重新问）', app._close_choice is None)


def _dlg_btn(text):
    for w in root.winfo_children():
        if not isinstance(w, tk.Toplevel) or not w.winfo_exists():
            continue
        stack = [w]
        while stack:
            cur = stack.pop()
            for c in cur.winfo_children():
                if isinstance(c, ttk.Button) and c.cget('text') == text:
                    return c
                stack.append(c)
    return None


root.after(250, lambda: _dlg_btn('直接退出').invoke())
check('首次点叉：弹框且「直接退出」返回 exit',
      app._ask_close_choice() == 'exit')
root.after(250, lambda: _dlg_btn('最小化到托盘').invoke())
check('「最小化到托盘」返回 tray', app._ask_close_choice() == 'tray')
root.after(250, lambda: [w.destroy() for w in root.winfo_children()
                         if isinstance(w, tk.Toplevel) and w.winfo_exists()
                         and w.title() == '关闭窗口'])
check('关掉询问框 = 取消（返回 None，什么都不做）',
      app._ask_close_choice() is None)

app._close_choice = None
root.after(250, lambda: _dlg_btn('直接退出').invoke())
check('首次询问后写入会话记忆',
      app._resolve_close_choice() == 'exit' and app._close_choice == 'exit')
check('本次内再点叉直接照做（不再弹框）',
      app._resolve_close_choice() == 'exit'
      and _dlg_btn('直接退出') is None)
app._force_quit = True
check('托盘右键退出置 _force_quit → 不问直接退',
      app._resolve_close_choice() == 'exit')
app._force_quit = False
app._close_choice = 'tray'
check('记忆为托盘时点叉按托盘走', app._resolve_close_choice() == 'tray')

# 16. 搜索连接线程级复用 + 陈旧取消钩子清理
_c1 = searcher._conn(db)
searcher._search_impl(db, '安全生产', cancel=lambda: False)
check('查询后连接被复用（同线程同库同连接）', searcher._conn(db) is _c1)
_tok = {'v': False}
searcher._search_impl(db, '安全生产', cancel=lambda: _tok['v'])
_tok['v'] = True   # 模拟陈旧令牌：若钩子残留，下一条查询会被误中断
check('陈旧取消钩子已清理（复用连接不被误中断）',
      searcher._conn(db).execute(
          'SELECT COUNT(*) FROM documents').fetchone()[0] == 1)
check('上下文行查询走复用连接后正常返回',
      len(searcher.get_context(db, os.path.join(lib, 'x.txt'), 2,
                               before=1, after=1)) >= 1)

root.destroy()
print(f'\n全部通过：{_passed} 项')
