# -*- coding: utf-8 -*-
"""Windows 窗口镀铬层（DWM）：Win11 圆角 + 深色标题栏 + 深色原生菜单。

全部能力通过 ctypes 调用系统 API：
- 低版本 Windows / 属性不支持时 DwmSetWindowAttribute 返回错误码，静默忽略；
- 非 Windows 平台导入即降级为空操作；
- 不改变任何窗口行为，只改系统绘制层（标题栏颜色、圆角策略、菜单底色）。
"""
from __future__ import annotations
import os
import sys
import time
import ctypes
from ctypes import wintypes

_IS_WIN = sys.platform.startswith('win')

# DWM 属性常量
_DWMWA_WINDOW_CORNER_PREFERENCE = 33
_DWMWCP_ROUND = 2
_DWMWA_USE_IMMERSIVE_DARK_MODE = 20      # Win10 2004+ 正式值
_DWMWA_USE_IMMERSIVE_DARK_MODE_OLD = 19  # 早期 Insider build
_DWMWA_BORDER_COLOR = 34
_DWMWA_TRANSITIONS_FORCEDISABLED = 3

if _IS_WIN:
    try:
        _dwmapi = ctypes.windll.dwmapi
        _user32 = ctypes.windll.user32
        _DwmSetWindowAttribute = _dwmapi.DwmSetWindowAttribute
        _DwmSetWindowAttribute.argtypes = (
            wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD)
        _DwmSetWindowAttribute.restype = ctypes.c_long
        _DwmExtendFrameIntoClientArea = _dwmapi.DwmExtendFrameIntoClientArea
        _DwmExtendFrameIntoClientArea.restype = ctypes.c_long
        _EnumWindowsProc = ctypes.WINFUNCTYPE(
            wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        # 只枚举本线程（GUI 线程）的窗口：菜单 #32768 必然属于创建它的
        # 线程，EnumWindows 却要逐个问遍全桌面（含所有后台进程）的窗口类名，
        # 在菜单悬停这种高频路径上是纯浪费。
        _EnumThreadWindows = _user32.EnumThreadWindows
        _EnumThreadWindows.argtypes = (wintypes.DWORD, _EnumWindowsProc,
                                       wintypes.LPARAM)
        _EnumThreadWindows.restype = wintypes.BOOL
        _GetCurrentThreadId = ctypes.windll.kernel32.GetCurrentThreadId
        _GetClassNameW = _user32.GetClassNameW
        _GetWindowThreadProcessId = _user32.GetWindowThreadProcessId
        _GetAncestor = _user32.GetAncestor
        _GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
        _GetAncestor.restype = wintypes.HWND
    except Exception:
        _IS_WIN = False


def _hwnd_of(widget) -> int:
    """tk 控件 → 顶层窗口 HWND（winfo_id 对 Toplevel/Tk 即其窗口句柄）"""
    try:
        return int(widget.winfo_id())
    except Exception:
        return 0


def _caption_hwnd(widget) -> int:
    """真正带标题栏的 Win32 顶层句柄。

    Tk 窗口映射后 winfo_id() 返回的是内部 TkChild，其父级 TkTopLevel
    才是标题栏宿主；映射前 TkChild 自己就是根。用 GetAncestor 两种时机都覆盖。"""
    inner = _hwnd_of(widget)
    if not inner or not _IS_WIN:
        return inner
    try:
        top = int(_GetAncestor(inner, 2))   # GA_ROOT = 2
        return top or inner
    except Exception:
        return inner


def _set_attr(hwnd: int, attr: int, value: int) -> bool:
    if not _IS_WIN or not hwnd:
        return False
    try:
        v = ctypes.c_int(value)
        return _DwmSetWindowAttribute(
            hwnd, attr, ctypes.byref(v), ctypes.sizeof(v)) == 0
    except Exception:
        return False


def window_rect(hwnd: int):
    """窗口外框 (left, top, right, bottom)，失败返回 None。

    注意：本模块把 user32.GetWindowRect 的 argtypes 固定成了自己的 _RECT，
    外部若传 wintypes.RECT 会直接抛 ArgumentError，所以统一从这里取。"""
    if not _IS_WIN or not hwnd:
        return None
    try:
        rc = _RECT()
        if _user32.GetWindowRect(hwnd, ctypes.byref(rc)):
            return (rc.left, rc.top, rc.right, rc.bottom)
    except Exception:
        pass
    return None


def set_dark_titlebar(hwnd: int, dark: bool):
    """标题栏 + 系统菜单走深色/浅色（先试正式属性，再试旧值）"""
    v = 1 if dark else 0
    if not _set_attr(hwnd, _DWMWA_USE_IMMERSIVE_DARK_MODE, v):
        _set_attr(hwnd, _DWMWA_USE_IMMERSIVE_DARK_MODE_OLD, v)


def set_rounded(hwnd: int):
    """Win11：显式请求圆角（默认已是圆角；显式设置保证覆盖系统策略）"""
    _set_attr(hwnd, _DWMWA_WINDOW_CORNER_PREFERENCE, _DWMWCP_ROUND)


class _MARGINS(ctypes.Structure):
    _fields_ = (('left', ctypes.c_int), ('right', ctypes.c_int),
                ('top', ctypes.c_int), ('bottom', ctypes.c_int))


def enable_popup_shadow(hwnd: int):
    """给 overrideredirect 无边框弹出窗补 DWM 投影。

    原理：把帧扩展 1px 到客户区，DWM 即按普通顶层窗给阴影；
    调用方通常已有 1px 自描边，扩展区与描边重合、不产生可见色差。"""
    if not _IS_WIN or not hwnd:
        return
    try:
        m = _MARGINS(1, 1, 1, 1)
        _DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(m))
    except Exception:
        pass


def apply_window(widget, dark: bool):
    """对一个 Tk/Toplevel 应用：圆角 + 标题栏配色。任何失败静默。
    必须在窗口已映射时调用（未映射时句柄是 TkChild，标题栏宿主尚未建立）；
    install() 已为主窗挂 <Map> 自动补刷，Toplevel 由调用方在 Map 时调用。"""
    if not _IS_WIN:
        return
    try:
        hwnd = _caption_hwnd(widget)
        if not hwnd:
            return
        set_rounded(hwnd)
        set_dark_titlebar(hwnd, dark)
    except Exception:
        pass


# ---------- 原生弹出菜单深色 ----------

_PID = os.getpid() if _IS_WIN else 0


def dark_owned_menus(dark: bool):
    """把本进程所有已弹出的标准菜单（#32768）刷成深/浅色。

    Tk 的级联子菜单由系统在 hover 时创建，Python 层无通知，
    因此在 <<MenuSelect>> / post 钩子中重复枚举即可，开销极小。"""
    if not _IS_WIN:
        return

    def _enum_cb(hwnd, _lparam):
        try:
            buf = ctypes.create_unicode_buffer(32)
            if _GetClassNameW(hwnd, buf, 32) and buf.value == '#32768':
                pid = wintypes.DWORD()
                _GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if pid.value == _PID:
                    set_dark_titlebar(hwnd, dark)
        except Exception:
            pass
        return True

    try:
        _EnumThreadWindows(_GetCurrentThreadId(),
                           _EnumWindowsProc(_enum_cb), 0)
    except Exception:
        pass


