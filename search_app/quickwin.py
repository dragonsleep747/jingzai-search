# -*- coding: utf-8 -*-
"""全局热键 + 迷你速查窗（批2 T5/T7）。

- HotkeyThread：独立消息线程注册 RegisterHotKey，触发经 Queue 通知主线程
- QuickWin：置顶无边框小窗，输入即搜，Enter 复制规范引用并自动粘贴
- paste_to_foreground：pywin32 前台切换 + 模拟 Ctrl+V
仅依赖已装的 pywin32；导入失败时置 _WIN32_OK=False，由 main 优雅降级。
"""
import os
import re
import time
import queue
import threading

import tkinter as tk
from tkinter import ttk
from tkinter import font as tkfont

try:
    import win32api
    import win32con
    import win32gui
    import win32process
    import pywintypes
    _WIN32_OK = True
except Exception:
    _WIN32_OK = False

try:
    import winchrome
except Exception:
    winchrome = None

from searcher import search, get_chapter_chain_str
from synonyms import expand_map

# ---------- 热键解析 ----------

_MOD_ALIASES = {
    'ctrl': 'control', 'control': 'control', 'ctl': 'control',
    'alt': 'alt', 'shift': 'shift', 'win': 'win', 'meta': 'win',
}


def parse_hotkey(text):
    """'ctrl+alt+j' → (MOD 位或值, 虚拟键码)。格式非法抛 ValueError。"""
    if not _WIN32_OK:
        raise RuntimeError('pywin32 不可用')
    mods = 0
    vk = None
    for tok in re.split(r'[+＋]', (text or '').strip().lower()):
        tok = tok.strip()
        if not tok:
            continue
        kind = _MOD_ALIASES.get(tok)
        if kind == 'control':
            mods |= win32con.MOD_CONTROL
        elif kind == 'alt':
            mods |= win32con.MOD_ALT
        elif kind == 'shift':
            mods |= win32con.MOD_SHIFT
        elif kind == 'win':
            mods |= win32con.MOD_WIN
        elif len(tok) == 1 and tok.isascii() and tok.isalnum():
            if vk is not None:
                raise ValueError('主键只能有一个')
            vk = ord(tok.upper())
        elif re.fullmatch(r'f(?:[1-9]|1[0-2])', tok):
            if vk is not None:
                raise ValueError('主键只能有一个')
            vk = win32con.VK_F1 + int(tok[1:]) - 1
        else:
            raise ValueError(f'不支持的按键：{tok}')
    if vk is None:
        raise ValueError('缺少主键（字母/数字/F1-F12）')
    if mods == 0:
        raise ValueError('至少要带一个修饰键（Ctrl/Alt/Shift）')
    return mods, vk


# ---------- 全局热键线程 ----------

class HotkeyThread(threading.Thread):
    """在独立线程注册全局热键并跑消息循环；事件写入 out_q。
    binds: [{'name','id','mods','vk','label'}]
    out_q 消息：('registered', [失败项...]) / ('hotkey', name)"""

    def __init__(self, binds, out_q):
        super().__init__(daemon=True)
        self.binds = binds
        self.out_q = out_q
        self.ready = threading.Event()
        self._stop_evt = threading.Event()
        self._tid = None

    def run(self):
        registered, failed = [], []
        for b in self.binds:
            try:
                win32gui.RegisterHotKey(None, b['id'], b['mods'], b['vk'])
                registered.append(b)
            except pywintypes.error:
                failed.append(b)
        self.ready.set()
        if failed:
            # 单个热键注册失败不回滚已成功者，仅把失败项上报给调用方
            self.out_q.put(('hotkey_failed', failed))
        if not registered:
            # 全部失败时无需维持消息循环
            return
        self._tid = win32api.GetCurrentThreadId()
        id2name = {b['id']: b['name'] for b in registered}
        try:
            while not self._stop_evt.is_set():
                rc, msg = win32gui.GetMessage(None, 0, 0)
                if not rc:          # 收到 WM_QUIT
                    break
                _hwnd, m, wp = msg[0], msg[1], msg[2]
                if m == win32con.WM_HOTKEY:
                    name = id2name.get(wp)
                    if name:
                        self.out_q.put(('hotkey', name))
        finally:
            for b in registered:
                try:
                    win32gui.UnregisterHotKey(None, b['id'])
                except Exception:
                    pass

    def stop(self):
        self._stop_evt.set()
        if self._tid:
            try:
                win32gui.PostThreadMessage(self._tid, win32con.WM_QUIT, 0, 0)
            except Exception:
                pass


# ---------- 前台窗口 / 自动粘贴 ----------

def foreground_title():
    """热键瞬间的前台窗口句柄与标题（供迷你窗粘贴回跳/剪藏记来源）"""
    if not _WIN32_OK:
        return None, ''
    try:
        hwnd = win32gui.GetForegroundWindow()
        return hwnd, win32gui.GetWindowText(hwnd)
    except Exception:
        return None, ''


def clipboard_has_files():
    """复制文件时剪贴板带 CF_HDROP；Tk 的 clipboard_get 会把文件列表
    误当路径文本，必须先显式拦截。"""
    if not _WIN32_OK:
        return False
    try:
        import win32clipboard
        win32clipboard.OpenClipboard()
        try:
            return bool(
                win32clipboard.IsClipboardFormatAvailable(
                    win32clipboard.CF_HDROP))
        finally:
            win32clipboard.CloseClipboard()
    except Exception:
        return False


