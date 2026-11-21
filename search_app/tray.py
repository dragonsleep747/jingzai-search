# -*- coding: utf-8 -*-
"""Windows 系统托盘图标（纯 ctypes 实现，无新依赖）。

用途：导入/扫描大批文件时，主窗最小化到托盘后台运行；
完成时托盘气泡通知 + 自动恢复窗口。

参考 quickwin.HotkeyThread 的线程模式：独立 daemon 线程跑消息循环，
主线程通过 queue.Queue 下发命令、接收事件。

仅 Windows；其他平台或 ctypes 异常时 _TRAY_OK=False，主程序优雅降级。
"""
import os
import sys
import queue
import threading

if sys.platform.startswith('win'):
    try:
        import ctypes
        from ctypes import wintypes

        _user32 = ctypes.windll.user32
        _shell32 = ctypes.windll.shell32
        _kernel32 = ctypes.windll.kernel32

        # ---------- Win32 常量 ----------
        NIM_ADD = 0
        NIM_MODIFY = 1
        NIM_DELETE = 2

        NIF_MESSAGE = 0x0001
        NIF_ICON = 0x0002
        NIF_TIP = 0x0004
        NIF_INFO = 0x0010

        NIIF_NONE = 0
        NIIF_INFO = 1
        NIIF_WARNING = 2
        NIIF_ERROR = 3
        NIIF_NOSOUND = 0x0010

        WM_APP = 0x8000
        _WM_TRAY = WM_APP + 1          # 托盘回调消息
        _WM_WAKE = WM_APP + 2          # 主线程唤醒消息线程用

        WM_NULL = 0x0000
        WM_QUIT = 0x0012
        WM_CLOSE = 0x0010
        WM_DESTROY = 0x0002

        WM_LBUTTONUP = 0x0202
        WM_LBUTTONDBLCLK = 0x0203
        WM_RBUTTONUP = 0x0205
        WM_MBUTTONUP = 0x0208

        PM_REMOVE = 0x0001

        IMAGE_ICON = 1
        LR_LOADFROMFILE = 0x0010
        LR_DEFAULTSIZE = 0x0040

        IDI_APPLICATION = 32512

        HWND_MESSAGE = -3

        # 窗口类样式：接收双击消息（不加则 WM_LBUTTONDBLCLK 永不产生）
        CS_DBLCLKS = 0x0008

        # 气泡通知回调：用户点击气泡（映射为左键单击处理）
        NIN_BALLOONUSERCLICK = 0x0405

        # ---------- 结构体 ----------
        class _GUID(ctypes.Structure):
            # 等价于 Win32 GUID（16 字节）
            _fields_ = [
                ('Data1', wintypes.DWORD),
                ('Data2', wintypes.USHORT),
                ('Data3', wintypes.USHORT),
                ('Data4', wintypes.BYTE * 8),
            ]

        class NOTIFYICONDATAW(ctypes.Structure):
            _fields_ = [
                ('cbSize', wintypes.DWORD),
                ('hWnd', wintypes.HWND),
                ('uID', wintypes.UINT),
                ('uFlags', wintypes.UINT),
                ('uCallbackMessage', wintypes.UINT),
                ('hIcon', wintypes.HICON),
                ('szTip', wintypes.WCHAR * 128),
                ('dwState', wintypes.DWORD),
                ('dwStateMask', wintypes.DWORD),
                ('szInfo', wintypes.WCHAR * 256),
                ('uTimeout', wintypes.UINT),       # union: uTimeout / uVersion
                ('szInfoTitle', wintypes.WCHAR * 64),
                ('dwInfoFlags', wintypes.DWORD),
                ('guidItem', _GUID),
                ('hBalloonIcon', wintypes.HICON),
            ]

        class WNDCLASSEXW(ctypes.Structure):
            _fields_ = [
                ('cbSize', wintypes.UINT),
                ('style', wintypes.UINT),
                ('lpfnWndProc', ctypes.c_void_p),
                ('cbClsExtra', wintypes.INT),
                ('cbWndExtra', wintypes.INT),
                ('hInstance', wintypes.HINSTANCE),
                ('hIcon', wintypes.HICON),
                ('hCursor', wintypes.HANDLE),
                ('hbrBackground', wintypes.HBRUSH),
                ('lpszMenuName', wintypes.LPCWSTR),
                ('lpszClassName', wintypes.LPCWSTR),
                ('hIconSm', wintypes.HICON),
            ]

        class MSG(ctypes.Structure):
            _fields_ = [
                ('hwnd', wintypes.HWND),
                ('message', wintypes.UINT),
                ('wParam', wintypes.WPARAM),
                ('lParam', wintypes.LPARAM),
                ('time', wintypes.DWORD),
                ('pt', wintypes.POINT),
            ]

        # WNDPROC 函数类型（必须用 WINFUNCTYPE，不能是 CFUNCTYPE）
        WNDPROC = ctypes.WINFUNCTYPE(
            ctypes.c_long, wintypes.HWND, wintypes.UINT,
            wintypes.WPARAM, wintypes.LPARAM)

        # ---------- 函数原型 ----------
        _user32.RegisterClassExW.argtypes = [ctypes.c_void_p]
        _user32.RegisterClassExW.restype = wintypes.ATOM
        _user32.UnregisterClassW.argtypes = [wintypes.LPCWSTR, wintypes.HINSTANCE]
        _user32.UnregisterClassW.restype = wintypes.BOOL
        _user32.CreateWindowExW.argtypes = [
            wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
            wintypes.INT, wintypes.INT, wintypes.INT, wintypes.INT,
            wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, ctypes.c_void_p]
        _user32.CreateWindowExW.restype = wintypes.HWND
        _user32.DestroyWindow.argtypes = [wintypes.HWND]
        _user32.DestroyWindow.restype = wintypes.BOOL
        _user32.PeekMessageW.argtypes = [
            ctypes.c_void_p, wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT]
        _user32.PeekMessageW.restype = wintypes.BOOL
        _user32.TranslateMessage.argtypes = [ctypes.c_void_p]
        _user32.TranslateMessage.restype = wintypes.BOOL
        _user32.DispatchMessageW.argtypes = [ctypes.c_void_p]
        _user32.DispatchMessageW.restype = ctypes.c_long
        _user32.DefWindowProcW.argtypes = [
            wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        _user32.DefWindowProcW.restype = ctypes.c_long
        _user32.PostQuitMessage.argtypes = [wintypes.INT]
        _user32.PostQuitMessage.restype = None
        _user32.GetCursorPos.argtypes = [ctypes.c_void_p]
        _user32.GetCursorPos.restype = wintypes.BOOL
        _user32.LoadImageW.argtypes = [
            wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT,
            wintypes.INT, wintypes.INT, wintypes.UINT]
        _user32.LoadImageW.restype = wintypes.HANDLE
        _user32.LoadIconW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR]
        _user32.LoadIconW.restype = wintypes.HICON
        # DestroyIcon：仅用于 LoadImage 从文件加载的图标（共享图标不可销毁）
        _user32.DestroyIcon.argtypes = [wintypes.HICON]
        _user32.DestroyIcon.restype = wintypes.BOOL
        _user32.PostThreadMessageW.argtypes = [
            wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        _user32.PostThreadMessageW.restype = wintypes.BOOL
        _shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.c_void_p]
        _shell32.Shell_NotifyIconW.restype = wintypes.BOOL
        _kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        _kernel32.GetModuleHandleW.restype = wintypes.HMODULE
        _kernel32.GetCurrentThreadId.argtypes = []
        _kernel32.GetCurrentThreadId.restype = wintypes.DWORD

        _TRAY_OK = True
    except Exception:
        _TRAY_OK = False