def _menu_opts(dark: bool):
    """Tk 原生菜单在深色主题下必须显式指定的配色。

    菜单 bg/fg 已由主题层（sv_ttk）配好，这里只动 selectcolor——
    它控制 check/radio 勾号颜色，默认跟随 SystemMenuText（深色），
    深色菜单上勾号会隐形；显式给高反差色即可。返回 dict 供 configure。"""
    return {'selectcolor': '#FFFFFF' if dark else '#1C1C1C'}


# ---------- 全局安装 ----------

_installed = False


def install(tk_root, get_dark):
    """安装一次：
    1) monkey-patch tk.Menu.tk_popup / post —— 右键菜单与菜单栏弹出后刷深色；
    2) bind_all <<MenuSelect>> —— 级联子菜单展开时补刷；
    3) 每次弹出菜单时强制刷新所有菜单配色（含 check/radio 标记色），
       解决深色主题下勾号看不见的问题；
    get_dark() -> bool 实时返回当前是否深色模式。
    """
    global _installed
    if not _IS_WIN or _installed:
        return
    try:
        import tkinter as tk
    except Exception:
        return

    import time as _time
    _enum_st = {'last': 0.0}

    def _refresh():
        try:
            dark_owned_menus(bool(get_dark()))
        except Exception:
            pass

    def _refresh_throttled(interval=0.25):
        """窗口枚举节流版：<<MenuSelect>> 悬停时高频触发，
        无节流的 EnumWindows 会持续抢占主线程造成卡顿。"""
        now = _time.monotonic()
        if now - _enum_st['last'] >= interval:
            _enum_st['last'] = now
            _refresh()

    import weakref
    known = getattr(install, '_known_menus', None)
    if known is None:
        known = install._known_menus = weakref.WeakSet()

    def _apply_menu_opts(menu, dark):
        """只在主题状态变化时 configure。

        对「已弹出」的菜单反复 configure（哪怕值相同）会触发系统重绘，
        鼠标悬停时与 active 高亮重绘互相打断 → 菜单持续闪烁。
        在菜单对象上缓存上次应用的主题，相同则直接跳过。"""
        if getattr(menu, '_wc_sel_dark', None) == dark:
            return
        try:
            menu.configure(**_menu_opts(dark))
            menu._wc_sel_dark = dark
        except Exception:
            pass

    def _refresh_menu_tree(menu, dark):
        """递归给整棵菜单树刷配色（含级联子菜单与勾号色）。"""
        _apply_menu_opts(menu, dark)
        try:
            known.add(menu)
        except Exception:
            pass
        try:
            end = menu.index('end')
        except Exception:
            return
        if end is None:
            return
        for i in range(end + 1):
            try:
                if menu.type(i) == 'cascade':
                    sub_name = menu.entrycget(i, 'menu')
                    if sub_name:
                        sub = menu.nametowidget(sub_name)
                        if sub and str(sub) != str(menu):
                            _refresh_menu_tree(sub, dark)
            except Exception:
                pass

    def _refresh_all_menus():
        dark = bool(get_dark())
        try:
            menubar_name = tk_root.cget('menu')
            if menubar_name:
                _refresh_menu_tree(tk_root.nametowidget(menubar_name), dark)
        except Exception:
            pass
        for m in list(known):
            try:
                _refresh_menu_tree(m, dark)
            except Exception:
                pass
        _refresh()

    _orig_popup = tk.Menu.tk_popup
    _orig_post = tk.Menu.post

    def _popup(self, x, y, entry=''):
        # 弹出前先把这棵菜单刷成当前主题色，再让系统弹
        try:
            _refresh_menu_tree(self, bool(get_dark()))
        except Exception:
            pass
        r = _orig_popup(self, x, y, entry)
        tk_root.after(1, _refresh)
        tk_root.after(40, _refresh)   # 级联首跳
        return r

    def _post(self, x, y):
        try:
            _refresh_menu_tree(self, bool(get_dark()))
        except Exception:
            pass
        r = _orig_post(self, x, y)
        tk_root.after(1, _refresh)
        tk_root.after(40, _refresh)
        return r

    tk.Menu.tk_popup = _popup
    tk.Menu.post = _post

    # Menubutton（齿轮等）的弹出走的是 Tcl 内置 class binding，
    # 不经过上面的 Python patch；用 class binding 在按下时预刷它挂的菜单
    def _on_mb_press(evt):
        w = evt.widget
        try:
            m = w.cget('menu')
            if m:
                _refresh_menu_tree(w.nametowidget(m), bool(get_dark()))
        except Exception:
            pass
        tk_root.after(1, _refresh)
        tk_root.after(40, _refresh)

    try:
        tk_root.bind_class('TMenubutton', '<ButtonPress-1>',
                           _on_mb_press, add='+')
        tk_root.bind_class('Menubutton', '<ButtonPress-1>',
                           _on_mb_press, add='+')
    except Exception:
        pass

    # 菜单已弹出后的导航（鼠标悬停/键盘移动高频触发）：
    # 弹出前整棵菜单树的 selectcolor 已刷好，这里绝不能再 configure
    # 正显示的菜单（重绘→闪烁）；只节流补刷 DWM，给新展开的级联
    # 系统窗口兜深色边框，最多 4 次/秒
    def _on_menu_select(evt):
        _refresh_throttled()

    try:
        tk_root.bind_all('<<MenuSelect>>', _on_menu_select, add='+')
    except Exception:
        pass

    # 主窗首次映射（及从最小化恢复）时 TkTopLevel 标题栏宿主才就绪 → 补刷一次
    def _on_root_map(evt):
        try:
            if evt.widget is tk_root:
                apply_window(tk_root, bool(get_dark()))
        except Exception:
            pass

    try:
        tk_root.bind('<Map>', _on_root_map, add='+')
    except Exception:
        pass
    _installed = True

    # 暴露给外部：主题切换时调用一次，刷新菜单栏 + 已弹菜单
    install.refresh_all_menus = _refresh_all_menus


# ---------- 无边框融合：自绘标题栏，保留系统圆角/阴影/贴靠 ----------
#
# 做法（Chromium / VS Code 同一路线）：
#   - 不改窗口样式（WS_CAPTION | WS_THICKFRAME 保留）→ DWM 继续画
#     Win11 圆角与投影，Aero Snap / Snap Layouts / 最小化动画全部保留；
#   - WM_NCCALCSIZE 把客户区撑满整个窗口（系统标题栏消失）；
#   - WM_NCHITTEST 自己报告命中：拖动区 HTCAPTION、边缘 8 向缩放、
#     三个按钮 HTMINBUTTON / HTMAXBUTTON / HTCLOSE（点击与 Snap Layouts
#     仍由系统处理）；
#   - 非客户区鼠标消息（hover / 右键系统菜单 / 失焦 / 最大化状态）
#     转成 Tk 虚拟事件发给自绘标题栏做视觉。

