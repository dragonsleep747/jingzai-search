# -*- coding: utf-8 -*-
"""Minimize/restore the candidate window N rounds (Windows-native ShowWindow) and
sample the restored window pixels via PrintWindow to prove no all-black regions.

Usage:
    python tools/minimize_restore_verify.py <hwnd> [rounds] [out_dir]
"""
import ctypes
import json
import os
import sys
import time
from ctypes import wintypes

try:
    from PIL import Image
except ImportError:
    Image = None

SW_MINIMIZE = 6
SW_RESTORE = 9
PW_RENDERFULLCONTENT = 2
SRCCOPY = 0x00CC0020


def main():
    hwnd = int(sys.argv[1], 0)
    rounds = int(sys.argv[2]) if len(sys.argv) > 2 else 20
    out_dir = sys.argv[3] if len(sys.argv) > 3 else '.tmp/discovery-integration'
    os.makedirs(out_dir, exist_ok=True)

    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32
    user32.IsWindow.restype = wintypes.BOOL
    user32.IsWindow.argtypes = [wintypes.HWND]
    user32.ShowWindow.restype = wintypes.BOOL
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetWindowRect.restype = wintypes.BOOL
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]

    results = []
    for round_no in range(1, rounds + 1):
        user32.ShowWindow(hwnd, SW_MINIMIZE)
        time.sleep(0.06)
        user32.ShowWindow(hwnd, SW_RESTORE)
        time.sleep(0.06)
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        width = rect.right - rect.left
        height = rect.bottom - rect.top
        alive = bool(user32.IsWindow(hwnd))
        sample = None
        mean = black_ratio = None
        if Image is not None and width > 0 and height > 0:
            sample = _capture(user32, gdi32, hwnd, width, height)
            if sample is not None:
                mean, black_ratio = sample
                if round_no in (1, 5, 10, 15, 20):
                    _save_sample(user32, gdi32, hwnd, width, height,
                                 os.path.join(out_dir, f'minimize-restore-{round_no:02d}.png'))
            else:
                mean = black_ratio = None
        results.append({'round': round_no, 'window_alive': alive,
                        'width': width, 'height': height,
                        'mean_luma': mean, 'black_ratio': black_ratio})

    report = {
        'hwnd': hwnd,
        'rounds': rounds,
        'window_alive_final': bool(user32.IsWindow(hwnd)),
        'all_alive': all(r['window_alive'] for r in results),
        'black_ratio_max': max((r['black_ratio'] for r in results if r['black_ratio'] is not None), default=None),
        'samples': results,
        'note': 'ShowWindow SW_MINIMIZE/SW_RESTORE on hidden desktop; PrintWindow pixel sampling.',
    }
    with open(os.path.join(out_dir, 'minimize-restore.json'), 'w', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def _capture(user32, gdi32, hwnd, width, height):
    hdc_window = user32.GetWindowDC(hwnd)
    if not hdc_window:
        return None
    hdc_mem = gdi32.CreateCompatibleDC(hdc_window)
    bmp = gdi32.CreateCompatibleBitmap(hdc_window, width, height)
    old = gdi32.SelectObject(hdc_mem, bmp)
    ok = user32.PrintWindow(hwnd, hdc_mem, PW_RENDERFULLCONTENT)
    if not ok:
        # Fallback: BitBlt from window DC (may not render layered content).
        gdi32.BitBlt(hdc_mem, 0, 0, width, height, hdc_window, 0, 0, SRCCOPY)
    data = _bitmap_bytes(gdi32, hdc_mem, bmp, width, height)
    gdi32.SelectObject(hdc_mem, old)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(hdc_mem)
    user32.ReleaseDC(hwnd, hdc_window)
    if data is None:
        return None
    img = Image.frombytes('RGB', (width, height), data, 'raw', 'BGRX')
    gray = img.convert('L')
    pixels = list(gray.getdata())
    mean = sum(pixels) / len(pixels)
    black_ratio = sum(1 for p in pixels if p < 12) / len(pixels)
    return round(mean, 2), round(black_ratio, 4)


def _save_sample(user32, gdi32, hwnd, width, height, path):
    hdc_window = user32.GetWindowDC(hwnd)
    if not hdc_window:
        return
    hdc_mem = gdi32.CreateCompatibleDC(hdc_window)
    bmp = gdi32.CreateCompatibleBitmap(hdc_window, width, height)
    old = gdi32.SelectObject(hdc_mem, bmp)
    ok = user32.PrintWindow(hwnd, hdc_mem, PW_RENDERFULLCONTENT)
    if not ok:
        gdi32.BitBlt(hdc_mem, 0, 0, width, height, hdc_window, 0, 0, SRCCOPY)
    data = _bitmap_bytes(gdi32, hdc_mem, bmp, width, height)
    if data is not None:
        Image.frombytes('RGB', (width, height), data, 'raw', 'BGRX').save(path)
    gdi32.SelectObject(hdc_mem, old)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(hdc_mem)
    user32.ReleaseDC(hwnd, hdc_window)


def _bitmap_bytes(gdi32, hdc_mem, bmp, width, height):
    import struct
    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ('biSize', wintypes.DWORD), ('biWidth', ctypes.c_long),
            ('biHeight', ctypes.c_long), ('biPlanes', wintypes.WORD),
            ('biBitCount', wintypes.WORD), ('biCompression', wintypes.DWORD),
            ('biSizeImage', wintypes.DWORD), ('biXPelsPerMeter', ctypes.c_long),
            ('biYPelsPerMeter', ctypes.c_long), ('biClrUsed', wintypes.DWORD),
            ('biClrImportant', wintypes.DWORD),
        ]
    header = BITMAPINFOHEADER()
    header.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    header.biWidth = width
    header.biHeight = -height  # top-down
    header.biPlanes = 1
    header.biBitCount = 32
    header.biCompression = 0
    buf = ctypes.create_string_buffer(width * height * 4)
    if not gdi32.GetDIBits(hdc_mem, bmp, 0, height, buf, ctypes.byref(header), 0):
        return None
    return buf.raw


if __name__ == '__main__':
    sys.exit(main())