else:
    _TRAY_OK = False


# 鼠标消息 → 事件名映射
_EVT_MAP = {
    WM_LBUTTONUP: 'lclick',
    WM_LBUTTONDBLCLK: 'lclick2',
    WM_RBUTTONUP: 'rclick',
    WM_MBUTTONUP: 'mclick',
    # 气泡被点击：复用左键单击回调
    NIN_BALLOONUSERCLICK: 'lclick',
} if _TRAY_OK else {}


class TrayThread(threading.Thread):
    """托盘图标线程：注册窗口类 + 创建消息窗口 + 跑消息循环。

    命令队列 cmd_q（主→线程）：
        ('add', icon_path, tip)        添加图标
        ('tip', tip)                   仅改 tooltip
        ('balloon', title, info, kind) 弹气泡（info/warn/error）
        ('del',)                       删图标
        ('quit',)                      退出线程

    事件队列 evt_q（线程→主）：
        ('lclick',)        左键单击
        ('lclick2',)       左键双击
        ('rclick', x, y)   右键弹菜单（带屏幕坐标）
        ('mclick',)        中键单击
    """

    def __init__(self, evt_q):
        super().__init__(daemon=True)
        if not _TRAY_OK:
            raise RuntimeError('tray 不可用（非 Windows 或 ctypes 失败）')
        self.cmd_q = queue.Queue()
        self.evt_q = evt_q
        self._tid = None
        # 注意：不能命名为 _stop，会与 threading.Thread 内部属性冲突
        self._stop_evt = threading.Event()
        self._hwnd = None
        self._hicon = None
        # 当前 _hicon 是否为从文件加载（需 DestroyIcon）；系统共享图标为 False
        self._hicon_owned = False
        self._nid = None
        # WNDPROC 必须保活到实例属性，否则 GC 后 ctypes 调用崩溃
        self._wndproc_ref = WNDPROC(self._wnd_proc)
        self._ready = threading.Event()
        self._hinst = _kernel32.GetModuleHandleW(None)

    # ---------- 线程入口 ----------
    def run(self):
        # 注册窗口类（重复注册返回 0，忽略）
        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
        # 必须显式带 CS_DBLCLKS，否则系统不向窗口投递双击消息
        wc.style = CS_DBLCLKS
        # WNDPROC 实例需 cast 成 c_void_p 才能赋给 lpfnWndProc
        wc.lpfnWndProc = ctypes.cast(self._wndproc_ref, ctypes.c_void_p)
        wc.hInstance = self._hinst
        wc.lpszClassName = 'JingZaiTrayWnd_v1'
        try:
            _user32.RegisterClassExW(ctypes.byref(wc))
        except Exception:
            pass

        # 创建 message-only 窗口
        try:
            self._hwnd = _user32.CreateWindowExW(
                0, 'JingZaiTrayWnd_v1', 'JingZai Tray', 0,
                0, 0, 0, 0, HWND_MESSAGE, None, self._hinst, None)
        except Exception:
            self._hwnd = None
        self._tid = _kernel32.GetCurrentThreadId()
        self._ready.set()

        # 消息循环：PeekMessage 非阻塞 + 短等待
        msg = MSG()
        while not self._stop_evt.is_set():
            self._drain_cmds()
            got = _user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE)
            if got:
                if msg.message == WM_QUIT:
                    break
                _user32.TranslateMessage(ctypes.byref(msg))
                _user32.DispatchMessageW(ctypes.byref(msg))
            else:
                self._stop_evt.wait(0.05)

        # 清理
        self._del_icon()
        if self._hwnd:
            try:
                _user32.DestroyWindow(self._hwnd)
            except Exception:
                pass
            self._hwnd = None
        try:
            _user32.UnregisterClassW('JingZaiTrayWnd_v1', self._hinst)
        except Exception:
            pass

    # ---------- WNDPROC ----------
    def _wnd_proc(self, hwnd, msg, wp, lp):
        if msg == _WM_TRAY:
            mouse = lp & 0xFFFF
            evt = _EVT_MAP.get(mouse)
            if evt:
                if evt == 'rclick':
                    pt = wintypes.POINT()
                    _user32.GetCursorPos(ctypes.byref(pt))
                    self.evt_q.put(('rclick', pt.x, pt.y))
                else:
                    self.evt_q.put((evt,))
            return 0
        if msg == WM_CLOSE:
            _user32.PostQuitMessage(0)
            return 0
        if msg == WM_DESTROY:
            _user32.PostQuitMessage(0)
            return 0
        return _user32.DefWindowProcW(hwnd, msg, wp, lp)

    # ---------- 命令处理 ----------
    def _drain_cmds(self):
        while True:
            try:
                cmd = self.cmd_q.get_nowait()
            except queue.Empty:
                return
            kind = cmd[0]
            try:
                if kind == 'add':
                    self._add_icon(cmd[1], cmd[2])
                elif kind == 'tip':
                    self._update_tip(cmd[1])
                elif kind == 'balloon':
                    self._show_balloon(cmd[1], cmd[2], cmd[3] if len(cmd) > 3 else 'info')
                elif kind == 'del':
                    self._del_icon()
                elif kind == 'quit':
                    self._stop_evt.set()
                    if self._hwnd:
                        _user32.PostMessageW(self._hwnd, WM_CLOSE, 0, 0)
            except Exception:
                pass

    # ---------- 图标操作 ----------
    def _load_icon(self, icon_path):
        """返回 (HICON, 是否需自行销毁)：
        LoadImageW 从文件加载的图标用完必须 DestroyIcon；
        LoadIconW 拿到的系统共享图标归属系统，不可销毁。"""
        if icon_path and os.path.exists(icon_path):
            try:
                h = _user32.LoadImageW(
                    None, icon_path, IMAGE_ICON, 0, 0,
                    LR_LOADFROMFILE | LR_DEFAULTSIZE)
                if h:
                    return h, True
            except Exception:
                pass
        # 回退：系统默认应用图标（共享，不可 DestroyIcon）
        try:
            return _user32.LoadIconW(0, IDI_APPLICATION), False
        except Exception:
            return None, False

    def _make_nid(self, hicon, tip, flags=None):
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self._hwnd
        nid.uID = 0
        nid.uFlags = flags if flags is not None else (
            NIF_MESSAGE | NIF_ICON | NIF_TIP)
        nid.uCallbackMessage = _WM_TRAY
        nid.hIcon = hicon or 0
        nid.szTip = (tip or '')[:127]
        return nid

    def _add_icon(self, icon_path, tip):
        # 同时记录图标来源：文件加载的退出时需要 DestroyIcon
        self._hicon, self._hicon_owned = self._load_icon(icon_path)
        self._nid = self._make_nid(self._hicon, tip)
        try:
            _shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(self._nid))
        except Exception:
            pass

    def _del_icon(self):
        if self._nid is None:
            return
        try:
            _shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self._nid))
        except Exception:
            pass
        self._nid = None
        # NIM_DELETE 后再释放图标：文件加载（owned）的调 DestroyIcon，
        # 系统共享图标不属于本进程，绝不能销毁
        if self._hicon and self._hicon_owned:
            try:
                _user32.DestroyIcon(self._hicon)
            except Exception:
                pass
        self._hicon = None
        self._hicon_owned = False

    def _update_tip(self, tip):
        if self._nid is None:
            return
        self._nid.szTip = (tip or '')[:127]
        try:
            _shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(self._nid))
        except Exception:
            pass

    def _show_balloon(self, title, info, kind='info'):
        if self._nid is None:
            return
        flag_map = {'info': NIIF_INFO, 'warn': NIIF_WARNING,
                    'error': NIIF_ERROR}
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self._hwnd
        nid.uID = 0
        nid.uFlags = NIF_INFO
        nid.uCallbackMessage = _WM_TRAY
        nid.hIcon = self._hicon or 0
        # 保留原 tip（部分 Windows 版本气泡期间会清空 tip）
        nid.szTip = self._nid.szTip
        nid.szInfo = (info or '')[:255]
        nid.szInfoTitle = (title or '')[:63]
        nid.dwInfoFlags = flag_map.get(kind, NIIF_INFO)
        try:
            _shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))
        except Exception:
            pass

    # ---------- 主线程 API ----------
    def wait_ready(self, timeout=2.0):
        """等线程就绪（_tid 已设置）。"""
        return self._ready.wait(timeout)

    def add_icon(self, icon_path, tip):
        self.cmd_q.put(('add', icon_path, tip))

    def update_tip(self, tip):
        self.cmd_q.put(('tip', tip))

    def show_balloon(self, title, info, kind='info'):
        self.cmd_q.put(('balloon', title, info, kind))

    def del_icon(self):
        self.cmd_q.put(('del',))

    def stop(self):
        """请求线程退出（幂等）。"""
        self._stop_evt.set()
        self.cmd_q.put(('quit',))
        # 投递 WM_NULL 唤醒可能在 PeekMessage 后等待的循环
        if self._tid:
            try:
                _user32.PostThreadMessageW(self._tid, _WM_WAKE, 0, 0)
            except Exception:
                pass