_WM_NCCALCSIZE = 0x0083
_WM_NCHITTEST = 0x0084
_WM_NCLBUTTONDOWN = 0x00A1
_WM_NCLBUTTONUP = 0x00A2
_WM_NCLBUTTONDBLCLK = 0x00A3
_WM_NCRBUTTONDOWN = 0x00A4
_WM_NCRBUTTONUP = 0x00A5
_WM_NCRBUTTONDBLCLK = 0x00A6
_WM_NCMBUTTONDOWN = 0x00A7
_WM_NCMBUTTONUP = 0x00A8
_WM_NCMBUTTONDBLCLK = 0x00A9
_WM_NCXBUTTONDOWN = 0x00AB
_WM_NCXBUTTONUP = 0x00AC
_WM_NCXBUTTONDBLCLK = 0x00AD
_WM_NCMOUSEMOVE = 0x00A0
_WM_MOUSELEAVE = 0x02A3
_WM_MOUSEMOVE = 0x0200
# 需要从子窗口冒泡给顶层的非客户区鼠标消息（拖动/缩放/标题按钮/双击）
_NC_MOUSE_MSGS = set(range(0x00A0, 0x00AE))
_WM_ACTIVATE = 0x0006
_WM_SIZE = 0x0005
_WM_SETCURSOR = 0x0020
_WM_SYSCOMMAND = 0x0112

_TME_LEAVE = 0x0002

_HTCLIENT = 1
_HTCAPTION = 2
_HTMINBUTTON = 8
_HTMAXBUTTON = 9
_HTLEFT = 10
_HTRIGHT = 11
_HTTOP = 12
_HTTOPLEFT = 13
_HTTOPRIGHT = 14
_HTBOTTOM = 15
_HTBOTTOMLEFT = 16
_HTBOTTOMRIGHT = 17
_HTCLOSE = 20

# 标题按钮自管：系统 DefWindowProc 的按钮消息循环与多层 TkChild 冒泡
# 冲突（UP 被其内部循环吞掉），DOWN/UP 在子窗口层截获后直接发系统命令
_CAP_BTN_CODES = (_HTMINBUTTON, _HTMAXBUTTON, _HTCLOSE)
_SC_MINIMIZE = 0xF020
_SC_MAXIMIZE = 0xF030
_SC_RESTORE = 0xF120
_SC_CLOSE = 0xF060

_SIZE_MAXIMIZED = 2
_GWLP_WNDPROC = -4
_SW_MINIMIZE = 6

# 「最小化守卫」：登记了守卫的窗口，SC_MINIMIZE 会被拦下（返回 0 不最小化），
# 只在本窗口的 frame 状态里标一个 defer_min 标志，由调用方在主线程正常
# 上下文里再真正最小化。原因是：在窗口过程里调 CallWindowProcW 处理
# SC_MINIMIZE 会派发嵌套消息（WM_WINDOWPOSCHANGED）重入本过程，如果此前
# 又在窗口过程里做过 ctypes 调用，嵌套回调会把线程状态弄丢 → GIL 崩溃
# （真机实测必崩）。拆成两步后，抓帧与最小化都发生在正常 Python 帧里。
_MIN_GUARDS = {}

# 「还原前贴遮罩」：任务栏还原时 SC_RESTORE 先于 DWM 还原动画到达窗口过程，
# 此刻窗口仍是 iconic、屏幕还没开画 —— 趁机用纯 Win32 SetWindowPos 把冻结帧
# 遮罩（主窗之外的另一顶层窗）盖到位，黑帧连第一帧都不露。等主线程轮询发现
# IsIconic 变化再贴已晚一帧，这正是残留的那 1~2 帧黑。遮罩句柄与覆盖矩形由
# 主线程（ui_dialogs._show_cover / main._grab_restore_cover）登记。
_COVER = {'hwnd': 0, 'rect': None, 'armed': False}
_HWND_TOPMOST = -1


def set_cover_window(hwnd: int):
    """登记遮罩窗句柄（Tk 主线程调用；0 或非 Win 平台忽略）"""
    if _IS_WIN and hwnd:
        _COVER['hwnd'] = int(hwnd)


def set_cover_rect(rect):
    """登记遮罩要覆盖的窗口矩形 (x, y, w, h)（抓冻结帧时更新）"""
    if rect:
        try:
            _COVER['rect'] = tuple(int(v) for v in rect)
        except Exception:
            pass


def set_cover_armed(on: bool):
    """遮罩是否允许预贴（窗口确已最小化时武装，撤遮罩时解除）。

    防止「窗口其实没被轮询看到最小化」时 SC_RESTORE 把遮罩贴上去却没人收。"""
    _COVER['armed'] = bool(on)

_SWP_NOMOVE = 0x0002
_SWP_NOSIZE = 0x0001
_SWP_NOZORDER = 0x0004
_SWP_NOACTIVATE = 0x0010
_SWP_SHOWWINDOW = 0x0040
_SWP_FRAMECHANGED = 0x0020
# RedrawWindow 标志：失效 + 擦除背景 + 重画非客户区 + 含全部子孙 + 同步绘制
_RDW_INVALIDATE = 0x0001
_RDW_INTERNALPAINT = 0x0002
_RDW_ERASE = 0x0004
_RDW_VALIDATE = 0x0008
_RDW_NOINTERNALPAINT = 0x0010
_RDW_NOERASE = 0x0020
_RDW_NOCHILDREN = 0x0040
_RDW_ALLCHILDREN = 0x0080
_RDW_UPDATENOW = 0x0100
_RDW_ERASENOW = 0x0200
_RDW_FRAME = 0x0400
_RDW_NOFRAME = 0x0800
_TPM_RETURNCMD = 0x0100
_TPM_RIGHTBUTTON = 0x0002
_TPM_NONOTIFY = 0x0080
_MONITOR_DEFAULTTONEAREST = 2

# 自绘标题栏几何（逻辑像素，NCHITTEST 时按窗口 DPI 换算物理像素）
CAPTION_H_DIP = 40
BTN_W_DIP = 46
# 隐形缩放带宽度：6 太细，鼠标稍往里几像素就抓不到（用户反馈「改不了窗口大小」
# 就是这个原因——功能没坏，是够不着）。8 DIP ≈ 系统边框手感。
BORDER_DIP = 8

# 缩放光标：客户区被自绘标题栏撑满后，系统不会再给这些区域设标准缩放光标，
# 必须自己在 WM_SETCURSOR 里按命中码上光标，否则用户完全看不出边可拖。
_IDC_SIZEWE = 32644
_IDC_SIZENS = 32645
_IDC_SIZENWSE = 32642
_IDC_SIZENESW = 32643
_IDC_ARROW = 32512
_SIZE_CURSORS = {
    _HTLEFT: _IDC_SIZEWE, _HTRIGHT: _IDC_SIZEWE,
    _HTTOP: _IDC_SIZENS, _HTBOTTOM: _IDC_SIZENS,
    _HTTOPLEFT: _IDC_SIZENWSE, _HTBOTTOMRIGHT: _IDC_SIZENWSE,
    _HTTOPRIGHT: _IDC_SIZENESW, _HTBOTTOMLEFT: _IDC_SIZENESW,
}
_cursor_cache = {}


def _load_size_cursor(cid: int):
    h = _cursor_cache.get(cid)
    if h is None:
        try:
            h = _user32.LoadCursorW(None, ctypes.cast(cid, ctypes.c_wchar_p))
        except Exception:
            h = 0
        _cursor_cache[cid] = h
    return h


class _RECT(ctypes.Structure):
    _fields_ = (('left', ctypes.c_long), ('top', ctypes.c_long),
                ('right', ctypes.c_long), ('bottom', ctypes.c_long))