def _force_set_foreground(hwnd, retries=6):
    """把目标窗口可靠地置为前台。同时 Attach 当前前台线程与目标线程，
    失败用 Alt 键 trick 解锁，返回最终是否真的成为前台。
    （WPS 等重型窗口一次 SetForegroundWindow 经常静默失败）"""
    try:
        root = win32gui.GetAncestor(hwnd, 2) or hwnd  # GA_ROOT
        cur_tid = win32api.GetCurrentThreadId()
        for _ in range(retries):
            fore = win32gui.GetForegroundWindow()
            tids = set()
            if fore:
                tids.add(win32process.GetWindowThreadProcessId(fore)[0])
            tids.add(win32process.GetWindowThreadProcessId(root)[0])
            attached = []
            for tid in tids:
                if tid and tid != cur_tid:
                    try:
                        win32process.AttachThreadInput(cur_tid, tid, True)
                        attached.append(tid)
                    except Exception:
                        pass
            try:
                win32gui.ShowWindow(root, win32con.SW_RESTORE)
                win32gui.BringWindowToTop(root)
                win32gui.SetForegroundWindow(root)
            except Exception:
                pass
            finally:
                for tid in attached:
                    try:
                        win32process.AttachThreadInput(cur_tid, tid, False)
                    except Exception:
                        pass
            time.sleep(0.05)
            if win32gui.GetForegroundWindow() == root:
                return True
            # 模拟一次 Alt 键，解除系统前台锁定后再试
            win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)
            win32api.keybd_event(win32con.VK_MENU, 0,
                                 win32con.KEYEVENTF_KEYUP, 0)
        return win32gui.GetForegroundWindow() == root
    except Exception:
        return False


def paste_to_foreground(hwnd):
    """切回先前台窗口并模拟 Ctrl+V。成功 True，失败 False（调用方降级提示）"""
    if not _WIN32_OK or not hwnd:
        return False
    try:
        if not _force_set_foreground(hwnd):
            return False
        time.sleep(0.12)                  # 等 WPS 这类重型窗完成激活
        ctrl_down = False
        try:
            win32api.keybd_event(win32con.VK_CONTROL, 0, 0, 0)
            ctrl_down = True
            win32api.keybd_event(0x56, 0, 0, 0)             # 'V'
            win32api.keybd_event(0x56, 0,
                                 win32con.KEYEVENTF_KEYUP, 0)
            return True
        finally:
            # 任何异常下都成对释放 Ctrl，避免 Ctrl 长期按下导致卡死
            if ctrl_down:
                try:
                    win32api.keybd_event(
                        win32con.VK_CONTROL, 0,
                        win32con.KEYEVENTF_KEYUP, 0)
                except Exception:
                    pass
    except Exception:
        return False


# ---------- 章节链缓存 ----------

_CHAIN_CACHE = {}


def _chapter_chain(db_path, path, line_no):
    """章节链（同一次检索里大量重复查询同一文件，缓存避免反复开库）"""
    key = (db_path, path, line_no)
    hit = _CHAIN_CACHE.get(key)
    if hit is not None:
        return hit
    try:
        val = get_chapter_chain_str(db_path, path, line_no) or ''
    except Exception:
        val = ''
    if len(_CHAIN_CACHE) > 4000:
        _CHAIN_CACHE.clear()
    _CHAIN_CACHE[key] = val
    return val


# ---------- 自绘圆角按钮 ----------

class _RBtn(tk.Label):
    """迷你窗圆角按钮：整块圆角位图 + 文字覆盖，四角透明贴合所在栏底色。"""

    def __init__(self, master, rounded_photo, text, command, font,
                 fill, hover, fg, corner, outline=None,
                 height=28, padx=12, radius=8):
        self._rp = rounded_photo
        self._font = font
        self._fill, self._hoverfill, self._fg = fill, hover, fg
        self._corner, self._outline = corner, outline
        self._h, self._padx, self._r = height, padx, radius
        self._cmd = command
        self._img = None
        self._hovering = False
        tk.Label.__init__(self, master, text=text, font=font, fg=fg,
                          bg=corner, bd=0, highlightthickness=0, takefocus=0,
                          compound='center', padx=0, pady=0, cursor='hand2')
        self._paint()
        self.bind('<Enter>', self._enter, add='+')
        self.bind('<Leave>', self._leave, add='+')
        self.bind('<ButtonRelease-1>', self._click, add='+')

    def _width(self):
        try:
            return tkfont.Font(font=self._font).measure(
                self.cget('text')) + self._padx * 2
        except Exception:
            return 96

    def _paint(self):
        if not self._rp:
            return
        col = self._hoverfill if self._hovering else self._fill
        img = self._rp(max(self._width(), self._h + 8), self._h, self._r,
                       col, self._outline, 1 if self._outline else 0)
        if img is not None:
            self._img = img
            tk.Label.configure(self, image=img)

    def _enter(self, _e):
        self._hovering = True
        self._paint()

    def _leave(self, _e):
        self._hovering = False
        self._paint()

    def _click(self, _e):
        if self._cmd:
            self._cmd()

    def retheme(self, fill, hover, fg, corner, outline=None):
        self._fill, self._hoverfill, self._fg = fill, hover, fg
        self._corner, self._outline = corner, outline
        tk.Label.configure(self, bg=corner, fg=fg)
        self._paint()


