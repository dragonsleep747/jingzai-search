# -*- coding: utf-8 -*-
"""首帧像素探针：验证 alpha=0 期间窗口表面是否真的是未初始化的黑色。

用法：python tools/_t_firstframe.py on|off
  off = 不做 force_paint（模拟修复前）
  on  = 做 force_paint（模拟修复后）

流程：建窗 -> alpha 0 -> 堆 UI -> [force_paint] -> alpha 1.0 -> 立刻采样
采样用 PrintWindow(PW_RENDERFULLCONTENT) + GetDIBits，统计纯黑占比与平均亮度。
"""
import sys
import ctypes
import tkinter as tk
from tkinter import ttk
from ctypes import wintypes

MODE = sys.argv[1] if len(sys.argv) > 1 else 'off'

# ---------- Win32 采样 ----------
gdi32 = ctypes.WinDLL('gdi32')
user32 = ctypes.WinDLL('user32')
user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
user32.PrintWindow.restype = wintypes.BOOL
gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
gdi32.SelectObject.restype = wintypes.HGDIOBJ
gdi32.BitBlt.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int,
                         ctypes.c_int, ctypes.c_int, wintypes.HDC,
                         ctypes.c_int, ctypes.c_int, wintypes.DWORD]
gdi32.BitBlt.restype = wintypes.BOOL
gdi32.DeleteDC.argtypes = [wintypes.HDC]
gdi32.DeleteDC.restype = wintypes.BOOL
gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
gdi32.DeleteObject.restype = wintypes.BOOL
gdi32.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT,
                            ctypes.c_uint, ctypes.c_void_p,
                            ctypes.c_void_p, wintypes.UINT]
gdi32.GetDIBits.restype = ctypes.c_int
PW_RENDERFULLCONTENT = 0x00000002
BI_RGB = 0
DIB_RGB_COLORS = 0
SRCCOPY = 0x00CC0020


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [('biSize', wintypes.DWORD), ('biWidth', ctypes.c_long),
                ('biHeight', ctypes.c_long), ('biPlanes', wintypes.WORD),
                ('biBitCount', wintypes.WORD), ('biCompression', wintypes.DWORD),
                ('biSizeImage', wintypes.DWORD), ('biXPelsPerMeter', ctypes.c_long),
                ('biYPelsPerMeter', ctypes.c_long), ('biClrUsed', wintypes.DWORD),
                ('biClrImportant', wintypes.DWORD)]


def sample(hwnd, w=320, h=200):
    """把窗口抓成小图，返回 (平均亮度, 纯黑占比)。"""
    hdc = user32.GetWindowDC(hwnd)
    if not hdc:
        return None
    try:
        mdc = gdi32.CreateCompatibleDC(hdc)
        bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
        old = gdi32.SelectObject(mdc, bmp)
        # 先拷一次，避免出现未初始化的位图
        gdi32.BitBlt(mdc, 0, 0, w, h, mdc, 0, 0, 0x00000042)  # BLACKNESS
        ok = user32.PrintWindow(hwnd, mdc, PW_RENDERFULLCONTENT)
        bi = BITMAPINFOHEADER()
        bi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bi.biWidth = w
        bi.biHeight = -h          # top-down
        bi.biPlanes = 1
        bi.biBitCount = 32
        bi.biCompression = BI_RGB
        buf = (ctypes.c_char * (w * h * 4))()
        gdi32.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bi), DIB_RGB_COLORS)
        gdi32.SelectObject(mdc, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mdc)
        if not ok:
            return None
        n = w * h
        total = 0
        black = 0
        step = 4 * 7               # 稀疏采样，够统计
        cnt = 0
        for i in range(0, n * 4, step):
            b = buf[i]
            g = buf[i + 1]
            r = buf[i + 2]
            lum = (r * 299 + g * 587 + b * 114) // 1000
            total += lum
            if lum <= 8:
                black += 1
            cnt += 1
        return (total // max(cnt, 1), round(black * 100.0 / max(cnt, 1), 1))
    finally:
        user32.ReleaseDC(hwnd, hdc)


# ---------- 建窗 ----------
root = tk.Tk()
root.geometry('1200x800+80+60')
root.title('firstframe-probe')
root.attributes('-alpha', 0.0)
root.configure(bg='#F4F6F9')

outer = tk.Frame(root, bg='#F4F6F9')
outer.pack(fill='both', expand=True)
left = tk.Frame(outer, bg='#FFFFFF', width=260)
left.pack(side='left', fill='y')
for i in range(30):
    ttk.Label(left, text='条目 %02d' % i).pack(anchor='w', padx=8, pady=2)
right = tk.Frame(outer, bg='#F4F6F9')
right.pack(side='left', fill='both', expand=True)
txt = tk.Text(right, bg='#FFFFFF')
txt.pack(fill='both', expand=True, padx=12, pady=12)
txt.insert('1.0', '内容\n' * 200)
for i in range(8):
    ttk.Button(right, text='按钮%d' % i).pack(side='left', padx=4)

root.update_idletasks()
root.update()

hwnd = root.winfo_id()
print('mode=%s hwnd=%s' % (MODE, hwnd))

if MODE == 'on':
    try:
        import winchrome
        winchrome.force_paint(root, deep=True, passes=2)
        print('force_paint called')
    except Exception as e:
        print('force_paint FAILED: %r' % (e,))
else:
    print('force_paint skipped')

# alpha 拉到 1 后立刻采样：暴露「表面到底有没有被画过」
root.attributes('-alpha', 1.0)
root.update_idletasks()
r = sample(hwnd)
print('RESULT mode=%s -> %s' % (MODE, r))
root.after(300, root.destroy)
root.mainloop()