class _NCCALCSIZE_PARAMS(ctypes.Structure):
    _fields_ = (('rgrc', _RECT * 3), ('lppos', ctypes.c_void_p))


class _MONITORINFO(ctypes.Structure):
    _fields_ = (('cbSize', wintypes.DWORD), ('rcMonitor', _RECT),
                ('rcWork', _RECT), ('dwFlags', wintypes.DWORD))


class _TRACKMOUSEEVENT(ctypes.Structure):
    _fields_ = (('cbSize', wintypes.DWORD), ('dwFlags', wintypes.DWORD),
                ('hwndTrack', wintypes.HWND), ('dwHoverTime', wintypes.DWORD))


# hwnd -> {'prev', 'widget', 'hover', 'tracked', 'maxed'}
_frames = {}
_wnd_proc_ref = None     # 回调对象必须常驻，防止 GC 后崩溃


def _s16(v):
    """lParam 打包的屏幕坐标是有符号 16 位（多屏负坐标）"""
    return v - 0x10000 if v >= 0x8000 else v


def _dpi_scale(hwnd):
    try:
        _user32.GetDpiForWindow.argtypes = (wintypes.HWND,)
        _user32.GetDpiForWindow.restype = wintypes.UINT
        d = _user32.GetDpiForWindow(hwnd)
        return (d or 96) / 96.0
    except Exception:
        return 1.0


def _frame_hit_test(st, hwnd, sx, sy):
    """返回 Win32 命中码；-1 表示不截获（交给原窗口过程）"""
    rc = _RECT()
    if not _user32.GetWindowRect(hwnd, ctypes.byref(rc)):
        return -1
    s = _dpi_scale(hwnd)
    cap = int(CAPTION_H_DIP * s + 0.5)
    btn_w = int(BTN_W_DIP * s + 0.5)
    border = int(BORDER_DIP * s + 0.5)
    zoomed = bool(_user32.IsZoomed(hwnd))

    # ① 右上三个标题按钮（优先于上缩放边，与原生窗口一致）
    if sy < rc.top + cap and sy >= rc.top:
        if rc.right - btn_w <= sx < rc.right:
            return _HTCLOSE
        if rc.right - 2 * btn_w <= sx < rc.right - btn_w:
            return _HTMAXBUTTON
        if rc.right - 3 * btn_w <= sx < rc.right - 2 * btn_w:
            return _HTMINBUTTON

    # ② 8 向隐形缩放带（最大化时窗口铺满工作区，不提供缩放）
    if not zoomed:
        l = sx < rc.left + border
        r = sx >= rc.right - border
        t = sy < rc.top + border
        b = sy >= rc.bottom - border
        if t and l:
            return _HTTOPLEFT
        if t and r:
            return _HTTOPRIGHT
        if b and l:
            return _HTBOTTOMLEFT
        if b and r:
            return _HTBOTTOMRIGHT
        if l:
            return _HTLEFT
        if r:
            return _HTRIGHT
        if t:
            return _HTTOP
        if b:
            return _HTBOTTOM

    # ③ 标题栏其余区域：拖动 / 双击最大化 / Aero Snap
    if rc.top <= sy < rc.top + cap:
        return _HTCAPTION
    return -1


# wndproc 是系统回调，会在 DefWindowProc 的标题栏模态循环（拖动/缩放）
# 中被重入；此时回调栈处于 C 模态循环内，直接调 Tcl（event_generate）
# 会造成 GIL/Tcl 线程状态损坏（非确定性 Fatal Python error）。
# 回调只入队，由 Tk 主线程 after 轮询在正常上下文派发。
_pending_events = []


def _emit(st, name, data=None):
    w = st.get('widget')
    if w is None:
        return
    try:
        payload = None if data is None else str(data)
        # 连续同名事件合并（拖动时 NCMOUSEMOVE hover 高频刷屏）
        if _pending_events and _pending_events[-1][0] is w and \
                _pending_events[-1][1] == name:
            _pending_events[-1] = (w, name, payload)
        else:
            _pending_events.append((w, name, payload))
    except Exception:
        pass


def pump_events(widget):
    """主线程轮询：把系统回调期间积压的虚拟事件派发给 Tk"""
    if not _pending_events:
        return
    items = _pending_events[:]
    del _pending_events[:]
    for w, name, data in items:
        if w is not widget:
            # 别的顶层暂存回去（当前架构只有一个无边框主窗）
            _pending_events.append((w, name, data))
            continue
        try:
            if data is None:
                w.event_generate(name)
            else:
                w.event_generate(name, data=data)
        except Exception:
            pass


def _show_system_menu(hwnd, sx, sy):
    """标题栏空白处右键：还原/移动/大小/最小化/最大化/关闭"""
    try:
        hmenu = _user32.GetSystemMenu(hwnd, False)
        if not hmenu:
            return
        cmd = _user32.TrackPopupMenu(
            hmenu, _TPM_RETURNCMD | _TPM_RIGHTBUTTON | _TPM_NONOTIFY,
            sx, sy, 0, hwnd, None)
        if cmd:
            _user32.PostMessageW(hwnd, _WM_SYSCOMMAND, cmd, 0)
    except Exception:
        pass


def _exec_caption_cmd(hwnd, code):
    """标题按钮在同一按钮上按下并松开 → 发对应系统命令"""
    if code == _HTMINBUTTON:
        cmd = _SC_MINIMIZE
    elif code == _HTCLOSE:
        cmd = _SC_CLOSE
    elif code == _HTMAXBUTTON:
        cmd = _SC_RESTORE if _user32.IsZoomed(hwnd) else _SC_MAXIMIZE
    else:
        return
    try:
        _user32.PostMessageW(hwnd, _WM_SYSCOMMAND, cmd, 0)
    except Exception:
        pass


_CAP_HOVER_EVENTS = {_HTMINBUTTON: '<<WcMinHover>>',
                     _HTMAXBUTTON: '<<WcMaxHover>>',
                     _HTCLOSE: '<<WcCloseHover>>'}

# 上次 WM_SETCURSOR 是不是我们自己在缩放带上设的光标。必须记住：
# SetCursor 设进去的光标系统不会替我们收回，而原窗口过程在「光标没变」
# 时通常直接返回 TRUE 不重设 —— 不记这个，双头缩放箭头就会黏在指针上。
_size_cursor_on = False


def _root_state(st):
    """标题栏悬停态挂在顶层那份 state 上，全窗共用一份。

    原来每个子窗口各存一份：指针从按钮里的 Label 挪到同一按钮的外框时，
    前者补发的 LEAVE 会晚于后者发出的 HOVER，把高亮清掉 → 悬停闪断、
    时灵时不灵。共用一份就不存在先后覆盖问题。"""
    return _frames.get(st.get('root')) or st


def _set_cap_hover(rst, code):
    if rst.get('hover') == code:
        return
    rst['hover'] = code
    ev = _CAP_HOVER_EVENTS.get(code)
    if ev:
        _emit(rst, ev)


def _clear_cap_hover(rst):
    if not rst.get('hover'):
        return
    rst['hover'] = 0
    _emit(rst, '<<WcBtnLeave>>')


