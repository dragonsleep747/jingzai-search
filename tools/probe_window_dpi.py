"""只读核对指定进程的窗口 DPI；可保存自有窗口的原始绘制截图。"""
import argparse
import ctypes
from ctypes import wintypes as w
import json
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('--pid', type=int, required=True)
parser.add_argument('--output', required=True)
parser.add_argument('--capture', action='store_true')
args = parser.parse_args()
process_ids = {args.pid}
try:
    import psutil
    process_ids.update(process.pid for process in psutil.Process(args.pid).children(recursive=True))
except Exception:
    pass
api = ctypes.windll.user32
api.SetThreadDpiAwarenessContext.argtypes = (w.HANDLE,)
api.SetThreadDpiAwarenessContext.restype = w.HANDLE
api.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
api.GetWindowThreadProcessId.argtypes = (w.HWND, ctypes.POINTER(w.DWORD))
api.GetWindowTextW.argtypes = (w.HWND, w.LPWSTR, ctypes.c_int)
api.GetWindowDpiAwarenessContext.argtypes = (w.HWND,)
api.GetWindowDpiAwarenessContext.restype = w.HANDLE
api.GetAwarenessFromDpiAwarenessContext.argtypes = (w.HANDLE,)
api.GetDpiForWindow.argtypes = (w.HWND,)
api.IsWindowVisible.argtypes = (w.HWND,)
rows = []

@ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
def visit(hwnd, unused):
    pid = w.DWORD()
    api.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if pid.value in process_ids and api.IsWindowVisible(hwnd):
        title = ctypes.create_unicode_buffer(512)
        api.GetWindowTextW(hwnd, title, len(title))
        if title.value and '兼容宿主' not in title.value:
            context = api.GetWindowDpiAwarenessContext(hwnd)
            kind = ctypes.create_unicode_buffer(256)
            api.GetClassNameW.argtypes = (w.HWND, w.LPWSTR, ctypes.c_int)
            api.GetClassNameW(hwnd, kind, len(kind))
            rect = w.RECT()
            api.GetWindowRect.argtypes = (w.HWND, ctypes.POINTER(w.RECT))
            api.GetWindowRect(hwnd, ctypes.byref(rect))
            rows.append({'hwnd': hwnd, 'pid': pid.value, 'title': title.value, 'class': kind.value,
                         'size': [rect.right - rect.left, rect.bottom - rect.top],
                         'awareness': api.GetAwarenessFromDpiAwarenessContext(context),
                         'dpi': api.GetDpiForWindow(hwnd)})
    return True

api.EnumWindows(visit, 0)
output = Path(args.output).resolve()
output.parent.mkdir(parents=True, exist_ok=True)
if args.capture and rows:
    import win32gui, win32ui
    from PIL import Image
    for number, row in enumerate(rows):
        handle = row['hwnd']
        left, top, right, bottom = win32gui.GetWindowRect(handle)
        native_dc = win32gui.GetWindowDC(handle)
        source = win32ui.CreateDCFromHandle(native_dc)
        canvas = source.CreateCompatibleDC()
        bitmap = win32ui.CreateBitmap()
        bitmap.CreateCompatibleBitmap(source, right - left, bottom - top)
        previous = canvas.SelectObject(bitmap)
        try:
            api.PrintWindow.argtypes = (w.HWND, w.HDC, w.UINT)
            if api.PrintWindow(handle, canvas.GetSafeHdc(), 2):
                info = bitmap.GetInfo()
                image = Image.frombuffer('RGB', (info['bmWidth'], info['bmHeight']),
                    bitmap.GetBitmapBits(True), 'raw', 'BGRX', 0, 1)
                path = output.with_name(output.stem + '-' + str(number) + '.png')
                image.save(path)
                row['capture'] = str(path)
        finally:
            canvas.SelectObject(previous)
            win32gui.DeleteObject(bitmap.GetHandle())
            canvas.DeleteDC()
            source.DeleteDC()
            win32gui.ReleaseDC(handle, native_dc)
output.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(rows, ensure_ascii=True))