# ---------- 迷你速查窗 ----------

class QuickWin:
    """置顶无边框检索小窗：双行卡片结果 + 输入即搜 + Enter 粘贴规范引用。
    所有 Tk 操作都在主线程（由 App 轮询驱动）。"""

    BASE_W, BASE_H = 640, 470      # 逻辑尺寸（按 DPI 缩放后使用）
    ROW_H = 46                     # 双行卡片行高
    PAGE = 50                      # 首屏/每次追加的条数
    PREFETCH = 200                 # 后台一次最多备好的候选数

    def __init__(self, app, db_path, cite_fn, rounded_photo=None):
        self.app = app
        self.db_path = db_path
        self.cite_fn = cite_fn            # (path, line_no) -> 规范引用文本
        self._rp = rounded_photo          # main 的圆角位图工具（不能 import main）
        self.visible = False
        self.rows = []                    # 全部候选 dict
        self.shown = 0                    # 已上屏条数（滚动到底再追加）
        self.sel = None
        self.hover = None
        self._kw = ''
        self._empty = '输入关键词，停顿即搜'
        self._seq = 0
        self._debounce_id = None
        self._poll_id = None
        self._result_q = queue.Queue()
        self._saved_hwnd = None
        # 首次显示时间戳：失焦自检在显示后 400ms 宽限内不关窗
        self._show_ts = 0.0
        self._drag = None
        self._hist_idx = None
        self._images = []                 # 保活圆角位图引用
        self._build()

    # ---------- 主题 ----------

    def _colors(self):
        """配色统一取自 App.PALETTE_*，与主窗顶栏同源"""
        p = self.app.palette()
        return dict(bg=p['bg'], box=p['band_bg'], fg=p['title_fg'],
                    sub=p['sub_fg'], border=p['band_line'],
                    hover=p['ab_hover'], sel=p['sel_bg'],
                    brand=p['brand'], hit=p['hit_fg'])

    def _dpi(self):
        try:
            return max(1.0, min(2.0, self.win.winfo_fpixels('1i') / 96.0))
        except Exception:
            return 1.0

    def _px(self, v):
        return int(round(v * self._scale))

    # ---------- 构建 ----------

    def _build(self):
        c = self._colors()
        self._c = c
        w = tk.Toplevel(self.app.root)
        self.win = w
        w.overrideredirect(True)
        w.attributes('-topmost', True)
        w.withdraw()
        w.configure(bg=c['border'])
        outer = tk.Frame(w, bg=c['border'], padx=1, pady=1)
        outer.pack(fill='both', expand=True)
        body = tk.Frame(outer, bg=c['bg'])
        body.pack(fill='both', expand=True)
        self._outer, self._body = outer, body
        self._frames = [body]
        self._labels = []

        self._scale = self._dpi()
        self.row_h = self._px(self.ROW_H)
        self.entry_h = self._px(34)
        self._f_h1 = tkfont.Font(family='Microsoft YaHei UI', size=10,
                                 weight='bold')
        self._f_title = tkfont.Font(family='Microsoft YaHei UI', size=9,
                                    weight='bold')
        self._f_body = tkfont.Font(family='Microsoft YaHei UI', size=9)
        self._f_hit = tkfont.Font(family='Microsoft YaHei UI', size=9,
                                  weight='bold')
        self._f_small = tkfont.Font(family='Microsoft YaHei UI', size=8)
        self._f_input = tkfont.Font(family='Microsoft YaHei UI', size=11)

        # —— 头部：标题 + 快捷键提示 ——
        head = tk.Frame(body, bg=c['bg'])
        head.pack(fill='x', padx=self._px(12), pady=(self._px(8), self._px(4)))
        self._frames.append(head)
        title = tk.Label(head, text='镜仔速查', bg=c['bg'], fg=c['fg'],
                         font=self._f_h1)
        title.pack(side='left')
        self.hint = tk.Label(
            head, text='↑↓ 选择 · Enter 粘贴 · Ctrl+C 复制 · Ctrl+O 定位 · Esc 关闭',
            bg=c['bg'], fg=c['sub'], font=self._f_small)
        self.hint.pack(side='right')
        self._labels += [(title, 'fg'), (self.hint, 'sub')]

        # —— 输入框：圆角卡片 + 无边框 Entry ——
        card = tk.Frame(body, bg=c['bg'], height=self.entry_h)
        card.pack(fill='x', padx=self._px(12), pady=(self._px(2), self._px(6)))
        card.pack_propagate(False)
        self._frames.append(card)
        self._card = card
        self._entry_bg = tk.Label(card, bg=c['bg'], bd=0, highlightthickness=0)
        self._entry_bg.place(x=0, y=0, relwidth=1, relheight=1)
        self.entry = tk.Entry(card, font=self._f_input, bd=0, relief='flat',
                              highlightthickness=0, bg=c['box'], fg=c['fg'],
                              insertbackground=c['fg'])
        self.entry.place(x=self._px(12), y=self._px(6), relwidth=1,
                         relheight=1, width=-self._px(24),
                         height=-self._px(12))
        card.bind('<Configure>', lambda e: self._paint_entry_bg())

        # —— 检索范围开关：与主窗共用同一个变量，双向同步 ——
        opts = tk.Frame(body, bg=c['bg'])
        opts.pack(fill='x', padx=self._px(12), pady=(0, self._px(4)))
        self._frames.append(opts)
        ttk.Checkbutton(opts, text='仅看有效', variable=self.app.only_active,
                        command=self._on_opt).pack(side='left')
        ttk.Checkbutton(opts, text='同义扩展', variable=self.app.syn_var,
                        command=self._on_opt).pack(side='left',
                                                   padx=(self._px(12), 0))

        # —— 结果区：Canvas 自绘双行卡片 ——
        mid = tk.Frame(body, bg=c['bg'])
        mid.pack(fill='both', expand=True, padx=(self._px(12), self._px(8)))
        self._frames.append(mid)
        self.cv = tk.Canvas(mid, bg=c['box'], highlightthickness=0, bd=0,
                            takefocus=1)
        sb = ttk.Scrollbar(mid, orient='vertical', command=self.cv.yview)
        self.cv.configure(yscrollcommand=sb.set)
        self.cv.pack(side='left', fill='both', expand=True)
        sb.pack(side='right', fill='y')

        # —— 底栏：状态 + 快存剪贴板 ——
        foot = tk.Frame(body, bg=c['bg'])
        foot.pack(fill='x', padx=self._px(12), pady=(self._px(6), self._px(8)))
        self._frames.append(foot)
        self.status = tk.Label(foot, text='', anchor='w', bg=c['bg'],
                               fg=c['sub'], font=self._f_small)
        self.status.pack(side='left', fill='x', expand=True)
        self._labels.append((self.status, 'sub'))
        self.save_btn = _RBtn(foot, self._rp, '快存剪贴板', self._quick_save,
                              self._f_small, c['box'], c['hover'], c['fg'],
                              c['bg'], outline=c['border'],
                              height=self._px(28), padx=self._px(11))
        self.save_btn.pack(side='right', padx=(self._px(6), self._px(16)))

        # —— 右下角缩放把手 ——
        self.grip = tk.Label(body, text='', bg=c['bg'], cursor='size_nw_se')
        self.grip.place(relx=1.0, rely=1.0, x=-self._px(13),
                        y=-self._px(13), width=self._px(10),
                        height=self._px(10))
        self.grip.bind('<Button-1>', self._rs_start)
        self.grip.bind('<B1-Motion>', self._rs_move)

        # —— 键盘：输入框与结果区都要能用 ——
        for wdg in (self.entry, self.cv):
            wdg.bind('<Up>', self._on_up)
            wdg.bind('<Down>', self._on_down)
            wdg.bind('<Prior>', self._on_pgup)
            wdg.bind('<Next>', self._on_pgdn)
            wdg.bind('<Home>', self._on_home)
            wdg.bind('<End>', self._on_end)
            wdg.bind('<Return>', self._on_enter)
            wdg.bind('<KP_Enter>', self._on_enter)
            wdg.bind('<Control-o>', self._on_locate)
            wdg.bind('<Control-O>', self._on_locate)
            wdg.bind('<Control-c>', self._on_copy)
            wdg.bind('<Control-C>', self._on_copy)
        # Esc 在任何子控件上都要能关窗（含开关与按钮）
        self.win.bind('<Escape>', lambda e: self.hide())
        self.entry.bind('<KeyRelease>', self._on_typed)

        # —— 鼠标：点选 / 悬停 / 双击定位 / 滚轮 / 拖动 / 缩放 ——
        self.cv.bind('<Button-1>', self._on_click)
        self.cv.bind('<Double-Button-1>', self._on_locate)
        self.cv.bind('<Motion>', self._on_motion)
        self.cv.bind('<Leave>', self._on_hover_leave)
        self.cv.bind('<MouseWheel>', self._on_wheel)
        self.cv.bind('<Configure>', lambda e: self._render())
        for wdg in (outer, body, head, title, self.hint, opts, mid, foot,
                    self.status):
            self._bind_drag(wdg)
        # 失焦检测：焦点离开本窗（含所有子控件）即关
        self._bind_focusout(body)
        self._paint_entry_bg()

    def _bind_drag(self, wdg):
        wdg.bind('<Button-1>', self._drag_start, add='+')
        wdg.bind('<B1-Motion>', self._drag_move, add='+')

    def _bind_focusout(self, widget):
        widget.bind('<FocusOut>',
                    lambda e: self.win.after(150, self._check_focus), add='+')
        for ch in widget.winfo_children():
            self._bind_focusout(ch)

    def _check_focus(self):
        if not self.visible:
            return
        # 首次显示后 400ms 宽限：AttachThreadInput/SetForegroundWindow
        # 失败的竞态期内不因 FocusOut 自检立即关窗（延后再查一次）
        if time.monotonic() - self._show_ts < 0.4:
            self.win.after(200, self._check_focus)
            return
        try:
            if _WIN32_OK:
                hwnd = win32gui.GetAncestor(self.win.winfo_id(), 2)
                # 窗口本身仍可见就保留，确保热键唤起不会表现为"没反应"
                if win32gui.IsWindowVisible(hwnd):
                    return
            if self.win.focus_displayof() is None:
                self.hide()
        except tk.TclError:
            pass

    # ---------- 圆角底图 ----------

    def _paint_entry_bg(self):
        if not self._rp:
            return
        w = max(self._card.winfo_width(), 60)
        img = self._rp(w, self.entry_h, self._px(9), self._c['box'],
                       self._c['border'], 1)
        if img is not None:
            self._img_entry = img
            self._entry_bg.configure(image=img)

    # ---------- 显示/隐藏 ----------

    def _force_foreground(self):
        """后台进程热键唤起时，overrideredirect 小窗默认抢不到前台焦点，
        会被失焦自检立刻关掉；用 AttachThreadInput 方式强制前置。"""
        if not _WIN32_OK:
            return
        try:
            hwnd = win32gui.GetAncestor(self.win.winfo_id(), 2)  # GA_ROOT
            fore = win32gui.GetForegroundWindow()
            cur_tid = win32api.GetCurrentThreadId()
            fore_tid = win32process.GetWindowThreadProcessId(fore)[0]
            win32process.AttachThreadInput(cur_tid, fore_tid, True)
            try:
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                win32gui.BringWindowToTop(hwnd)
                win32gui.SetForegroundWindow(hwnd)
            finally:
                win32process.AttachThreadInput(cur_tid, fore_tid, False)
        except Exception:
            pass

    def _clamp_to_monitor(self, x, y, w, h):
        """多屏边缘保护：把窗口夹回它所在显示器的工作区内"""
        if not _WIN32_OK:
            return x, y
        try:
            mon = win32api.MonitorFromPoint(
                (x + w // 2, y + h // 3), win32con.MONITOR_DEFAULTTONEAREST)
            mx0, my0, mx1, my1 = win32api.GetMonitorInfo(mon)['Work']
            w = min(w, mx1 - mx0)
            h = min(h, my1 - my0)
            x = min(max(x, mx0), mx1 - w)
            y = min(max(y, my0), my1 - h)
        except Exception:
            pass
        return x, y

    def _apply_geometry(self):
        m = re.match(r'(\d+)x(\d+)\+(-?\d+)\+(-?\d+)',
                     str(self.app.cfg.get('quickwin_geo') or ''))
        if m:
            gw, gh, gx, gy = (int(v) for v in m.groups())
        else:
            gw, gh = self._px(self.BASE_W), self._px(self.BASE_H)
            sw, sh = (self.win.winfo_screenwidth(),
                      self.win.winfo_screenheight())
            gx, gy = (sw - gw) // 2, int(sh * 0.25)
        gw = max(gw, self._px(400))
        gh = max(gh, self._px(300))
        gx, gy = self._clamp_to_monitor(gx, gy, gw, gh)
        self.win.geometry(f'{gw}x{gh}+{gx}+{gy}')

    def show(self):
        self._apply_colors()
        self._saved_hwnd = None
        if _WIN32_OK:
            try:
                self._saved_hwnd = win32gui.GetForegroundWindow()
            except Exception:
                self._saved_hwnd = None
        self.entry.delete(0, 'end')
        self.rows = []
        self.shown = 0
        self.sel = None
        self.hover = None
        self._hist_idx = None
        self._kw = ''
        self._empty = '输入关键词，停顿即搜'
        self._seq += 1                    # 作废所有在途搜索结果
        self._set_status(self._empty)
        self.win.deiconify()
        self.visible = True
        # 记录首次显示时刻，供失焦自检 400ms 宽限使用
        self._show_ts = time.monotonic()
        self.win.update_idletasks()
        # 首次映射后补 Win11 圆角 + DWM 投影（overrideredirect 默认都没有）
        if _WIN32_OK and winchrome is not None \
                and not getattr(self, '_chrome_done', False):
            try:
                _hw = win32gui.GetAncestor(self.win.winfo_id(), 2)
                winchrome.set_rounded(_hw)
                winchrome.enable_popup_shadow(_hw)
                self._chrome_done = True
            except Exception:
                pass
        self._apply_geometry()
        self._render()
        self.win.lift()
        self._force_foreground()
        self.entry.focus_force()

    def hide(self, restore=True):
        self.visible = False
        if self._debounce_id:
            self.win.after_cancel(self._debounce_id)
            self._debounce_id = None
        self._remember()
        self.win.withdraw()
        # 把焦点还给先前台窗口（不粘贴，仅恢复）
        if restore and self._saved_hwnd and _WIN32_OK:
            _force_set_foreground(self._saved_hwnd)

    def _remember(self):
        """记住位置尺寸 + 搜索历史（上限 20 条，去重后置顶）"""
        try:
            kw = self.entry.get().strip()
            if kw:
                hist = [k for k in (self.app.cfg.get('quickwin_history') or [])
                        if k != kw]
                hist.insert(0, kw)
                self.app.cfg['quickwin_history'] = hist[:20]
            geo = (f'{self.win.winfo_width()}x{self.win.winfo_height()}'
                   f'+{self.win.winfo_x()}+{self.win.winfo_y()}')
            if geo != self.app.cfg.get('quickwin_geo'):
                self.app.cfg['quickwin_geo'] = geo
            self.app._save_config()
        except Exception:
            pass

    def _apply_colors(self):
        c = self._colors()
        self._c = c
        self.win.configure(bg=c['border'])
        self._outer.configure(bg=c['border'])
        for f in self._frames:
            try:
                f.configure(bg=c['bg'])
            except tk.TclError:
                pass
        for lbl, kind in self._labels:
            try:
                lbl.configure(bg=c['bg'], fg=c[kind])
            except tk.TclError:
                pass
        self.entry.configure(bg=c['box'], fg=c['fg'],
                             insertbackground=c['fg'])
        self.cv.configure(bg=c['box'])
        self._entry_bg.configure(bg=c['bg'])
        self.grip.configure(bg=c['bg'])
        self.save_btn.retheme(c['box'], c['hover'], c['fg'], c['bg'],
                              c['border'])
        self._paint_entry_bg()
        self._render()

    def _set_status(self, t):
        try:
            self.status.configure(text=t)
        except tk.TclError:
            pass

    # ---------- 检索 ----------

    def _on_opt(self, *_a):
        """开关变化：有词立刻重搜，没词只更新提示"""
        if self.entry.get().strip():
            if self._debounce_id:
                self.win.after_cancel(self._debounce_id)
                self._debounce_id = None
            self._search()
        else:
            self._set_status('检索范围：仅看有效' if self.app.only_active.get()
                             else '检索范围：全部文件')

    def _on_typed(self, evt):
        if evt.keysym in ('Return', 'KP_Enter', 'Escape', 'Tab',
                          'Up', 'Down', 'Left', 'Right',
                          'Home', 'End'):
            return
        self._hist_idx = None
        if self._debounce_id:
            self.win.after_cancel(self._debounce_id)
        self._debounce_id = self.win.after(300, self._search)

    def _search(self):
        self._debounce_id = None
        kw = self.entry.get().strip()
        if not kw:
            self._seq += 1
            self.rows = []
            self.shown = 0
            self.sel = None
            self._kw = ''
            self._empty = '输入关键词，停顿即搜'
            self._render()
            self._set_status(self._empty)
            return
        self._seq += 1
        seq = self._seq
        self._kw = kw
        self._set_status(f'正在搜「{kw[:20]}」…')
        db_path = self.db_path
        only_active = bool(self.app.only_active.get())
        syn_map = {}
        if self.app.syn_var.get():
            try:
                syn_map = expand_map(
                    kw.split(), self.app.cfg.get('custom_synonyms', ''))
            except Exception:
                syn_map = {}

        def work():
            try:
                res = search(db_path, kw, only_active=only_active,
                             syn_map=syn_map)
                total = len(res)
                seen, uniq = set(), []
                for r in res:
                    key = (r['path'], r['line_no'])
                    if key in seen:
                        continue
                    seen.add(key)
                    uniq.append(r)
                    if len(uniq) >= QuickWin.PREFETCH:
                        break
                rows = []
                for r in uniq:
                    # [Page N]/[Slide N] 前缀已在位置列显示，正文里去掉免重复
                    body = re.sub(r'^\[(?:Page|Slide)\s*\d+\]\s*',
                                  '', r['line_text'] or '')
                    rows.append({
                        'path': r['path'],
                        'line_no': r['line_no'],
                        'name': os.path.splitext(
                            os.path.basename(r['path']))[0],
                        'text': ' '.join(body.split()),
                        'page': self.app._page_label(
                            r['line_text'], r['line_no'], r['path']),
                        'chain': _chapter_chain(db_path, r['path'],
                                                r['line_no']),
                    })
            except Exception:
                rows, total = [], 0
            self._result_q.put((seq, rows, total, kw))

        threading.Thread(target=work, daemon=True).start()
        if self._poll_id is None:
            self._poll_id = self.win.after(80, self._poll_results)

    def _poll_results(self):
        try:
            while True:
                seq, rows, total, kw = self._result_q.get_nowait()
                self._fill(seq, rows, total, kw)
        except queue.Empty:
            pass
        if self.visible:
            self._poll_id = self.win.after(80, self._poll_results)
        else:
            self._poll_id = None

    def _fill(self, seq, rows, total, kw):
        if seq != self._seq or not self.visible:
            return
        self.rows = rows
        self.shown = min(len(rows), self.PAGE)
        self.hover = None
        if rows:
            self.sel = 0
            self.cv.yview_moveto(0)
            extra = (f' · 已载入 {len(rows)}' if total > len(rows) else '')
            self._set_status(f'共 {total} 条命中{extra}')
        else:
            self.sel = None
            self._empty = f'没搜到「{kw[:20]}」\n换个关键词，或关掉「仅看有效」再试'
            self._set_status(f'没搜到「{kw[:20]}」')
        self._render()

    # ---------- 绘制 ----------

    def _ellipsize(self, text, font, maxw):
        if maxw <= 0 or not text:
            return ''
        if font.measure(text) <= maxw:
            return text
        lo, hi = 0, len(text)
        while lo < hi:
            mid = (lo + hi) // 2
            if font.measure(text[:mid] + '…') <= maxw:
                lo = mid + 1
            else:
                hi = mid
        return text[:max(0, lo - 1)] + '…'

    def _round_rect(self, x0, y0, x1, y1, r, fill):
        pts = [x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r, x1, y1 - r,
               x1, y1, x1 - r, y1, x0 + r, y1, x0, y1, x0, y1 - r,
               x0, y0 + r, x0, y0]
        self.cv.create_polygon(pts, smooth=True, fill=fill, outline=fill)

    def _snippet_parts(self, text, kw):
        """正文片段切段：[(文字, 是否命中), ...]，命中词单独成段便于着色"""
        if not text:
            return []
        width, lead = 220, 12
        low, k = text.lower(), (kw or '').lower()
        pos = low.find(k) if k else -1
        if pos < 0 and k:
            try:
                pos = self.app._find_fuzzy_pos(text, k)
            except Exception:
                pos = -1
        if pos < 0:
            start, seg, rel = 0, text[:width], []
        else:
            start = max(0, pos - lead)
            seg = text[start:start + width]
            rel, i = [], seg.lower().find(k)
            while i >= 0:
                rel.append(i)
                i = seg.lower().find(k, i + len(k))
            if not rel:                        # 模糊命中：只标首个近似位
                rel = [max(0, pos - start)]
        parts, cur = [], 0
        for p in rel:
            if p > cur:
                parts.append((seg[cur:p], False))
            parts.append((seg[p:p + len(k)], True))
            cur = p + len(k)
        if cur < len(seg):
            parts.append((seg[cur:], False))
        if start > 0:
            parts.insert(0, ('…', False))
        if start + width < len(text):
            parts.append(('…', False))
        return parts

    def _draw_snippet(self, x, y, maxw, item, c):
        cx = x
        for text, hit in self._snippet_parts(item['text'], self._kw):
            if not text:
                continue
            f = self._f_hit if hit else self._f_body
            if cx + f.measure(text) > x + maxw:
                text = self._ellipsize(text, f, max(0, x + maxw - cx))
                if not text:
                    break
            self.cv.create_text(cx, y, text=text, anchor='w', font=f,
                                fill=c['hit'] if hit else c['sub'])
            cx += f.measure(text)
            if cx >= x + maxw:
                break

    def _render(self):
        cv = self.cv
        cv.delete('all')
        c = getattr(self, '_c', None) or self._colors()
        w = cv.winfo_width()
        if w <= 1:
            w = max(self.win.winfo_width() - self._px(42), 200)
        rh = self.row_h
        if not self.rows:
            cv.create_text(w // 2, max(self._px(70), cv.winfo_height() // 2),
                           text=self._empty, font=self._f_body,
                           fill=c['sub'], justify='center', width=w - 40)
            cv.configure(scrollregion=(0, 0, w, 1))
            return
        pad = self._px(10)
        for i in range(self.shown):
            it = self.rows[i]
            y0 = i * rh
            if i == self.sel or i == self.hover:
                col = c['sel'] if i == self.sel else c['hover']
                self._round_rect(self._px(4), y0 + self._px(2),
                                 w - self._px(6), y0 + rh - self._px(3),
                                 self._px(6), col)
            # 第一行：文件名 · 章节链 · L行号 页码
            name = it['name']
            cv.create_text(pad, y0 + rh * 0.30, text=name, anchor='w',
                           font=self._f_title, fill=c['fg'])
            x = pad + self._f_title.measure(name) + self._px(8)
            tail = ' · '.join(p for p in (
                it['chain'],
                f"L{it['line_no']}" + (f" {it['page']}" if it['page'] else ''),
            ) if p)
            tail = self._ellipsize(tail, self._f_small,
                                   w - x - self._px(14))
            if tail:
                cv.create_text(x, y0 + rh * 0.30, text=tail, anchor='w',
                               font=self._f_small, fill=c['sub'])
            # 第二行：正文片段（命中词着色）
            self._draw_snippet(pad, y0 + rh * 0.72,
                               w - pad * 2, it, c)
        cv.configure(scrollregion=(0, 0, w, self.shown * rh))

    # ---------- 选择/滚动 ----------

    def _load_more(self, render=True):
        if self.shown >= len(self.rows):
            return
        self.shown = min(len(self.rows), self.shown + self.PAGE)
        if render:
            self._render()

    def _ensure_visible(self, i):
        ch = self.cv.winfo_height()
        if ch <= 1:
            return
        total = max(self.shown * self.row_h, 1)
        top = self.cv.canvasy(0)
        y0, y1 = i * self.row_h, (i + 1) * self.row_h
        if y0 < top:
            self.cv.yview_moveto(y0 / total)
        elif y1 > top + ch:
            self.cv.yview_moveto(max(0.0, (y1 - ch) / total))

    def _select(self, i):
        if not self.rows:
            return
        i = max(0, min(len(self.rows) - 1, i))
        # 目标行还在「未上屏」区间时先补齐，否则选中了却看不见
        while i >= self.shown and self.shown < len(self.rows):
            self._load_more(render=False)
        self.sel = i
        self._ensure_visible(i)
        self._render()

    def _index_at(self, y):
        i = int(self.cv.canvasy(y) // self.row_h)
        return i if 0 <= i < self.shown else None

    def _on_click(self, e):
        i = self._index_at(e.y)
        if i is None:
            return
        self.sel = i
        self._render()

    def _on_motion(self, e):
        i = self._index_at(e.y)
        if i != self.hover:
            self.hover = i
            self.cv.configure(cursor='hand2' if i is not None else 'arrow')
            self._render()

    def _on_hover_leave(self, _e):
        if self.hover is not None:
            self.hover = None
            self._render()

    def _on_wheel(self, e):
        self.cv.yview_scroll(int(-e.delta / 120) * 3, 'units')
        if self.cv.yview()[1] > 0.985:
            self._load_more()
        return 'break'

    # ---------- 键盘 ----------

    def _on_up(self, _evt=None):
        if not self.entry.get().strip() and self._history(-1):
            return 'break'
        self._move(-1)
        return 'break'

    def _on_down(self, _evt=None):
        if not self.entry.get().strip() and self._hist_idx is not None \
                and self._history(1):
            return 'break'
        self._move(1)
        return 'break'

    def _move(self, delta):
        if not self.rows:
            return
        self._select(0 if self.sel is None else self.sel + delta)

    def _on_pgup(self, _evt=None):
        step = max(1, self.cv.winfo_height() // self.row_h - 1)
        self._move(-step)
        return 'break'

    def _on_pgdn(self, _evt=None):
        step = max(1, self.cv.winfo_height() // self.row_h - 1)
        self._move(step)
        return 'break'

    def _on_home(self, _evt=None):
        if self.rows:
            self._select(0)
        return 'break'

    def _on_end(self, _evt=None):
        if self.rows:
            self._select(len(self.rows) - 1)
        return 'break'

    def _history(self, step):
        """↑↓ 在空输入下翻搜索历史；返回 True 表示已消费按键"""
        hist = [k for k in (self.app.cfg.get('quickwin_history') or []) if k]
        if not hist:
            return False
        i = 0 if self._hist_idx is None else self._hist_idx + step
        if i < 0:
            self._hist_idx = None
            self.entry.delete(0, 'end')
            self._set_status('输入关键词，停顿即搜')
            return True
        if i >= len(hist):
            return True
        self._hist_idx = i
        self.entry.delete(0, 'end')
        self.entry.insert(0, hist[i])
        self.entry.icursor('end')
        self._search()
        return True

    # ---------- 动作 ----------

    def _cite(self):
        if self.sel is None or self.sel >= len(self.rows):
            return None
        it = self.rows[self.sel]
        try:
            return self.cite_fn(it['path'], it['line_no']) or None
        except Exception:
            return None

    def _on_enter(self, _evt=None):
        cite = self._cite()
        if not cite:
            self._set_status('该条无法生成引用')
            return 'break'
        self.app.root.clipboard_clear()
        self.app.root.clipboard_append(cite)
        hwnd = self._saved_hwnd
        self.hide(restore=False)
        ok = paste_to_foreground(hwnd)
        if not ok:
            # 粘贴失败：把前台焦点归还原窗口，避免原窗口失去激活状态
            try:
                _force_set_foreground(hwnd)
            except Exception:
                pass
        msg = ('已粘贴规范引用到先前窗口'
               if ok else '已复制规范引用到剪贴板（未能自动粘贴，请手动 Ctrl+V）')
        self.app.root.after(150, lambda: self.app._toast(msg))
        return 'break'

    def _on_copy(self, _evt=None):
        """Ctrl+C：只复制规范引用，不自动粘贴（粘贴留给 Enter）"""
        cite = self._cite()
        if not cite:
            return None            # 没结果 → 交回 Entry 默认复制文本
        self.app.root.clipboard_clear()
        self.app.root.clipboard_append(cite)
        self._set_status('已复制规范引用（未粘贴）')
        return 'break'

    def _on_locate(self, _evt=None):
        if self.sel is None or self.sel >= len(self.rows):
            return 'break'
        it = self.rows[self.sel]
        path, line_no = it['path'], it['line_no']
        self.hide()
        self.app.root.after(120,
                            lambda: self.app._open_located(path, line_no))
        return 'break'

    def _quick_save(self):
        self.hide()
        self.app.root.after(120, self.app.quick_save_clipboard)

    # ---------- 拖动 / 缩放 ----------

    def _drag_start(self, e):
        self._drag = (e.x_root, e.y_root,
                      self.win.winfo_x(), self.win.winfo_y())

    def _drag_move(self, e):
        if not self._drag:
            return
        self.win.geometry('+%d+%d' % (
            self._drag[2] + e.x_root - self._drag[0],
            self._drag[3] + e.y_root - self._drag[1]))

    def _rs_start(self, e):
        self._drag = (e.x_root, e.y_root,
                      self.win.winfo_width(), self.win.winfo_height())

    def _rs_move(self, e):
        if not self._drag:
            return
        w = max(self._px(400), self._drag[2] + e.x_root - self._drag[0])
        h = max(self._px(300), self._drag[3] + e.y_root - self._drag[1])
        self.win.geometry(f'{w}x{h}')