def _track_cap_leave(rst, hwnd):
    """只在「指针当前所在的那个窗口」上挂一次离开跟踪。

    两个坑必须避开：
    1) 不能用 TME_NONCLIENT —— 无边框窗客户区已撑满整窗，指针永远不在
       非客户区，系统会立刻补发 WM_NCMOUSELEAVE，而处理函数又重挂一次，
       消息队列就此空转（实测指针一到标题按钮 CPU 就打满、整个程序发卡）；
    2) 不能在离开处理里重挂 —— 那个窗口此时已不在指针下，同样立刻回一条
       离开消息，形成死循环。所以这里只挂，离开处理只清标记不重挂。"""
    if rst.get('tracked'):
        return
    te = _TRACKMOUSEEVENT()
    te.cbSize = ctypes.sizeof(_TRACKMOUSEEVENT)
    te.dwFlags = _TME_LEAVE
    te.hwndTrack = hwnd
    te.dwHoverTime = 0xFFFFFFFF
    try:
        if _user32.TrackMouseEvent(ctypes.byref(te)):
            rst['tracked'] = True
            rst['track_hwnd'] = hwnd
    except Exception:
        pass


def _refresh_cap_hover(st, root_hwnd, sx, sy):
    """按实时屏幕坐标刷新标题按钮悬停态（进入/切换/离开都走这里）"""
    rst = _root_state(st)
    code = _frame_hit_test(st, root_hwnd, sx, sy)
    if code in _CAP_BTN_CODES:
        _set_cap_hover(rst, code)
    else:
        _clear_cap_hover(rst)
    return rst


def _clear_cap_hover_on_move(st, root_hwnd):
    """客户区鼠标移动时补清标题按钮高亮。

    高亮只有两条清理路径：标题栏内的 WM_NCMOUSEMOVE、指针离开整窗的
    WM_MOUSELEAVE。但无边框窗客户区已撑满整窗，TME_LEAVE 只在指针离开
    窗口时才发；于是「从右上角按钮往下挪进正文」两条都不走，高亮就黏在
    按钮上不灭。这里按实时坐标补判一次。没有高亮时立即返回，零开销。"""
    rst = _root_state(st)
    if not rst.get('hover'):
        return
    pt = wintypes.POINT()
    if _user32.GetCursorPos(ctypes.byref(pt)):
        _refresh_cap_hover(st, root_hwnd, pt.x, pt.y)
    else:
        _clear_cap_hover(rst)


_MSGLOG_MSGS = {0x0005, 0x0006, 0x0018, 0x0046, 0x0047, 0x0112}


def _msglog(hwnd, msg, wparam, lparam, inner):
    """诊断用（WINCHROME_MSGLOG 环境变量指向日志文件时开启）。全量记。"""
    path = os.environ.get('WINCHROME_MSGLOG')
    if not path:
        return
    try:
        if msg == 0x0112:
            name = 'WM_SYSCOMMAND %s' % hex(int(wparam) & 0xFFF0)
        elif msg == 0x0005:
            name = 'WM_SIZE %s' % {0: 'RESTORED', 1: 'MINIMIZED',
                                   2: 'MAXIMIZED'}.get(int(wparam), int(wparam))
        elif msg == 0x0018:
            name = 'WM_SHOWWINDOW w=%s' % (int(wparam) and 'SHOW' or 'HIDE')
        else:
            name = hex(msg)
        with open(path, 'a', encoding='utf-8') as _f:
            _f.write('%d %s hwnd=%s inner=%d iconic=%d\n' % (
                int(time.monotonic() * 1000), name,
                int(hwnd), int(bool(inner)), int(bool(_user32.IsIconic(hwnd)))))
    except Exception:
        pass


def _msglog_extra(text):
    """诊断用：无条件写一行（只要 WINCHROME_MSGLOG 已设置），与 _msglog 配合。"""
    path = os.environ.get('WINCHROME_MSGLOG')
    if not path:
        return
    try:
        with open(path, 'a', encoding='utf-8') as _f:
            _f.write('%d EXTRA %s\n' % (int(time.monotonic() * 1000), text))
    except Exception:
        pass


