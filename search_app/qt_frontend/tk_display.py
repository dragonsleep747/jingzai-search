"""兼容工具的原生 DPI、阅读字号及弹窗布局适配。"""
import ctypes
from ctypes import wintypes
import math
import tkinter as tk
from tkinter import font as tkfont, ttk

from .display import display_sizes, dpi_info, fit_size

ICON_FONTS = ('mdl2', 'fluent icons', 'wingdings', 'webdings', 'marlett')


def is_icon(family):
    return any(name in family.lower() for name in ICON_FONTS)


def prepare_tk(root, legacy, settings):
    ui, text = display_sizes(settings)
    dpi = dpi_info(root.winfo_id())['dpi'] or 96
    root.tk.call('tk', 'scaling', dpi / 72)
    for name in ('TkDefaultFont', 'TkMenuFont', 'TkHeadingFont', 'TkCaptionFont', 'TkSmallCaptionFont', 'TkIconFont'):
        try:
            tkfont.nametofont(name, root=root).configure(family='Microsoft YaHei UI', size=ui)
        except tk.TclError:
            pass
    tkfont.nametofont('TkTextFont', root=root).configure(family='Microsoft YaHei UI', size=text)
    legacy.App.FONT_TITLE = ('Microsoft YaHei UI', ui + 3, 'bold')
    legacy.App.FONT_MAIN = ('Microsoft YaHei UI', text)
    legacy.App.FONT_UI = ('Microsoft YaHei UI', ui)
    legacy.App.FONT_UI_B = ('Microsoft YaHei UI', ui, 'bold')
    legacy.App.FONT_HINT = ('Microsoft YaHei UI', max(10, ui - 1))
    legacy.App.FONT_CODE = ('Consolas', text)


