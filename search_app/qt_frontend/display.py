"""进程级高 DPI 配置及阅读字号；本模块不创建 GUI 对象。"""
import ctypes
from ctypes import wintypes
import sys


def display_sizes(settings):
    ui = max(10, min(14, int(settings.get('dialog_font', 11))))
    text = max(ui, min(20, int(settings.get('dialog_text_font', 12))))
    return ui, text


def dpi_info(hwnd=None):
    if sys.platform != 'win32':
        return {'awareness': None, 'dpi': 96, 'per_monitor_v2': False}
    api = ctypes.WinDLL('user32', use_last_error=True)
    api.GetThreadDpiAwarenessContext.restype = wintypes.HANDLE
    api.GetWindowDpiAwarenessContext.argtypes = (wintypes.HWND,)
    api.GetWindowDpiAwarenessContext.restype = wintypes.HANDLE
    api.GetAwarenessFromDpiAwarenessContext.argtypes = (wintypes.HANDLE,)
    context = api.GetWindowDpiAwarenessContext(hwnd) if hwnd else api.GetThreadDpiAwarenessContext()
    api.AreDpiAwarenessContextsEqual.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    api.GetDpiForWindow.argtypes = (wintypes.HWND,)
    return {'awareness': api.GetAwarenessFromDpiAwarenessContext(context),
            'dpi': api.GetDpiForWindow(hwnd) if hwnd else api.GetDpiForSystem(),
            'per_monitor_v2': bool(api.AreDpiAwarenessContextsEqual(context, ctypes.c_void_p(-4)))}


def enable_high_dpi():
    """在任何 HWND 创建前调用。EXE 另有 manifest，覆盖源码运行情况。"""
    if sys.platform != 'win32':
        return
    api = ctypes.WinDLL('user32', use_last_error=True)
    try:
        api.SetProcessDpiAwarenessContext.argtypes = (wintypes.HANDLE,)
        api.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        if dpi_info()['awareness'] == 2:
            return
    except (AttributeError, OSError):
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except (AttributeError, OSError):
        try:
            api.SetProcessDPIAware()
        except (AttributeError, OSError):
            pass


def fit_size(width, height, available_width, available_height):
    """保留可见窗口边框和任务栏空间。输入均使用所属 GUI 的坐标单位。"""
    return min(width, max(280, int(available_width * .94))), min(height, max(200, int(available_height * .90)))