def _make_wnd_proc():
    def _wnd_proc(hwnd, msg, wparam, lparam):
        st = _frames.get(hwnd)
        if st is None:
            # 理论上不会发生（子类化已随注销移除）；交给系统默认处理
            return _user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        inner = bool(st.get('inner'))
        root_hwnd = st.get('root', hwnd)
        _msglog(hwnd, msg, wparam, lparam, inner)

        # TkChild 子窗口：①命中测试把标题栏/按钮/缩放带报成非客户区码；
        # ②随后的非客户鼠标消息系统仍发给子窗口，而子窗口自身的
        # DefWindowProc 不会执行拖动/缩放/标题按钮（那是顶层默认行为），
        # 必须冒泡转发给顶层窗口处理；③子窗口类未必有 CS_DBLCLKS，
        # 双击消息在此合成；其余消息原样放行。
        # 缩放光标：命中码是八向缩放带时自己上对应光标（客户区撑满后
        # 系统不会再管这些区域的箭头），其余一律放行给默认处理
        if msg == _WM_SETCURSOR:
            global _size_cursor_on
            cid = _SIZE_CURSORS.get(int(lparam) & 0xFFFF)
            if cid:
                hcur = _load_size_cursor(cid)
                if hcur:
                    _user32.SetCursor(hcur)
                    _size_cursor_on = True
                    return 1
            elif _size_cursor_on:
                # 刚从缩放带挪进来：缩放光标是我们 SetCursor 设的，系统
                # 不会收，原过程又常直接返回 TRUE 不重设 → 双头箭头一直黏
                # 在指针上。这里显式复位一次箭头，随后交还原过程覆盖
                # （输入框 I 形、分栏条双箭头等由 Tk 自己设回来）
                _size_cursor_on = False
                hcur = _load_size_cursor(_IDC_ARROW)
                if hcur:
                    _user32.SetCursor(hcur)
            return _user32.CallWindowProcW(
                st['prev'], hwnd, msg, wparam, lparam)

        if inner and msg != _WM_NCHITTEST:
            if (msg in (_WM_NCLBUTTONDOWN, _WM_NCLBUTTONUP)
                    and int(wparam) in _CAP_BTN_CODES):
                # 标题按钮：DOWN 记下，UP 同码则执行；不冒泡给顶层，
                # 避开系统标题按钮的内部消息循环
                code = int(wparam)
                if msg == _WM_NCLBUTTONDOWN:
                    st['btn_dn'] = code
                else:
                    if st.get('btn_dn') == code:
                        _exec_caption_cmd(root_hwnd, code)
                    st['btn_dn'] = None
                return 0
            if msg == _WM_MOUSEMOVE:
                # 客户区鼠标移动：放行给 Tk，但先补判一次标题按钮高亮
                _clear_cap_hover_on_move(st, root_hwnd)
                return _user32.CallWindowProcW(
                    st['prev'], hwnd, msg, wparam, lparam)
            if msg in _NC_MOUSE_MSGS:
                fwd = msg
                if msg == _WM_NCLBUTTONDOWN:
                    now = time.monotonic()
                    sx0 = _s16(lparam & 0xFFFF)
                    sy0 = _s16((lparam >> 16) & 0xFFFF)
                    last_t = st.get('ldn_t', 0.0)
                    last_p = st.get('ldn_p')
                    dbl_time = 0
                    try:
                        dbl_time = _user32.GetDoubleClickTime() or 500
                    except Exception:
                        dbl_time = 500
                    if (last_p == (sx0, sy0)
                            and 0 < (now - last_t) * 1000.0 <= dbl_time):
                        fwd = _WM_NCLBUTTONDBLCLK
                        st['ldn_t'] = 0.0
                    else:
                        st['ldn_t'] = now
                        st['ldn_p'] = (sx0, sy0)
                return _user32.SendMessageW(
                    root_hwnd, fwd, wparam, lparam)
            return _user32.CallWindowProcW(
                st['prev'], hwnd, msg, wparam, lparam)

        if msg == _WM_SYSCOMMAND and not inner:
            _cmd = int(wparam) & 0xFFF0
            if _cmd == _SC_MINIMIZE:
                # 最小化守卫：拦下这次最小化，让调用方在主线程正常上下文里
                # 先抓冻结帧、再真正最小化（原因见 _MIN_GUARDS 注释）
                if _MIN_GUARDS.get(root_hwnd):
                    st['defer_min'] = True
                    return 0
                return _user32.CallWindowProcW(
                    st['prev'], hwnd, msg, wparam, lparam)
            if _cmd == _SC_RESTORE and _user32.IsIconic(hwnd) \
                    and _COVER.get('armed'):
                _ch = _COVER.get('hwnd')
                _rect = _COVER.get('rect')
                _covered = 0
                if _ch and _rect:
                    _x, _y, _w, _h = _rect
                    _covered = int(bool(_user32.SetWindowPos(
                        _ch, wintypes.HWND(_HWND_TOPMOST),
                        _x, _y, _w, _h,
                        _SWP_NOACTIVATE | _SWP_SHOWWINDOW)))
                _msglog_extra('SC_RESTORE preshow armed=%s hwnd=%s rect=%s ok=%s'
                              % (bool(_COVER.get('armed')), bool(_ch),
                                 bool(_rect), _covered))
            return _user32.CallWindowProcW(
                st['prev'], hwnd, msg, wparam, lparam)

        if msg == _WM_NCCALCSIZE:
            if inner:
                return _user32.CallWindowProcW(
                    st['prev'], hwnd, msg, wparam, lparam)
            if wparam:   # TRUE：可改建议的客户区矩形
                try:
                    p = _NCCALCSIZE_PARAMS.from_address(lparam)
                    if _user32.IsZoomed(hwnd):
                        # 最大化：铺满当前显示器工作区（不挡任务栏、不溢出屏幕）
                        mon = _user32.MonitorFromWindow(
                            hwnd, _MONITOR_DEFAULTTONEAREST)
                        if mon:
                            mi = _MONITORINFO()
                            mi.cbSize = ctypes.sizeof(_MONITORINFO)
                            if _user32.GetMonitorInfoW(mon, ctypes.byref(mi)):
                                p.rgrc[0] = mi.rcWork
                    return 0   # 客户区 = 整个窗口，系统标题栏不再绘制
                except Exception:
                    pass
            return 0

        if msg == _WM_NCHITTEST:
            sx = _s16(lparam & 0xFFFF)
            sy = _s16((lparam >> 16) & 0xFFFF)
            code = _frame_hit_test(st, root_hwnd, sx, sy)
            if code >= 0:
                return code
            return _user32.CallWindowProcW(
                st['prev'], hwnd, msg, wparam, lparam)

        if msg == _WM_NCMOUSEMOVE:
            # 命中码按消息里的实时坐标现算，不信 wparam：NC 消息由「答出
            # 命中码的那个子窗口」收到，嵌套 TkChild 层层转换后 wparam 未必
            # 还对得上指针位置
            sx = _s16(lparam & 0xFFFF)
            sy = _s16((lparam >> 16) & 0xFFFF)
            rst = _refresh_cap_hover(st, root_hwnd, sx, sy)
            if rst.get('hover'):
                _track_cap_leave(rst, hwnd)
            # 不放行给 Tk/DefWindowProc：系统标题栏已不存在，且其内部的
            # 按钮悬停跟踪会与我们的 TrackMouseEvent 打架
            return 0

        if msg == _WM_MOUSELEAVE:
            rst = _root_state(st)
            # 只认自己挂的那次（别的窗口的 WM_MOUSELEAVE 是 Tk 的 Leave
            # 事件在用，必须原样放行，否则控件的悬停绑定会失灵）
            if rst.get('track_hwnd') == hwnd:
                rst['tracked'] = False
                rst['track_hwnd'] = None
                pt = wintypes.POINT()
                if _user32.GetCursorPos(ctypes.byref(pt)):
                    _refresh_cap_hover(st, root_hwnd, pt.x, pt.y)
                else:
                    _clear_cap_hover(rst)
            return _user32.CallWindowProcW(
                st['prev'], hwnd, msg, wparam, lparam)

        if msg == _WM_MOUSEMOVE:
            # 顶层自身收到客户区移动（少见）：同样补判，然后放行
            _clear_cap_hover_on_move(st, root_hwnd)
            return _user32.CallWindowProcW(
                st['prev'], hwnd, msg, wparam, lparam)

        if msg == _WM_NCRBUTTONUP:
            sx = _s16(lparam & 0xFFFF)
            sy = _s16((lparam >> 16) & 0xFFFF)
            if _frame_hit_test(st, hwnd, sx, sy) == _HTCAPTION:
                _show_system_menu(hwnd, sx, sy)
                return 0
            return _user32.CallWindowProcW(
                st['prev'], hwnd, msg, wparam, lparam)

        if msg == _WM_ACTIVATE:
            _emit(st, '<<WcActivate>>' if (wparam & 0xFFFF) else
                  '<<WcDeactivate>>')
            return _user32.CallWindowProcW(
                st['prev'], hwnd, msg, wparam, lparam)

        if msg == 0x0046 and not inner:
            # WM_WINDOWPOSCHANGING：任务栏点图标还原走 ShowWindow(SW_RESTORE)，
            # **不发 SC_RESTORE**（探针实测），因此上面的 SC_RESTORE 分支盖不到
            # 这条路径。此消息在窗口真正显示前到达，此刻 iconic 已翻 0 但遮罩还
            # armed（= 之前确实最小化过），抢在 DWM 黑快照前把冻结帧盖到位。
            if _COVER.get('armed') and not bool(_user32.IsIconic(hwnd)):
                _ch = _COVER.get('hwnd')
                _rect = _COVER.get('rect')
                _ok = 0
                if _ch and _rect:
                    _x, _y, _w, _h = _rect
                    _ok = int(bool(_user32.SetWindowPos(
                        _ch, wintypes.HWND(_HWND_TOPMOST),
                        _x, _y, _w, _h,
                        _SWP_NOACTIVATE | _SWP_SHOWWINDOW)))
                _msglog_extra('WM_WINDOWPOSCHANGING preshow armed=%s ok=%s'
                              % (bool(_COVER.get('armed')), _ok))
            return _user32.CallWindowProcW(
                st['prev'], hwnd, msg, wparam, lparam)

        if msg == 0x0047 and not inner:
            # WM_WINDOWPOSCHANGED：Tk wrapper 不透传顶层 WM_SIZE，
            # 最大化/还原状态在这里用 IsZoomed 直接判定
            zoomed = bool(_user32.IsZoomed(hwnd))
            if zoomed != st['maxed']:
                st['maxed'] = zoomed
                _emit(st, '<<WcMaxState>>', 1 if zoomed else 0)
            return _user32.CallWindowProcW(
                st['prev'], hwnd, msg, wparam, lparam)

        if msg == _WM_SIZE:
            sw = int(wparam)
            zoomed = (sw == _SIZE_MAXIMIZED)
            if zoomed != st['maxed']:
                st['maxed'] = zoomed
                _emit(st, '<<WcMaxState>>', 1 if zoomed else 0)
            return _user32.CallWindowProcW(
                st['prev'], hwnd, msg, wparam, lparam)

        return _user32.CallWindowProcW(
            st['prev'], hwnd, msg, wparam, lparam)

    return _wnd_proc