class TkDisplayPolicy:
    def __init__(self, root, app):
        self.root, self.app = root, app
        self.ui, self.text = display_sizes(app.cfg)
        self.fonts = {}
        self.pending = {}
        self.scheduled = False
        self.sized = set()
        original_center = app._center_win
        def center(window, width, height, modal=True):
            original_center(window, width, height, modal)
            window._geometry_dpi_scaled = True
        app._center_win = center
        root.bind_all('<Map>', self.mapped, add='+')
        self.prepare_quick()

    def prepare_quick(self):
        quick = self.app.quick_win
        if quick:
            for name, size in [('_f_h1', self.ui + 1), ('_f_title', self.ui), ('_f_body', self.text),
                               ('_f_hit', self.text), ('_f_small', max(10, self.ui - 1)), ('_f_input', self.text)]:
                font = getattr(quick, name, None)
                if font:
                    font.configure(size=max(size, int(font.cget('size'))))
            quick.row_h = max(quick.row_h, int((quick._f_title.metrics('linespace') + quick._f_body.metrics('linespace')) * 1.65))

    def prepare_qa(self):
        qa = getattr(self.app, '_qa_window', None)
        if qa:
            qa._qa_font = ('Microsoft YaHei UI', self.text)
            qa._qa_font_ls = tkfont.Font(root=self.root, family='Microsoft YaHei UI', size=self.text).metrics('linespace')
            qa._schedule_reflow()

    def mapped(self, event):
        try:
            window = event.widget.winfo_toplevel()
            if window is self.root:
                return
            if not getattr(window,'_escape_installed',False):
                window._escape_installed=True
                def close(_event,target=window):
                    try:
                        command=target.protocol('WM_DELETE_WINDOW')
                        target.tk.call(command) if command else target.destroy()
                    except tk.TclError:
                        pass
                    return 'break'
                window.bind('<Escape>',close,add='+')
            self.pending[str(window)] = window
            if not self.scheduled:
                self.scheduled = True
                self.root.after_idle(self.apply_pending)
        except (tk.TclError, AttributeError):
            pass

    def apply_pending(self):
        self.scheduled = False
        pending, self.pending = self.pending, {}
        for window in pending.values():
            try:
                if not window.winfo_exists():
                    continue
                self.polish(window)
                if str(window) not in self.sized:
                    self.sized.add(str(window))
                    self.root.after(220, lambda target=window: self.fit_window(target))
            except tk.TclError:
                pass

    def font(self, specification, minimum):
        try:
            actual = tkfont.Font(root=self.root, font=specification).actual()
        except tk.TclError:
            return None
        if is_icon(actual['family']):
            return None
        size = int(actual['size'])
        if size < 0:
            pixels = math.ceil(minimum * float(self.root.tk.call('tk', 'scaling')))
            size = -max(-size, pixels)
        else:
            size = max(size, minimum)
        actual['size'] = size
        key = tuple(sorted(actual.items()))
        if key not in self.fonts:
            self.fonts[key] = tkfont.Font(root=self.root, **actual)
        return self.fonts[key]

    def polish(self, window):
        scale = float(self.root.tk.call('tk', 'scaling')) / (96 / 72)
        style = ttk.Style(self.root)
        style.configure('.', font=('Microsoft YaHei UI', self.ui))
        style.configure('TButton', font=('Microsoft YaHei UI', self.ui))
        style.configure('Treeview', font=('Microsoft YaHei UI', self.ui),
                        rowheight=max(round((28 if self.app.cfg.get('compact',True) else 34) * scale), tkfont.Font(root=self.root, family='Microsoft YaHei UI', size=self.ui).metrics('linespace') + 10))
        style.configure('Treeview.Heading', font=('Microsoft YaHei UI', self.ui, 'bold'))
        todo = [window]
        while todo:
            widget = todo.pop()
            todo.extend(widget.winfo_children())
            kind = widget.winfo_class()
            floor = self.text if kind in ('Text', 'Listbox') else max(10, self.ui - 1) if kind in ('Label', 'TLabel') else self.ui
            try:
                specification = widget.cget('font')
                if specification:
                    font = self.font(specification, floor)
                    if font:
                        widget.configure(font=font)
            except tk.TclError:
                pass
            if kind == 'Text':
                for tag in widget.tag_names():
                    specification = widget.tag_cget(tag, 'font')
                    if specification:
                        font = self.font(specification, self.text if tag not in ('meta', 'hint') else max(10, self.ui - 1))
                        if font:
                            widget.tag_configure(tag, font=font)
        categories = getattr(window, '_cat_tree', None)
        if categories:
            font = tkfont.Font(root=self.root, font=style.lookup('Treeview', 'font'))
            measured = max((font.measure(categories.item(item, 'text')) for item in categories.get_children()), default=180)
            width = min(round(measured + 34 * scale), round(window.winfo_screenwidth() * .25))
            categories.column('#0', width=width, minwidth=width, stretch=False)

    def work_area(self, window):
        try:
            class MonitorInfo(ctypes.Structure):
                _fields_ = [('cbSize', wintypes.DWORD), ('monitor', wintypes.RECT),
                            ('work', wintypes.RECT), ('flags', wintypes.DWORD)]
            api = ctypes.windll.user32
            api.MonitorFromWindow.argtypes = (wintypes.HWND, wintypes.DWORD)
            api.MonitorFromWindow.restype = wintypes.HANDLE
            info = MonitorInfo()
            info.cbSize = ctypes.sizeof(info)
            api.GetMonitorInfoW.argtypes = (wintypes.HANDLE, ctypes.POINTER(MonitorInfo))
            api.GetMonitorInfoW(api.MonitorFromWindow(window.winfo_id(), 2), ctypes.byref(info))
            return info.work.left, info.work.top, info.work.right, info.work.bottom
        except (AttributeError, OSError):
            return 0, 0, window.winfo_screenwidth(), window.winfo_screenheight() - 48

    def fit_window(self, window):
        try:
            if not window.winfo_exists() or not window.winfo_viewable():
                return
            if self.app.quick_win and window is self.app.quick_win.win:
                return
            window.update_idletasks()
            left, top, right, bottom = self.work_area(window)
            dpi = dpi_info(window.winfo_id())['dpi'] or 96
            factor = max(1., self.ui / 10)
            if not getattr(window, '_geometry_dpi_scaled', False):
                factor *= dpi / 96
            width = max(window.winfo_width() * factor, min(window.winfo_reqwidth(), (right - left) * .92))
            height = max(window.winfo_height() * factor, min(window.winfo_reqheight(), (bottom - top) * .87))
            width, height = map(int, fit_size(width, height, right - left, bottom - top))
            x = max(left, min(window.winfo_rootx(), right - width - 12))
            y = max(top + 12, min(window.winfo_rooty(), bottom - height - 40))
            window.geometry(f'{width}x{height}+{x}+{y}')
        except tk.TclError:
            pass

    def describe(self, windows):
        result = []
        for window in windows:
            actual_text_sizes = []
            todo = [window]
            while todo:
                widget = todo.pop()
                todo.extend(widget.winfo_children())
                if widget.winfo_class() == 'Text':
                    actual_text_sizes.append(tkfont.Font(root=self.root, font=widget.cget('font')).actual()['size'])
            result.append({'title': window.title(), **dpi_info(window.winfo_id()),
                'tk_scaling': float(self.root.tk.call('tk', 'scaling')),
                'ui_font': self.ui, 'text_font': self.text,
                'actual_text_fonts': sorted(set(actual_text_sizes)),
                'row_height': ttk.Style(self.root).lookup('Treeview', 'rowheight'),
                'geometry': window.geometry()})
        return result