def _set_proc_fn():
    fn = getattr(_user32, 'SetWindowLongPtrW', None)
    if fn is None:
        fn = _user32.SetWindowLongW
    fn.argtypes = (wintypes.HWND, ctypes.c_int, ctypes.c_void_p)
    fn.restype = ctypes.c_void_p
    return fn


def disable_transitions(widget):
    """请求关掉本窗口的 DWM 过渡动画（最小化/还原不再有系统动画）。

    实测说明：该属性写入返回 hr=0，但**并不足以消掉还原黑帧**（真机对照
    实验：设与不设都是 47~155ms 纯黑），所以它现在只是「尽量关掉动画」的
    优化项，真正解决黑帧的是 main.py 的「冻结帧遮罩」。保留调用无害。"""
    if not _IS_WIN:
        return
    try:
        hwnd = _caption_hwnd(widget)
        if hwnd:
            _set_attr(hwnd, _DWMWA_TRANSITIONS_FORCEDISABLED, 1)
    except Exception:
        pass


def set_minimize_guard(widget, on):
    """开关本窗口的「最小化守卫」：开着时 SC_MINIMIZE 会被拦下等人来执行。

    必须在窗口过程外调用（普通 Tk 上下文）。"""
    if not _IS_WIN:
        return
    try:
        hwnd = _caption_hwnd(widget)
    except Exception:
        return
    if not hwnd:
        return
    if on:
        _MIN_GUARDS[hwnd] = True
    else:
        _MIN_GUARDS.pop(hwnd, None)


def take_minimize_request(widget) -> bool:
    """取走「刚拦下一次最小化」的标记；返回 True 表示需要调用方补做最小化。"""
    if not _IS_WIN:
        return False
    try:
        hwnd = _caption_hwnd(widget)
        st = _frames.get(hwnd)
    except Exception:
        return False
    return bool(st.pop('defer_min', False)) if st else False


def minimize_now(widget):
    """在主线程正常上下文里补发最小化，**只投递、不等待**。

    这里刻意用 PostMessageW 而不是 ShowWindow/SendMessage：
      · ShowWindow/SendMessage 会**在调用点同步派发**WM_WINDOWPOSCHANGED，
        嵌套回到本窗口过程；而此时线程状态已被 python 侧的 CDLL 调用释放，
        嵌套回调会丢线程状态 → GIL 崩溃（实测必崩）。
      · PostMessageW 只是入队，之后由 Tk 自己的消息泵派发 —— 和用户点任务栏
        最小化完全同一条路径，安全。
    调用前记得临时撤掉守卫（否则又被拦一次），派发完再装回来。"""
    if not _IS_WIN:
        return
    try:
        hwnd = _caption_hwnd(widget)
        if hwnd:
            _user32.PostMessageW(hwnd, _WM_SYSCOMMAND, _SC_MINIMIZE, 0)
    except Exception:
        pass


def _hook_descendants(hwnd, widget):
    """把顶层窗口下全部子孙 HWND 挂上命中测试（幂等）。

    Tk 为每个可见 widget 建真实子窗口，且运行中会动态新增（弹出面板、
    下拉、菜单内容等）；每次调用只挂新增部分，已挂的跳过。"""
    if _wnd_proc_ref is None:
        return
    set_proc = _set_proc_fn()
    pending = []

    def _enum_cb(child, _lparam):
        if child and child not in _frames:
            pending.append(child)
        return True

    try:
        _user32.EnumChildWindows.argtypes = (
            wintypes.HWND, _EnumWindowsProc, wintypes.LPARAM)
        _user32.EnumChildWindows.restype = wintypes.BOOL
        _user32.EnumChildWindows(hwnd, _EnumWindowsProc(_enum_cb), 0)
    except Exception:
        return
    for child in pending:
        try:
            prev = set_proc(
                child, _GWLP_WNDPROC,
                ctypes.cast(_wnd_proc_ref, ctypes.c_void_p))
            if prev:
                _frames[child] = {
                    'prev': prev, 'widget': widget, 'inner': True,
                    'root': hwnd, 'hover': 0, 'tracked': False,
                    'track_hwnd': None, 'maxed': False}
        except Exception:
            pass


def hook_descendants(widget):
    """外部增量补挂入口：新 widget 映射后调用（幂等，开销极小）"""
    if not _IS_WIN or _wnd_proc_ref is None:
        return
    try:
        hwnd = _caption_hwnd(widget)
        if hwnd in _frames:
            _hook_descendants(hwnd, widget)
    except Exception:
        pass


def _enum_children(hwnd):
    """列出 hwnd 的全部子孙 HWND（含自身，去重、顺序稳定）"""
    out = [int(hwnd)]
    seen = {int(hwnd)}
    try:
        _user32.EnumChildWindows.argtypes = (
            wintypes.HWND, _EnumWindowsProc, wintypes.LPARAM)
        _user32.EnumChildWindows.restype = wintypes.BOOL
        _user32.EnumChildWindows(int(hwnd), _EnumWindowsProc(
            lambda child, _l: (seen.add(int(child)) is None
                               and out.append(int(child))) or True), 0)
    except Exception:
        pass
    return out


def force_paint(widget, deep=True, passes=2):
    """强制窗口（含全部子 HWND）立刻完成一次真实 WM_PAINT。

    Tk 的 update()/update_idletasks() 只跑 Tk 自己的事件循环，不会强制
    Win32 层同步绘制；窗口首次映射后若从未真正画过，淡入时显出来的就是
    那张未初始化的黑色分层表面（“启动黑一下才变正常”）。

    这里对每个 HWND 发 RedrawWindow(RDW_INVALIDATE|ERASE|FRAME|ALLCHILDREN|
    UPDATENOW)，UPDATENOW 让它同步派发 WM_PAINT 后再返回，因此函数返回时
    像素已经落地。子控件各自有独立 HWND，父窗的 ALLCHILDREN 只递归失效、
    不保证同步绘制完成，所以逐个再刷一遍（deep=True）。

    返回实际刷新的 HWND 数量；非 Windows / 取不到句柄返回 0。
    """
    if not _IS_WIN:
        return 0
    try:
        hwnd = _caption_hwnd(widget) or _hwnd_of(widget)
        if not hwnd:
            return 0
        hwnd = int(hwnd)
        _user32.RedrawWindow.argtypes = (
            wintypes.HWND, ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT)
        _user32.RedrawWindow.restype = wintypes.BOOL
        _user32.UpdateWindow.argtypes = (wintypes.HWND,)
        _user32.UpdateWindow.restype = wintypes.BOOL
        flags = _RDW_INVALIDATE | _RDW_ERASE | _RDW_FRAME
        flags |= _RDW_ALLCHILDREN | _RDW_UPDATENOW
        targets = _enum_children(hwnd) if deep else [hwnd]
        n = 0
        for _ in range(max(1, int(passes))):
            for h in targets:
                try:
                    _user32.RedrawWindow(
                        wintypes.HWND(h), None, None, wintypes.UINT(flags))
                    _user32.UpdateWindow(wintypes.HWND(h))
                    n += 1
                except Exception:
                    pass
        return n
    except Exception:
        return 0


def enable_frameless(widget) -> bool:
    """对 Tk/Toplevel 启用无边框融合（幂等）。需窗口已映射。

    自绘标题栏的视觉在 Tk 层实现，通过这些虚拟事件驱动：
      <<WcMinHover>> <<WcMaxHover>> <<WcCloseHover>> <<WcBtnLeave>>
      <<WcActivate>> <<WcDeactivate>>
      <<WcMaxState>>  data: '1'=最大化 '0'=还原
    """
    global _wnd_proc_ref
    if not _IS_WIN:
        return False
    try:
        hwnd = _caption_hwnd(widget)
        if not hwnd or hwnd in _frames:
            return hwnd in _frames
        if _wnd_proc_ref is None:
            _proto = ctypes.WINFUNCTYPE(
                ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT,
                ctypes.c_size_t, ctypes.c_ssize_t)
            _wnd_proc_ref = _proto(_make_wnd_proc())
            _user32.CallWindowProcW.argtypes = (
                ctypes.c_void_p, wintypes.HWND, wintypes.UINT,
                ctypes.c_size_t, ctypes.c_ssize_t)
            _user32.CallWindowProcW.restype = ctypes.c_ssize_t
            _user32.DefWindowProcW.argtypes = (
                wintypes.HWND, wintypes.UINT,
                ctypes.c_size_t, ctypes.c_ssize_t)
            _user32.DefWindowProcW.restype = ctypes.c_ssize_t
            _user32.GetWindowRect.argtypes = (
                wintypes.HWND, ctypes.POINTER(_RECT))
            _user32.GetWindowRect.restype = wintypes.BOOL
            _user32.IsZoomed.argtypes = (wintypes.HWND,)
            _user32.IsZoomed.restype = wintypes.BOOL
            _user32.SetWindowPos.argtypes = (
                wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                ctypes.c_int, ctypes.c_int, wintypes.UINT)
            _user32.SetWindowPos.restype = wintypes.BOOL
            _user32.TrackMouseEvent.argtypes = (
                ctypes.POINTER(_TRACKMOUSEEVENT),)
            _user32.TrackMouseEvent.restype = wintypes.BOOL
            _user32.GetSystemMenu.argtypes = (wintypes.HWND, wintypes.BOOL)
            _user32.GetSystemMenu.restype = wintypes.HMENU
            _user32.TrackPopupMenu.argtypes = (
                wintypes.HMENU, wintypes.UINT, ctypes.c_int, ctypes.c_int,
                ctypes.c_int, wintypes.HWND, ctypes.c_void_p)
            _user32.TrackPopupMenu.restype = ctypes.c_int
            _user32.PostMessageW.argtypes = (
                wintypes.HWND, wintypes.UINT, ctypes.c_size_t,
                ctypes.c_ssize_t)
            _user32.SendMessageW.argtypes = (
                wintypes.HWND, wintypes.UINT, ctypes.c_size_t,
                ctypes.c_ssize_t)
            _user32.SendMessageW.restype = ctypes.c_ssize_t
            _user32.GetDoubleClickTime.argtypes = ()
            _user32.GetDoubleClickTime.restype = wintypes.UINT
            _user32.GetCursorPos.argtypes = (
                ctypes.POINTER(wintypes.POINT),)
            _user32.GetCursorPos.restype = wintypes.BOOL
            _user32.LoadCursorW.argtypes = (wintypes.HINSTANCE,
                                            ctypes.c_wchar_p)
            _user32.LoadCursorW.restype = ctypes.c_void_p
            _user32.SetCursor.argtypes = (ctypes.c_void_p,)
            _user32.SetCursor.restype = ctypes.c_void_p
            _user32.MonitorFromWindow.argtypes = (
                wintypes.HWND, wintypes.DWORD)
            _user32.MonitorFromWindow.restype = wintypes.HMONITOR
            _user32.GetMonitorInfoW.argtypes = (
                wintypes.HMONITOR, ctypes.POINTER(_MONITORINFO))
            _user32.GetMonitorInfoW.restype = wintypes.BOOL

        set_proc = getattr(_user32, 'SetWindowLongPtrW', None)
        if set_proc is None:
            set_proc = _user32.SetWindowLongW
        set_proc.argtypes = (
            wintypes.HWND, ctypes.c_int, ctypes.c_void_p)
        set_proc.restype = ctypes.c_void_p

        def _subclass(target, state):
            prev = set_proc(
                target, _GWLP_WNDPROC,
                ctypes.cast(_wnd_proc_ref, ctypes.c_void_p))
            if not prev:
                return False
            state['prev'] = prev
            _frames[target] = state
            return True

        if not _subclass(hwnd, {'widget': widget, 'inner': False,
                                'root': hwnd, 'hover': 0,
                                'tracked': False, 'track_hwnd': None,
                                'maxed': bool(_user32.IsZoomed(hwnd))}):
            return False
        # Tk 为每个 widget 建真实 HWND（层层 TkChild 子孙），真实鼠标
        # 输入命中的是最内层那个；只挂主绘图窗不够，必须把整棵子孙树
        # 都挂上命中测试（子窗口返回 HTCAPTION 等后，NC 消息由系统发给顶层）
        _hook_descendants(hwnd, widget)
        # 强制重算非客户区 → 系统标题栏立即消失
        _user32.SetWindowPos(
            hwnd, 0, 0, 0, 0, 0,
            _SWP_NOMOVE | _SWP_NOSIZE | _SWP_NOZORDER |
            _SWP_NOACTIVATE | _SWP_FRAMECHANGED)
        return True
    except Exception:
        return False


def disable_frameless(widget) -> bool:
    """还原系统标题栏（frameless 开关回退用）"""
    if not _IS_WIN:
        return False
    try:
        hwnd = _caption_hwnd(widget)
        st = _frames.pop(hwnd, None)
        if st is None:
            return False
        # 还原顶层 + 全部子孙（含运行中增量补挂的）
        targets = [h for h, sub in list(_frames.items())
                   if sub.get('root') == hwnd]
        set_proc = _set_proc_fn()
        for target in targets:
            sub = _frames.pop(target, None)
            if sub is not None:
                try:
                    set_proc(target, _GWLP_WNDPROC, sub['prev'])
                except Exception:
                    pass
        _user32.SetWindowPos(
            hwnd, 0, 0, 0, 0, 0,
            _SWP_NOMOVE | _SWP_NOSIZE | _SWP_NOZORDER |
            _SWP_NOACTIVATE | _SWP_FRAMECHANGED)
        return True
    except Exception:
        return False
