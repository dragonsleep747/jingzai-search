# -*- coding: utf-8 -*-
"""
镜仔搜索 · 文件内容检索系统
Copyright (c) 2026 SleepDragon
SPDX-License-Identifier: MIT

本软件由SleepDragon独立开发，Licensed under the MIT License; see LICENSE in the repository root.
"""

"""镜仔搜索 · 文件内容检索 GUI（Tkinter）

P0-1  扫描可中途停止（按钮切换为「停止扫描」）
P0-5  错误日志窗口，查看/清空失败文件与完整堆栈
P0-2  IGNORE_DIRS 黑名单自动跳过
P1-6  多关键词：空格=AND，a|b 或 a OR b=OR，-词=NOT
P1-12 导出 CSV：路径/行号/内容/上下文
P1-13 类型下拉：全部/文本/Office/PDF/代码
P1-14 上下文行数：可调输入框，默认 5
P1-15 配置持久化：config.json 存目录/历史关键词/窗口尺寸
"""
import os
import csv
import json
import re
import shutil
import sqlite3
import threading
import subprocess
import sys
import time
import webbrowser
from urllib.parse import quote
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk, filedialog, messagebox, simpledialog

from indexer import (
    index_directory, get_errors, get_error_traceback,
    clear_errors, count_errors,
    import_file_to_library, link_file_to_library,
    plan_import, replace_library_file, is_registered,
    list_library, remove_from_library, library_stats,
    rename_in_library,
    find_duplicates, refresh_index, rebuild_bigram, bigram_ready,
    add_bookmark, remove_bookmark, list_bookmarks, is_bookmarked,
    set_bookmark_note, find_missing_documents, delete_documents,
    save_reading_position, get_reading_position,
    update_metadata, get_metadata,
    record_error,
    get_category_rules, save_category_rules, category_counts,
    category_audit, apply_categories, set_category_ignored,
    apply_categories_ex, revert_categories,
    OTHER_CATEGORY, CATEGORY_FOLDERS, index_single_file,
    _file_sha256, _sha_in_library, _quick_fingerprint,
    index_batch_files,
)
from searcher import (
    search, search_fuzzy, extract_page, _PAGE_RE, SearchCancelled,
    get_context, get_paragraph, get_line_range, get_line_text,
    aggregate_results, get_chapter_for_line, get_chapter_chain_str,
    list_chapters, fetch_context_lines,
    find_refs_in_text, resolve_ref, find_clause_line,
    outgoing_refs, incoming_refs, incoming_refs_multi, diff_documents,
    ref_network_warnings,
    compliance_gap_scan,
    find_version_siblings, parse_advanced_query, MAX_HITS,
    _norm_variants,
)
from core import (
    _get_app_dir, APP_DIR, _resource_path, CONFIG_PATH,
    DEFAULT_FEEDBACK_URL, TYPE_OPTIONS, CTX_LINES,
    load_config, save_config, _DB_MIN_SIZE, _resolve_data_root,
    _gen_token, kw_terms, KW_HINT, build_citation,
)
from service import (
    fmt_size, fmt_eta, fmt_mtime, valid_date, eff_sort_key,
    find_fuzzy_pos, is_table_row, parse_dropped_paths, line_path_from_tags,
    prepare_filter_cache, plan_row_cost, parse_excel_loc, snapshot_db,
    page_label, snippet, bar_pts, find_acrobat, find_sumatra,
)
from export import write_digest_docx, build_search_hits
from runtime import (
    _STARTUP_CFG, DATA_ROOT, DB_PATH, BACKUP_DIR, MAX_BACKUPS,
    _URL_RE, LIBRARY_DIR, _DB_RELOCATED,
)
from updater import scan_local as _scan_local, build_search_url as _build_search_url
from synonyms import expand_map, describe as syn_describe
from watcher import FileWatcher
from ui_dialogs import DialogsMixin

# 批2/批3 可选第三方依赖（热键迷你窗/动效/窗口镀铬/联网检索/托盘/
# Ollama 问答窗/引用关系图/拖拽/sv-ttk 主题）统一在 thirdparty.py
# 加载降级，main 与 ui_dialogs 共享同一份模块对象与标志
import queue
import ipc
from thirdparty import (
    quickwin, _HOTKEY_OK, anim, winchrome, websearch, _tray_mod, _TRAY_OK,
    qa_win, _QA_OK, graph_html, _GRAPH_OK,
    TkinterDnD, DND_FILES, _DND_AVAILABLE, sv_ttk, _SVTTK,
)

# 路径 / 配置 / 常量（_get_app_dir / APP_DIR / _resource_path / CONFIG_PATH /
# load_config / save_config / TYPE_OPTIONS / CTX_LINES 等）已抽到 core.py，
# 本模块通过顶部 `from core import ...` 引用，保持对外接口不变。

# 还原防黑帧：常驻轮询主窗是否被最小化（每次一次 user32.IsIconic，开销可忽略），
# 一发现它从最小化回来就连续抢画这么多拍（10ms × 60 ≈ 600ms）。
RESTORE_POLL_MS = 10
RESTORE_KEEP_TICKS = 60


# load_config / save_config 已抽到 core.py（顶部 from core import 引用）

def _grab_screen_region(x, y, w, h):
    """屏幕取色：BitBlt 桌面 DC 的一块区域 → PIL.Image（失败返回 None）。

    为什么不用 PrintWindow：它会给目标窗口发 WM_PRINT，若从窗口过程里调用会
    同步回调进我们自己的窗口过程，重入 ctypes 回调丢线程状态 → 直接崩。
    BitBlt 是纯 GDI，不发任何窗口消息，更安全（调用点见 _grab_restore_cover）。
    只在窗口「还正常显示」时调用才有意义：一旦最小化生效，屏幕上就只剩别的
    东西了。
    """
    if w <= 0 or h <= 0:
        return None
    import ctypes
    from ctypes import wintypes
    from PIL import Image
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32
    SRCCOPY = 0x00CC0020
    hdc_screen = user32.GetDC(0)
    if not hdc_screen:
        return None
    hdc_mem = bmp = None
    try:
        hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
        bmp = gdi32.CreateCompatibleBitmap(hdc_screen, w, h)
        if not hdc_mem or not bmp:
            return None
        gdi32.SelectObject(hdc_mem, bmp)
        if not gdi32.BitBlt(hdc_mem, 0, 0, w, h, hdc_screen, x, y, SRCCOPY):
            return None

        class _BMIH(ctypes.Structure):
            _fields_ = (('biSize', wintypes.DWORD), ('biWidth', ctypes.c_long),
                        ('biHeight', ctypes.c_long), ('biPlanes', wintypes.WORD),
                        ('biBitCount', wintypes.WORD),
                        ('biCompression', wintypes.DWORD),
                        ('biSizeImage', wintypes.DWORD),
                        ('biXPelsPerMeter', ctypes.c_long),
                        ('biYPelsPerMeter', ctypes.c_long),
                        ('biClrUsed', wintypes.DWORD),
                        ('biClrImportant', wintypes.DWORD))
        bi = _BMIH()
        bi.biSize = ctypes.sizeof(_BMIH)
        bi.biWidth = w
        bi.biHeight = -h            # 负值 = 自顶向下，省一次翻转
        bi.biPlanes = 1
        bi.biBitCount = 32
        bi.biCompression = 0        # BI_RGB
        buf = ctypes.create_string_buffer(w * h * 4)
        if not gdi32.GetDIBits(hdc_mem, bmp, 0, h, buf,
                               ctypes.byref(bi), 0):
            return None
        return Image.frombuffer('RGB', (w, h), buf.raw, 'raw', 'BGRX', 0, 1)
    except Exception:
        return None
    finally:
        try:
            if bmp:
                gdi32.DeleteObject(bmp)
        except Exception:
            pass
        try:
            if hdc_mem:
                gdi32.DeleteDC(hdc_mem)
        except Exception:
            pass
        try:
            user32.ReleaseDC(0, hdc_screen)
        except Exception:
            pass


# _DB_MIN_SIZE / _resolve_data_root 已抽到 core.py（顶部 from core import 引用）
# DATA_ROOT / DB_PATH / BACKUP_DIR / MAX_BACKUPS / _URL_RE / LIBRARY_DIR /
# _DB_RELOCATED / _STARTUP_CFG 已抽到 runtime.py（顶部 from runtime import 引用）


# _gen_token / kw_terms / KW_HINT / build_citation 已抽到 core.py

class Tooltip:
    """轻量悬停提示：<Enter> 后 400ms 弹出小窗，<Leave> 即消失"""
    def __init__(self, widget, text):
        self.widget = widget
        self.text = text
        self.tip = None
        self._after_id = None
        widget.bind('<Enter>', self._schedule, add='+')
        widget.bind('<Leave>', self._hide, add='+')
        widget.bind('<ButtonPress>', self._hide, add='+')

    def _schedule(self, _evt=None):
        self._cancel()
        self._after_id = self.widget.after(400, self._show)

    def _cancel(self):
        if self._after_id:
            try:
                self.widget.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None

    def _show(self):
        if self.tip or not self.text:
            return
        x = self.widget.winfo_rootx() + 12
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        win = tk.Toplevel(self.widget)
        win.wm_overrideredirect(True)
        win.wm_geometry(f'+{x}+{y}')
        tk.Label(win, text=self.text, background='#FFFBE6',
                 relief='solid', borderwidth=1,
                 font=('Microsoft YaHei UI', 8), padx=6, pady=3
                 ).pack()
        self.tip = win
        # 淡入 + 上浮 4px（uiverse tooltip 风格；减弱动效时直显）
        if anim is not None and not anim.reduced():
            try:
                win.update_idletasks()
                win.attributes('-alpha', 0.0)
                win.geometry(f'+{x}+{y + 4}')
                anim.fade_in(win, 110)
                anim.Tweener(win).tween(
                    y + 4, y, 110,
                    lambda _t, ny: win.geometry(f'+{x}+{int(ny)}'),
                    easing='ease_out_cubic')
            except Exception:
                pass

    def _hide(self, _evt=None):
        self._cancel()
        if self.tip:
            try:
                self.tip.destroy()
            except Exception:
                pass
            self.tip = None


class ReadonlyText(tk.Text):
    """只读正文框：用户既敲不进字也删不掉内容（含链接），
    程序内部写入时自动临时解锁，调用方无需关心 state。
    选中/复制/滚动/链接点击均不受影响（Tk 的 disabled 只挡插入与删除）。"""

    def insert(self, index, chars, *args):
        self.configure(state='normal')
        try:
            return super().insert(index, chars, *args)
        finally:
            self.configure(state='disabled')

    def delete(self, index1, index2=None):
        self.configure(state='normal')
        try:
            return super().delete(index1, index2)
        finally:
            self.configure(state='disabled')


def _rounded_photo(w, h, radius, fill, outline=None, border_w=0, corner=None):
    """PIL 生成圆角矩形 PhotoImage（RGBA）。PIL 不可用返回 None。

    Tk 8.6 没有原生圆角能力，圆角按钮全靠这张位图：
    - 直接画整块按钮底（不做 9 宫格，尺寸即最终尺寸）；
    - 3 倍超采样后 LANCZOS 缩回，边缘无锯齿。

    corner=None 时四角 alpha=0（配 tk.Label 用，透出控件自身 bg）；
    corner='#RRGGBB' 时四角填该色不透明——ttk image element 会在位图底下
    先刷一层样式 background，透明角只会露出那层底色，圆角等于白做，
    所以给 ttk 用时必须把「栏底色」直接画进角里。
    返回值必须由调用方持有引用，否则被 GC 后图片会变空白。"""
    try:
        from PIL import Image, ImageDraw, ImageTk
    except Exception:
        return None
    w, h, radius = max(int(w), 2), max(int(h), 2), max(int(radius), 0)
    s = 3
    try:
        img = Image.new('RGBA', (w * s, h * s),
                        corner if corner else (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        o = (border_w * s) / 2.0
        d.rounded_rectangle([o, o, w * s - 1 - o, h * s - 1 - o],
                            radius=radius * s, fill=fill, outline=outline,
                            width=int(border_w) * s)
        return ImageTk.PhotoImage(img.resize((w, h), Image.LANCZOS))
    except Exception:
        return None


class RoundButton(tk.Label):
    """圆角按钮：PIL 生成整块圆角底图，文字以 compound=center 覆盖其上。

    刻意兼容 tk.Button 的 bg/background/activebackground 配置习惯，
    让既有三处调用点（hover 渐变补间、AI 呼吸动画、主题切换换色）
    一行不改即可复用；四角透明处用 corner_bg 与所在栏底色对齐，视觉隐形。
    """

    def __init__(self, master, text='', command=None, font=None, height=32,
                 padx=14, radius=9, bg='#FFFFFF', fg='#000000',
                 activebackground=None, corner_bg='#F4F6F9', cursor='hand2'):
        self._rb_bg = str(bg)
        self._rb_fg = str(fg)
        self._rb_hover = str(activebackground or bg)
        self._rb_corner = str(corner_bg)
        self._rb_h = max(int(height), 18)
        self._rb_padx = int(padx)
        self._rb_r = max(int(radius), 0)
        self._rb_font = font
        self._rb_cache = {}
        tk.Label.__init__(self, master, text=text, font=font, fg=self._rb_fg,
                          bg=self._rb_corner, bd=0, highlightthickness=0,
                          compound='center', padx=0, pady=0, takefocus=0,
                          cursor=cursor)
        self._rb_paint()
        if command is not None:
            self.bind('<ButtonRelease-1>', lambda _e: command(), add='+')

    # —— 渲染 ——
    def _rb_width(self):
        try:
            f = tkfont.Font(font=self._rb_font or self.cget('font'))
            return f.measure(self.cget('text')) + self._rb_padx * 2
        except Exception:
            return 120

    def _rb_paint(self):
        w = max(self._rb_width(), self._rb_h + 10)
        img = self._rb_cache.get((w, self._rb_bg))
        if img is None:
            img = _rounded_photo(w, self._rb_h, self._rb_r, self._rb_bg)
            if img is None:          # PIL 缺失：退化成普通方块按钮
                tk.Label.configure(self, bg=self._rb_bg)
                return
            # 呼吸/hover 会扫过大量中间色，缓存封顶避免无限增长
            if len(self._rb_cache) > 240:
                self._rb_cache.clear()
            self._rb_cache[(w, self._rb_bg)] = img
        tk.Label.configure(self, image=img, bg=self._rb_corner)

    # —— 兼容 tk.Button 的配置接口 ——
    def configure(self, cnf=None, **kw):
        if cnf is not None and not isinstance(cnf, dict):
            return tk.Label.configure(self, cnf)
        opts = dict(cnf) if isinstance(cnf, dict) else {}
        opts.update(kw)
        dirty = False
        for k in ('background', 'bg'):
            if k in opts:
                v = str(opts.pop(k))
                if v != self._rb_bg:
                    self._rb_bg = v
                    dirty = True
        for k in ('foreground', 'fg'):
            if k in opts:
                self._rb_fg = str(opts.pop(k))
                tk.Label.configure(self, fg=self._rb_fg)
        if 'activebackground' in opts:
            self._rb_hover = str(opts.pop('activebackground'))
        # 文字/字体变了要重算位图宽度，否则圆角底还是旧尺寸
        if 'text' in opts or 'font' in opts:
            dirty = True
        opts.pop('activeforeground', None)     # Label 无此项，静默吞掉
        if dirty:
            self._rb_paint()
        if opts:
            return tk.Label.configure(self, **opts)
        return None

    config = configure

    def cget(self, key):
        if key in ('bg', 'background'):
            return self._rb_bg
        if key in ('fg', 'foreground'):
            return self._rb_fg
        if key == 'activebackground':
            return self._rb_hover
        return tk.Label.cget(self, key)

    def set_corner_bg(self, color):
        """四角透明区跟随所在栏底色（主题切换时调用）"""
        color = str(color)
        if color != self._rb_corner:
            self._rb_corner = color
            self._rb_cache.clear()
            self._rb_paint()


class App(DialogsMixin):
    # 状态码 ↔ 中文显示（库弹窗/列表/清单导出统一使用）
    STATUS_LABELS = {'active': '有效', 'void': '废止', 'replaced': '已替代'}
    STATUS_VALUES = {'有效': 'active', '废止': 'void', '已替代': 'replaced'}
    # 字体层级：标题12粗 / 正文10 / 界面9 / 辅助8（数字列统一等宽 Consolas）
    FONT_TITLE = ('Microsoft YaHei UI', 12, 'bold')
    FONT_MAIN = ('Microsoft YaHei UI', 10)
    FONT_UI = ('Microsoft YaHei UI', 9)
    FONT_UI_B = ('Microsoft YaHei UI', 9, 'bold')
    FONT_HINT = ('Microsoft YaHei UI', 8)
    FONT_CODE = ('Consolas', 10)
    # Fluent 图标：Segoe MDL2 Assets（Win10+ 自带）；同串中文自动 fallback 雅黑
    FONT_ICON = ('Segoe MDL2 Assets', 9)
    FONT_ICON_B = ('Segoe MDL2 Assets', 9, 'bold')
    FONT_ICON_S = ('Segoe MDL2 Assets', 8)
    FONT_ICON_BIG = ('Segoe MDL2 Assets', 44)
    # MDL2 码位（字形已逐个人工核对）
    IC_SEARCH = '\ue721'      # 放大镜
    IC_GEAR = '\ue713'        # 齿轮（设置）
    IC_WARN = '\ue7ba'        # 警告三角
    IC_STAR = '\ue734'        # 星形（描边）
    IC_STAR_FILL = '\ue735'   # 星形（实心）
    IC_DOWNLOAD = '\ue896'    # 下载/入箱
    IC_CHAT = '\ue8bd'        # 对话气泡（AI 问答）
    IC_FOLDER = '\ue8b7'      # 文件夹
    IC_FOLDER_ADD = '\ue8f4'  # 文件夹+（入库）
    IC_DOC = '\ue7c3'         # 文档
    IC_BOOKS = '\ue8f1'       # 书列（库管理）
    IC_SWAP = ''        # 重命名式双词（同义词）
    IC_HISTORY = ''     # 时钟（历史记录）
    IC_LINK = '\ue71b'         # 链接/引用
    IC_GLOBE = '\ue909'        # 地球/全球
    IC_PIN = '\ue81d'          # 定位标记
    IC_COPY = '\ue8c8'         # 复制
    IC_CHECK = '\ue73e'        # 对勾/成功
    IC_REFRESH = '\ue72c'      # 刷新/循环
    IC_SKIP = '\ue76d'         # 跳过/下一步
    IC_PACKAGE = '\ue7b8'      # 包裹/打包

    # —— Fluent 设计令牌：浅/深两套，标题栏·AppBar·弹层头部唯一配色出口 ——
    PALETTE_LIGHT = dict(
        bg='#F4F6F9', ab_hover='#E9EDF2', ab_press='#DFE4EA',
        band_bg='#FFFFFF', band_line='#DFE4EA',
        title_fg='#1B2632', sub_fg='#66707D', brand='#2F5E8C',
        cap_ink='#1B2632', cap_dim='#7B8694', cap_hover='#E9EDF2',
        sel_bg='#D6E3F0', hit_fg='#C13A28')
    PALETTE_DARK = dict(
        bg='#202020', ab_hover='#3A3A3C', ab_press='#45464A',
        band_bg='#2B2D30', band_line='#3A3B3F',
        title_fg='#F5F6F7', sub_fg='#9A9DA2', brand='#2889DE',
        cap_ink='#F4F4F5', cap_dim='#8C8C92', cap_hover='#333337',
        sel_bg='#31465C', hit_fg='#FFD166')


    def __init__(self, root):
        self.root = root
        self.root.title('镜仔搜索 · 文件内容检索')
        # 窗口图标（标题栏 + 任务栏）
        icon_path = _resource_path('app_icon.ico')
        if os.path.exists(icon_path):
            try:
                self.root.iconbitmap(default=icon_path)
                self.root.iconbitmap(icon_path)
            except Exception:
                pass
        # 先读配置再定主题（记住上次选择的深色/浅色）
        self.cfg = load_config()
        # 以下偏好已固化为默认行为（对应开关已从更多菜单移除，2026-10-01）
        self.cfg['frameless'] = True        # 无边框融合标题栏
        self.cfg['appbar_flat'] = True      # 扁平工具栏
        self.cfg['autowatch'] = True        # 自动监听文件变化
        self.cfg['inbox_auto_check'] = True  # 启动时检查待入库文件
        if _SVTTK:
            sv_ttk.set_theme('dark' if self.cfg.get('dark') else 'light')
        self.dark = tk.BooleanVar(value=bool(self.cfg.get('dark', False)))
        # 「减弱动效」开关：照顾低配办公机与晕动敏感用户
        self.reduce_motion_var = tk.BooleanVar(
            value=bool(self.cfg.get('reduce_motion', False)))
        if anim is not None:
            anim.set_reduce_motion(self.reduce_motion_var.get())
        geo = self.cfg.get('window', '')
        m = re.match(r'(\d+)x(\d+)', geo) if geo else None
        if m and int(m.group(1)) >= 600 and int(m.group(2)) >= 400:
            self.root.geometry(geo)
        else:
            # 首次打开：居中显示 1200x760
            _W, _H = 1200, 760
            _sw = self.root.winfo_screenwidth()
            _sh = self.root.winfo_screenheight()
            _x = max((_sw - _W) // 2, 0)
            _y = max((_sh - _H) // 2, 0)
            self.root.geometry(f'{_W}x{_H}+{_x}+{_y}')
        self.root_dir = tk.StringVar(value=self.cfg.get('root_dir', ''))
        self.keyword = tk.StringVar()
        self.cs = tk.BooleanVar(value=False)
        self.search_mode = tk.StringVar(value='exact')      # exact/fuzzy
        self.file_type = tk.StringVar(value='all')          # P1-13
        self.autowatch = tk.BooleanVar(value=bool(
            self.cfg.get('autowatch', True)))           # P1-11
        self.link_only = tk.BooleanVar(
            value=bool(self.cfg.get('link_only', False)))  # 库：仅登记路径（不复制文件）
        self.only_active = tk.BooleanVar(value=bool(
            self.cfg.get('only_active', True)))         # 仅看有效文件
        self.syn_var = tk.BooleanVar(value=bool(             # 同义词语义扩展
            self.cfg.get('synonym', True)))
        # 点叉行为：会话级记忆（None=本次启动还没问过 / 'tray' / 'exit'）。
        # 刻意只放内存、不落盘 —— 彻底退出后下次启动重新询问（用户要求）。
        self._close_choice = None
        self.results = []
        self.groups = []                                     # P0-2 聚合分组
        self._ref_targets = {}                               # 预览区引用链接映射
        self._prev_hist = []                                 # 预览区跳转历史栈（path,line_no）
        self._suppress_on_select_clear = False               # 内部跳转期间禁止清空历史栈
        self._score_map = {}                                 # 模糊标注映射
        self._cluster_rows = {}      # (path, 首行) → 该片段簇全部命中行号
        self._cluster_cross = {}     # (path, 首行) → (下行行号, 被切断的词)
        self._cur_cross = None       # 当前预览的跨行命中 (下行行号, 词)
        self._more_map = {}          # 「…还有 N 处」行 iid → (g, 折叠簇, 起始序号)
        self._tree_item_paths = {}   # 结果树父项 iid → 文件路径
        self._cur_hit_lines = None   # 当前预览需覆盖的全部命中行（合并行用）
        self.stop_event = None
        self._task_busy = False      # 入库等长任务进行中（退出确认保护）
        self.watcher = None                                  # P1-11
        # —— 后台运行（系统托盘）状态 ——
        self._tray = None              # 当前 TrayThread 实例
        self._tray_evt_q = None        # 托盘事件队列
        self._bg_mode = False          # 是否处于后台运行状态
        self._force_quit = False       # 托盘「退出程序」置位，绕过点叉隐藏
        self._pw_ref = None            # 当前进度窗引用（恢复时用）
        self._pw_stop_event = None     # 当前进度窗的 stop_event（右键取消用）
        self._bg_done_msg = None       # 后台期间完成的提示文案（恢复后回放）
        # 窗口尺寸变化时记录
        self.root.bind('<Configure>', self._on_resize)
        # F1 打开使用说明
        self.root.bind('<F1>', lambda e: self.show_help())
        # 快捷键：Ctrl+F 聚焦搜索 / F5 重扫 / Ctrl+E 导出
        self.root.bind('<Control-f>', self._focus_search)
        self.root.bind('<Control-F>', self._focus_search)
        self.root.bind('<F5>', lambda e: self._scan_action())
        self.root.bind('<Control-e>', lambda e: self.export_csv())
        self.root.bind('<Control-Alt-Left>', lambda e: self._go_back())
        # 语义索引启动时自动后台更新（有新文件/文件变动才跑，没变动秒退）
        self.qa_auto_var = tk.BooleanVar(
            value=bool(self.cfg.get('qa_auto', False)))
        self.autostart_var = tk.BooleanVar(value=self._get_autostart())
        self._build_ui()
        # 启动后台线程：自动增量更新索引（文件变化重索引、消失的移出）
        # 延迟 8 秒：避开启动高峰，也给移动硬盘/网络盘留出挂载时间
        self.root.after(8000, lambda: threading.Thread(
            target=self._auto_refresh_index, daemon=True).start())
        # 批2：全局热键 + 迷你速查窗（pywin32 缺失时不影响主程序）
        self._hotkey_q = queue.Queue()
        self._hotkey_thread = None
        self._hotkey_labels = {}
        self.quick_win = None
        self.ipc_server = None
        self._qa_window = None
        self._wins = {}   # 功能窗口单例表：key → Toplevel
        if quickwin is not None:
            self.quick_win = quickwin.QuickWin(
                self, DB_PATH, lambda p, l: build_citation(DB_PATH, p, l),
                rounded_photo=_rounded_photo)
            self._start_hotkeys()
            self.root.after(150, self._poll_hotkey)
        # 数据目录被自动定位到非默认位置 → 提示用户 + 记入 config
        if _DB_RELOCATED:
            try:
                self.cfg['last_db_path'] = DB_PATH
                save_config(self.cfg)
            except Exception:
                pass
            self.root.after(800, lambda: self._show_toast(
                f'已自动定位到库文件：{DATA_ROOT}', 'ok'))
        # 启动时如果默认位置没有 index.db 且也没找到任何候选 → 检查 backup
        elif not os.path.exists(DB_PATH):
            self.root.after(800, self._check_and_offer_backup_restore)

    def _check_and_offer_backup_restore(self):
        """启动时 index.db 不存在但 backup 有备份 → 询问是否恢复最新一份。"""
        if not os.path.isdir(BACKUP_DIR):
            self._show_toast(
                '未找到现有库，已创建空库（拖入文件开始入库）', 'idle')
            return
        try:
            backups = []
            for fn in os.listdir(BACKUP_DIR):
                if not fn.lower().endswith('.db'):
                    continue
                fp = os.path.join(BACKUP_DIR, fn)
                if not os.path.isfile(fp):
                    continue
                try:
                    if os.path.getsize(fp) >= _DB_MIN_SIZE:
                        backups.append((fn, fp, os.path.getmtime(fp),
                                        os.path.getsize(fp)))
                except OSError:
                    continue
            # 按修改时间倒序
            backups.sort(key=lambda x: x[2], reverse=True)
        except OSError:
            backups = []

        if not backups:
            self._show_toast(
                '未找到现有库，已创建空库（拖入文件开始入库）', 'idle')
            return

        latest_name, latest_path, _, latest_sz = backups[0]
        sz_mb = latest_sz / (1024 * 1024)
        # 弹模态框让用户选择
        if messagebox.askyesno(
            '发现库备份',
            f'未找到当前库文件（index.db），但 backup 目录有 '
            f'{len(backups)} 份备份。\n\n'
            f'最新备份：\n  {latest_name}\n  大小：{sz_mb:.1f} MB\n\n'
            f'是否将最新备份恢复为当前库？\n'
            f'（选"否"则创建空库，可稍后用 ⚙ → 打开数据库文件位置 手动指定）',
            parent=self.root):
            try:
                import shutil
                shutil.copy2(latest_path, DB_PATH)
                self._show_toast(
                    f'已从备份恢复：{latest_name}', 'ok')
                # 触发后续初始化流程
                self.root.after(500, self._check_empty_library)
                self.root.after(800, self._auto_refresh_index)
            except Exception as e:
                messagebox.showerror(
                    '恢复失败',
                    f'恢复备份时出错：{e}\n\n'
                    f'可手动用 ⚙ → 打开数据库文件位置 指定 {latest_path}',
                    parent=self.root)
        else:
            self._show_toast('已创建空库，可用 ⚙ → 打开数据库文件位置 指定其他位置', 'idle')


    def _auto_refresh_index(self):
        """启动时后台增量更新索引：mtime/size 变化的重索引，消失的移出"""
        if not os.path.exists(DB_PATH):
            return
        # 上次退出时自动备份失败 → 启动告警一次（读完即清除）
        if self.cfg.get('backup_failed'):
            def _warn_backup():
                msg = self.cfg.pop('backup_failed', '')
                self._save_config()
                self._show_toast(
                    f'上次自动备份失败：{msg}（请检查磁盘空间）', 'error', 6000)
            self.root.after(0, _warn_backup)
        try:
            # 存在失联文件时先打快照再自检（refresh 内有双启动确认与
            # 未挂载保护，快照是最后一道保险）
            if find_missing_documents(DB_PATH):
                snapshot_db(DB_PATH, BACKUP_DIR, 'pre_refresh')
            r = refresh_index(DB_PATH)
        except Exception:
            return
        suspected = r.get('suspected_offline', 0)
        if suspected:
            def _warn_offline():
                self.status.configure(
                    text=f'⚠ 检测到 {suspected} 个文件暂时无法访问，'
                         '疑似存储未挂载，已保留全部索引未做清理'
                         + (f'（已重索引 {r["updated"]} 个）'
                            if r['updated'] else ''),
                    foreground='#c0392b')
                self.root.after(15000, lambda: self.status.configure(
                    text='就绪 · 拖拽文件到窗口直接入库', foreground=''))
            self.root.after(0, _warn_offline)
        else:
            n = r['updated'] + r['removed']
            if n:
                def _done():
                    self.status.configure(text=
                        f'索引已自动更新：重索引 {r["updated"]} 个'
                        + (f'，移除失效 {r["removed"]} 个' if r['removed'] else ''))
                self.root.after(0, _done)
        # bigram 双字索引回填（旧库首次升级时后台跑一次，完成后 2 字词提速）
        self._ensure_bigram_bg()

    def _ensure_bigram_bg(self):
        """bigram 未就绪时后台回填；状态栏显示进度，完成提示一次"""
        if not os.path.exists(DB_PATH):
            return
        try:
            if bigram_ready(DB_PATH):
                return
        except Exception:
            return

        def _prog(done, total):
            if done % max(1, total // 20) == 0 or done == total:
                pct = done * 100 // max(1, total)
                def _u():
                    self.status.configure(
                        text=f'正在优化两字词搜索索引… {pct}%（期间一切功能正常）')
                self.root.after(0, _u)

        def work():
            try:
                r = rebuild_bigram(DB_PATH, progress=_prog)
            except Exception:
                return
            if not r.get('stopped'):
                def _fin():
                    self.status.configure(
                        text=f'两字词索引已就绪（用时{r["seconds"]:.0f}秒），'
                             '搜索「安全」等两字词提速约10倍')
                    self.root.after(6000, lambda: (
                        self.status.configure(text='就绪 · 拖拽文件到窗口直接入库')
                        if self.status.cget('text').startswith('两字词索引')
                        else None))
                self.root.after(0, _fin)
        threading.Thread(target=work, daemon=True).start()

    # -------- 批2：全局热键 / 迷你窗 / 剪贴板快存 / 远程唤起 --------




    def show_hotkey_settings(self):
        """⚙ 菜单：修改两个全局热键"""
        win = self._get_win('hotkey')
        if win is None:
            return
        win.title('热键设置')
        self._center_win(win, 440, 280)
        win.transient(self.root)
        frm = ttk.Frame(win, padding=16)
        frm.pack(fill='both', expand=True)
        ttk.Label(frm, text='格式：ctrl+alt+j（至少一个修饰键 + 字母/数字/F1-F12）'
                  ).grid(row=0, column=0, columnspan=2, sticky='w', pady=(0, 10))
        ttk.Label(frm, text='迷你速查窗：').grid(row=1, column=0, sticky='e', pady=6)
        e1 = ttk.Entry(frm, width=22)
        e1.insert(0, self.cfg.get('quick_hotkey', 'ctrl+alt+j'))
        e1.grid(row=1, column=1, sticky='w', pady=6)
        ttk.Label(frm, text='剪贴板快存：').grid(row=2, column=0, sticky='e', pady=6)
        e2 = ttk.Entry(frm, width=22)
        e2.insert(0, self.cfg.get('clip_hotkey', 'ctrl+alt+m'))
        e2.grid(row=2, column=1, sticky='w', pady=6)

        def _save():
            v1, v2 = e1.get().strip().lower(), e2.get().strip().lower()
            if not _HOTKEY_OK:
                messagebox.showinfo('提示', '当前环境不支持全局热键（缺 pywin32）',
                                    parent=win)
                return
            try:
                quickwin.parse_hotkey(v1)
                quickwin.parse_hotkey(v2)
            except ValueError as ex:
                messagebox.showwarning('热键格式错误', str(ex), parent=win)
                return
            if v1 == v2:
                messagebox.showwarning('热键冲突', '两个热键不能相同', parent=win)
                return
            self.cfg['quick_hotkey'] = v1
            self.cfg['clip_hotkey'] = v2
            save_config(self.cfg)
            self._restart_hotkeys()
            win.destroy()
            self._toast(f'热键已更新：{v1} / {v2}')

        btns = ttk.Frame(frm)
        btns.grid(row=3, column=0, columnspan=2, pady=(14, 0))
        ttk.Button(btns, text='保存', style='Accent.TButton', command=_save).pack(side='left', padx=6)
        ttk.Button(btns, text='取消', command=win.destroy).pack(side='left')


    # -------- 默认存档位置 --------


    # -------- 库文件定位（搬动 index.db 后让软件找回） --------
    def _locate_db_file(self):
        """⚙ 菜单：手动指定 index.db 位置（搬动后下次启动自动用此路径）"""
        from tkinter import filedialog as _fd
        init_dir = os.path.dirname(DB_PATH) if os.path.exists(DB_PATH) \
            else APP_DIR
        p = _fd.askopenfilename(
            title='选择 index.db 文件',
            initialdir=init_dir,
            filetypes=[('SQLite 数据库', '*.db'), ('所有文件', '*.*')])
        if not p:
            return
        # 检查文件大小（避免误选空库）
        try:
            sz = os.path.getsize(p)
        except OSError:
            sz = 0
        if sz < _DB_MIN_SIZE:
            if not messagebox.askyesno(
                    '提示',
                    f'所选文件仅 {sz} 字节，可能不是有效库。\n'
                    f'仍要绑定此路径吗？',
                    parent=self.root):
                return
        # 写入 config，下次启动自动定位到此
        self.cfg['last_db_path'] = p
        self._save_config()
        # 若与当前 DB_PATH 同位置 → 无需重启
        if os.path.normcase(os.path.abspath(p)) == \
                os.path.normcase(os.path.abspath(DB_PATH)):
            self._toast(f'库文件就在此处，无需重启：{p}')
        else:
            messagebox.showinfo(
                '已绑定库路径',
                f'已绑定：{p}\n\n请重启软件使新路径生效。',
                parent=self.root)

    # -------- 意见反馈（WPS 在线表格） --------

    # -------- 开机自启动（HKCU Run 键，无需管理员） --------
    RUN_KEY = r'Software\Microsoft\Windows\CurrentVersion\Run'
    RUN_NAME = '镜仔搜索'

    def _get_autostart(self):
        if not sys.platform.startswith('win'):
            return False
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self.RUN_KEY, 0,
                                winreg.KEY_READ) as k:
                winreg.QueryValueEx(k, self.RUN_NAME)
            return True
        except OSError:
            return False

    def _toggle_autostart(self):
        if not sys.platform.startswith('win'):
            messagebox.showinfo('提示', '开机自启动仅支持 Windows')
            self.autostart_var.set(False)
            return
        if not getattr(sys, 'frozen', False):
            messagebox.showinfo(
                '提示', '源码运行时自启动会指向 python.exe，'
                        '请打包成 exe 后再开启。')
            self.autostart_var.set(False)
            return
        import winreg
        try:
            if self.autostart_var.get():
                with winreg.CreateKey(winreg.HKEY_CURRENT_USER,
                                      self.RUN_KEY) as k:
                    winreg.SetValueEx(k, self.RUN_NAME, 0, winreg.REG_SZ,
                                      f'"{sys.executable}"')
                self._toast('已开启：开机自动启动镜仔搜索')
            else:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self.RUN_KEY,
                                    0, winreg.KEY_SET_VALUE) as k:
                    winreg.DeleteValue(k, self.RUN_NAME)
                self._toast('已关闭开机自启动')
        except OSError as ex:
            self.autostart_var.set(self._get_autostart())
            messagebox.showerror('设置失败', str(ex))





    def show_ocr_status(self):
        """⚙ 菜单：显示 RapidOCR/onnxruntime/模型/Tesseract 回退状态"""
        self.status.configure(text='正在检测 OCR 组件…')
        self.root.update_idletasks()

        def _work():
            try:
                from parsers import ocr_status
                st = ocr_status()
            except Exception as e:
                st = None
                err = str(e)
            self.root.after(0, lambda: self._show_ocr_status_dlg(
                st, err if st is None else None))

        threading.Thread(target=_work, daemon=True).start()


    def show_ref_graph(self):
        """T12：生成单文件离线引用关系图并用默认浏览器打开"""
        if not _GRAPH_OK or not os.path.exists(DB_PATH):
            self._toast('索引库不存在，无法生成图谱')
            return
        path = filedialog.asksaveasfilename(
            title='保存关系图谱', defaultextension='.html',
            initialdir=self._export_dir(), initialfile='引用关系图.html',
            filetypes=[('网页文件', '*.html')])
        if not path:
            return
        self._toast('正在统计引用关系并布局…')

        def _work():
            try:
                html, stats = graph_html.generate_graph(DB_PATH)
                with open(path, 'w', encoding='utf-8') as f:
                    f.write(html)
            except Exception as e:
                self.root.after(0, lambda: messagebox.showerror(
                    '生成失败', f'关系图谱生成失败：{e}'))
                return
            self.root.after(0, lambda: self._graph_done(path, stats))

        threading.Thread(target=_work, daemon=True).start()






    def quick_save_clipboard(self):
        """Ctrl+Alt+M / 迷你窗按钮：剪贴板文本 → library/剪藏/ → 原位建索引"""
        if not os.path.exists(DB_PATH):
            self._toast('索引库不存在，请先扫描建库')
            return
        if quickwin is not None and quickwin.clipboard_has_files():
            self._toast('剪贴板里是文件不是文本，请在文档里复制文字后再快存')
            return
        try:
            text = self.root.clipboard_get()
        except tk.TclError:
            self._toast('剪贴板里没有可快存的文本')
            return
        text = (text or '').strip()
        if not text:
            self._toast('剪贴板是空的，没有可保存的内容')
            return
        text = text[:50000]
        src_title = ''
        if quickwin is not None:
            try:
                _, src_title = quickwin.foreground_title()
            except Exception:
                src_title = ''
        ts = time.strftime('%Y-%m-%d %H:%M:%S')
        folder = os.path.join(LIBRARY_DIR, OTHER_CATEGORY)
        try:
            os.makedirs(folder, exist_ok=True)
        except Exception as e:
            self._toast(f'快存失败：无法创建剪藏目录（{e}）')
            return
        fn = os.path.join(folder, f'剪藏_{time.strftime("%Y%m%d_%H%M%S")}.txt')
        body = (f'剪存时间：{ts}\n来源窗口：{src_title or "未知"}\n'
                + '-' * 40 + f'\n{text}\n')
        try:
            with open(fn, 'w', encoding='utf-8') as f:
                f.write(body)
        except Exception as e:
            self._toast(f'快存失败：{e}')
            return
        self.status.configure(text='正在快存剪贴板并建索引…')

        def _work():
            try:
                status = index_single_file(fn, DB_PATH)
                ok = status in ('ok', 'skip')
                msg = ('已快存到「剪藏」，现在就能搜到'
                       if ok else f'快存失败：{status}')
            except Exception as e:
                msg = f'快存失败：{e}'
            try:
                self.root.after(0, lambda: self._toast(msg))
            except RuntimeError:
                pass   # 窗口正关闭，回调无意义

        threading.Thread(target=_work, daemon=True).start()

    # -------- 动效辅助（uiverse 风格 hover 渐变/按压过渡；全部静默降级）--------




    # -------- AI 入口按钮慢速呼吸 --------

    def _qa_breathe_start(self):
        if anim is None or not self._anim_ok():
            return
        base = self._qa_btn_colors()[0]
        # 与白色混合变亮一档；深色模式下同样是提亮
        peak = anim.hex_lerp(base, '#FFFFFF', 0.25)
        b = getattr(self, '_qa_breathe', None)
        if b is None:
            b = anim.Breathe(self.qa_btn)
            self._qa_breathe = b
        b.start(lambda _t, col: (not self._qa_hovered
                                  and self._qa_safe_bg(col)),
                base, peak, half_period_ms=1300)






    def _bind_btn_anim(self, btn, kind, icon=False):
        """给 ttk.Button 挂 hover 背景渐变 + 按压过渡。
        克隆独立样式避免互相干扰；hover 颜色用补间驱动而非 ttk 瞬时 map。
        icon=True 时克隆样式沿用 MDL2 图标字体（否则中文按钮字体会顶掉字形）"""
        if anim is None:
            return
        try:
            pal = self._anim_palette(kind)
            st = ttk.Style()
            self._anim_seq = getattr(self, '_anim_seq', 0) + 1
            uniq = f'HA{self._anim_seq}.TButton'
            btn_font = pal['font']
            if icon:
                btn_font = (self.FONT_ICON_B if kind == 'accent'
                            else self.FONT_ICON)
            opts = dict(font=btn_font, padding=pal['padding'],
                        background=pal['bg'], bordercolor=pal['bg'],
                        focusthickness=0)
            if pal.get('fg'):
                opts['foreground'] = pal['fg']
            st.configure(uniq, **opts)
            # sv_ttk 的 Accent/Link 等有专属元素布局（AccentButton.button），
            # 克隆样式默认回退 Button.button，浅色主题下 accent 按钮会变
            # 白底白字看不见；把源样式布局原样拷过来（hover 补间照常生效）。
            # 注意要走底层 Tcl 字符串透传：st.layout() 返回的 Python 结构
            # 再传回去会因 dict/str 转换变成非法 Tcl。
            src = ('Icon.' if icon else '') + {
                'accent': 'Accent.TButton', 'link': 'Link.TButton',
                'danger': 'Danger.TButton'}.get(kind, 'TButton')
            lay = self.root.tk.call('ttk::style', 'layout', src)
            if lay:
                self.root.tk.call('ttk::style', 'layout', uniq, lay)
            # 只保留 disabled 的 map；active/pressed 由补间接管
            st.map(uniq, foreground=[('disabled', '#888888')])
            btn.configure(style=uniq)
            info = {'btn': btn, 'kind': kind, 'style': uniq,
                    'font': btn_font, 'layout_src': src,
                    'hover': False, 'press': False,
                    'tw': anim.Tweener(self.root)}
            self._anim_btns.append(info)
            btn.bind('<Enter>', lambda e, i=info: self._btn_hover(i, True),
                     add='+')
            btn.bind('<Leave>', lambda e, i=info: self._btn_hover(i, False),
                     add='+')
            btn.bind('<ButtonPress-1>',
                     lambda e, i=info: self._btn_press(i, True), add='+')
            btn.bind('<ButtonRelease-1>',
                     lambda e, i=info: self._btn_press(i, False), add='+')
        except Exception:
            pass


    def _btn_to(self, info, target, ms):
        """把按钮背景从当前色补间到目标色"""
        pal = self._anim_palette(info['kind'])
        if not self._anim_ok():
            self._btn_set_bg(info, target if target else pal['bg'])
            return
        cur = self._btn_cur_bg(info)
        if cur == target:
            return
        info['tw'].tween_color(
            cur, target, ms,
            lambda _t, c: self._btn_set_bg(info, c))





    def _bind_all_button_anims(self):
        """给主窗口内所有 ttk.Button 挂渐变（对话框 Toplevel 除外）"""
        self._anim_btns = getattr(self, '_anim_btns', [])
        bound = {id(i['btn']) for i in self._anim_btns}
        def _walk(w):
            for ch in w.winfo_children():
                try:
                    if (ch.winfo_class() == 'TButton'
                            and ch.winfo_toplevel() == self.root
                            and id(ch) not in bound
                            and not (ch.cget('style') or '').startswith(
                                'AppBar.')):
                        # AppBar.* 扁平按钮的 hover/press 由 ttk map 原生
                        # 接管，不挂渐变（挂了会被克隆成 sv_ttk 精灵图）
                        style = ch.cget('style') or 'TButton'
                        icon = style.startswith('Icon.')
                        base = style[5:] if icon else style
                        kind = {'Accent.TButton': 'accent',
                                'Link.TButton': 'link',
                                'Danger.TButton': 'danger'}.get(
                                    base, 'default')
                        self._bind_btn_anim(ch, kind, icon=icon)
                        bound.add(id(ch))
                except Exception:
                    pass
                _walk(ch)
        _walk(self.root)


    def _bind_tk_hover(self, btn, get_colors):
        """给 tk.Button 挂 hover 背景渐变；get_colors() -> (常态, hover)。
        颜色在事件触发时实时读取，主题切换无需重绑。"""
        if anim is None:
            return
        if not hasattr(self, '_tk_hover_tws'):
            self._tk_hover_tws = {}
        tw = anim.Tweener(self.root)
        self._tk_hover_tws[id(btn)] = tw
        def _enter(_e):
            if not self._anim_ok():
                try:
                    btn.configure(bg=get_colors()[1])
                except Exception:
                    pass
                return
            try:
                cur = btn.cget('bg')
                tw.tween_color(cur, get_colors()[1], 140,
                               lambda _t, c: btn.configure(bg=c))
            except Exception:
                pass
        def _leave(_e):
            if not self._anim_ok():
                try:
                    btn.configure(bg=get_colors()[0])
                except Exception:
                    pass
                return
            try:
                cur = btn.cget('bg')
                tw.tween_color(cur, get_colors()[0], 140,
                               lambda _t, c: btn.configure(bg=c))
            except Exception:
                pass
        btn.bind('<Enter>', _enter, add='+')
        btn.bind('<Leave>', _leave, add='+')

    def _set_progress(self, value, immediate=False):
        """进度条平滑补间到目标值；immediate=True 或减弱动效时直跳"""
        try:
            target = max(0, min(100, float(value)))
            if immediate or not self._anim_ok():
                self.progress.configure(value=target)
                return
            cur = float(self.progress['value'])
            if abs(target - cur) < 0.5:
                self.progress.configure(value=target)
                return
            tw = getattr(self, '_progress_tw', None)
            if tw is None:
                tw = anim.Tweener(self.root)
                self._progress_tw = tw
            tw.tween(cur, target, 200,
                     lambda _t, v: self.progress.configure(value=v))
        except Exception:
            try:
                self.progress.configure(value=value)
            except Exception:
                pass


    def _toggle_dark_animated(self):
        """深色模式切换：整窗轻微压暗再恢复，弱化硬闪"""
        if self._anim_ok():
            try:
                self.root.attributes('-alpha', 0.94)
                tw = anim.Tweener(self.root)
                tw.tween(0.94, 1.0, 180,
                         lambda _t, v: self.root.attributes('-alpha', v))
            except Exception:
                pass
        self._apply_ui_theme()
        # 标题栏 + 原生菜单跟随深色
        if winchrome is not None:
            winchrome.apply_window(self.root, self.dark.get())
            _rm = getattr(winchrome.install, 'refresh_all_menus', None)
            if _rm is not None:
                _rm()
            else:
                winchrome.dark_owned_menus(self.dark.get())



    # -------- 输入框 focus 描边渐变 --------

    def _bind_input_focus_anim(self):
        """克隆出不带 focus map 的输入框样式，描边色由补间接管。"""
        if anim is None:
            return
        self._input_focus = []
        specs = ((self.kw_entry, 'FocusSearch.TCombobox',
                 dict(padding=(30, 1), borderradius=8)),
                 (self.fe, 'Filter.TEntry', dict(borderradius=6)))
        for w, sname, extra in specs:
            try:
                w.configure(style=sname)
            except Exception:
                continue
            info = {'w': w, 'style': sname, 'extra': extra,
                    'tw': anim.Tweener(self.root), 'focused': False}
            self._input_focus.append(info)
            w.bind('<FocusIn>',
                   lambda _e, i=info: self._input_focus_to(i, True), add='+')
            w.bind('<FocusOut>',
                   lambda _e, i=info: self._input_focus_to(i, False), add='+')
        self._refresh_input_focus_styles()


    def _input_focus_to(self, info, on):
        info['focused'] = on
        if not self._anim_ok():
            ttk.Style().configure(
                info['style'],
                bordercolor=info['brand'] if on else info['normal'])
            return
        try:
            cur = ttk.Style().lookup(info['style'], 'bordercolor') \
                or info['normal']
        except Exception:
            cur = info['normal']
        target = info['brand'] if on else info['normal']
        info['tw'].tween_color(
            cur, target, 150,
            lambda _t, c: ttk.Style().configure(info['style'],
                                                bordercolor=c))

    # -------- 结果树行 hover 高亮 --------




    # -------- 分栏线 hover --------

    def _bind_sash_hover(self):
        """鼠标移到分栏拖拽条上：发丝线变品牌蓝（ttk + 经典 tk 两个 pane）"""
        self._sash_active = False
        # —— 光标继承隔离（必须的，不是保险）——
        # Tk 的 -cursor 是继承的：分栏条上设的双箭头会传给全部子控件；而且
        # 指针滑进子控件那一瞬间 Tk 就按继承解析过一次光标，事后再把 pane
        # 光标复位并不会重设 → 双箭头黏在结果树/章节目录/正文里（用户实测）。
        # 给两个 pane 的直接子控件钉死 arrow，继承链到此终止；子控件内部
        # 自带光标的（Entry=ibeam、预览 Text=xterm、按钮=hand2）不受影响。
        for _p in (self.main_pane, self.combined_pane):
            for _ch in _p.winfo_children():
                try:
                    _ch.configure(cursor='arrow')
                except tk.TclError:
                    pass
        # 颜色必须**每次现读** self._sash_normal/_sash_hover：原来在绑定这一刻
        # 抓成闭包局部量，浅色下绑定后切深色，鼠标一进一出分栏就被刷回浅色
        # （深色模式漏出「白框」的根因：实测 hover 变 #2F5E8C、移开变 #DFE4EA）
        def _normal():
            return getattr(self, '_sash_normal', '#E3E5E8')

        def _hover():
            return getattr(self, '_sash_hover', '#0F6CBD')

        def _on_ttk(e):
            hit = False
            try:
                # tk 8.6 的 Python 层 PanedWindow.identify() 有 bug（命中也返回 ''），
                # 直接走底层 Tcl：命中 sash 时返回 '0' 这类非空索引串
                p = self.main_pane
                hit = bool(str(p.tk.call(str(p), 'identify', e.x, e.y)))
            except Exception:
                hit = False
            if hit and not self._sash_active:
                self._sash_active = True
                self.main_pane.configure(cursor='sb_h_double_arrow')
                ttk.Style().configure('TPanedwindow', background=_hover())
            elif not hit and self._sash_active:
                self._sash_active = False
                self.main_pane.configure(cursor='')
                ttk.Style().configure('TPanedwindow', background=_normal())

        def _on_tk(e):
            hit = False
            try:
                hit = self.combined_pane.identify(e.x, e.y) != ''
            except Exception:
                hit = False
            if hit:
                self.combined_pane.configure(cursor='sb_v_double_arrow',
                                             bg=_hover())
            else:
                self.combined_pane.configure(
                    cursor='', bg=(_normal()
                                   if not self._sash_active else _hover()))

        # 双箭头光标只该出现在分栏条上：Tk 的子控件会继承父窗口光标，
        # 一旦离开分栏条不复位，正文/结果树就会一直是双箭头（用户实测反馈）
        def _leave_ttk(_e=None):
            if self._sash_active:
                self._sash_active = False
                ttk.Style().configure('TPanedwindow', background=_normal())
            try:
                self.main_pane.configure(cursor='')
            except tk.TclError:
                pass

        def _leave_tk(_e=None):
            try:
                self.combined_pane.configure(cursor='', bg=_normal())
            except tk.TclError:
                pass

        self.main_pane.bind('<Motion>', _on_ttk, add='+')
        self.main_pane.bind('<Leave>', _leave_ttk, add='+')
        self.combined_pane.bind('<Motion>', _on_tk, add='+')
        self.combined_pane.bind('<Leave>', _leave_tk, add='+')

    def _toast(self, msg):
        """右下角轻提示（不抢焦点，2.2 秒自动消失）；淡入入场、淡出消失"""
        try:
            t = tk.Toplevel(self.root)
            t.overrideredirect(True)
            t.attributes('-topmost', True)
            lbl = tk.Label(t, text=msg, bg='#333333', fg='#ffffff',
                           padx=14, pady=9, wraplength=380, justify='left',
                           font=('Microsoft YaHei UI', 10))
            lbl.pack()
            self.root.update_idletasks()
            w, h = t.winfo_reqwidth(), t.winfo_reqheight()
            sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
            t.geometry(f'{w}x{h}+{sw - w - 24}+{sh - h - 72}')
            if self._anim_ok():
                t.attributes('-alpha', 0.0)
                anim.fade_in(t, 150)
                def _close():
                    if getattr(t, '_closing', False):
                        return
                    t._closing = True
                    anim.fade_out(t, 180, on_done=lambda: t.destroy())
                t.after(2200, _close)
            else:
                t.after(2200, t.destroy)
        except Exception:
            try:
                self.status.configure(text=msg)
            except Exception:
                pass

    # -------- UI 构建 --------
    # 品牌色（档案蓝·官档配色），亮/暗两套；主按钮、聚焦框、选中态统一用它
    _ACCENT = '#2F5E8C'


    def _configure_control_styles(self):
        """控件样式体系：①Accent 主按钮 ②默认次按钮 ③Link 文字按钮
        ④Danger 危险按钮；统一高度/间距与品牌色。主题切换后需重调（sv_ttk
        set_theme 会重置自定义样式）。"""
        st = ttk.Style()
        dark = self.dark.get()
        st.configure('.', font=self.FONT_UI)
        # 覆盖 sv_ttk 默认底色为冷灰纸调，统一所有窗口背景
        if not dark:
            st.configure('TFrame', background='#F4F6F9')
            st.configure('TLabel', background='#F4F6F9')
        frame_bg = str(st.lookup('TFrame', 'background') or
                       ('#1c1c1c' if dark else '#F4F6F9'))
        # 根窗口底色**两种主题都要刷**：原来只在浅色分支刷，浅转深后根窗仍是
        # 旧色，分栏/内边距的 1~2px 缝隙就漏出一圈白框（用户截图就是这个）。
        # 取 TFrame 同色，缝隙与四周 frame 完全一致，视觉上隐形。
        try:
            self.root.configure(bg=frame_bg)
        except Exception:
            pass
        # 分栏线颜色提到函数开头：后面任何一步异常都不会残留上一主题的旧色
        self._sash_normal = '#3A3B3F' if dark else '#DFE4EA'
        self._sash_hover = '#0F6CBD' if dark else '#2F5E8C'

        # —— 圆角按钮元素：Tk 无原生圆角，用 PIL 生成整块圆角位图当 ttk image
        # element。每套元素按「角色+主题」只建一次并常驻（同名 element_create
        # 会 TclError），切换主题时改用另一套元素名，布局重挂即完成换色。
        self._round_els = getattr(self, '_round_els', {})
        self._round_imgs = getattr(self, '_round_imgs', [])
        # 统一按钮高 32px。9 宫格的 border 必须 ≤ 圆角半径，否则弧线会被
        # 「上/下拉伸条」切成直角；同时 2*border+文字行高(17) 必须 ≤ 32，
        # 否则中文按钮会顶到 33+。取 7/7 同时满足两条（实测三字体一律 32）。
        _RH, _RR, _RB = 32, 7, 7

        def _rnd(role, rest, hover, press, dis=None):
            """建/复用一套圆角元素，返回元素基名；PIL 缺失时返回 None"""
            base = f'Rnd{role}{"D" if dark else "L"}'
            if base in self._round_els:
                return base
            w = _RB * 2 + 4
            names = {}
            for key, col in (('rest', rest), ('dis', dis or rest),
                             ('press', press), ('hover', hover)):
                # corner=frame_bg：把所在栏的底色画进四角（ttk 会在位图下
                # 先刷样式 background，透明角只会露出那层底色 → 直角实心块）
                ph = _rounded_photo(w, _RH, _RR, col, corner=frame_bg)
                if ph is None:
                    return None
                self._round_imgs.append(ph)   # 元素持有图片名，靠这里保活
                names[key] = str(ph)
            imglist = (f'{names["rest"]} disabled {names["dis"]} '
                       f'pressed {names["press"]} active {names["hover"]}')
            try:
                st.element_create(base + '.button', 'image', imglist,
                                  border=_RB, sticky='nsew')
            except tk.TclError:
                pass
            self._round_els[base] = True
            return base

        def _rnd_layout(base, pad_el='Button'):
            return [(base + '.button', {
                'sticky': 'nswe', 'children': [
                    (f'{pad_el}.padding', {
                        'sticky': 'nswe', 'children': [
                            (f'{pad_el}.label', {'sticky': ''})]})]})]

        # —— 统计徽章标签：暖灰底胶囊，包裹命中数字 ——
        _stats_bg = '#3A3B3F' if dark else '#E9EDF2'
        _stats_fg = '#F5F6F7' if dark else '#244C78'
        st.configure('Stats.TLabel', background=_stats_bg,
                     foreground=_stats_fg, padding=(10, 4))

        # —— ② 默认次按钮：自建圆角元素，与主按钮/Link 同一套外观 ——
        # 这是所有 HA* 动画克隆的布局源，所以改这里＝全窗默认按钮统一 32px。
        # 竖向 padding 必须给 0：位图已定高 32，再加 pady 只会撑高。
        _def_bg = str(st.lookup('TButton', 'background') or frame_bg)
        _dmix = '#FFFFFF' if dark else '#000000'
        _def_el = _rnd('Default', _def_bg,
                       anim.hex_lerp(_def_bg, _dmix, 0.10) if anim else _def_bg,
                       anim.hex_lerp(_def_bg, _dmix, 0.18) if anim else _def_bg)
        if _def_el:
            st.layout('TButton', _rnd_layout(_def_el))
        st.configure('TButton', font=self.FONT_UI, padding=(12, 0),
                     focusthickness=0)

        # —— ① 主按钮（浅色档案蓝 / 深色蓝）：实心白字 + 圆角 ——
        # sv_ttk 的 AccentButton.button 是固定蓝色精灵，background 存了也不渲染；
        # 改用自建圆角位图元素（_rnd），颜色/hover/按下/禁用四态都由图片给出。
        if dark:
            acc, acc_hover, acc_press, acc_fg, acc_dis, acc_dis_fg = (
                '#2889DE', '#3D9AE8', '#1E6FBE', '#FFFFFF',
                '#33373D', '#888888')
        else:
            acc, acc_hover, acc_press, acc_fg, acc_dis, acc_dis_fg = (
                '#2F5E8C', '#3A6FA2', '#274D74', '#FFFFFF',
                '#D7DEE6', '#8A93A0')
        _acc_el = _rnd('Accent', acc, acc_hover, acc_press, acc_dis)
        for _acc_style, _acc_font in (
                ('Accent.TButton', self.FONT_UI_B),
                ('Icon.Accent.TButton', self.FONT_ICON_B)):
            if _acc_el:
                st.layout(_acc_style, _rnd_layout(_acc_el))
            st.configure(_acc_style, font=_acc_font,
                         background=acc, foreground=acc_fg,
                         bordercolor=acc, lightcolor=acc, darkcolor=acc,
                         relief='flat', padding=(12, 0), focusthickness=0)
            st.map(_acc_style,
                   background=[('disabled', acc_dis),
                               ('active', acc_hover),
                               ('pressed', acc_press)],
                   bordercolor=[('disabled', acc_dis),
                                ('active', acc_hover),
                                ('pressed', acc_press)],
                   foreground=[('disabled', acc_dis_fg)])

        # —— ④ 危险按钮：红字 + hover 浅红底（自带圆角元素，不继承 TButton 的灰底）——
        if dark:
            _dgr_fg, _dgr_hover_fg = '#FF99A4', '#FFB3BB'
            _dgr_hover, _dgr_press = '#4A2726', '#5A2E2D'
        else:
            _dgr_fg, _dgr_hover_fg = '#C13A28', '#A61F10'
            _dgr_hover, _dgr_press = '#FBE9E5', '#F6CECA'
        _dgr_el = _rnd('Danger', frame_bg, _dgr_hover, _dgr_press)
        if _dgr_el:
            st.layout('Danger.TButton', _rnd_layout(_dgr_el))
        st.configure('Danger.TButton', font=self.FONT_UI,
                     foreground=_dgr_fg, padding=(12, 0), focusthickness=0,
                     background=frame_bg, bordercolor=frame_bg)
        st.map('Danger.TButton', foreground=[('active', _dgr_hover_fg)])

        # —— ③ 文字按钮：底色与窗口同（视觉隐形），hover 浮出浅灰圆角胶囊 ——
        _lnk_el = _rnd('Ghost', frame_bg,
                       '#3A3A3C' if dark else '#E9EDF2',
                       '#45464A' if dark else '#DFE4EA')
        if _lnk_el:
            st.layout('Link.TButton', _rnd_layout(_lnk_el))
        st.configure('Link.TButton', font=self.FONT_UI,
                     background=frame_bg, bordercolor=frame_bg,
                     padding=(12, 0), focusthickness=0)
        st.map('Link.TButton',
               background=[('active', '#3A3A3C' if dark else '#E9EDF2'),
                           ('pressed', '#45464A' if dark else '#DFE4EA')])

        # —— Fluent 图标按钮：仅字体覆盖，颜色沿 TButton/Accent 层级继承 ——
        # 命名 Icon.X.TButton 让默认值沿 Icon.X.TButton→X.TButton→TButton 回退
        st.configure('Icon.TButton', font=self.FONT_ICON, padding=(12, 0),
                     focusthickness=0)
        st.configure('Icon.Accent.TButton', font=self.FONT_ICON_B,
                     padding=(12, 0), focusthickness=0)
        # 齿轮菜单按钮：常态与栏同底（隐形），hover 同样浮圆角胶囊
        if _lnk_el:
            st.layout('Icon.TMenubutton', _rnd_layout(_lnk_el, 'Menubutton'))
        st.configure('Icon.TMenubutton', font=self.FONT_ICON, padding=(12, 0))

        # —— Fluent AppBar 按钮：与栏同底无描边，hover 浮圆角胶囊 ——
        # 常态底色直接取所在栏底色 frame_bg（不是 PALETTE.bg）：深色下两者
        # 不同（#1c1c1c vs #202020），用 PALETTE.bg 会在栏上浮出一层灰块。
        _pal = self.PALETTE_DARK if dark else self.PALETTE_LIGHT
        ab_bg = _pal['bg']
        ab_hover = _pal['ab_hover']
        ab_press = _pal['ab_press']
        _bar_el = _rnd('Bar', frame_bg, ab_hover, ab_press)
        if _bar_el:
            st.layout('AppBar.Icon.TButton', _rnd_layout(_bar_el))
        st.configure('AppBar.Icon.TButton', font=self.FONT_ICON,
                     background=ab_bg, bordercolor=ab_bg,
                     lightcolor=ab_bg, darkcolor=ab_bg,
                     relief='flat', padding=(12, 0), focusthickness=0)
        st.map('AppBar.Icon.TButton',
               background=[('active', ab_hover), ('pressed', ab_press)],
               bordercolor=[('active', ab_hover), ('pressed', ab_press)])

        # —— Treeview：行高 32 + 品牌金选中态 ——
        st.configure('Treeview', rowheight=32, font=self.FONT_UI)
        st.configure('Treeview.Heading', font=self.FONT_UI_B)
        if dark:
            st.map('Treeview',
                   background=[('selected', '#0F6CBD')],
                   foreground=[('selected', '#FFFFFF')])
        else:
            # 底色跟随纸白卡片（sv_ttk 默认 #fafafa 偏冷灰，与纸调页面同系）
            st.configure('Treeview', background='#FCFDFE',
                         fieldbackground='#FCFDFE')
            st.map('Treeview',
                   background=[('selected', '#D6E3F0')],
                   foreground=[('selected', '#1B2632')])

        # —— 细滚动条：宽 6px 无箭头感，槽色与底同（Fluent 细条）——
        sb_trough = '#202020' if dark else '#F4F6F9'
        sb_thumb = '#5A5C61' if dark else '#C3CAD3'
        try:
            st.configure('Vertical.TScrollbar', width=6,
                         troughcolor=sb_trough, bordercolor=sb_trough,
                         background=sb_thumb, arrowsize=7, relief='flat')
            st.configure('Horizontal.TScrollbar', height=6,
                         troughcolor=sb_trough, bordercolor=sb_trough,
                         background=sb_thumb, arrowsize=7, relief='flat')
            st.map('Vertical.TScrollbar',
                   background=[('active', '#7A7D83' if dark
                                else '#A2A6AC')])
            st.map('Horizontal.TScrollbar',
                   background=[('active', '#7A7D83' if dark
                                else '#A2A6AC')])
        except tk.TclError:
            pass

        # —— 品牌进度条：圆角细条（6px），槽色浅灰 ——
        pb_trough = '#2D2E32' if dark else '#E9EDF2'
        try:
            st.configure('Brand.Horizontal.TProgressbar',
                         thickness=6, troughcolor=pb_trough,
                         background=acc, bordercolor=pb_trough,
                         lightcolor=acc, darkcolor=acc,
                         borderwidth=0)
        except tk.TclError:
            pass

        # —— 分栏线：发丝 2px，常态边框色、hover 品牌金（色值已在函数开头定）——
        try:
            # sashwidth 是 ttk 样式选项（构造器直传会 TclError），只能在此设
            st.configure('TPanedwindow', background=self._sash_normal,
                         sashwidth=4)
        except tk.TclError:
            pass
        # 经典 tk.PanedWindow 不走 ttk 样式，直接刷 bg（sash 与 bg 同色）
        cp = getattr(self, 'combined_pane', None)
        if cp is not None:
            try:
                cp.configure(bg=self._sash_normal)
            except tk.TclError:
                pass

        # —— 搜索框：圆角 + 左侧为放大镜留位 + focus 品牌金描边 ——
        # 竖向 padding 取 1，输入域高度正好 32px，与同一排的圆角按钮严丝合缝
        st.configure('Search.TCombobox', padding=(30, 1))
        if dark:
            st.configure('Search.TCombobox', fieldbackground='#2D2E32',
                         bordercolor='#3A3B40', borderradius=8)
            st.map('Search.TCombobox',
                   fieldbackground=[('focus', '#36373C'),
                                    ('!focus', '#2D2E32')],
                   bordercolor=[('focus', '#4FA3F0')])
        else:
            st.configure('Search.TCombobox', fieldbackground='#FFFFFF',
                         bordercolor='#DFE4EA', borderradius=8)
            st.map('Search.TCombobox',
                   fieldbackground=[('focus', '#FFFFFF'),
                                    ('!focus', '#FFFFFF')],
                   bordercolor=[('focus', '#2F5E8C'),
                                ('!focus', '#DFE4EA')])

        # 放大镜图标底色随主题（嵌在搜索框内，须与输入域同色）
        ic = getattr(self, '_search_icon', None)
        if ic is not None:
            try:
                ic.configure(bg='#2D2E32' if dark else '#FFFFFF')
            except tk.TclError:
                pass
        # 语法 ? 按钮在顶栏上（不再嵌在输入域内），底色跟随顶栏
        sb = getattr(self, '_syntax_btn', None)
        if sb is not None:
            try:
                sb.configure(bg=frame_bg)
            except tk.TclError:
                pass
        # 清空小叉（Canvas 绘制）现在嵌在输入域里，底色要跟输入域内底色一致
        cb = getattr(self, '_clear_kw_btn', None)
        if cb is not None:
            try:
                cb.configure(bg='#2D2E32' if dark else '#FFFFFF')
            except tk.TclError:
                pass
        # 圆角彩色入口（AI 镜仔智答）四角透明区跟随顶栏底色
        _rb = getattr(self, 'qa_btn', None)
        if isinstance(_rb, RoundButton):
            _rb.set_corner_bg(frame_bg)
        # 主题色可能已变 → 重算所有挂接按钮的渐变配色
        self._refresh_btn_anims()

    # —— 无边框融合标题栏（Win32 命中/拖动/缩放见 winchrome.enable_frameless）——
    IC_CAP_MIN = '\ue921'        # Segoe MDL2：最小化
    IC_CAP_MAX = '\ue922'        # 最大化
    IC_CAP_RESTORE = '\ue923'    # 还原
    IC_CAP_CLOSE = '\ue8bb'      # 关闭
    FONT_CAPTION_BTN = ('Segoe MDL2 Assets', 10)
    FONT_CAPTION_TITLE = ('Microsoft YaHei UI', 9)

    def _build_titlebar(self):
        """40px 自绘标题栏：图标+标题在左，最小化/最大化/关闭在右。

        拖动、8 向缩放、双击最大化、Aero Snap、Snap Layouts、按钮点击
        全部由 winchrome 的 WM_NCHITTEST 交系统处理；这里只做视觉，
        通过 <<Wc*>> 虚拟事件响应 hover / 失焦 / 最大化状态。"""
        self._titlebar = None
        if winchrome is None or not self.cfg.get('frameless', True):
            return
        bar = tk.Frame(self.root, height=40, bd=0,
                       highlightthickness=0, relief='flat')
        bar.pack(fill='x', side='top')
        bar.pack_propagate(False)

        left = tk.Frame(bar, bd=0, highlightthickness=0)
        left.pack(side='left', padx=(12, 0))

        icon_lbl = tk.Label(left, bd=0, highlightthickness=0)
        icon_lbl.pack(side='left', padx=(0, 8))
        try:
            from PIL import Image, ImageTk
            png = _resource_path('app_icon.png')
            if os.path.exists(png):
                s = max(self.root.winfo_fpixels('1i') / 96.0, 1.0)
                px = max(int(18 * s + 0.5), 18)
                img = Image.open(png).convert('RGBA').resize(
                    (px, px), Image.LANCZOS)
                self._title_icon = ImageTk.PhotoImage(img, master=self.root)
                icon_lbl.configure(image=self._title_icon)
        except Exception:
            pass

        title_lbl = tk.Label(left, text='镜仔搜索',
                             font=self.FONT_CAPTION_TITLE, bd=0)
        title_lbl.pack(side='left')

        def _cap_btn(sym):
            f = tk.Frame(bar, width=46, height=40, bd=0,
                         highlightthickness=0, relief='flat')
            f.pack_propagate(False)
            l = tk.Label(f, text=sym, font=self.FONT_CAPTION_BTN, bd=0)
            l.place(relx=0.5, rely=0.5, anchor='center')
            return f, l

        clf, cll = _cap_btn(self.IC_CAP_CLOSE)
        clf.pack(side='right')
        mxf, mxl = _cap_btn(self.IC_CAP_MAX)
        mxf.pack(side='right')
        mnf, mnl = _cap_btn(self.IC_CAP_MIN)
        mnf.pack(side='right')

        self._cap = {
            'bar': bar, 'left': left, 'icon': icon_lbl, 'title': title_lbl,
            'min': (mnf, mnl), 'max': (mxf, mxl), 'close': (clf, cll),
            'maxl': mxl, 'active': True, 'hover': None}
        self._titlebar = bar
        self._cap_paint()

        self.root.bind('<<WcMinHover>>',
                       lambda _e: self._cap_on_hover('min'), add='+')
        self.root.bind('<<WcMaxHover>>',
                       lambda _e: self._cap_on_hover('max'), add='+')
        self.root.bind('<<WcCloseHover>>',
                       lambda _e: self._cap_on_hover('close'), add='+')
        self.root.bind('<<WcBtnLeave>>', self._cap_on_leave, add='+')
        self.root.bind('<<WcActivate>>',
                       lambda _e: self._cap_on_active(True), add='+')
        self.root.bind('<<WcDeactivate>>',
                       lambda _e: self._cap_on_active(False), add='+')
        self.root.bind('<<WcMaxState>>', self._cap_on_maxstate, add='+')
        self.root.after_idle(self._cap_on_maxstate)






    def _build_ui(self):
        # 全局控件样式（4 级按钮体系 + 搜索框；亮暗在方法内部分流）
        self._configure_control_styles()
        # 无边框融合：自绘标题栏必须最先 pack，占住窗口最顶 40px
        self._build_titlebar()
        # appbar_flat：Fluent AppBar 风格（无描边浮底按钮）；关闭回退描边按钮
        self.appbar_flat = bool(self.cfg.get('appbar_flat', True))
        bar_btn = 'AppBar.Icon.TButton' if self.appbar_flat else 'Icon.TButton'
        # ===== 顶栏分两行：工具栏（左：操作按钮） + 搜索栏（右：彩色入口 + 搜索框 + 设置）=====
        top_toolbar = ttk.Frame(self.root, padding=(10, 6, 10, 0))
        top_toolbar.pack(fill='x')
        top_search = ttk.Frame(self.root, padding=(10, 4, 10, 6))
        top_search.pack(fill='x')
        # AppBar 与主内容之间的发丝线分层（扁平模式专属）
        if self.appbar_flat:
            ttk.Separator(self.root).pack(side='top', fill='x')

        # —— 工具栏左组：文件操作 ——
        self.scan_btn = ttk.Button(top_toolbar,
                                   text=f'{self.IC_FOLDER_ADD} 扫描入库',
                                   style=bar_btn,
                                   command=self._scan_action)
        self.scan_btn.pack(side='left')
        # 悬停 tooltip 区分两个入库入口
        self._bind_btn_tip(
            self.scan_btn,
            '扫描当前文件夹：自动找目录下所有文件并入库（适合初次建库）')
        ttk.Label(top_toolbar, text='F5', foreground='#aaa',
                  font=('Microsoft YaHei UI', 7)
                  ).pack(side='left', padx=(1, 0))
        ttk.Button(top_toolbar,
                   text=f'{self.IC_FOLDER} 选目录', style=bar_btn,
                   command=self.choose_dir
                   ).pack(side='left', padx=(3, 0))
        _imp_btn = ttk.Button(top_toolbar,
                              text=f'{self.IC_DOC} 导入文件', style=bar_btn,
                              command=self.import_files_dialog)
        _imp_btn.pack(side='left', padx=(4, 0))
        self._bind_btn_tip(
            _imp_btn,
            '手动选择文件：只导入你点选的这几个文件（适合零散补充）')
        ttk.Separator(top_toolbar, orient='vertical').pack(
            side='left', fill='y', padx=9)
        # —— 工具栏中组：核心功能入口 ——
        ttk.Button(top_toolbar,
                   text=f'{self.IC_BOOKS} 库管理', style=bar_btn,
                   command=self.show_library
                   ).pack(side='left')
        ttk.Button(top_toolbar,
                   text=f'{self.IC_STAR_FILL} 书签', style=bar_btn,
                   command=self.show_bookmarks
                   ).pack(side='left', padx=(4, 0))
        # 联网检索制度：外网官方来源检索入口（原在 ⚙ 菜单里藏太深，
        # 现放到 AppBar 工具栏末位，与库管理/书签同排）
        self.web_btn = ttk.Button(
            top_toolbar, text=f'{self.IC_GLOBE} 联网检索',
            style=bar_btn, command=self.show_websearch)
        self.web_btn.pack(side='left', padx=(4, 0))
        if self.appbar_flat:
            # 主动维护 active 态：clam 元素的 hover 浮底读 state active；
            # 真机 Tk 会自动设，这里 bind 是幂等双保险（也兼容注入式输入）
            def _arm_hover(w):
                w.bind('<Enter>',
                       lambda _e, x=w: x.state(['active']), add='+')
                w.bind('<Leave>',
                       lambda _e, x=w: x.state(['!active']), add='+')
            for _w in top_toolbar.winfo_children():
                try:
                    if _w.winfo_class() == 'TButton':
                        _arm_hover(_w)
                except Exception:
                    pass

        # —— 搜索栏 ——
        # AI 彩色入口用 RoundButton（PIL 圆角底图）：高度由位图锁死 32px。
        _bar_bg = ttk.Style().lookup('TFrame', 'background') or '#F4F6F9'
        # AI 镜仔智答：全站唯一彩色实心按钮（主色突出）
        self.qa_btn = RoundButton(
            top_search, text=f'{self.IC_CHAT} AI 镜仔智答',
            command=self.show_qa_window,
            font=self.FONT_ICON_B, height=32, padx=14,
            bg='#2F5E8C', fg='#FFFFFF', activebackground='#3A6FA2',
            corner_bg=_bar_bg)
        self.qa_btn.pack(side='left', padx=(0, 10))
        # AI 入口按钮：hover 背景渐变（颜色随主题实时读取）
        self._bind_tk_hover(self.qa_btn, self._qa_btn_colors)
        # AI 入口慢速呼吸，引导发现（hover 时让位给 hover 渐变色）
        self._qa_hovered = False
        self.qa_btn.bind('<Enter>',
                         lambda _e: self._qa_breathe_pause(), add='+')
        self.qa_btn.bind('<Leave>',
                         lambda _e: self._qa_breathe_resume(), add='+')
        if anim is not None:
            self.root.after(600, self._qa_breathe_start)
        # 最小化/后台时暂停呼吸装饰，恢复时继续（省后台 CPU/重绘）
        self.root.bind('<Unmap>',
                       lambda _e: self._qa_breathe_on_visibility(False),
                       add='+')
        self.root.bind('<Map>',
                       lambda _e: self._qa_breathe_on_visibility(True),
                       add='+')
        # 关键词输入：历史下拉 + 占位语法提示（圆角胶囊 + 内嵌放大镜）
        search_wrap = ttk.Frame(top_search)
        search_wrap.pack(side='left', fill='x', expand=True, padx=(0, 8))
        self._search_wrap = search_wrap
        _top_bg = ttk.Style().lookup('TFrame', 'background') \
            or self.root.cget('bg')
        # 语法速查 ? 按钮：挂在输入域**外侧**右缘，与框内的 ✕ 共用同一条
        # 水平中线（✕ 在框内紧挨下拉箭头左侧，? 在框外）。
        # 间距严格按「一个空格」算：右侧占位一个 空格+字形+空格 的宽度，
        # 窗口变窄时伸缩的是输入域，? 永不被挤没。
        # （历史教训：? 曾浮在输入域右端内侧，压住下拉箭头的热区）
        _f_syntax = ('Microsoft YaHei UI', 10, 'bold')
        self._syntax_btn = tk.Label(search_wrap, text='?', bd=0,
                                    cursor='hand2', bg=_top_bg, fg='#888',
                                    font=_f_syntax)
        self._sp_syntax = tkfont.Font(font=_f_syntax).measure(' ')  # 一个空格
        # height=32 是这一排的「基准高」：占位框撑住 32，输入域随 fill='both'
        # 被拉到 32（ttk 自带 padding 只能给 31/33，差 1px 会看得出错位）
        self._syntax_slot = ttk.Frame(
            search_wrap, height=32,
            width=self._sp_syntax * 2 + self._syntax_btn.winfo_reqwidth())
        self._syntax_slot.pack(side='right', fill='y')
        self._syntax_slot.pack_propagate(False)
        self._syntax_btn.bind('<Button-1>', lambda e: self._show_syntax_card())
        self.kw_entry = ttk.Combobox(search_wrap, textvariable=self.keyword,
                                     values=self.cfg.get('recent_keywords', []),
                                     width=34,
                                     font=('Microsoft YaHei UI', 10),
                                     style='Search.TCombobox')
        self.kw_entry.pack(side='left', fill='both', expand=True)
        # ? 贴着输入域右缘外侧一个空格处，纵向取输入域中线（与框内 ✕ 齐平）
        self._syntax_btn.place(in_=self.kw_entry, relx=1.0,
                               x=self._sp_syntax, rely=0.5, anchor='w')
        # 右侧占位框比 ? 晚建，默认压在 ? 上面（Tk 后建者在上）——
        # 这里把 ? 提上来，否则 ? 会被占位框整块盖住（实测过）
        self._syntax_btn.lift()
        # 内嵌放大镜（place 于输入域左侧，底色随主题与输入域一致）
        self._search_icon = tk.Label(
            search_wrap, text=self.IC_SEARCH, bd=0,
            bg='#FFFFFF' if not self.dark.get() else '#2D2E32',
            font=('Segoe MDL2 Assets', 10))
        self._search_icon.place(x=9, rely=0.5, anchor='w')
        self.kw_entry.bind('<Return>', lambda e: self.do_search())
        self.kw_entry.bind('<KeyRelease>', self._on_kw_typed)
        # 搜索框清空小叉：Canvas 绘制（支持 hover 旋转 90° 动画）
        # 底色取 ttk.Frame 主题色，与顶栏融为一体（主题切换时在样式方法里刷新）
        self._clear_kw_btn = tk.Canvas(
            search_wrap, width=18, height=18, bd=0, cursor='hand2',
            bg='#FFFFFF' if not self.dark.get() else '#2D2E32',
            highlightthickness=0)
        # 放进输入域右端内侧：右边缘留 26px 给 ttk 自带的下拉箭头，
        # 免得两个热区又叠在一起（原来它挂在输入域外面，离得远）
        self._clear_kw_btn.place(in_=self.kw_entry, relx=1.0, x=-26,
                                 rely=0.5, anchor='e')
        _cx_l1 = self._clear_kw_btn.create_line(
            5, 5, 13, 13, fill='#c0c0c0', width=2, capstyle='round')
        _cx_l2 = self._clear_kw_btn.create_line(
            13, 5, 5, 13, fill='#c0c0c0', width=2, capstyle='round')
        _cx_state = {'angle': 0.0, 'color': '#c0c0c0', 'tw': None}
        self._clear_kw_btn._cx_l1 = _cx_l1
        self._clear_kw_btn._cx_l2 = _cx_l2
        self._clear_kw_btn._cx_state = _cx_state

        def _cx_redraw(angle=None, color=None):
            """按角度/颜色重绘 ✕（绕中心 9,9 旋转）"""
            st = _cx_state
            a = st['angle'] if angle is None else angle
            col = st['color'] if color is None else color
            import math as _m
            rad = _m.radians(a)
            ca, sa = _m.cos(rad), _m.sin(rad)
            def _rot(x, y):
                dx, dy = x - 9, y - 9
                return 9 + dx * ca - dy * sa, 9 + dx * sa + dy * ca
            for line, pts in ((_cx_l1, (5, 5, 13, 13)),
                              (_cx_l2, (13, 5, 5, 13))):
                x1, y1 = _rot(pts[0], pts[1])
                x2, y2 = _rot(pts[2], pts[3])
                self._clear_kw_btn.coords(line, x1, y1, x2, y2)
                self._clear_kw_btn.itemconfigure(line, fill=col)

        def _cx_anim(to_angle, to_color, ms=150):
            if not self._anim_ok():
                _cx_state['angle'] = to_angle
                _cx_state['color'] = to_color
                _cx_redraw()
                return
            if _cx_state['tw'] is None and anim is not None:
                _cx_state['tw'] = anim.Tweener(self.root)
            tw = _cx_state['tw']
            a0, c0 = _cx_state['angle'], _cx_state['color']
            tw.cancel()
            tw_a = anim.Tweener(self.root)
            tw_a.tween(a0, to_angle, ms,
                       lambda _t, v: (_cx_state.__setitem__('angle', v),
                                      _cx_redraw()),
                       easing='ease_out_back')
            tw.tween_color(c0, to_color, ms,
                           lambda _t, c: (_cx_state.__setitem__('color', c),
                                          _cx_redraw()))
            _cx_state['tw'] = tw

        self._clear_kw_btn.bind('<Button-1>',
                                lambda e: self._clear_search())
        # 搜索框有字时显红，hover 时显红+旋转，其余灰
        def _refresh_clear_btn(_evt=None):
            text = self.kw_entry.get()
            has_content = bool(text and text != KW_HINT)
            _cx_anim(0.0, '#d04040' if has_content else '#c0c0c0')
        self._refresh_clear_btn = _refresh_clear_btn
        self.kw_entry.bind('<KeyRelease>', _refresh_clear_btn, add='+')
        self.kw_entry.bind('<FocusIn>', _refresh_clear_btn, add='+')
        self.kw_entry.bind('<FocusOut>', _refresh_clear_btn, add='+')
        self._clear_kw_btn.bind(
            '<Enter>', lambda e: _cx_anim(90.0, '#d04040'))
        self._clear_kw_btn.bind('<Leave>', _refresh_clear_btn)
        self._kw_ctx = tk.Menu(self.kw_entry, tearoff=0)
        def _kw_right_click(evt):
            self._kw_ctx.delete(0, 'end')
            sel = self.kw_entry.get()
            if sel and sel in self.cfg.get('recent_keywords', []):
                self._kw_ctx.add_command(
                    label=f'删除「{sel[:20]}」',
                    command=lambda: self._remove_keyword(sel))
                self._kw_ctx.add_separator()
            self._kw_ctx.add_command(label='清除全部历史',
                                      command=self._clear_keywords)
            self._kw_ctx.tk_popup(evt.x_root, evt.y_root)
        self.kw_entry.bind('<Button-3>', _kw_right_click)
        # 占位提示：未输入时灰色显示高级语法前缀，聚焦清空、失焦还原
        def _clear_kw_hint(_evt=None):
            if self.kw_entry.get() == KW_HINT:
                self.kw_entry.delete(0, 'end')
                self.kw_entry.configure(foreground='')
        def _show_kw_hint(_evt=None):
            if not self.kw_entry.get():
                self.keyword.set(KW_HINT)
                self.kw_entry.configure(foreground='gray')
        self._clear_kw_hint = _clear_kw_hint
        self.kw_entry.bind('<FocusIn>', _clear_kw_hint)
        self.kw_entry.bind('<FocusOut>', _show_kw_hint)
        # Esc = 重置：清空搜索框并同步清空结果区
        self.kw_entry.bind(
            '<Escape>',
            lambda e: (self.keyword.set(''), self._reset_results()))
        self.keyword.set(KW_HINT)
        self.kw_entry.configure(foreground='gray')
        # 搜索记录下拉：鼠标移出即收回（弹出列表是 Tcl 侧控件，按路径挂）
        self._install_hist_autoclose()
        # 右锚定组先 pack：窗口变窄时伸缩的是搜索框，齿轮/帮助永不被挤没
        # pack(side='right') 顺序：最右是「使用说明」，其左为齿轮，再左为分隔线
        ttk.Button(top_search, text='使用说明 F1', style='Link.TButton',
                   command=self.show_help
                   ).pack(side='right', padx=(0, 2))
        self.more_btn = ttk.Menubutton(top_search, text=self.IC_GEAR,
                                       style='Icon.TMenubutton')
        self.more_btn.pack(side='right')
        ttk.Separator(top_search, orient='vertical').pack(
            side='right', fill='y', padx=9)
        self.search_btn = ttk.Button(top_search,
                                     text=f'{self.IC_SEARCH} 搜索',
                                     style='Icon.Accent.TButton',
                                     command=self.do_search)
        self.search_btn.pack(side='left')
        ttk.Label(top_search, text='Ctrl+F', foreground='#aaa',
                  font=('Microsoft YaHei UI', 7)
                  ).pack(side='left', padx=(1, 0))
        # 更多菜单：界面与搜索 / 工具 / 后台与启动 / 文件位置 四组
        # （2026-10-01 精简：无边框/扁平工具栏/自动监听/待入库检查/jingzai 协议
        #  固化为默认；上下文行数移到结果区；仅登记路径移到库管理；
        #  OCR 状态/备份份数/库位置并入索引体检；搜索模板两项合一）
        more_menu = tk.Menu(self.more_btn, tearoff=0)

        def _group(title):
            more_menu.add_command(label=title, state='disabled')

        _group('界面与搜索')
        more_menu.add_checkbutton(label='区分大小写', variable=self.cs)
        more_menu.add_checkbutton(label='深色模式', variable=self.dark,
                                  command=self._toggle_dark_animated)
        more_menu.add_checkbutton(label='减弱动效',
                                  variable=self.reduce_motion_var,
                                  command=self._toggle_reduce_motion)
        more_menu.add_separator()
        more_menu.add_command(label='同义词库设置…',
                              command=self.show_synonym_settings)
        more_menu.add_command(label='搜索模板…',
                              command=self.show_search_templates)
        more_menu.add_command(label='热键设置…',
                              command=self.show_hotkey_settings)
        more_menu.add_command(label='索引体检…（含 OCR 状态 / 备份 / 库位置）',
                              command=self.show_index_health)
        more_menu.add_separator()
        _group('工具')
        # 联网检索制度已提到主界面搜索行右侧（self.web_btn），此处不再重复
        more_menu.add_command(label='生成引用关系图（HTML）…',
                              command=self.show_ref_graph)
        more_menu.add_command(label='导出结果 CSV (Ctrl+E)',
                              command=self.export_csv)
        more_menu.add_separator()
        _group('后台与启动')
        more_menu.add_checkbutton(label='开机自启动',
                                  variable=self.autostart_var,
                                  command=self._toggle_autostart)
        more_menu.add_separator()
        _group('文件位置')
        more_menu.add_command(label='待入库文件…',
                              command=self.show_inbox_review)
        more_menu.add_command(label='下载监控文件夹…',
                              command=self._choose_download_watch_dir)
        more_menu.add_command(label='默认存档位置…',
                              command=self._choose_export_dir)
        more_menu.add_separator()
        more_menu.add_command(label='意见反馈…', command=self.open_feedback)
        self.more_btn.configure(menu=more_menu)

        # ===== 第二行：筛选（模式/类型）+ 搜索统计 =====
        bar2 = ttk.Frame(self.root, padding=(10, 6, 10, 4))
        bar2.pack(fill='x')
        ttk.Label(bar2, text='模式:').pack(side='left')
        for _val, _lbl in (('exact', '精确'), ('fuzzy', '模糊')):
            ttk.Radiobutton(bar2, text=_lbl, value=_val,
                            variable=self.search_mode).pack(side='left',
                                                            padx=(4, 0))
        ttk.Separator(bar2, orient='vertical').pack(
            side='left', fill='y', padx=10)
        ttk.Label(bar2, text='类型:').pack(side='left')
        self.type_cb = ttk.Combobox(bar2, state='readonly', width=7,
                                    values=[lbl for _, lbl in TYPE_OPTIONS])
        self.type_cb.current(0)
        self.type_cb.pack(side='left', padx=(5, 0))
        self.type_cb.bind('<<ComboboxSelected>>', self._on_type_change)
        ttk.Checkbutton(bar2, text='仅看有效', variable=self.only_active,
                        command=self.do_search).pack(side='left', padx=(10, 0))
        ttk.Checkbutton(bar2, text='同义扩展', variable=self.syn_var,
                        command=self.do_search).pack(side='left', padx=(8, 0))
        # 统计文字 + 命中分布柱状图（画布可横向滚动，铺开全部文件）
        self.stats_label = ttk.Label(bar2, text='', anchor='w',
                                     style='Stats.TLabel',
                                     font=self.FONT_UI_B)
        self.stats_label.pack(side='left', padx=(12, 4))
        self.stats_chart = ttk.Frame(bar2)
        self.stats_chart.pack(side='left', fill='both', expand=True, padx=(0, 4))
        self._chart_canvas = tk.Canvas(self.stats_chart, height=64,
                                       highlightthickness=0)
        self._chart_xsb = ttk.Scrollbar(self.stats_chart, orient='horizontal',
                                        command=self._chart_canvas.xview)
        self._chart_canvas.configure(xscrollcommand=self._chart_xsb.set)
        self._chart_canvas.pack(side='top', fill='both', expand=True)
        self._chart_xsb.pack(side='bottom', fill='x')
        # 滚轮横向滚动；悬停状态栏显示全名；点击跳到该文件首条命中
        # 首搜前画布是空的，_draw_distribution_chart 还没跑过 →
        # _chart_hover_idx 必须先初始化，否则鼠标一进画布就 AttributeError
        self._chart_hover_idx = -1
        self._chart_canvas.bind(
            '<MouseWheel>', lambda e: self._chart_canvas.xview_scroll(
                -1 if e.delta > 0 else 1, 'units'))
        self._chart_canvas.bind('<Motion>', self._chart_hover)
        self._chart_canvas.bind('<Button-1>', self._chart_click)
        # 柱状图字体（tkfont 对象可实测像素宽，用于防重叠截断）
        self._chart_name_font = tkfont.Font(
            family='Microsoft YaHei UI', size=8)
        self._chart_count_font = tkfont.Font(
            family='Microsoft YaHei UI', size=8, weight='bold')

        # ===== 底部状态栏 26px：进度 / 状态点 / 状态 / 当前目录 / 错误角标 =====
        ttk.Separator(self.root).pack(side='bottom', fill='x')
        bottom = ttk.Frame(self.root, height=26, padding=(10, 0, 10, 0))
        bottom.pack(side='bottom', fill='x')
        bottom.pack_propagate(False)
        self.progress = ttk.Progressbar(bottom, mode='determinate',
                                        length=150,
                                        style='Brand.Horizontal.TProgressbar')
        self.progress.pack(side='left')
        # 状态圆点：就绪灰 / 搜索蓝 / 成功绿 / 出错红
        self.status_dot = tk.Canvas(bottom, width=10, height=10,
                                    highlightthickness=0, borderwidth=0)
        self.status_dot.pack(side='left', padx=(10, 0))
        self._dot = self.status_dot.create_oval(1, 1, 9, 9, fill='#9AA0A6',
                                                outline='')
        self.status = ttk.Label(bottom, text='就绪 · 拖拽文件到窗口直接入库',
                                anchor='w')
        self.status.pack(side='left', fill='x', expand=True, padx=6)
        # 当前目录截断显示，悬停 tooltip 看全路径
        self._dir_lbl = ttk.Label(bottom, text='', foreground='gray')
        self._dir_lbl.pack(side='left', padx=(0, 10))
        self.root_dir.trace_add('write', lambda *_: self._update_dir_label())
        self._update_dir_label()
        # 需处理角标：文件缺失/无文本时出现，点击直达问题文件管理（后台计数）
        # 圆角胶囊样式，与全窗按钮体系统一；四角用状态栏底色对齐
        _bar_bg = str(ttk.Style().lookup('TFrame', 'background')
                      or self.palette()['bg'])
        self.prob_btn = RoundButton(
            bottom, text='', command=self.show_problem_files,
            font=self.FONT_ICON_S, height=24, radius=12, padx=10,
            bg='#FFF8C5', fg='#9A6700', activebackground='#FBEFA8',
            corner_bg=_bar_bg)
        self.err_btn = RoundButton(
            bottom, text=f'{self.IC_WARN} 错误 0', command=self.show_errors,
            font=self.FONT_ICON_S, height=24, radius=12, padx=10,
            bg='#FDE7E9', fg='#C5221F', activebackground='#FBD9DC',
            corner_bg=_bar_bg)
        # 默认不 pack，有错误时才出现（_refresh_err_count 控制）
        self._refresh_err_count()

        # ===== 主区：combined(上=章节目录 + 下=搜索命中) | 右=预览正文 =====
        pane = ttk.PanedWindow(self.root, orient='horizontal')
        pane.pack(fill='both', expand=True, padx=10, pady=(2, 6))
        self.main_pane = pane

        # —— 左：tk.PanedWindow（垂直分割，可拖动调整上下比例）——
        # 用 tk.PanedWindow 而不是 ttk 的：tk 版支持 minsize，空容器也不会被挤没
        # 发丝扁平 sash：4px 热区（Tk 无法分离视觉宽/热区），flat 无凹槽
        combined_pw = tk.PanedWindow(pane, orient='vertical',
                                     sashwidth=4, sashpad=0,
                                     sashrelief='flat', bd=0,
                                     bg=self._sash_initial_bg())
        pane.add(combined_pw, weight=2)
        self.combined_pane = combined_pw
        self.left_pane = combined_pw

        # —— 先 add 搜索命中区域（上半，先 add = 出现在上）——
        tree_holder = ttk.Frame(combined_pw)
        combined_pw.add(tree_holder, minsize=80)

        # 结果内过滤框
        filt = ttk.Frame(tree_holder)
        filt.pack(side='top', fill='x')
        ttk.Label(filt, text='结果内过滤:', foreground='gray',
                  font=('Microsoft YaHei UI', 8)).pack(side='left', padx=(4, 2))
        self.filter_var = tk.StringVar()
        fe = ttk.Entry(filt, textvariable=self.filter_var)
        self.fe = fe
        fe.pack(side='left', fill='x', expand=True, padx=2)
        fe.bind('<KeyRelease>', self._on_filter_change)

        # 搜索结果 Treeview（列已瘦身：类型/行号/页码并入位置标签）
        # lineno 为零宽隐藏列：存命中行号供点击跳转，不显示
        self.tree = ttk.Treeview(tree_holder,
                                 columns=('hits', 'lineno', 'text'),
                                 show='tree headings')
        self.tree.heading('#0', text='文件 / 位置')
        self.tree.heading('hits', text='命中')
        self.tree.heading('lineno', text='')
        self.tree.heading('text', text='内容片段')
        self.tree.column('#0', width=280, anchor='w', stretch=False)
        self.tree.column('hits', width=64, anchor='e', stretch=False)
        self.tree.column('lineno', width=0, minwidth=0, anchor='center',
                         stretch=False)
        self.tree.column('text', width=430, anchor='w', stretch=True)
        _c0 = self._theme_colors()
        self.tree.tag_configure('file', background=_c0['tree_file_bg'],
                                foreground=_c0['tree_file_fg'],
                                font=self.FONT_UI_B)
        self.tree.tag_configure('line', background=_c0['tree_line_bg'])
        self.tree.tag_configure('odd', background=_c0['tree_odd_bg'])
        self.tree.tag_configure('fuzzyline', background=_c0['tree_fuzzy_bg'],
                                foreground=_c0['tree_fuzzy_fg'])
        self.tree.tag_configure('crossline',
                                foreground=self.palette()['brand'])
        self.tree.tag_configure('void', foreground=_c0['void_fg'],
                                 font=(self.FONT_UI[0], 9, 'overstrike'))
        self.tree.tag_configure('replaced', foreground=_c0['replaced_fg'],
                                 font=(self.FONT_UI[0], 9, 'italic'))
        for _cid, _w in (self.cfg.get('tree_cols') or {}).items():
            try:
                self.tree.column(_cid, width=int(_w))
            except Exception:
                pass
        self.tree.bind('<ButtonRelease-1>', self._on_tree_col_resized, add='+')
        self._tree_sb = ttk.Scrollbar(tree_holder, orient='vertical',
                                       command=self.tree.yview)
        hsb = ttk.Scrollbar(tree_holder, orient='horizontal',
                            command=self.tree.xview)
        self._tree_expanded = set()
        self.tree.configure(xscrollcommand=hsb.set,
                           yscrollcommand=self._tree_yscroll_set)
        self._tree_sb.pack(side='right', fill='y')
        hsb.pack(side='bottom', fill='x')
        self.tree.pack(side='left', fill='both', expand=True)
        self.tree.bind('<<TreeviewSelect>>', self.on_select)
        self.tree.bind('<Double-1>', self.open_file)
        self.tree.bind('<Button-3>', self._result_right_click)
        self.tree.bind('<<TreeviewOpen>>', self._on_tree_open_track)
        # 文件行悬停 500ms 弹完整路径 tooltip（文件名截断时自救）
        self.tree.bind('<Motion>', self._on_tree_motion, add='+')
        self.tree.bind('<Leave>', self._hide_tree_tip, add='+')
        # 惯性滚动（动量衰减，触摸板体验提升）
        if anim is not None:
            try:
                self._tree_scroller = anim.SmoothScroller(self.tree, axis='y')
            except Exception:
                pass

        # —— 后 add 章节目录（下半，后 add = 出现在下）——
        chf = ttk.Frame(combined_pw)
        combined_pw.add(chf, minsize=60)
        ttk.Label(chf, text='章节目录', padding=(4, 2)).pack(anchor='w')
        self.chap_tree = ttk.Treeview(chf, show='tree', selectmode='browse')
        self.chap_tree.column('#0', width=220, anchor='w')
        cvs = ttk.Scrollbar(chf, orient='vertical', command=self.chap_tree.yview)
        self.chap_tree.configure(yscrollcommand=cvs.set)
        cvs.pack(side='right', fill='y')
        self.chap_tree.pack(side='left', fill='both', expand=True)
        # 初始空状态占位
        self.chap_tree.insert('', 'end', text='搜索后在此显示该文件的章节结构',
                              tags=('ph',))
        self.chap_tree.tag_configure(
            'ph', foreground='gray', font=self.FONT_HINT)
        self.chap_tree.bind('<<TreeviewSelect>>', self._on_chapter_select)
        # 章节目录惯性滚动
        if anim is not None:
            try:
                anim.SmoothScroller(self.chap_tree, axis='y')
            except Exception:
                pass
        self.chap_tree.bind('<Button-1>', self._on_chap_click, add='+')
        self._chapters_path = None
        c0 = self._theme_colors()
        self.chap_tree.tag_configure('lv1', font=self.FONT_UI_B,
                                      foreground=c0['chap_fg'])
        self.chap_tree.tag_configure('lv2', font=self.FONT_UI)
        self.chap_tree.tag_configure('lv3', font=self.FONT_HINT,
                                      foreground=c0['meta_fg'])

        # —— 右：预览正文（不再需要 ppane，chap_tree 已移到 combined_pw 下半）——
        right = ttk.Frame(pane)
        self.right_pane = right
        pane.add(right, weight=3)
        # 内部阅读模式：隐藏整个 combined_pw 后显示的返回条（默认收起）
        self.read_bar = ttk.Frame(right, padding=(0, 0, 0, 4))
        ttk.Button(self.read_bar, text='← 返回搜索结果',
                   command=self._exit_reading).pack(side='left')
        self._read_path = None
        rbar = ttk.Frame(right, padding=(0, 0, 0, 4))
        rbar.pack(fill='x')
        self.rbar = rbar
        # 返回按钮（仅引用跳转后出现，点击回到上一个文件位置）
        self.back_btn = ttk.Button(
            rbar, text='← 返回上一个', command=self._go_back,
            state='disabled')
        self.back_btn.pack(side='left', padx=(0, 6))
        self._cur_path = None
        self._cur_line = None
        # pbody 直接在 right 里，不再套 ppane
        pbody = ttk.Frame(right)
        pbody.pack(fill='both', expand=True)
        self._pfont = tkfont.Font(
            family=self.cfg.get('preview_family') or 'Microsoft YaHei UI',
            size=int(self.cfg.get('preview_font', 10)))
        self._pfont_size = self._pfont.cget('size')
        # 默认按词自动换行（长句不用左右拖），可在底部工具栏关掉，偏好记忆
        _wrap = 'word' if self.cfg.get('preview_wrap', True) else 'none'
        self.preview_wrap_var = tk.BooleanVar(value=_wrap == 'word')
        # 只读：正文（含来源链接）不允许被误删改，要改请用「打开文件」
        self.preview = ReadonlyText(pbody, wrap=_wrap,
                                    font=self._pfont,
                                    padx=16, pady=12,
                                    background='#FCFDFE',
                                    foreground='#1B2632',
                                    selectbackground='#D6E3F0',
                                    state='disabled')
        # 表格对齐：每列 150px 一个 tab stop，6 列够大多数 Word/Excel 表格
        self.preview.configure(tabs=tuple(i * 150 for i in range(1, 7)))
        psb = ttk.Scrollbar(pbody, orient='vertical',
                            command=self.preview.yview)
        phsb = ttk.Scrollbar(pbody, orient='horizontal',
                             command=self.preview.xview)
        self.preview.configure(yscrollcommand=psb.set,
                               xscrollcommand=phsb.set)
        # pack 顺序：先底部控件（toolbar 在最底，phsb 在其上），
        # 再右侧 psb，最后 preview 占剩余空间
        # —— 底部工具栏 ——
        # 删了"复制段落/复制引用"后精简为两组：左（操作按钮）右（模式切换）
        # 工具栏上方加一条 1px 分隔线，和正文预览区分开
        ttk.Separator(pbody, orient='horizontal').pack(side='bottom', fill='x')
        bottom_bar = ttk.Frame(pbody)
        bottom_bar.pack(side='bottom', fill='x', padx=8, pady=4)
        self.bottom_bar = bottom_bar
        # 左组：导出汇编 + 收藏 + 引用关系 + 打开文件
        left_grp = ttk.Frame(bottom_bar)
        left_grp.pack(side='left', fill='x', expand=True)
        # 导出汇编（原在左侧 filt 里，挪到此处重建）
        self.digest_btn = ttk.Button(left_grp, text='导出汇编',
                                     command=self._export_results_digest,
                                     state='disabled')
        self.digest_btn.pack(side='left', padx=(0, 8))
        self.bm_btn = ttk.Button(left_grp,
                                 text=f'{self.IC_STAR} 收藏此条',
                                 style='Icon.TButton',
                                 command=self._toggle_bookmark)
        self.bm_btn.pack(side='left', padx=(0, 8))
        ttk.Button(left_grp, text='引用关系',
                   command=self.show_refs).pack(side='left', padx=(0, 8))
        self.open_file_btn = ttk.Button(left_grp, text='打开文件',
                   command=self._open_current_file)
        self.open_file_btn.pack(side='left')
        # 右组：模式切换（整体靠右，内部左到右自然阅读）
        right_grp = ttk.Frame(bottom_bar)
        right_grp.pack(side='right')
        self.preview_mode = tk.StringVar(value='paragraph')
        ttk.Label(right_grp, text='模式:',
                  font=self.FONT_HINT, foreground='#666'
                  ).pack(side='left', padx=(0, 2))
        ttk.Radiobutton(right_grp, text='整段', variable=self.preview_mode,
                       value='paragraph', command=self._refresh_preview,
                       padding=0
                       ).pack(side='left', padx=(0, 4))
        ttk.Radiobutton(right_grp, text='上下文', variable=self.preview_mode,
                       value='context', command=self._refresh_preview,
                       padding=0
                       ).pack(side='left', padx=(0, 8))
        ttk.Checkbutton(right_grp, text='自适应换行', variable=self.preview_wrap_var,
                        command=self._toggle_preview_wrap, padding=0
                        ).pack(side='left', padx=(0, 8))
        ttk.Label(right_grp, text='Ctrl+C 复制 · Ctrl+滚轮缩放',
                  font=self.FONT_HINT, foreground='#999'
                  ).pack(side='left')

        # phsb 在 toolbar 之上
        phsb.pack(side='bottom', fill='x')
        psb.pack(side='right', fill='y')
        self.preview.pack(side='top', fill='both', expand=True)
        # 空状态占位（居中淡灰字，有内容后自动隐藏）
        self._preview_ph = tk.Label(
            pbody, text='← 点击左侧搜索结果，在此查看正文\n\n'
                        '行号图例：L125 = 正文第 125 行 · P8 = 第 8 页'
                        '（仅 PDF / PPT 有页码）',
            foreground='#98A1AC', bg='#FCFDFE', justify='center',
            font=('Microsoft YaHei UI', 10))
        self._preview_ph.place(relx=0.5, rely=0.5, anchor='center')
        # Ctrl+滚轮 缩放预览字号
        self.preview.bind('<Control-MouseWheel>', self._preview_zoom)
        # 预览区惯性滚动
        if anim is not None:
            try:
                self._preview_scroller = anim.SmoothScroller(self.preview, axis='y')
            except Exception:
                pass
        # 标签样式：高亮 / 章节链 / 文件信息
        self.preview.tag_configure('hl', background='#FBDCD2',
                                   foreground='#9C1F10')
        self.preview.tag_configure('chap', foreground='#2F5E8C',
                                   background='#E9EFF5',
                                   font=('Microsoft YaHei UI', 9, 'bold'),
                                   borderwidth=1, relief='solid',
                                   lmargin1=8, lmargin2=8, rmargin=8)
        self.preview.tag_configure('meta', foreground='#7B8694',
                                   font=('Microsoft YaHei UI', 9))
        self.preview.tag_configure('hit', background='#FBE3DC',
                                   foreground='#6B2014')
        self.preview.tag_configure('fuzzymeta', foreground='#C05621',
                                   font=('Microsoft YaHei UI', 9, 'bold'))
        # 结果内过滤词高亮（绿色；创建晚于 hit/hl，命中行内也可见）
        self.preview.tag_configure('fhl', background='#D6EFDC',
                                   foreground='#14532D')
        # 同义词高亮（橙色；创建晚于 hit/hl，命中行内也可见）
        self.preview.tag_configure('syn', background='#FBE5C8',
                                   foreground='#7A4A10')
        # 命中行会整行铺「hit」底色，而 Tk 里后创建的标签优先级更高，
        # 于是先建的「hl」（关键词黄底红字）在命中行里被整行底色盖掉——
        # 用户实测：搜到的行只看到一层浅底，看不出关键词在哪。
        # 把 hl 抬到 hit 之上（仍在 fhl/syn 之下，过滤词/同义词优先级不降）。
        self.preview.tag_raise('hl', 'hit')
        # —— 表格渲染样式 ——
        self.preview.tag_configure('tbl_header',
                                   background='#E9EFF5',
                                   foreground='#1B2632',
                                   font=('Microsoft YaHei UI', 10, 'bold'))
        self.preview.tag_configure('tbl_row', background='#FFFFFF')
        self.preview.tag_configure('tbl_row_alt', background='#F7F9FB')
        self.preview.tag_configure('tbl_hit',
                                   background='#FBE3DC',
                                   foreground='#6B2014')
        self.preview.tag_configure('tbl_sep', foreground='#C0C8D1',
                                   background='#F0F2F5',
                                   font=('Microsoft YaHei UI', 8))
        # 正文里的 http(s) 链接（单击用默认浏览器打开）
        self.preview.tag_configure('weblink', foreground='#2F5E8C',
                                   underline=True)
        self.preview.tag_bind('weblink', '<Button-1>', self._on_weblink_click)
        self.preview.tag_bind('weblink', '<Enter>',
                              lambda e: self.preview.configure(cursor='hand2'))
        self.preview.tag_bind('weblink', '<Leave>',
                              lambda e: self.preview.configure(cursor=''))
        # 只读正文：敲字/删除时给一句提示，别让用户以为软件卡了
        self.preview.bind('<KeyPress>', self._on_preview_key)
        # 可点击的《文件》引用链接
        self.preview.tag_configure('reflink', foreground='#2F5E8C',
                                   underline=True)
        self.preview.tag_configure('refmiss', foreground='#9AA3AE',
                                   underline=True)
        self.preview.tag_configure('refdead', foreground='#C13A28',
                                   underline=True)
        # 正文排版：行距微松（Fluent 阅读感），信息块 meta 不挂此标签保持紧凑
        self.preview.tag_configure('body',
                                   spacing1=4, spacing2=1, spacing3=3)
        # 绑定事件：单击 reflink/refdead = 内部预览中跳转（最顺手）
        # 右键菜单同时提供"外部打开原文件"和"在预览中跳转"
        self.preview.tag_bind('reflink', '<Button-1>', self._on_ref_preview_goto)
        self.preview.tag_bind('reflink', '<Enter>',
                              lambda e: self._ref_hover(e, 'reflink'))
        self.preview.tag_bind('reflink', '<Leave>',
                              lambda e: self._ref_hover(e, None))
        self.preview.tag_bind('refmiss', '<Button-1>', self._on_ref_miss_click)
        self.preview.tag_bind('refmiss', '<Enter>',
                              lambda e: self._ref_hover(e, 'refmiss'))
        self.preview.tag_bind('refmiss', '<Leave>',
                              lambda e: self._ref_hover(e, None))
        self.preview.tag_bind('refdead', '<Button-1>', self._on_ref_preview_goto)
        self.preview.tag_bind('refdead', '<Enter>',
                              lambda e: self._ref_hover(e, 'refdead'))
        self.preview.tag_bind('refdead', '<Leave>',
                              lambda e: self._ref_hover(e, None))
        # 右键菜单：复制段落 / 复制引用 / 收藏
        self._preview_ctx = tk.Menu(self.preview, tearoff=0)
        def _preview_right_click(evt):
            self._preview_ctx.delete(0, 'end')
            ref_tag, ref_info = self._ref_target_at(evt)
            # —— 灰色缺失引用（refmiss）—— 搜官网选项
            if ref_tag and ref_info and ref_info[0] is None:
                miss_title = ref_info[2] if len(ref_info) > 2 else ''
                def _open_std():
                    webbrowser.open(f'https://www.baidu.com/s?wd={quote(miss_title)}+site%3Aopenstd.samr.gov.cn')
                def _open_mohur():
                    webbrowser.open(f'https://www.baidu.com/s?wd={quote(miss_title)}+site%3Amohurd.gov.cn')
                def _open_baidu():
                    webbrowser.open(f'https://www.baidu.com/s?wd={quote(miss_title)}')
                self._preview_ctx.add_command(
                    label=f'{self.IC_SEARCH} 搜《{miss_title[:25]}》国家标准系统',
                    command=_open_std)
                self._preview_ctx.add_command(
                    label=f'{self.IC_SEARCH} 搜住建部官网',
                    command=_open_mohur)
                self._preview_ctx.add_command(
                    label=f'{self.IC_GLOBE} 百度搜',
                    command=_open_baidu)
                self._preview_ctx.add_separator()
                self._preview_ctx.add_command(
                    label=f'{self.IC_COPY} 复制标题',
                    command=lambda: self.root.clipboard_clear() or self.root.clipboard_append(miss_title))
                self._preview_ctx.add_separator()
            # 如果右键点在引用链接上，优先显示打开原文件
            elif ref_tag and ref_info[0]:
                ref_name = os.path.basename(ref_info[0])
                def _open_ref():
                    self._open_located(ref_info[0], ref_info[1])
                def _goto_ref():
                    self._on_ref_preview_goto(evt)
                self._preview_ctx.add_command(
                    label=f'{self.IC_FOLDER} 打开《{ref_name}》原文件', command=_open_ref)
                self._preview_ctx.add_command(
                    label=f'{self.IC_LINK} 在预览中跳转《{ref_name}》', command=_goto_ref)
                self._preview_ctx.add_separator()
            self._preview_ctx.add_command(label='复制段落 (Ctrl+C)',
                                          command=self._copy_paragraph)
            self._preview_ctx.add_command(label='复制引用',
                                          command=self._copy_citation)
            self._preview_ctx.add_separator()
            self._preview_ctx.add_command(
                label=self.bm_btn.cget('text'),
                command=self._toggle_bookmark)
            self._preview_ctx.add_command(label='打开当前文件',
                                          command=self._open_current_file)
            self._preview_ctx.add_command(label='打开当前文件夹',
                                          command=self._open_current_dir)
            self._preview_ctx.tk_popup(evt.x_root, evt.y_root)
        self.preview.bind('<Button-3>', _preview_right_click)
        # Ctrl+C 复制
        self.preview.bind('<Control-c>', self._copy_paragraph_evt)

        # 拖拽注册（tkinterdnd2 不可用时静默跳过）
        if _DND_AVAILABLE:
            self.root.drop_target_register(DND_FILES)
            self.root.dnd_bind('<<Drop>>', self._on_drop)
        # 控件全部建好后应用主题配色（含深色启动）
        self._apply_ui_theme()
        # 主窗口全部按钮挂 hover 渐变 + 按压过渡（uiverse 风格）
        self._bind_all_button_anims()
        # 所有 Toplevel 对话框自动淡入上滑（菜单/tooltip/下拉等 overrideredirect 跳过）
        self._bind_global_popup_anim()
        # Windows 窗口镀铬层：主窗圆角 + 标题栏深色；hook 所有原生菜单
        if winchrome is not None:
            winchrome.install(self.root, lambda: self.dark.get())
            winchrome.apply_window(self.root, self.dark.get())
            # 关掉 DWM 过渡：任务栏最小化→还原不再走系统动画。否则还原时
            # DWM 会先按「Tk 还没重绘的重定向表面」（全黑）合成一帧，且不受
            # 窗口透明度约束，用户会看到「从全黑变正常」。
            winchrome.disable_transitions(self.root)
            # 无边框融合：窗口映射（TkTopLevel 标题栏宿主就绪）后安装，
            # 幂等；after 兜底首次启动，<Map> 兜底最小化恢复等场景
            if getattr(self, '_titlebar', None) is not None:
                def _arm_frameless(*_):
                    try:
                        winchrome.enable_frameless(self.root)
                        winchrome.disable_transitions(self.root)
                    except Exception:
                        pass
                def _rehook_descendants(*_):
                    try:
                        winchrome.hook_descendants(self.root)
                    except Exception:
                        pass
                self.root.after(50, _arm_frameless)
                self.root.bind(
                    '<Map>',
                    lambda _e: self.root.after_idle(_arm_frameless),
                    add='+')
                # Tk 为动态新建/映射的 widget 建真实子窗口，需增量补挂
                self.root.bind_all(
                    '<Map>',
                    lambda _e: self.root.after_idle(_rehook_descendants),
                    add='+')
                # 系统回调里积压的标题栏虚拟事件，在主线程正常上下文派发
                # （避免在 Win32 模态循环重入栈上调 Tcl 导致 GIL 崩溃）
                if not getattr(self, '_wc_pump_on', False):
                    self._wc_pump_on = True
                    def _wc_pump():
                        try:
                            winchrome.pump_events(self.root)
                        except Exception:
                            pass
                        self.root.after(33, _wc_pump)
                    self.root.after(33, _wc_pump)
        # 「还原防黑帧」：点任务栏图标把窗口还原回来时，Windows 先丢弃又重建
        # 窗口表面，Tk 要等 WM_PAINT 才重绘 → 露约 110ms 纯黑（真机实测
        # 47~150ms，mean=0）。这里起一个常驻轮询，一见窗口回来就抢画。
        # 注意：任务栏最小化**不发 <Unmap>**、state() 也一直报 'normal'，
        # 所以只能轮询 IsIconic，详见 _watch_restore。
        self._watch_restore()
        # 搜索框 / 结果内过滤框 focus 描边渐变
        self._bind_input_focus_anim()
        # 结果树行 hover 高亮
        self._bind_tree_row_hover()
        # 分栏线 hover 显蓝
        self._bind_sash_hover()
        # 恢复 PanedWindow 分栏比例（必须在窗口 map 后，否则 sashpos 不可用）
        self.root.after(200, self._restore_pane_sash)
        # 启动后检测空库管理 → 显示引导
        self.root.after(100, self._check_empty_library)
        # 恢复上次浏览位置（关键词+文件+行号）
        # 若上次有关键词，恢复后清空回空态，避免旧结果占屏
        self.root.after(300, self._restore_session)
        # 体检：错误角标 + 需处理文件角标（延迟5秒，避开启动高峰）
        self.root.after(5000, self._refresh_err_count)
        # 待入库文件检查（延迟 2s 不挡启动）
        self.root.after(2000, self._check_inbox_on_startup)
        # 主窗口就绪，关闭 PyInstaller 启动画面（打包环境才有，不关会停留到程序退出）
        try:
            import pyi_splash
            pyi_splash.close()
        except Exception:
            pass

    def _build_guide(self):
        """空库引导覆盖层：大虚线拖放卡片（默认隐藏，place 覆盖主区）"""
        # cursor='arrow'：覆盖层是 main_pane 的动态子控件，不钉死会继承
        # 分栏条的双箭头光标（理由同 _bind_sash_hover）
        g = tk.Frame(self.main_pane, bd=0, highlightthickness=0,
                     cursor='arrow')
        cv = tk.Canvas(g, highlightthickness=0, bd=0)
        cv.pack(fill='both', expand=True, padx=40, pady=30)
        inner = tk.Frame(cv, bd=0, highlightthickness=0)
        c = self._theme_colors()
        cv.configure(bg=c['preview_bg'])
        inner.configure(bg=c['preview_bg'])
        item = cv.create_window(0, 0, window=inner, anchor='center')
        # 图标放在固定高度容器里用 place 定位，漂浮时不触发整卡重排
        icon_holder = tk.Frame(inner, height=78, bg=c['preview_bg'])
        icon_holder.pack(pady=(0, 6))
        icon_holder.pack_propagate(False)
        big = tk.Label(icon_holder, text=self.IC_DOWNLOAD,
                       font=self.FONT_ICON_BIG,
                       bg=c['preview_bg'], fg=c['chap_fg'])
        big.place(relx=0.5, rely=0.5, anchor='center')
        tk.Label(inner, text='把文件拖到这里即可入库',
                 font=('Microsoft YaHei UI', 16, 'bold'),
                 bg=c['preview_bg'], fg=c['preview_fg']
                 ).pack(pady=2)
        tk.Label(inner, text='支持 PDF / Word / Excel / TXT / 图片，可同时拖入多个\n'
                             '入库后输入关键词，立刻定位到具体条款出处',
                 font=self.FONT_UI, bg=c['preview_bg'], fg=c['meta_fg'],
                 justify='center').pack(pady=(4, 18))
        row = tk.Frame(inner, bg=c['preview_bg'])
        row.pack()
        ttk.Button(row,
                   text=f'{self.IC_DOC} 选择文件入库',
                   style='Icon.Accent.TButton',
                   command=self.import_files_dialog
                   ).pack(side='left', padx=8)
        ttk.Button(row,
                   text=f'{self.IC_FOLDER_ADD} 扫描入库',
                   style='Icon.TButton',
                   command=self.choose_dir
                   ).pack(side='left', padx=8)

        self._guide_animating = False

        def _redraw(_evt=None):
            cv.delete('dash')
            w = cv.winfo_width()
            h = cv.winfo_height()
            # 入场动画进行中由补间控制 item 位置，避免 Configure 抢坐标
            if not self._guide_animating:
                cv.coords(item, w // 2, h // 2)
            cv.create_rectangle(8, 8, w - 8, h - 8, dash=(7, 6),
                                outline=c['meta_fg'], width=2, tags='dash')
            cv.tag_lower('dash')
        cv.bind('<Configure>', _redraw)
        self.guide = g
        self._guide_cv = cv
        self._guide_item = item
        self._guide_icon = big

    def _show_guide(self):
        if not hasattr(self, 'guide'):
            self._build_guide()
        self.guide.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.guide.lift()
        self.guide.update_idletasks()
        cv = self._guide_cv
        w, h = cv.winfo_width(), cv.winfo_height()
        if w > 1 and h > 1 and self._anim_ok():
            # 卡片从下方 14px 弹入（ease-out）
            self._guide_animating = True
            cv.coords(self._guide_item, w // 2, h // 2 + 14)
            tw = anim.Tweener(cv)

            def _done():
                self._guide_animating = False
            tw.tween(h // 2 + 14, h // 2, 220,
                     lambda _t, ny: cv.coords(self._guide_item,
                                              w // 2, int(ny)),
                     _done, easing='ease_out_cubic')
        # 图标缓慢上下漂浮
        fl = getattr(self, '_guide_float', None)
        if fl is None and anim is not None:
            fl = anim.FloatLoop(
                cv, lambda dy: self._guide_icon.place_configure(
                    y=int(dy - 6)),
                amplitude=7, period_ms=2000)
            self._guide_float = fl
        if fl is not None and self._anim_ok():
            fl.start()






    # -------- 入库前预览确认 + 可中断进度窗 --------

    _PLAN_LABELS = {
        'new': ('新增', 'normal'),
        'dup_skip': ('内容重复·跳过', 'dup'),
        'conflict': ('同名不同内容', 'conflict'),
        'reindex': ('重建索引', 'normal'),
        'linked': ('已注册·重建', 'normal'),
        'new_link': ('新增链接', 'normal'),
        'error': ('无法读取', 'dup'),
    }

    def _start_import_flow(self, paths, link_mode):
        """拖拽/选文件统一入口：后台分析 → 预览确认 → 进度窗执行"""
        # 同 realpath 去重
        seen, files = set(), []
        for p in paths:
            try:
                rp = os.path.realpath(p)
            except Exception:
                rp = p
            if rp not in seen:
                seen.add(rp)
                files.append(p)
        if not files:
            return
        stop_plan = threading.Event()
        # 分析窗
        pw = tk.Toplevel(self.root)
        pw.title('入库分析')
        self._center_win(pw, 440, 190, modal=False)   # 长时进度，不锁主界面
        pw.transient(self.root)
        pw.resizable(False, False)
        self._dialog_header(pw, self.IC_FOLDER_ADD, '入库分析',
                            '正在分析拖入的文件…')
        pname = ttk.Label(pw, text='', foreground='gray')
        pname.pack(anchor='w', padx=16, pady=(10, 0))
        pbar = ttk.Progressbar(pw, mode='determinate', maximum=len(files),
                               style='Brand.Horizontal.TProgressbar')
        pbar.pack(fill='x', padx=16, pady=8)

        def _cancel_plan():
            stop_plan.set()
            pw.destroy()

        ttk.Button(pw, text='取消', style='Danger.TButton', command=_cancel_plan).pack(
            anchor='e', padx=16, pady=(0, 10))

        def _plan():
            plans = []
            for i, p in enumerate(files, 1):
                if stop_plan.is_set():
                    return
                try:
                    size = os.path.getsize(p)
                except OSError:
                    size = 0
                try:
                    if link_mode:
                        action = ('linked' if is_registered(DB_PATH, p)
                                  else 'new_link')
                        pre = (action, p, None)
                    else:
                        pre = plan_import(p, DB_PATH)
                        action = pre[0]
                except Exception:
                    pre = ('error', p, None)
                    action = 'error'
                plans.append((p, size, pre))
                self.root.after(
                    0,
                    lambda i=i, p=p: (
                        pbar.configure(value=i),
                        pname.configure(
                            text=f'{i}/{len(files)}  {os.path.basename(p)}'),
                    ) if pw.winfo_exists() else None)
            self.root.after(0, lambda: self._show_import_confirm(
                pw, plans, link_mode))

        threading.Thread(target=_plan, daemon=True).start()

    def _show_import_confirm(self, plan_win, plans, link_mode):
        """分析结果确认窗：勾选要入库的文件，重复项默认不选"""
        try:
            plan_win.destroy()
        except Exception:
            pass
        if not plans:
            return
        dlg = tk.Toplevel(self.root)
        dlg.title('确认入库')
        # Treeview 每行行高 28px，14 行就要 392px；加按钮栏/heading/padding 至少 560px
        n_items = len(plans)
        tv_h = min(max(n_items + 2, 5), 16)   # 动态：文件数 + 2 留白，5~16 之间
        dlg_h = tv_h * 28 + 174   # 树 + 统一头部/按钮/padding
        dlg_h = min(max(dlg_h, 534), 674)   # 保底 534，封顶 674
        self._center_win(dlg, 780, dlg_h)
        dlg.transient(self.root)
        self._bind_esc_close(dlg)
        self._dialog_header(dlg, self.IC_FOLDER_ADD, '确认入库',
                            '勾选要入库的文件（内容重复的已自动取消勾选）')
        holder = ttk.Frame(dlg)
        holder.pack(fill='both', expand=True, padx=12)
        tree = ttk.Treeview(holder, columns=('size', 'result'),
                            show='tree headings', height=tv_h)
        tree.heading('#0', text='文件名')
        tree.heading('size', text='大小')
        tree.heading('result', text='检测结果')
        tree.column('#0', width=480)
        tree.column('size', width=90, anchor='e')
        tree.column('result', width=150)
        sb = ttk.Scrollbar(holder, command=tree.yview)
        tree.configure(yscrollcommand=sb.set)
        sb.pack(side='right', fill='y')
        tree.pack(side='left', fill='both', expand=True)
        tree.tag_configure('dup', foreground='#999999')
        tree.tag_configure('conflict', foreground='#C13A28')
        tree.tag_configure('checked', foreground='#1a1a1a')

        checked = set()
        iid_path = {}
        for p, size, pre in plans:
            action = pre[0]
            lbl, tag = self._PLAN_LABELS.get(action, (action, 'normal'))
            default_on = action not in ('dup_skip', 'error')
            mark = '☑ ' if default_on else '☐ '
            iid = tree.insert(
                '', 'end', text=mark + os.path.basename(p),
                values=(fmt_size(size), lbl),
                tags=(tag,))
            iid_path[iid] = (p, pre)
            if default_on:
                checked.add(iid)

        def _toggle(_evt=None):
            for iid in tree.selection():
                if iid in checked:
                    checked.discard(iid)
                    tree.item(iid,
                        text='☐ ' + tree.item(iid, 'text')[2:])
                else:
                    checked.add(iid)
                    tree.item(iid,
                        text='☑ ' + tree.item(iid, 'text')[2:])
            _refresh_count()

        tree.bind('<ButtonRelease-1>', _toggle)
        bar = ttk.Frame(dlg, padding=(12, 6, 12, 10))
        bar.pack(fill='x')
        cnt_lbl = ttk.Label(bar, text='')
        cnt_lbl.pack(side='left')

        def _all(on):
            checked.clear()
            for iid in iid_path:
                if on:
                    checked.add(iid)
                tree.item(iid, text=('☑ ' if on else '☐ ')
                               + tree.item(iid, 'text')[2:])
            _refresh_count()

        ttk.Button(bar, text='全选', command=lambda: _all(True)).pack(
            side='left', padx=4)
        ttk.Button(bar, text='清空', command=lambda: _all(False)).pack(
            side='left')

        def _refresh_count():
            cnt_lbl.configure(text=f'已选 {len(checked)} / {len(plans)} 份')

        _refresh_count()

        def _cancel():
            dlg.destroy()

        def _confirm():
            chosen = [iid_path[iid] for iid in checked]
            if not chosen:
                messagebox.showinfo('入库', '未选择任何文件', parent=dlg)
                return
            dlg.destroy()
            files = [p for p, _pre in chosen]
            preplans = {p: pre for p, pre in chosen}
            self._run_import_progress(files, link_mode, preplans)

        ttk.Button(bar, text='开始入库', style='Accent.TButton', command=_confirm).pack(
            side='right', padx=4)
        ttk.Button(bar, text='取消', style='Danger.TButton', command=_cancel).pack(
            side='right')

    def _run_import_progress(self, files, link_mode, preplans,
                             title='正在入库', ext_stop_event=None,
                             on_done=None):
        """通用执行阶段：进度窗 + 当前文件名 + 速度/剩余时间 + 可中断 +
        可「后台运行」最小化到托盘。

        扫描入库与拖拽/选文件入库共用本方法。
        title: '正在入库' / '正在扫描入库' 等，决定窗口标题与提示文案
        ext_stop_event: 外部预创建的 stop_event（扫描入库用，让进度窗的
            取消按钮与扫描按钮的停止按钮联动同一事件）
        on_done: 工作结束后的额外回调（如 self._scan_finished 恢复扫描
            按钮文本），无返回参数
        """
        stop_event = ext_stop_event if ext_stop_event is not None \
            else threading.Event()
        self._task_busy = True
        # 清旧的后台按钮引用（上次进度窗被 destroy 后残留）
        self._progress_bg_btn = None
        pw = tk.Toplevel(self.root)
        pw.title(title)
        self._center_win(pw, 540, 280, modal=False)   # 长时进度，不锁主界面
        pw.transient(self.root)
        pw.protocol('WM_DELETE_WINDOW', lambda: None)  # 防误关
        self._dialog_header(
            pw, self.IC_DOWNLOAD, title,
            '可随时取消，已完成的文件会保留')
        name_lbl = ttk.Label(pw, text='', foreground='gray')
        name_lbl.pack(anchor='w', padx=16, pady=(12, 0))
        pbar = ttk.Progressbar(pw, mode='determinate', maximum=len(files),
                               style='Brand.Horizontal.TProgressbar')
        pbar.pack(fill='x', padx=16, pady=8)
        pct_lbl = ttk.Label(pw, text=f'0 / {len(files)}（0%）')
        pct_lbl.pack(anchor='w', padx=16)
        # 新增：速度 / 已处理大小 / 剩余时间
        info_frame = ttk.Frame(pw)
        info_frame.pack(fill='x', padx=16, pady=(2, 6))
        size_lbl = ttk.Label(info_frame, text='已处理 0 B / 0 B',
                             foreground='gray')
        size_lbl.pack(anchor='w')
        meta_row = ttk.Frame(info_frame)
        meta_row.pack(fill='x')
        speed_lbl = ttk.Label(meta_row, text='速度 —', foreground='gray')
        speed_lbl.pack(side='left')
        eta_lbl = ttk.Label(meta_row, text='剩余 —', foreground='gray')
        eta_lbl.pack(side='right')

        # 统计基线
        import time as _time
        t0 = _time.time()
        bytes_done = [0]           # 用 list 闭包可变
        bytes_total = 0
        for _p in files:
            try:
                bytes_total += os.path.getsize(_p)
            except OSError:
                pass

        # 按钮区：[后台运行] ←空→ [取消]
        btn_bar = ttk.Frame(pw, padding=(16, 0, 16, 10))
        btn_bar.pack(fill='x')
        bg_btn = ttk.Button(btn_bar,
                            text=f'{self.IC_FOLDER} 后台运行',
                            style='Icon.TButton', command=lambda: None)
        bg_btn.pack(side='left')
        self._progress_bg_btn = bg_btn   # 存引用，恢复时 re-enable
        cancel_btn = ttk.Button(btn_bar, text='取消', style='Danger.TButton', command=lambda: None)
        cancel_btn.pack(side='right')

        def _cancel():
            stop_event.set()
            pct_lbl.configure(text='正在停止…')
            cancel_btn.configure(state='disabled')

        cancel_btn.configure(command=_cancel)

        def _to_bg():
            """点击「后台运行」：隐藏主窗 + 进度窗 → 显示托盘图标"""
            if not _TRAY_OK or self._bg_mode:
                # 不可用回退：仅最小化（先抓冻结帧，还原时才有遮罩可贴）
                try:
                    self._grab_restore_cover()
                    pw.iconify()
                    self.root.iconify()
                except Exception:
                    pass
                return
            bg_btn.configure(state='disabled')
            ok = self._minimize_to_tray(pw, stop_event)
            if not ok:
                # 托盘启动失败 → 回退到最小化
                bg_btn.configure(state='normal')
                try:
                    self._grab_restore_cover()
                    pw.iconify()
                    self.root.iconify()
                except Exception:
                    pass

        bg_btn.configure(command=_to_bg)
        if not _TRAY_OK:
            bg_btn.configure(state='disabled')

        # UI 节流状态：100ms 内最多刷一次 GUI
        _ui_throttle = {'pending': False, 'last_done': 0, 'last_name': '',
                        'last_label': '', 'last_path': None}

        def _ui_cb(done, total, name, label, path=None):
            # 累计已处理字节
            if path:
                try:
                    bytes_done[0] += os.path.getsize(path)
                except OSError:
                    pass
            # 节流：100ms 内只更新一次 GUI，避免大批量导入时主线程被 after 淹没
            _ui_throttle['last_done'] = done
            _ui_throttle['last_name'] = name
            _ui_throttle['last_label'] = label
            _ui_throttle['last_path'] = path
            if _ui_throttle['pending']:
                return
            _ui_throttle['pending'] = True

            def _upd():
                _ui_throttle['pending'] = False
                done = _ui_throttle['last_done']
                name = _ui_throttle['last_name']
                label = _ui_throttle['last_label']
                if self._bg_mode:
                    # 后台模式只更 tooltip，不刷 GUI
                    if self._tray:
                        try:
                            self._tray.update_tip(
                                f'{done}/{total} 份 · {name}')
                        except Exception:
                            pass
                    return
                if not pw.winfo_exists():
                    return
                pbar.configure(value=done)
                pct = (done * 100 // total) if total else 0
                pct_lbl.configure(text=f'{done} / {total}（{pct}%）')
                name_lbl.configure(text=f'{name}（{label}）')
                # 速度 / 剩余 / 大小
                elapsed = _time.time() - t0
                bd = bytes_done[0]
                bt = bytes_total
                size_lbl.configure(
                    text=f'已处理 {fmt_size(bd)} / {fmt_size(bt)}')
                if elapsed > 0.3 and bd > 0 and bt > 0:
                    avg = bd / elapsed
                    remain_s = (bt - bd) / avg if avg > 0 else 0
                    speed_lbl.configure(text=f'速度 {fmt_size(avg)}/s')
                    eta_lbl.configure(text=f'剩余 {fmt_eta(remain_s)}')
                elif elapsed > 0.3 and done > 0 and total > 0:
                    avg_f = done / elapsed
                    remain_f = (total - done) / avg_f if avg_f > 0 else 0
                    speed_lbl.configure(text=f'速度 {avg_f:.1f} 份/s')
                    eta_lbl.configure(text=f'剩余 {fmt_eta(remain_f)}')
                # 同步更新主窗状态栏和进度条（同样节流，不另起 after）
                try:
                    self.status.configure(
                        text=f'入库 {done}/{total}: {name} ({label})')
                    self._set_progress(done * 100 // total if total else 0)
                except Exception:
                    pass

            # 100ms 节流：用 after 延迟，取消之前的延迟调用
            if hasattr(self, '_ui_throttle_job'):
                try:
                    self.root.after_cancel(self._ui_throttle_job)
                except Exception:
                    pass
            self._ui_throttle_job = self.root.after(100, _upd)

        def _work():
            result = self._import_batch_loop(
                files, link_mode, stop_event=stop_event,
                ui_cb=_ui_cb, preplans=preplans)
            def _done():
                # 后台运行状态：托盘气泡 + 自动恢复
                was_bg = self._bg_mode
                if was_bg:
                    try:
                        pw.deiconify()
                    except Exception:
                        pass
                try:
                    pw.destroy()
                except Exception:
                    pass
                # 清理 bg_btn 引用
                self._progress_bg_btn = None
                if result is None:
                    self._task_busy = False
                    self._set_status('已取消入库', 'idle')
                    if was_bg:
                        self._tray_balloon('已取消', '入库已取消',
                                           'info')
                        self.root.after(800, self._restore_from_tray)
                    else:
                        self._show_toast('已取消入库', 'idle')
                    return
                self._set_progress(100)
                if result['ok']:
                    self.preview.delete('1.0', 'end')
                    self._hide_guide()
                lw = getattr(self, '_lib_win', None)
                if lw is not None and lw.winfo_exists() and result['ok']:
                    self._refresh_library_win(lw)
                msg = (f"入库完成：成功 {result['ok']} 份"
                       + (f"，重复跳过 {result['dup']} 份" if result['dup'] else '')
                       + (f"，跳过 {result['skipped']} 份"
                          if result['skipped'] else '')
                       + (f"，失败 {result['failed']} 份"
                          if result['failed'] else ''))
                self._set_status(msg, 'error' if result['failed'] else 'ok')
                # —— 跳过/失败明细报告弹窗 ——
                detail = result.get('skipped_detail') or []
                need_report = bool(detail)
                if was_bg:
                    self._tray_balloon(
                        '入库完成', msg,
                        'info' if not result['failed'] else 'warn')
                    # 后台恢复后再弹报告（避免和 restore 冲突）
                    if need_report:
                        self.root.after(1800, lambda: (
                            self._restore_from_tray(),
                            self._show_import_report(result)))
                    else:
                        self.root.after(1500, self._restore_from_tray)
                elif need_report:
                    self._show_import_report(result)
                elif result['failed']:
                    messagebox.showinfo('入库完成（有失败）', msg,
                                        parent=self.root)
                else:
                    self._show_toast(msg, 'ok')
                if result['imported_ok']:
                    self._check_uncategorized(result['imported_ok'])
                # 扫描入库等外部场景的收尾回调（恢复按钮文本等）
                self._task_busy = False
                self._refresh_err_count()
                if on_done is not None:
                    try:
                        on_done()
                    except Exception:
                        pass
            self.root.after(0, _done)

        threading.Thread(target=_work, daemon=True).start()

    def _show_import_report(self, result):
        """入库完成后弹可滚动明细列表：显示所有跳过/重复/失败/超时文件及原因。"""
        detail = result.get('skipped_detail') or []
        if not detail:
            return
        dlg = tk.Toplevel(self.root)
        dlg.title('入库明细报告')
        dlg.transient(self.root)
        # 预估：每条明细 2 行（路径+原因），每行 ~16px（Consolas 9pt + padding）
        # 再加顶部汇总 + 底部按钮栏 ≈ 160px
        n_detail = len(detail)
        text_lines = min(max(n_detail * 2 + 2, 6), 24)
        # Text height 单位是行高，Consolas 9pt 约 16px/行
        est_h = text_lines * 16 + 214
        est_h = min(max(est_h, 454), 674)
        self._center_win(dlg, 660, est_h, modal=True)
        dlg.grab_set()
        self._dialog_header(dlg, self.IC_DOC, '入库明细报告')
        # 顶部汇总
        top = ttk.Frame(dlg, padding=(12, 8))
        top.pack(fill='x')
        ttk.Label(top, text='入库完成：', font=('Microsoft YaHei UI', 10, 'bold')
                  ).pack(side='left')
        parts = [f'{self.IC_CHECK} 成功 {result["ok"]}']
        if result['dup']:
            parts.append(f'{self.IC_WARN} 重复 {result["dup"]}')
        if result['skipped']:
            parts.append(f'{self.IC_SKIP} 跳过 {result["skipped"]}')
        if result['failed']:
            parts.append(f'{self.IC_CAP_CLOSE} 失败 {result["failed"]}')
        ttk.Label(top, text='  |  '.join(parts),
                  foreground='#555').pack(side='left')
        ttk.Label(top, text=f'共处理 {result.get("total", 0)} 份',
                  foreground='#999').pack(side='right')

        # 可滚动列表
        body = ttk.Frame(dlg)
        body.pack(fill='both', expand=True, padx=12, pady=(0, 8))
        sb = ttk.Scrollbar(body, orient='vertical')
        txt = tk.Text(body, wrap='none', height=text_lines,
                      font=('Consolas', 9),
                      background='#F4F6F9', foreground='#333',
                      yscrollcommand=sb.set)
        sb.configure(command=txt.yview)
        txt.pack(side='left', fill='both', expand=True)
        sb.pack(side='right', fill='y')

        # 分类着色
        txt.tag_configure('dup', foreground='#e67e22')
        txt.tag_configure('skip', foreground='#3498db')
        txt.tag_configure('fail', foreground='#c0392b')
        txt.tag_configure('timeout', foreground='#8e44ad')
        txt.tag_configure('path', foreground='#222')

        for path, reason in detail:
            tag = 'skip'
            rl = reason[:80]
            if '重复' in reason:
                tag = 'dup'
            elif '失败' in reason or '异常' in reason or 'error' in reason.lower():
                tag = 'fail'
            elif 'timeout' in reason.lower() or '超时' in reason:
                tag = 'timeout'
            txt.insert('end', f'[{reason[:12]}] ', tag)
            txt.insert('end', f'{os.path.basename(path)}\n', 'path')
            txt.insert('end', f'  → {rl}\n', tag)

        txt.configure(state='disabled')  # 只读

        # 底部：复制 + 关闭
        bot = ttk.Frame(dlg, padding=(12, 4, 12, 10))
        bot.pack(fill='x')

        def _copy_all():
            lines = [f'# 入库明细报告',
                     f'# 成功 {result["ok"]} / 重复 {result["dup"]} / '
                     f'跳过 {result["skipped"]} / 失败 {result["failed"]}',
                     '']
            for path, reason in detail:
                lines.append(f'[{reason}] {path}')
            try:
                self.root.clipboard_clear()
                self.root.clipboard_append('\n'.join(lines))
                self.root.update()
                self._show_toast('已复制明细到剪贴板', 'ok')
            except Exception:
                pass

        ttk.Button(bot, text=f'{self.IC_COPY} 复制全部到剪贴板',
                   command=_copy_all).pack(side='left')
        ttk.Button(bot, text='关闭',
                   command=dlg.destroy).pack(side='right')



    # ---------- 系统托盘 · 后台运行 ----------

    def _watch_restore(self):
        """常驻轮询 + 冻结帧遮罩：消掉「最小化 → 还原」那一瞬的纯黑。

        黑帧根因（真机 + 真实索引，PIL 逐帧采样）：点任务栏图标还原后
        47~150ms 整窗纯黑（mean=0），157ms 才画出内容 —— Windows 在最小化时
        丢弃了窗口的重定向表面，还原过渡动画合成的是张黑快照。

        触发点不能用 <Unmap>：实测任务栏最小化**根本不发 <Unmap>**，Tk 的
        wm state() 也全程返回 'normal'（Tk 只记录自己请求的状态，不跟 Windows
        的 IsIconic 走），所以只能常驻轮询 IsIconic。

        纯抢画（连续 update()）不够：实测 94~110ms 时窗口表面已有内容，屏幕
        仍是黑的 —— 说明黑帧来自系统过渡动画本身，不是 Tk 重绘慢。因此改用
        「冻结帧遮罩」：最小化生效**之前**抓一张干净画面，还原当刻把这个置顶
        无边框窗贴上去，等真窗口画好再撤掉（实测零黑帧）。
        """
        self._restore_hidden = False
        self._restore_keep = 0
        self._restore_hw = None
        # 冻结帧遮罩（启动即建好并 withdraw，避免最小化当刻才建窗造成卡顿）
        self._cover = None
        self._cover_img = None
        self._cover_photo = None
        self._cover_rect = None
        self._cover_dirty = False
        self._reguard_at = 0.0
        self._build_cover()
        try:
            winchrome.set_minimize_guard(self.root, True)
        except Exception:
            pass
        self.root.after(RESTORE_POLL_MS, self._poll_restore)


    def _grab_restore_cover(self):
        """抓一张当前画面存成冻结帧（在最小化真正执行之前调用）。

        本方法只在主线程正常上下文里被调用（_poll_restore / _minimize_to_tray），
        不在 Win32 窗口过程里 —— 窗口过程里做 ctypes 调用会因嵌套回调丢线程
        状态而崩溃，见 winchrome._MIN_GUARDS。
        """
        try:
            hwnd = self._restore_hwnd()
            if not hwnd:
                return
            rc = winchrome.window_rect(hwnd)
            if not rc:
                return
            w, h = rc[2] - rc[0], rc[3] - rc[1]
            # 矩形先落定并告知 winchrome：即使抓帧失败，遮罩也按此矩形盖
            # 纯色纸底（好过露黑帧），SC_RESTORE 预贴依赖这个矩形。
            self._cover_rect = (w, h, rc[0], rc[1])
            try:
                winchrome.set_cover_rect(self._cover_rect)
            except Exception:
                pass
            img = _grab_screen_region(rc[0], rc[1], w, h)
            if img is None:
                # 抓不到帧：清掉旧图（避免错位旧帧），走纯色遮罩兜底
                self._cover_img = None
                self._cover_photo = None
                self._cover_dirty = False
                return
            self._cover_img = img
            self._cover_photo = None
            self._cover_dirty = True      # 转 PhotoImage 交给轮询在主线程做
        except Exception:
            self._cover_img = None

    def _prepare_cover_photo(self):
        """把冻结帧转成 PhotoImage 并挂到遮罩 Label（正常 Tk 上下文，提前做好）"""
        img = getattr(self, '_cover_img', None)
        self._cover_dirty = False
        if img is None or self._cover is None:
            return
        try:
            from PIL import ImageTk
            self._cover_photo = ImageTk.PhotoImage(img, master=self.root)
            self._cover_label.configure(image=self._cover_photo)
            # 趁还在屏幕外，先按目标尺寸把表面建好、画好（此时距离还原还有
            # 几百 ms）；还原当刻只挪位置、不 resize，避免又出现一帧未初始化
            if self._cover_rect:
                w, h = self._cover_rect[0], self._cover_rect[1]
                self._cover.geometry('%dx%d+-40000+-40000' % (w, h))
        except Exception:
            self._cover_photo = None




    def _poll_restore(self):
        import ctypes
        # ① 刚拦下一次最小化：趁窗口还在屏幕上先抓冻结帧（必须在窗口过程
        #    之外做，见 winchrome._MIN_GUARDS），然后撤守卫 + 投递最小化，
        #    由 Tk 自己的消息泵去派发（不能在这里同步最小化，会 GIL 崩）
        try:
            if winchrome.take_minimize_request(self.root):
                self._grab_restore_cover()
                winchrome.set_minimize_guard(self.root, False)
                winchrome.minimize_now(self.root)
                self._reguard_at = time.monotonic() + 0.2
        except Exception:
            pass
        # 守卫晚一点再装回来：太早装回会把刚投递出去的那次最小化又拦掉
        if self._reguard_at and time.monotonic() >= self._reguard_at:
            self._reguard_at = 0.0
            try:
                winchrome.set_minimize_guard(self.root, True)
            except Exception:
                pass
        # ② 抓帧在主线程把它转成 PhotoImage（还原前有几百 ms，足够提前备好）
        if self._cover_dirty:
            self._prepare_cover_photo()
        hwnd = self._restore_hwnd()
        hidden = bool(hwnd) and bool(ctypes.windll.user32.IsIconic(hwnd))
        if not hidden:
            try:
                hidden = self.root.state() in ('iconic', 'withdrawn')
            except Exception:
                pass
        if hidden:
            self._restore_hidden = True
            self._restore_keep = 0
            try:
                winchrome.set_cover_armed(True)
            except Exception:
                pass
            self._hide_cover()
        elif self._restore_hidden:
            # 刚还原：立刻贴冻结帧盖住黑帧，同时连续抢画让真窗口尽快画好
            self._restore_hidden = False
            self._restore_keep = RESTORE_KEEP_TICKS
            self._show_cover()
        if self._restore_keep > 0:
            self._restore_keep -= 1
            self._force_paint()
            if self._restore_keep == 0:
                self._hide_cover()
                try:
                    winchrome.set_cover_armed(False)
                except Exception:
                    pass
        self.root.after(RESTORE_POLL_MS, self._poll_restore)





    def _ask_close_choice(self):
        """本次启动第一次点关闭叉时询问：「最小化到托盘」还是「直接退出」。

        返回 'tray' / 'exit'；关掉对话框（X 或 Esc）= 取消，返回 None。
        只影响本次会话（调用方写入 self._close_choice，不落盘），
        彻底退出进程后自然失效，下次启动重新询问。
        """
        ans = {'v': None}
        top = tk.Toplevel(self.root)
        top.title('关闭窗口')
        top.resizable(False, False)
        top.transient(self.root)
        self._center_win(top, 420, 220)
        self._dialog_header(top, self.IC_WARN, '关闭窗口后怎么处理？',
                            '本次启动内沿用你的选择；彻底退出后下次重新询问')

        body = ttk.Frame(top, padding=(16, 12, 16, 4))
        body.pack(fill='both', expand=True)
        ttk.Label(body, justify='left', text=(
            '· 最小化到托盘：窗口收进托盘继续后台运行\n'
            '（双击托盘图标可随时唤回）\n'
            '· 直接退出：结束程序，正在进行的入库任务会中断')).pack(
                anchor='w')

        bar = ttk.Frame(top, padding=(12, 10))
        bar.pack(side='bottom', fill='x')

        def choose(v):
            ans['v'] = v
            top.destroy()

        # 两个按钮等宽（统一大小），默认推荐「最小化到托盘」用品牌色
        ttk.Button(bar, text='最小化到托盘', width=13, style='Accent.TButton',
                   command=lambda: choose('tray')).pack(side='right', padx=4)
        ttk.Button(bar, text='直接退出', width=13,
                   command=lambda: choose('exit')).pack(side='right', padx=4)
        top.protocol('WM_DELETE_WINDOW', top.destroy)
        top.bind('<Escape>', lambda e: top.destroy())
        try:
            top.wait_window()
        except Exception:
            pass
        return ans['v']



    def _tray_menu(self, x, y):
        """托盘右键菜单：显示主窗口 / 取消导入 / 退出程序。"""
        try:
            m = tk.Menu(self.root, tearoff=0)
            m.add_command(label='显示主窗口',
                          command=self._restore_from_tray)
            if self._pw_stop_event is not None:
                def _cancel_import():
                    try:
                        self._pw_stop_event.set()
                    except Exception:
                        pass
                m.add_command(label='取消导入', command=_cancel_import)
            m.add_separator()

            def _quit_app():
                # 触发主窗的关闭协议；先置位绕过「点叉隐藏到托盘」分支
                self._force_quit = True
                try:
                    self.root.protocol('WM_DELETE_WINDOW')()
                except Exception:
                    try:
                        self.root.destroy()
                    except Exception:
                        pass
            m.add_command(label='退出程序', command=_quit_app)
            m.tk_popup(x, y)
        except Exception:
            pass


    # ---------- 弹窗等级模态 ----------

    def _get_win(self, key):
        """功能窗口单例：同名窗口已存在则提到前台并返回 None；
        否则新建、登记、接管关闭清理后返回窗口。
        用法：win = self._get_win('library')
              if win is None: return"""
        old = self._wins.get(key)
        if old is not None:
            try:
                if old.winfo_exists():
                    try:
                        if old.state() == 'iconic':
                            old.deiconify()
                    except Exception:
                        pass
                    old.lift()
                    old.focus_force()
                    return None
            except Exception:
                pass
        win = tk.Toplevel(self.root)
        self._wins[key] = win

        def _on_close():
            self._wins.pop(key, None)
            win.destroy()
        win.protocol('WM_DELETE_WINDOW', _on_close)
        return win

    def _apply_modal(self, dlg):
        """弹窗等级限制：dlg 关闭前，其所有上级窗口被锁定——
        点击上级窗口任何位置只响铃并弹回弹窗（点不动），标题栏 X
        也无法越级关闭。dlg 关闭时自动逐级解锁。"""
        if not hasattr(self, '_modal_state'):
            self._modal_state = {}     # 上级窗口 → {'depth': int, 'orig': str}
            self._modal_overlays = {}  # 弹窗 id → [(上级窗口, 遮罩), ...]
        overlays = []
        p, seen = dlg.master, set()
        while isinstance(p, (tk.Toplevel, tk.Tk)):
            if id(p) in seen:
                break
            seen.add(id(p))
            try:
                ov = tk.Frame(p, bd=0, highlightthickness=0, bg=p.cget('bg'))
                ov.place(x=0, y=0, relwidth=1, relheight=1)
                ov.lift()
            except Exception:
                break

            def _deny(_e, pp=p):
                pp.bell()          # 越级点击：仅响铃，不产生任何操作
                try:
                    dlg.lift()
                    dlg.focus_force()
                except Exception:
                    pass
                return 'break'
            for seq in ('<Button-1>', '<Button-2>', '<Button-3>'):
                ov.bind(seq, _deny)
            # 越级关闭保护：按嵌套深度记录并恢复原关闭处理器
            st = self._modal_state.setdefault(p, {'depth': 0, 'orig': None})
            if st['depth'] == 0:
                st['orig'] = p.protocol('WM_DELETE_WINDOW') or ''
            st['depth'] += 1
            p.protocol('WM_DELETE_WINDOW',
                       lambda pp=p: self._deny_close(pp, dlg))
            overlays.append((p, ov))
            p = p.master
        if not overlays:
            return
        self._modal_overlays[id(dlg)] = overlays
        dlg.bind('<Destroy>', self._release_modal, add='+')
        try:
            dlg.lift()
            dlg.focus_force()
        except Exception:
            pass




    @staticmethod
    def _bind_esc_close(win):
        win.bind('<Escape>', lambda e: win.destroy())




    def _ask_conflict_policy(self, name):
        """同名冲突弹窗（主线程显示，工作线程阻塞等待）。
        返回 'replace' / 'both' / 'skip'"""
        ev = threading.Event()
        answer = {'v': 'skip'}

        def _dlg():
            dlg = tk.Toplevel(self.root)
            dlg.title('文件名冲突')
            self._center_win(dlg, 500, 380)   # 居中 + 锁定主界面
            dlg.transient(self.root)
            self._dialog_header(dlg, self.IC_WARN, '文件名冲突')
            ttk.Label(dlg, padding=(16, 12, 16, 4), justify='left', text=(
                f'库中已存在同名文件，但内容不同：\n{name}\n\n'
                '如何处理本次入库的所有同名文件？')).pack(anchor='w')
            ttk.Label(dlg, padding=(16, 0), foreground='#888', justify='left',
                      text='· 替换新版：旧文件移入回收站（可恢复）\n'
                           '· 保留两份：新文件自动加 _1 后缀\n'
                           '· 跳过：本次全部不导入').pack(anchor='w')
            bar = ttk.Frame(dlg, padding=12)
            bar.pack(side='bottom', fill='x')

            def choose(v):
                answer['v'] = v
                dlg.destroy()
                ev.set()
            ttk.Button(bar, text='替换新版', style='Accent.TButton',
                       command=lambda: choose('replace')).pack(side='right', padx=4)
            ttk.Button(bar, text='保留两份',
                       command=lambda: choose('both')).pack(side='right', padx=4)
            ttk.Button(bar, text='跳过',
                       command=lambda: choose('skip')).pack(side='right', padx=4)
            dlg.protocol('WM_DELETE_WINDOW', lambda: choose('skip'))

        self.root.after(0, _dlg)
        ev.wait()
        return answer['v']






    # -------- 章节目录侧栏 --------




    # -------- 扫描索引 --------



    def _scan_walk_thread(self, d, stop_event, link_mode):
        """扫描阶段一：walk 收集目录下所有文件 → 弹通用进度窗走入库流程。
        进度窗的工作线程会调用 _import_batch_loop，无需在此重复。"""
        import os as _os
        from indexer import IGNORE_DIRS
        all_files = []
        for dirpath, dirnames, files in _os.walk(d):
            if stop_event.is_set():
                # walk 期间被取消
                self.root.after(0,
                    lambda: (self._set_status('已停止扫描', 'idle'),
                             self._scan_finished()))
                return
            dirnames[:] = [x for x in dirnames if x.lower() not in IGNORE_DIRS]
            for fn in files:
                all_files.append(_os.path.join(dirpath, fn))
        # walk 完成 → 切到主线程弹进度窗（用同一 stop_event 联动停止按钮）
        self.root.after(0, lambda: self._run_import_progress(
            all_files, link_mode, {}, title='正在扫描入库',
            ext_stop_event=stop_event, on_done=self._scan_finished))


    def _badge_pop(self, btn, family=None, base=9):
        """角标首次出现：字号弹跳一次（uiverse badge bounce）"""
        if not self._anim_ok():
            return
        fam = family or self.FONT_UI[0]
        tw = anim.Tweener(self.root)
        tw.tween(base, base + 2.5, 90,
                 lambda _t, s: btn.config(font=(fam, int(s), 'bold')),
                 lambda: tw.tween(base + 2.5, base, 180,
                                  lambda _t, s: btn.config(
                                      font=(fam, int(s))),
                                  easing='ease_out_cubic'))


    def _refresh_prob_badge(self):
        """后台线程统计 缺失+无文本 文件数，完成后更新角标（不卡 UI）"""
        if not os.path.exists(DB_PATH):
            self.prob_btn.pack_forget()
            return

        def _work():
            n = 0
            try:
                for it in list_library(DB_PATH):
                    if not os.path.exists(it['path']) \
                            or (it.get('line_count') or 0) == 0:
                        n += 1
            except Exception:
                return
            try:
                self.root.after(0, lambda: self._apply_prob_badge(n))
            except Exception:
                pass
        threading.Thread(target=_work, daemon=True).start()


    # -------- 状态栏 --------
    _DOT_COLORS = {'idle': '#9AA0A6', 'busy': '#4E88C4',
                   'ok': '#34A853', 'error': '#EA4335'}

    def _set_status(self, text, kind='idle'):
        """更新状态文字 + 圆点颜色（idle/busy/ok/error），颜色渐变过渡"""
        try:
            self.status.configure(text=text)
            target = self._DOT_COLORS.get(kind, '#9AA0A6')
            if self._anim_ok():
                try:
                    cur = self.status_dot.itemcget(self._dot, 'fill')
                    if cur and cur != target:
                        anim.Tweener(self.root).tween_color(
                            cur, target, 160,
                            lambda _t, c: self.status_dot.itemconfigure(
                                self._dot, fill=c))
                        return
                except Exception:
                    pass
            self.status_dot.itemconfigure(self._dot, fill=target)
        except Exception:
            pass

    def _set_status_count(self, n, text_fn, kind='ok'):
        """状态栏「找到 N 条」数字从 0 滚动到 n；text_fn(v) 生成完整文案"""
        n = int(n)
        if n <= 0 or not self._anim_ok():
            self._set_status(text_fn(n), 'idle' if n <= 0 else kind)
            return
        # 先设圆点 + 起始文案
        self._set_status(text_fn(0), kind)
        anim.count_up(self.status,
                      lambda v: self.status.configure(text=text_fn(int(v))),
                      0, n, 300)

    def _update_dir_label(self):
        """目录过长时中间截断，悬停显示完整路径"""
        d = self.root_dir.get()
        if not d:
            self._dir_lbl.configure(text='')
            return
        if len(d) > 38:
            d_show = d[:15] + ' … ' + d[-20:]
        else:
            d_show = d
        self._dir_lbl.configure(text=self.IC_FOLDER + ' ' + d_show)
        Tooltip(self._dir_lbl, d)



    # -------- 浏览位置记忆（重启后回到上次看到的条款） --------




    # -------- Toast：右下角自动消失，不抢焦点 --------
    _TOAST_STYLE = {
        'ok':    ('#34A853', '✓ '),
        'error': ('#EA4335', '✕ '),
        'idle':  ('#5F6368', ''),
        'info':  ('#4E88C4', 'ℹ '),
    }

    def _show_toast(self, msg, kind='ok', ms=3200):
        try:
            color, icon = self._TOAST_STYLE.get(kind,
                                                self._TOAST_STYLE['ok'])
            dark = self.dark.get()
            box_bg = '#2D2E31' if dark else 'white'
            fg = '#E8EAED' if dark else '#202124'
            edge = '#4A4D51' if dark else '#DADCE0'
            t = tk.Toplevel(self.root)
            t.wm_overrideredirect(True)
            t.attributes('-topmost', True)
            bar = tk.Frame(t, bg=color, width=4)
            bar.pack(side='left', fill='y')
            box = tk.Frame(t, bg=box_bg, highlightbackground=edge,
                           highlightthickness=1)
            box.pack(side='left', fill='both', expand=True)
            lbl = tk.Label(box, text=icon + msg, bg=box_bg, fg=fg,
                           font=self.FONT_UI, padx=12, pady=9, justify='left',
                           wraplength=340)
            lbl.pack()

            def _place(dy=0):
                toasts = getattr(self, '_toasts', [])
                self.root.update_idletasks()
                w = t.winfo_reqwidth()
                h = t.winfo_reqheight()
                x = self.root.winfo_rootx() + self.root.winfo_width() - w - 18
                y = (self.root.winfo_rooty() + self.root.winfo_height()
                     - 18 - (h + 8) * (len(toasts) + 1))
                t.wm_geometry(f'+{x}+{y + dy}')
            toasts = getattr(self, '_toasts', [])
            toasts.append(t)
            self._toasts = toasts
            # 入场：淡入 + 上滑 12px（uiverse 通知动效）
            if self._anim_ok():
                t.attributes('-alpha', 0.0)
                _place(12)
                anim.fade_in(t, 180)
                _tw = anim.Tweener(t)
                _tw.tween(12, 0, 180,
                          lambda _t, v: (_place(int(v))
                                         if not getattr(t, '_closing', False)
                                         else None),
                          easing='ease_out_cubic')
            else:
                _place()

            def _dismiss(*_a):
                if getattr(t, '_closing', False):
                    return
                t._closing = True
                def _fin():
                    try:
                        t.destroy()
                    except Exception:
                        pass
                    if t in self._toasts:
                        self._toasts.remove(t)
                if self._anim_ok():
                    try:
                        anim.fade_out(t, 160, on_done=_fin)
                        return
                    except Exception:
                        pass
                _fin()
            for w in (t, bar, box, lbl):
                w.bind('<Button-1>', _dismiss)
            t.after(ms, _dismiss)
        except Exception:
            pass

    # -------- P1-11: 自动监听 --------



    def _toggle_watch(self):
        """勾选/取消勾选「自动监听」"""
        if self.autowatch.get():
            d = self.root_dir.get()
            if not d or not os.path.isdir(d):
                messagebox.showerror('错误', '请先选择要监听的目录')
                self.autowatch.set(False)
                return
            if not os.path.exists(DB_PATH):
                messagebox.showwarning('提示', '请先扫描建索引，再开启自动监听')
                self.autowatch.set(False)
                return
            # 启动 watcher
            if self.watcher and self.watcher.is_running():
                return
            self.watcher = FileWatcher(d, DB_PATH,
                                       on_event=self._on_watch_event)
            try:
                self.watcher.start()
                self.status.configure(text='自动监听已开启')
            except Exception as e:
                messagebox.showerror('监听失败', str(e))
                self.autowatch.set(False)
                self.watcher = None
        else:
            # 停止 watcher
            if self.watcher:
                try:
                    self.watcher.stop()
                except Exception:
                    pass
                self.watcher = None
            self.status.configure(text='自动监听已关闭')


    # ---- 入库后自动同步语义索引（本地 AI 增量更新） ----

    _QA_AUTO_LOCK = threading.Lock()   # 同一时刻只跑一轮自动索引


    def _auto_qa_work(self):
        try:
            import ai_qa
            db = self._qa_db()
            base = self.cfg.get('ollama_base') or 'http://127.0.0.1:11434'
            embed = self.cfg.get('ollama_embed') or 'bge-m3'
            # 建表（首次）+ 算增量：没有新东西就安静退出
            conn = sqlite3.connect(db, timeout=30)
            try:
                ai_qa.init_ai_tables(conn)
                todo, gone = ai_qa.build_plan(conn)
            finally:
                conn.close()
            if not todo and not gone:
                return
            # 确保 Ollama 在线（未运行自动拉起）；连不上只提醒一次
            try:
                ai_qa.list_models(base)
            except Exception:
                if not (ai_qa.try_launch_ollama()
                        and ai_qa.wait_ready(base, tries=60)):
                    if not getattr(self, '_qa_auto_warned', False):
                        self._qa_auto_warned = True
                        self.root.after(0, lambda: self._toast(
                            '新文件已入库；本地 AI 未就绪，'
                            '打开 AI 镜仔智答后会自动补齐索引'))
                    return
                ai_qa.list_models(base)
            tags = ai_qa.list_models(base)
            if embed not in tags and embed.split(':')[0] not in [
                    t.split(':')[0] for t in tags]:
                if not getattr(self, '_qa_auto_warned', False):
                    self._qa_auto_warned = True
                    self.root.after(0, lambda: self._toast(
                        '新文件已入库；向量模型未安装，'
                        '未自动更新 AI 镜仔智答索引'))
                return
            info = ai_qa.build_semantic_index(db, base, embed)
            n = (info or {}).get('chunks') or 0
            if n:
                self.root.after(0, lambda: self._toast(
                    f'新文件已同步到 AI 镜仔智答索引（{n} 条条款）'))

            def _refresh_qa():
                qw = getattr(self, '_qa_window', None)
                if qw is not None and qw.win.winfo_exists():
                    qw.refresh_index_label()
            self.root.after(0, _refresh_qa)
        except Exception as e:
            # 自动任务不打断入库主流程，但失败要露出（状态栏），避免静默丢索引
            err = (str(e) or e.__class__.__name__)[:60]

            def _note(msg=err):
                try:
                    self.status.configure(
                        text=f'AI 镜仔智答索引自动更新失败：{msg}')
                except Exception:
                    pass
            try:
                self.root.after(0, _note)
            except Exception:
                pass
        finally:
            self._QA_AUTO_LOCK.release()
            if getattr(self, '_qa_auto_pending', False):
                self._qa_auto_pending = False
                self.root.after(500, lambda: self._auto_qa_refresh(['again']))


    # -------- 搜索 --------
    _SPINNER_FRAMES = ('⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏')

    def _start_search_spinner(self):
        """搜索中：按钮文字换为旋转的 Braille spinner"""
        if not self._anim_ok():
            return
        self._spinner_i = 0
        self._spinner_on = True
        self.search_btn.configure(text=self._SPINNER_FRAMES[0] + ' 搜索中')

        def _tick():
            if not getattr(self, '_spinner_on', False):
                return
            self._spinner_i = (self._spinner_i + 1) % len(self._SPINNER_FRAMES)
            try:
                self.search_btn.configure(
                    text=self._SPINNER_FRAMES[self._spinner_i] + ' 搜索中')
            except Exception:
                return
            self._spinner_job = self.root.after(80, _tick)
        self._spinner_job = self.root.after(80, _tick)


    def do_search(self, auto=False):
        """读取参数后交给后台线程搜索，避免模糊模式全表扫描冻结界面

        auto=True 表示「输入停顿自动触发」：此时不做零结果模糊回退 ——
        用户在打字，每次停顿都跑两份全表扫（精确 + 模糊）纯属浪费，
        空结果卡片里保留的「改用模糊」按钮已能覆盖这个意图。"""
        # 新搜索开始 → 清空引用跳转历史栈（开启新会话）
        if self._prev_hist:
            self._prev_hist = []
            self.back_btn.configure(state='disabled')
        kw = self.keyword.get().strip()
        if not kw or kw == KW_HINT:
            return
        self._last_searched = kw   # 输入即搜去重：同词不重复搜
        if not os.path.exists(DB_PATH):
            messagebox.showwarning('提示', '请先扫描建索引')
            return
        mode = self.search_mode.get()
        oa = self.only_active.get()
        cs = self.cs.get()
        ft = self.file_type.get()
        # 同义词语义扩展：正向词 → {原词:[同义词,...]}（组内任一命中即算命中）
        syn_map = {}
        if self.syn_var.get():
            syn_map = expand_map(kw_terms(kw),
                                 self.cfg.get('custom_synonyms', ''))
        # 序号防乱序：连续搜索时只采用最后一次的结果
        self._search_seq = getattr(self, '_search_seq', 0) + 1
        seq = self._search_seq
        self.search_btn.configure(state='disabled')
        self._start_search_spinner()
        self._set_status(f'正在搜索「{kw[:30]}」…', 'busy')
        def _cancelled():
            """已被更新的搜索取代 → 后台线程应尽早放手，别和新搜索抢 IO"""
            return seq != self._search_seq

        def _worker():
            fuzzy_fallback = False
            try:
                if mode == 'fuzzy':
                    res = search_fuzzy(DB_PATH, kw, file_type=ft,
                                       only_active=oa, syn_map=syn_map,
                                       cancel=_cancelled)
                else:
                    res = search(DB_PATH, kw, case_sensitive=cs,
                                 file_type=ft, only_active=oa,
                                 syn_map=syn_map, cancel=_cancelled)
                    # 精确模式零结果 → 自动回退一次模糊（大小写敏感时不回退，
                    # 尊重用户的精确意图）。有结果则静默采用，状态栏低调提示。
                    if not res and not cs and not auto:
                        fz = search_fuzzy(DB_PATH, kw, file_type=ft,
                                          only_active=oa, syn_map=syn_map,
                                          cancel=_cancelled)
                        if fz:
                            res = fz
                            fuzzy_fallback = True
                # 先按文件聚合（全量），再只为「首屏会渲染的片段」取上下文：
                # 每个文件前 3 簇。避免旧逻辑 res[:500] 只覆盖前两个大文件。
                groups = aggregate_results(res)
                plan = []
                for g in groups:
                    for cl in g.get('clusters', [])[:3]:
                        for m in cl['lines']:
                            plan.append({'path': g['path'],
                                         'line_no': m['line_no']})
                ctx = fetch_context_lines(DB_PATH, plan, radius=1)
                # 过滤用小写文本在后台线程一次算好：主线程每敲一个过滤字符
                # 都全量 .lower() 是白费——命中行可达数万条。
                prepare_filter_cache(groups)
            except SearchCancelled:
                return   # 已有更新的搜索，本次结果作废（按钮/状态栏归新搜索管）
            except Exception as e:
                record_error(DB_PATH, 'search', e)   # 落库便于事后排查
                res, groups, ctx = e, [], {}
            self.root.after(0, lambda: self._search_done(
                seq, kw, mode, res, syn_map, ctx, groups,
                fuzzy_fallback=fuzzy_fallback))
        threading.Thread(target=_worker, daemon=True).start()





    def _search_done(self, seq, kw, mode, res, syn_map=None, ctx=None,
                     groups=None, fuzzy_fallback=False):
        """后台搜索完成：回到主线程填充结果"""
        self.search_btn.configure(state='normal')
        self._stop_search_spinner()
        if seq != getattr(self, '_search_seq', 0):
            return   # 已有更新的搜索，丢弃本次结果
        self._syn_map = syn_map or {}   # 预览区同义词橙色高亮用
        self._ctx_map = ctx or {}       # 命中行前后文 {(path,line):{prev,next}}
        if isinstance(res, Exception):
            self._set_status(f'搜索出错：{str(res)[:40]}', 'error')
            messagebox.showerror('搜索错误', str(res))
            self._refresh_err_count()   # 让「错误」角标立刻出现
            return
        self.results = res
        # 全量聚合：不再按列表顺序截断，避免「大部头汇编」独占配额而使
        # 其余文件整体消失（实测：搜「安全生产」曾只显示 2/113 个文件）。
        # 渲染侧由 _populate_tree 的「每文件前 3 簇 + 展开」控制列表长度。
        self.groups = groups if groups is not None \
            else aggregate_results(self.results)
        self._hit_capped = len(self.results) >= MAX_HITS
        # 模糊标注映射 (path, line_no) -> score（1.0 精确 / 0.8 一字之差）
        self._score_map = {(r['path'], r['line_no']): r.get('score', 1.0)
                           for r in self.results}
        self._cluster_rows = {}
        self._cluster_cross = {}
        # 清空结果内过滤输入框（新搜索重置）
        self.filter_var.set('')
        
        self._populate_tree()
        # 启动恢复：渲染完再定位到上次浏览的条款
        if getattr(self, '_pending_restore', None):
            self._apply_pending_restore()
        # 模糊模式下状态栏区分精确/模糊命中数
        trunc_tip = (f' · 命中数已达上限 {MAX_HITS}，请细化关键词'
                     if getattr(self, '_hit_capped', False) else '')
        syn_tip = syn_describe(syn_map) if syn_map else ''
        cross_n = sum(1 for r in self.results if r.get('cross_word'))
        cross_tip = f' · 跨行补回 {cross_n} 条（词被换行切断）' if cross_n else ''
        if mode == 'fuzzy':
            fz = sum(1 for r in self.results if r.get('score', 1.0) < 1.0)
            total = len(self.results)
            if fz:
                self._set_status_count(
                    total,
                    lambda v: f'找到 {v} 条（精确 {total - fz}'
                              f' · 模糊 {fz}）{trunc_tip}{syn_tip}{cross_tip}')
            else:
                self._set_status_count(
                    total,
                    lambda v: f'找到 {v} 条（全部精确命中）'
                              f'{trunc_tip}{syn_tip}{cross_tip}')
        elif fuzzy_fallback:
            fz = sum(1 for r in self.results if r.get('score', 1.0) < 1.0)
            self._set_status_count(
                len(self.results),
                lambda v: f'无精确结果，已显示 {v} 条相近结果'
                          f'（模糊 {fz} 条，片段前 ≈ 标记）'
                          f'{trunc_tip}{syn_tip}{cross_tip}')
        else:
            self._set_status_count(
                len(self.results),
                lambda v: f'找到 {v} 条{trunc_tip}{syn_tip}{cross_tip}')
        # 搜索 0 结果 → 可点击建议卡片
        if not self.results:
            self._cur_path = None
            self._cur_line = None
            self._cur_hit_lines = []
            self._cur_cross = None
            self._refresh_bookmark_btn()
            self._show_zero_results(kw, mode)
        # 更新统计条
        self._update_stats(kw)
        # P1-15: 保存关键词历史
        self._push_recent_keyword(kw)

    def _show_zero_results(self, kw, mode):
        """0 结果：预览区显示可点击的建议卡片（点一下直接执行，不用手动切开关）"""
        pv = self.preview
        pv.delete('1.0', 'end')
        self._zero_btns = []  # 保持按钮引用防 GC
        pv.insert('end', '\n\n', 'meta')
        pv.insert('end', f'   {self.IC_SEARCH} 未找到包含「{kw}」的内容\n\n', 'chap')

        def _add_button(txt, cmd, hint=None):
            b = ttk.Button(pv, text=txt, command=cmd, width=22)
            self._zero_btns.append(b)
            pv.window_create('end', window=b, padx=32, pady=2)
            if hint:
                pv.insert('end', '   ' + hint + '\n', 'meta')
            else:
                pv.insert('end', '\n')

        pv.insert('end', '   一键换个方式再试：\n', 'meta')
        if mode != 'fuzzy':
            _add_button('改用模糊搜索',
                        lambda: (self.search_mode.set('fuzzy'),
                                 self.do_search()),
                        '可识别一字之差 / 错字')
        if self.only_active.get():
            _add_button('包含已废止文件一起搜',
                        lambda: (self.only_active.set(False),
                                 self.do_search()),
                        '取消「仅看有效」过滤')
        if not self.syn_var.get():
            _add_button('开启同义词扩展',
                        lambda: (self.syn_var.set(True),
                                 self.do_search()),
                        '如：安全帽↔头盔、临边↔洞口')
        _add_button('打开库确认文件已入库',
                        self.show_library)
        pv.insert('end', '\n', 'meta')
        # 历史词一键再搜
        recent = [k for k in self.cfg.get('recent_keywords', []) if k != kw][:6]
        if recent:
            pv.insert('end', '   或者试试最近搜过的词：\n', 'meta')
            pv.insert('end', '   ')
            for k in recent:
                b = ttk.Button(pv, text=k, width=max(6, min(16, len(k) + 2)),
                               command=lambda kk=k: (
                                   self.keyword.set(kk), self.do_search()))
                self._zero_btns.append(b)
                pv.window_create('end', window=b, padx=3)
            pv.insert('end', '\n')




    # 每帧最多插入多少结果行：够小以免卡住事件循环，够大以免分太多帧。
    # 首帧只铺一屏多一点 —— 用户按下回车后最先要的是「有反应」，
    # 后面几帧再把剩下的补满。
    _BATCH_ROWS = 120
    _BATCH_FIRST = 30



    # -------- 重建后的滚动/选中还原 --------






        # 原「上下文行」灰字行已删除：右侧预览窗格本就显示 ±N 行真实上下文，
        # 树里再挂上下各 1 行（还截断 160 字）纯属重复且把列表拉长


    @staticmethod
    def _page_label(line_text, line_no, name):
        """结果树位置标签的页码：PDF/PPT 取真实页码，其余格式无页码概念 → 略"""
        return page_label(line_text, line_no, name)

    @staticmethod
    def _snippet(text, kw, fuzzy=False, width=200, lead=10):
        """列表片段：关键词尽量靠前（前留 lead 字上下文），保证在结果列
        可视宽度内直接看到命中词；模糊行定位一字之差的实际片段。"""
        return snippet(text, kw, fuzzy, width, lead)


    def _update_stats(self, kw):
        """更新顶部统计条：文字摘要（数字滚动）+ 全文件命中分布柱状图（可横向滚动）"""
        total = len(self.results)
        files = len(self.groups)
        fuzzy_n = sum(g.get('fuzzy_hits', 0) for g in self.groups)
        suffix = (f' · 模糊命中 {fuzzy_n} 条（片段前 ≈ 标记）'
                  if fuzzy_n else '') + ' · 点柱子跳文件'
        last = getattr(self, '_stats_last', None)
        self._stats_last = (total, files)

        def _fmt(t, f):
            return f'「{kw}」→ 共 {t} 条命中 · {f} 个文件' + suffix

        # 数字滚动：从上次结果滚到新值（首次/同值/减弱动效时直显）
        if anim is None or not self._anim_ok() or last is None \
                or last == (total, files):
            self.stats_label.configure(text=_fmt(total, files))
        else:
            tw = getattr(self, '_stats_tw', None)
            if tw is None:
                tw = anim.Tweener(self.root)
                self._stats_tw = tw
            t0, f0 = last

            def _step(_t, p):
                try:
                    self.stats_label.configure(text=_fmt(
                        int(round(t0 + (total - t0) * p)),
                        int(round(f0 + (files - f0) * p))))
                except Exception:
                    pass
            tw.tween(0.0, 1.0, 260, _step, easing='ease_out_cubic')
        self._draw_distribution_chart()


    def _chart_grow_one(self, canvas, bi):
        """单根柱子从宽度 0 生长到目标宽度，数字随后淡入"""
        tw = anim.Tweener(canvas)
        self._chart_tweens.append(tw)
        cx, bw = bi['cx'], bi['bw']

        def _step(_t, v):
            canvas.coords(bi['id'], *self._bar_pts(
                cx, v, bi['y0'], bi['y1'], bi['r']))

        def _done():
            bi['grown'] = True
            self._chart_grown = all(x.get('grown') for x in
                                    getattr(self, '_chart_bar_infos', []))
            # 命中数字从背景色淡入
            canvas_conf_bg = canvas.cget('bg')
            tw2 = anim.Tweener(canvas)
            self._chart_tweens.append(tw2)
            tw2.tween_color(canvas_conf_bg, bi['num_fg'], 180,
                            lambda _t, col: canvas.itemconfigure(
                                bi['num'], fill=col))
        # 目标是「半宽」＝bw/2：柱子的可见宽度就是 bw。原来这里传的是 bw，
        # 长出来是 bw*2（78 的柱子长成 158、越出自己的列钻进隔壁），而且
        # 一悬停又按 bw/2 收回去，看着像柱子突然缩水。
        tw.tween(1, bw / 2.0, 240, _step, _done, easing='ease_out_cubic')

    def _chart_set_hover(self, idx):
        """柱子 hover：向两侧加宽、向下加厚 + 提亮；离开恢复（生长结束后）。
        只定点刷新旧/新两根，避免柱子多时每次悬停遍历全量重绘。
        注意顶边不动——往上长会啃到上方的文件名（数字上方/下方都留了空位）。"""
        if not self._chart_grown:
            return
        prev = self._chart_hover_idx
        if prev == idx:
            return
        self._chart_hover_idx = idx
        canvas = self._chart_canvas
        infos = getattr(self, '_chart_bar_infos', [])

        def _restore(j):
            if 0 <= j < len(infos):
                bi = infos[j]
                try:
                    canvas.coords(bi['id'], *self._bar_pts(
                        bi['cx'], bi['bw'] / 2.0, bi['y0'], bi['y1'],
                        bi['r']))
                    canvas.itemconfigure(bi['id'], fill=bi['color'])
                except Exception:
                    pass

        _restore(prev)
        if 0 <= idx < len(infos):
            bi = infos[idx]
            try:
                col = (anim.hex_lerp(bi['color'], '#FFFFFF', 0.18)
                       if not bi['dead'] else bi['color'])
                canvas.coords(bi['id'], *self._bar_pts(
                    bi['cx'], bi['bw'] / 2.0 + 3, bi['y0'],
                    bi['y1'] + bi['hover_dn'], bi['r']))
                canvas.itemconfigure(bi['id'], fill=col)
                canvas.tag_raise(bi['id'])
            except Exception:
                pass

    @staticmethod
    def _bar_pts(cx, half, y0, y1, r):
        """柱子的圆角矩形点集。Tk 没有圆角矩形，靠「每个角点写两遍」把样条
        拉成直角带圆（create_polygon(smooth=True)）；半径自动收缩，不超过
        半宽/半高，柱子很细时退化成直角矩形。"""
        return bar_pts(cx, half, y0, y1, r)

    @staticmethod
    def _fit_text(font, text, max_w):
        """按字体实测像素宽截断文本，超宽尾部加「…」，保证不超出 max_w"""
        if font.measure(text) <= max_w:
            return text
        while text and font.measure(text + '…') > max_w:
            text = text[:-1]
        return text + '…'









    def _bind_btn_tip(self, widget, text, delay=500):
        """给任意 widget 绑悬停 tooltip（复用 _show_tree_tip / _hide_tree_tip）"""
        widget.bind('<Enter>', lambda e, w=widget, t=text: self._show_btn_tip(w, t))
        widget.bind('<Leave>', lambda e: self._hide_tree_tip())











    # -------- 自适应重排：合并 PDF/Word 逐行提取造成的硬断行 --------
    # 现象：PDF 抽取的正文一行只有 40~50 字（真实排版的一个视觉行），宽窗格里
    # 逐行显示会让右侧空掉一大半（用户反馈「预览界面右边留白太多」）。
    # 做法：同一自然段内的硬断行并成一条，交给 Text 按窗格宽度重新折行。
    # 「换行」关闭时不重排，保持逐行原样（核对原文、看表格时用）。
    _RF_END = '。；；！？：”」』…）)]%'        # 行尾已完整：出现即不再向后并
    _RF_NEXT = re.compile(
        r'^\s*(?:'
        r'[（(]\s*[0-9一二三四五六七八九十]+\s*[)）]'    # （1） （一）
        r'|[0-9]+\s*[.、)）]'                             # 1.  2、
        r'|[一二三四五六七八九十]+\s*[、.]'              # 一、 二.
        r'|第\s*[0-9一二三四五六七八九十百]+\s*[条章节款项]'
        r'|[^：:\s]{1,6}[：:]'                            # 编 辑：xx 这类标签行
        r')')
    _RF_HEAD = re.compile(
        r'^\s*(?:第\s*[0-9一二三四五六七八九十百]+\s*[章节目]'
        r'|[（(]\s*[0-9一二三四五六七八九十]+\s*[)）]\s*$'
        r'|附件\s*[0-9]*|目\s*录)')
    _RF_TOC = re.compile(r'[.·．]{3,}\s*\d*\s*$')       # 目录的点线引导行








    def _smooth_see(self, index, duration_ms=240):
        """平滑滚动到目标行（补间替代瞬跳）；已在视口内/失败时降级为直接 see"""
        if anim is None or not self._anim_ok():
            self.preview.see(index)
            return
        try:
            cur = self.preview.yview()[0]
            self.preview.update_idletasks()
            self.preview.see(index)
            tgt = self.preview.yview()[0]
            if abs(tgt - cur) < 0.003:
                return   # 已在视口内，无需滚动
            self.preview.yview_moveto(cur)
            tw = getattr(self, '_see_tw', None)
            if tw is None:
                tw = anim.Tweener(self.root)
                self._see_tw = tw
            tw.tween(cur, tgt, duration_ms,
                     lambda _t, v: self.preview.yview_moveto(v),
                     easing='ease_out_cubic')
        except Exception:
            try:
                self.preview.see(index)
            except Exception:
                pass

    def _fade_hit_highlight(self):
        """命中行底色从预览底色淡入到高亮色（200ms），静默降级"""
        if not self._anim_ok():
            return
        try:
            c = self._theme_colors()
            hit_bg = self.preview.tag_cget('hit', 'background') or c['hit_bg']
            base = c['preview_bg']
            if not hit_bg or hit_bg == base:
                return
            tw = getattr(self, '_hit_tw', None)
            if tw is None:
                tw = anim.Tweener(self.root)
                self._hit_tw = tw
            self.preview.tag_configure('hit', background=base)
            tw.tween_color(
                base, hit_bg, 220,
                lambda _t, col: self.preview.tag_configure(
                    'hit', background=col))
        except Exception:
            pass

    def _insert_with_refs(self, text, extra_tags=None):
        """把一行文本插入预览区，《文件》引用段 + http(s) 链接渲染成可点击。
        extra_tags: 附加的 tag 列表（如表格行的斑马纹、表头底色等）"""
        import re as _re
        from searcher import REF_RE
        cache = getattr(self, '_ref_status_cache', None)
        if cache is None:
            cache = self._ref_status_cache = {}
        extra = tuple(extra_tags) if extra_tags else ()
        resolve_cache = getattr(self, '_ref_resolve_cache', None)
        clause_cache = getattr(self, '_ref_clause_cache', None)
        # 《引用》与 http(s) 链接合并成一个扫描序列（重叠时《引用》优先）
        spans = [(m.start(), m.end(), 0, m) for m in REF_RE.finditer(text)]
        spans += [(m.start(), m.end(), 1, m) for m in _URL_RE.finditer(text)]
        spans.sort(key=lambda s: (s[0], s[2]))
        pos = 0
        for _st, _en, _kind, m in spans:
            if _st < pos:          # 与前一个匹配重叠 → 跳过
                continue
            if _st > pos:
                self.preview.insert('end', text[pos:_st], extra)
            if _kind == 1:
                # 纯链接（非《引用》）：渲染为可点击，单击浏览器打开
                self.preview.insert('end', m.group(0), ('weblink',) + extra)
                pos = _en
                continue
            self._ref_seq += 1
            tag = f'ref_{self._ref_seq}'
            title = m.group(1).strip()
            clause = (m.group(2) or '').strip()
            # 同一次渲染内《同一标题》只解析一次
            if resolve_cache is not None and title in resolve_cache:
                target = resolve_cache[title]
            else:
                target = resolve_ref(DB_PATH, title)
                if resolve_cache is not None:
                    resolve_cache[title] = target
            if target:
                # 同一 (文件,条款) 只定位一次
                ck = (target, clause)
                if clause_cache is not None and ck in clause_cache:
                    tgt_line = clause_cache[ck]
                else:
                    tgt_line = find_clause_line(DB_PATH, target, clause) or 1
                    if clause_cache is not None:
                        clause_cache[ck] = tgt_line
                self._ref_targets[tag] = (target, tgt_line)
                # 目标状态带缓存（一次预览渲染内不重复查库）
                st = cache.get(target)
                if st is None:
                    st = get_metadata(DB_PATH, target).get('status', 'active')
                    cache[target] = st
                style = 'refdead' if st in ('void', 'replaced') else 'reflink'
                self.preview.insert('end', m.group(0),
                                    (style, tag) + extra)
            else:
                self._ref_targets[tag] = (None, None, title)
                self.preview.insert('end', m.group(0),
                                    ('refmiss', tag) + extra)
            pos = m.end()
        if pos < len(text):
            self.preview.insert('end', text[pos:], extra)

    def _ref_link_color(self, tag, on):
        """《引用》链接 hover：前景色渐变提亮；离开回到主题色"""
        if tag not in ('reflink', 'refmiss', 'refdead'):
            return
        c = self._theme_colors()
        base = {'reflink': c['reflink_fg'], 'refmiss': c['refmiss_fg'],
                'refdead': c['refdead_fg']}.get(tag)
        if not base:
            return
        target = anim.hex_lerp(base, '#FFFFFF', 0.25) if on else base
        if not self._anim_ok():
            self.preview.tag_configure(tag, foreground=target)
            return
        if not hasattr(self, '_ref_tws'):
            self._ref_tws = {}
        tw = self._ref_tws.get(tag)
        if tw is None:
            tw = anim.Tweener(self.preview)
            self._ref_tws[tag] = tw
        try:
            cur = self.preview.tag_cget(tag, 'foreground') or base
        except Exception:
            cur = base
        tw.tween_color(cur, target, 140,
                       lambda _t, col: self.preview.tag_configure(
                           tag, foreground=col))

    def _ref_bg(self, inst_tag, on):
        """引用实例 hover 底色淡入/淡出（preview_bg 与品牌浅金之间渐变）"""
        if anim is None or not self._anim_ok():
            return
        c = self._theme_colors()
        base, hover = c['preview_bg'], anim.hex_lerp(
            c['preview_bg'], c['reflink_fg'], 0.16)
        if not hasattr(self, '_ref_bg_tws'):
            self._ref_bg_tws = {}
        tw = self._ref_bg_tws.get(inst_tag)
        if tw is None:
            tw = anim.Tweener(self.preview)
            self._ref_bg_tws[inst_tag] = tw
        try:
            cur = self.preview.tag_cget(inst_tag, 'background') or base
        except Exception:
            cur = base
        tw.tween_color(
            cur, hover if on else base, 140,
            lambda _t, col: self.preview.tag_configure(
                inst_tag, background=col))





    def _on_weblink_click(self, evt):
        """单击正文里的 http(s) 链接：用系统默认浏览器打开"""
        idx = self.preview.index(f'@{evt.x},{evt.y}')
        url = ''
        ranges = self.preview.tag_ranges('weblink')
        for i in range(0, len(ranges) - 1, 2):
            a, b = ranges[i], ranges[i + 1]
            if self.preview.compare(a, '<=', idx) \
                    and self.preview.compare(idx, '<', b):
                url = self.preview.get(a, b).strip()
                break
        if not url:
            return
        try:
            webbrowser.open(url)
            self.status.configure(text=f'已在浏览器打开：{url}')
        except Exception as e:
            self.status.configure(text=f'打开链接失败：{e}')




    # -------- 书签 --------

    def _bm_pop(self):
        """收藏星星弹跳：样式字号 9→12 加粗→9 回弹。
        ttk.Button 不支持实例级 font，只能动画它当前挂的克隆样式（每按钮独立）"""
        if not self._anim_ok():
            return
        fam = self.FONT_ICON[0]
        sty = self.bm_btn.cget('style') or 'Icon.TButton'
        st = ttk.Style()
        tw = anim.Tweener(self.root)
        tw.tween(9, 12, 90,
                 lambda _t, s: st.configure(
                     sty, font=(fam, int(s), 'bold')),
                 lambda: tw.tween(
                     12, 9, 160,
                     lambda _t, s: st.configure(
                         sty, font=(fam, int(s))),
                     easing='ease_out_cubic'))


    def show_bookmarks(self):
        """书签列表面板：双击跳转主窗口预览"""
        win = self._get_win('bookmarks')
        if win is None:
            return
        win.title('书签')
        self._center_win(win, 900, 480, modal=False)   # 浏览型，不锁主界面
        win.transient(self.root)
        self._dialog_header(
            win, self.IC_STAR_FILL, '书签',
            '双击书签跳转到对应条款 · 右键删除')
        bar = ttk.Frame(win)
        bar.pack(fill='x')
        ttk.Button(bar, text='导出汇编', style='Accent.TButton',
                   command=lambda: self._export_bookmark_digest(
                       list_bookmarks(DB_PATH))).pack(side='left',
                                                      padx=8, pady=8)
        body = ttk.Frame(win)
        body.pack(fill='both', expand=True, padx=8, pady=(0, 8))
        cols = ('name', 'title', 'note', 'line', 'time')
        tree = ttk.Treeview(body, columns=cols, show='headings')
        tree.heading('name', text='文件')
        tree.heading('title', text='所在章节/摘要')
        tree.heading('note', text='备注')
        tree.heading('line', text='行')
        tree.heading('time', text='收藏时间')
        tree.column('name', width=200)
        tree.column('title', width=300)
        tree.column('note', width=140)
        tree.column('line', width=50, anchor='center')
        tree.column('time', width=140, anchor='center')
        vsb = ttk.Scrollbar(body, orient='vertical', command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        tree.pack(side='left', fill='both', expand=True)
        vsb.pack(side='right', fill='y')

        def reload_tree():
            tree.delete(*tree.get_children())
            for b in list_bookmarks(DB_PATH):
                tree.insert('', 'end', iid=str(b['id']),
                            values=(b['name'] or os.path.basename(b['path']),
                                    b['title'] or '', b.get('note') or '',
                                    b['line_no'],
                                    time.strftime('%Y-%m-%d %H:%M',
                                                  time.localtime(b['created_at']))))

        def jump(_evt=None):
            sel = tree.selection()
            if not sel:
                return
            b = next((x for x in list_bookmarks(DB_PATH)
                      if str(x['id']) == sel[0]), None)
            if b and os.path.exists(b['path']):
                self._show_preview(b['path'], b['line_no'])
                self.root.lift()

        def delete_one(_evt=None):
            sel = tree.selection()
            if sel and messagebox.askyesno('确认', '删除这个书签？'):
                remove_bookmark(DB_PATH, bookmark_id=int(sel[0]))
                reload_tree()

        def edit_note():
            sel = tree.selection()
            if not sel:
                return
            b = next((x for x in list_bookmarks(DB_PATH)
                      if str(x['id']) == sel[0]), None)
            if not b:
                return
            from tkinter import simpledialog
            note = simpledialog.askstring(
                '书签备注', '备注内容：', initialvalue=b.get('note') or '',
                parent=win)
            if note is not None:
                set_bookmark_note(DB_PATH, b['id'], note)
                reload_tree()

        def _bm_ctx(e):
            row = tree.identify_row(e.y)
            if not row:
                return
            tree.selection_set(row)
            m = tk.Menu(tree, tearoff=0)
            m.add_command(label='编辑备注…', command=edit_note)
            m.add_command(label='删除书签', command=delete_one)
            m.tk_popup(e.x_root, e.y_root)

        tree.bind('<Double-1>', jump)
        tree.bind('<Button-3>', _bm_ctx)
        reload_tree()
        if not tree.get_children():
            messagebox.showinfo('书签', '还没有收藏。预览条款时点「☆ 收藏此条」即可添加。')

    # -------- 索引体检 --------
    def show_index_health(self):
        """索引体检：列出磁盘上已失联的索引条目，可勾选清理"""
        win = self._get_win('index_health')
        if win is None:
            return
        win.title('索引体检')
        self._center_win(win, 980, 460, modal=False)
        win.transient(self.root)
        self._dialog_header(
            win, self.IC_WARN, '索引体检',
            '失联索引清理 · 库整理 · OCR 状态 · 自动备份份数 · 数据库位置（不影响磁盘）')
        bar = ttk.Frame(win)
        bar.pack(fill='x')
        info_lbl = ttk.Label(bar, text='扫描中…')
        info_lbl.pack(side='left', padx=8, pady=8)
        size_lbl = ttk.Label(bar, text='', foreground='#888')
        size_lbl.pack(side='right', padx=8, pady=8)
        body = ttk.Frame(win)
        body.pack(fill='both', expand=True, padx=8, pady=(0, 8))
        cols = ('name', 'path', 'status', 'lines')
        tree = ttk.Treeview(body, columns=cols, show='headings',
                            selectmode='extended')
        tree.heading('name', text='文件')
        tree.heading('path', text='完整路径')
        tree.heading('status', text='状态')
        tree.heading('lines', text='行数')
        tree.column('name', width=200)
        tree.column('path', width=420)
        tree.column('status', width=70, anchor='center')
        tree.column('lines', width=60, anchor='e')
        vsb = ttk.Scrollbar(body, orient='vertical', command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        tree.pack(side='left', fill='both', expand=True)
        vsb.pack(side='right', fill='y')

        def reload_tree():
            tree.delete(*tree.get_children())
            missing = find_missing_documents(DB_PATH)
            st_lbl = {'void': '已废止', 'replaced': '已被替代'}
            for d in missing:
                tree.insert('', 'end', iid=str(d['id']),
                            values=(d['name'] or os.path.basename(d['path']),
                                    d['path'],
                                    st_lbl.get(d['status'], '现行'),
                                    d['line_count']))
            total = library_stats(DB_PATH).get('files', 0)
            # 失联比例过高 → 疑似存储未挂载：不预选、禁用清理按钮
            suspected = len(missing) > max(50, total * 0.2)
            if suspected:
                info_lbl.configure(
                    text=f'⚠ 发现 {len(missing)} 条失联索引（超过总数 20%），'
                         '疑似存储未挂载，已阻止批量清理。请检查磁盘连接后重新体检。',
                    foreground='#c0392b')
                del_btn.configure(state='disabled')
            elif missing:
                info_lbl.configure(
                    text=f'发现 {len(missing)} 条失联索引，'
                         '请确认后手动勾选再点「删除选中」',
                    foreground='#e67e22')
                del_btn.configure(state='normal')
            else:
                info_lbl.configure(
                    text='索引健康：全部文档路径均可访问',
                    foreground='')
                del_btn.configure(state='disabled')
            size_lbl.configure(text=self._db_size_text())
            # 不再默认全选，避免误清理

        def _delete():
            sel = tree.selection()
            if not sel:
                self.status.configure(text='未选择任何条目')
                return
            if not messagebox.askyesno(
                    '确认清理', f'将从索引中删除 {len(sel)} 条失联文档'
                               f'（不影响磁盘文件本身），确定？', parent=win):
                return
            snapshot_db(DB_PATH, BACKUP_DIR, 'pre_clean')
            n = delete_documents(DB_PATH, [int(i) for i in sel])
            self.status.configure(text=f'已清理 {n} 条失联索引')
            reload_tree()

        btns = ttk.Frame(win)
        btns.pack(fill='x', padx=8, pady=(0, 8))
        del_btn = ttk.Button(btns, text='删除选中', style='Danger.TButton',
                             command=_delete)
        del_btn.pack(side='left')
        ttk.Button(btns, text='库整理（压缩+统计）',
                   command=lambda: self._run_db_maintenance(
                       win, info_lbl, size_lbl)).pack(side='left', padx=6)
        ttk.Button(btns, text='重新体检',
                   command=reload_tree).pack(side='left', padx=6)
        # 原更多菜单的三项维护入口并入本窗口
        ttk.Button(btns, text='OCR 状态',
                   command=self.show_ocr_status).pack(side='left', padx=6)
        ttk.Button(btns, text='自动备份份数…',
                   command=self._choose_max_backups).pack(side='left', padx=6)
        ttk.Button(btns, text='打开数据库文件位置…',
                   command=self._locate_db_file).pack(side='left', padx=6)
        ttk.Button(btns, text='关闭', command=win.destroy).pack(side='right')
        reload_tree()

    # -------- 联网检索制度 --------



    def _show_web_preview(self, item, keyword, text, path, body_start=0):
        """联网结果软件内预览：全文高亮搜索词并滚到第一处，可逐处跳转。"""
        key = 'webpreview'
        old = self._wins.pop(key, None)
        if old is not None:
            try:
                old.destroy()
            except Exception:
                pass
        win = tk.Toplevel(self.root)
        self._wins[key] = win
        win.title('联网预览 · ' + (item.get('title') or ''))
        self._center_win(win, 980, 720, modal=False)
        win.transient(self.root)

        def _close():
            self._wins.pop(key, None)
            win.destroy()
        win.protocol('WM_DELETE_WINDOW', _close)
        win.bind('<Escape>', lambda e: _close())

        title = item.get('title') or ''
        sub = ' · '.join(x for x in ((item.get('source_name') or ''),
                                     item.get('status') or '',
                                     item.get('office') or '',
                                     item.get('publish') or '') if x)
        self._dialog_header(win, self.IC_GLOBE, title, sub)

        c = self._theme_colors()
        bar = ttk.Frame(win, padding=(12, 6, 12, 2))
        bar.pack(fill='x')
        info = tk.StringVar(value='')
        ttk.Label(bar, textvariable=info, font=self.FONT_HINT,
                  foreground=c['meta_fg']).pack(side='left')
        ttk.Button(bar, text='下一处',
                   command=lambda: _goto(state['i'] + 1)).pack(side='right')
        ttk.Button(bar, text='上一处',
                   command=lambda: _goto(state['i'] - 1)
                   ).pack(side='right', padx=(0, 6))

        body = ttk.Frame(win, padding=(12, 0, 12, 0))
        body.pack(fill='both', expand=True)
        txt = ReadonlyText(body, wrap='word', font=self.FONT_MAIN,
                           padx=14, pady=10, background=c['preview_bg'],
                           foreground=c['preview_fg'],
                           selectbackground=c['preview_sel'],
                           state='disabled')
        sb = ttk.Scrollbar(body, orient='vertical', command=txt.yview)
        txt.configure(yscrollcommand=sb.set)
        sb.pack(side='right', fill='y')
        txt.pack(side='left', fill='both', expand=True)
        txt.tag_configure('meta', foreground=c['meta_fg'],
                          font=self.FONT_HINT)
        # 全部命中：淡底；当前一处：亮底加粗，滚到可见
        txt.tag_configure('hit', background=c['hit_bg'],
                          foreground=c['hit_fg'])
        txt.tag_configure('cur', background=c['hl_bg'], foreground=c['hl_fg'],
                          font=(self.FONT_MAIN[0], self.FONT_MAIN[1], 'bold'))

        # 元数据头部灰字显示（download_flk 写入的标题/机关/日期/来源），
        # 正文起始字符偏移用于定位——避免标题里的词把位置抢到第一行
        lines = text.split('\n')
        head = '\n'.join(lines[:body_start]) if body_start > 0 else ''
        rest = '\n'.join(lines[body_start:]) if body_start > 0 else text
        if head:
            txt.insert('end', head + '\n', 'meta')
        txt.insert('end', rest)
        body_off = len(head) + 1 if head else 0

        # 法规库里少数文件是扫描件，官方阅读器取不到文本（只剩元数据头部）。
        # 这时不能说「未找到关键词」，否则用户会以为搜索结果错了。
        body_blank = not rest.strip()
        if body_blank:
            txt.insert('end', '\n（该文件的正文没能从官方接口提取到文本，'
                              '通常是扫描件或正文放在附件里。\n'
                              '请点右下角「在浏览器打开」查看原文。）', 'meta')

        terms = self._web_locate_terms(keyword)
        spans = []
        if terms and not body_blank:
            # 只在正文里找：标题里命中不算（那正是「命中」列要揭穿的
            # 模糊匹配假结果），否则两者会互相打架
            rx = re.compile('|'.join(re.escape(t) for t in terms))
            spans = [(m.start(), m.end())
                     for m in rx.finditer(text, body_off)]
        for s, e in spans:
            txt.tag_add('hit', f'1.0+{s}c', f'1.0+{e}c')

        state = {'i': -1}

        def _goto(i):
            if not spans:
                return
            i %= len(spans)
            txt.tag_remove('cur', '1.0', 'end')
            s, e = spans[i]
            a = f'1.0+{s}c'
            txt.tag_add('cur', a, f'1.0+{e}c')
            txt.see(a)
            state['i'] = i
            info.set(f'关键词「{keyword}」共 {len(spans)} 处 · 第 {i + 1} 处')

        if spans:
            _goto(0)
        elif body_blank:
            info.set('该法规正文未能提取到文本（多为扫描件），'
                     '请点「在浏览器打开」查看原文')
        else:
            alt = [t for t in terms if t != keyword]
            info.set(f'本文正文不含「{keyword}」'
                     + (f'、{"、".join(alt)}' if alt else '')
                     + '（法规库按分词模糊匹配返回该结果，'
                       '可点「在浏览器打开」核对原文）')

        foot = ttk.Frame(win, padding=(12, 6, 12, 10))
        foot.pack(fill='x')
        ttk.Label(foot, font=self.FONT_HINT, foreground=c['meta_fg'],
                  text='正文已缓存到下载目录，回联网检索窗口点「下载入库」即可入库'
                  ).pack(side='left')
        url = websearch.open_url_of(item)
        if url:
            ttk.Button(foot, text=f'{self.IC_GLOBE} 在浏览器打开',
                       style='Icon.TButton',
                       command=lambda: webbrowser.open(url)
                       ).pack(side='right', padx=(6, 0))
        ttk.Button(foot, text='关闭', command=_close).pack(side='right')

    def show_websearch(self):
        """联网检索官方制度 / 法规 / 标准，支持下载入库"""
        if websearch is None:
            self._toast('联网检索模块加载失败')
            return
        win = self._get_win('websearch')
        if win is None:
            return
        win.title('联网检索制度')
        self._center_win(win, 1080, 580, modal=False)
        win.transient(self.root)
        self._dialog_header(
            win, self.IC_GLOBE, '联网检索制度',
            '输入关键词 → 勾选来源 → 搜索 → 双击结果在软件内预览并定位关键词'
            ' → 一键下载入库')

        # ---- 顶部：搜索框 + 来源勾选 ----
        top = ttk.Frame(win, padding=(12, 8, 12, 4))
        top.pack(fill='x')
        ttk.Label(top, text='关键词').pack(side='left')
        kw_var = tk.StringVar()
        kw_entry = ttk.Entry(top, textvariable=kw_var, width=34)
        kw_entry.pack(side='left', padx=(4, 8))
        kw_entry.bind('<Return>', lambda e: _do_search())
        ttk.Button(top, text='搜索', style='Accent.TButton',
                   command=lambda: _do_search()).pack(side='left')

        src_frame = ttk.Frame(win, padding=(12, 0, 12, 2))
        src_frame.pack(fill='x')
        ttk.Label(src_frame, text='来源：', foreground='gray').pack(side='left')
        # 分两行：能出结果的来源一行，只能跳浏览器的另起一行并右移对齐
        # （六个挤一行要 1200+ px，窗口 1080 会把最后一个勾选项切掉）
        jump_frame = ttk.Frame(win, padding=(60, 0, 12, 4))
        jump_frame.pack(fill='x')
        src_vars = {}
        for s in websearch.SOURCES:
            v = tk.BooleanVar(value=s['has_api'])
            src_vars[s['id']] = v
            if s['has_api']:
                parent, txt = src_frame, s['name']
            else:
                parent = jump_frame
                txt = s['name'] + '（网页跳转，软件内取不到结果）'
            cb = ttk.Checkbutton(parent, text=txt, variable=v)
            cb.pack(side='left', padx=(4, 0))

        # ---- 中部：结果列表 ----
        body = ttk.Frame(win, padding=(12, 0, 12, 0))
        body.pack(fill='both', expand=True)
        cols = ('source', 'title', 'verify', 'status', 'office', 'publish',
                'type')
        tree = ttk.Treeview(body, columns=cols, show='headings',
                            selectmode='extended')
        tree.heading('source', text='来源')
        tree.heading('title', text='标题')
        tree.heading('verify', text='命中')
        tree.heading('status', text='时效性')
        tree.heading('office', text='发布机关')
        tree.heading('publish', text='发布日期')
        tree.heading('type', text='类型')
        tree.column('source', width=118, anchor='center')
        tree.column('title', width=340)
        tree.column('verify', width=84, anchor='center')
        tree.column('status', width=66, anchor='center')
        tree.column('office', width=152)
        tree.column('publish', width=90, anchor='center')
        tree.column('type', width=72, anchor='center')
        # 时效性配色：有效=绿，已修改=档案蓝，已废止=红，尚未生效=灰蓝
        tree.tag_configure('st-valid', foreground='#2E7D32')
        tree.tag_configure('st-changed', foreground='#2F5E8C')
        tree.tag_configure('st-dead', foreground='#C0392B')
        tree.tag_configure('st-pending', foreground='#5B6B7C')
        vsb = ttk.Scrollbar(body, orient='vertical', command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        tree.pack(side='left', fill='both', expand=True)
        vsb.pack(side='right', fill='y')

        # ---- 底部：状态 + 按钮 ----
        bottom = ttk.Frame(win, padding=(12, 6, 12, 8))
        bottom.pack(fill='x')
        status_lbl = ttk.Label(bottom, text='输入关键词后点「搜索」',
                               foreground='gray')
        status_lbl.pack(side='left')

        btn_bar = ttk.Frame(win, padding=(12, 0, 12, 12))
        btn_bar.pack(fill='x')
        open_btn = ttk.Button(btn_bar, text=f'{self.IC_GLOBE} 查看网页',
                              style='Icon.TButton',
                              command=lambda: _open_web())
        open_btn.pack(side='left', padx=(0, 6))
        dl_btn = ttk.Button(btn_bar, text=f'{self.IC_DOWNLOAD} 下载入库',
                            style='Icon.TButton',
                            command=lambda: _download())
        dl_btn.pack(side='left', padx=(0, 6))
        dl_all_btn = ttk.Button(btn_bar, text='全部下载入库',
                                command=lambda: _download(all_=True))
        dl_all_btn.pack(side='left', padx=(0, 6))
        # 注意：_open_save_dir 定义在下方，这里必须用 lambda 延迟取
        # （直接传函数名会在构建窗口时抛 UnboundLocalError，
        #   连带把后面的搜索逻辑一起吞掉，表现为「点了搜索没反应」）
        ttk.Button(btn_bar, text=f'{self.IC_FOLDER} 打开下载目录',
                   style='Icon.TButton',
                   command=lambda: _open_save_dir()).pack(side='left', padx=(0, 6))
        ttk.Button(btn_bar, text='关闭', command=win.destroy).pack(side='right')

        # ---- 内部状态 ----
        _results = []          # 当前显示的 result dict 列表
        _searching = {'on': False}
        _jumped = []           # 本次已跳浏览器的来源名
        _last_kw = {'v': ''}      # 出结果那一轮的关键词（定位用，非输入框现值）
        _fetching = {'on': False}  # 预览抓正文中，防重复点
        _iid_item = {}         # 行 iid → result dict（表格会因核对置顶重排，
                               #   所以不能靠行序下标回查 _results）
        _fetch_lock = threading.Lock()   # 核对与双击可能同时抓同一份正文
        _vseq = {'n': 0}       # 核对轮次，换关键词后旧一轮的结果直接丢弃
        _VERIFY_TOP = 10       # 只自动核对前 N 条，几十条法规逐条抓太慢

        def _do_search():
            if _searching['on']:
                return
            kw = kw_var.get().strip()
            if not kw:
                status_lbl.configure(text='请输入关键词')
                return
            _last_kw['v'] = kw
            chosen = [sid for sid, v in src_vars.items() if v.get()]
            if not chosen:
                status_lbl.configure(text='请至少勾选一个来源')
                return
            tree.delete(*tree.get_children())
            _results.clear()
            _iid_item.clear()
            _vseq['n'] += 1          # 作废上一轮的正文核对
            _searching['on'] = True
            status_lbl.configure(text='搜索中…')

            # 无接口的来源：软件内取不到结果，只能打开浏览器搜索页
            _jumped.clear()
            for s in websearch.SOURCES:
                if s['id'] in chosen and not s['has_api']:
                    url = websearch.get_jump_url(s['id'], kw)
                    if url:
                        webbrowser.open(url)
                        _jumped.append(s['name'])
            # 只保留能取数的来源继续搜索
            chosen = [sid for sid in chosen if any(s['id'] == sid and s['has_api'] for s in websearch.SOURCES)]
            if not chosen:
                _searching['on'] = False
                names = '、'.join(_jumped) or '所选来源'
                status_lbl.configure(
                    text=f'{names} 仅支持网页跳转，已在浏览器打开搜索页；'
                         f'软件内出结果请勾选法规库 / 国标 / 行业标准 / '
                         f'地方标准这几个来源')
                return

            def _work():
                try:
                    # 下发给来源库的查询词：原词 + 行话别名 + 同义词（软件
                    # 本地检索那套，默认开），合并去重限量后一次带过去
                    terms = websearch.merge_terms(
                        websearch.expand_keyword(kw), self._web_synonyms(kw))
                    results, errors = websearch.search_all(
                        kw, chosen, callback=None, terms=terms)
                except Exception as e:
                    results, errors = {}, {'all': str(e)}
                win.after(0, lambda: _on_results(results, errors, kw))

            threading.Thread(target=_work, daemon=True).start()

        _status_tags = {'有效': 'st-valid', '已修改': 'st-changed',
                        '已废止': 'st-dead', '尚未生效': 'st-pending'}

        def _on_results(results, errors, kw=''):
            _searching['on'] = False
            total = 0
            pending = []           # 待自动核对的「法规库」行
            loc = self._web_locate_terms(kw) if kw else []
            # 按来源表顺序铺结果（新来源只要进 SOURCES 就自动排上）
            for sid in [s['id'] for s in websearch.SOURCES]:
                for item in results.get(sid, []):
                    _results.append(item)
                    total += 1
                    st = item.get('status', '')
                    can_verify = item.get('source') in websearch.TEXT_SOURCES
                    if can_verify:
                        cell = '核对中'       # 后台抓正文核对，稍后填 ✓N处/✕无
                    elif item.get('source') in websearch.TITLE_VERIFY:
                        # 标准类来源的正文是官方图片版/需登录，取不到文字，
                        # 只能核对标题——好在标准检索本身就是标题检索，标题
                        # 不含词的基本都是模糊匹配带出来的
                        cell = ('✓ 标题' if websearch.title_hits(
                            loc, item['title']) else '✕ 标题')
                    else:
                        cell = '—'
                    iid = tree.insert('', 'end', values=(
                        item['source_name'], item['title'], cell, st,
                        item['office'], item['publish'], item['type']),
                        tags=(_status_tags.get(st),) if st in _status_tags else ())
                    _iid_item[iid] = item
                    if can_verify:
                        pending.append((iid, item))
            err_txt = ''
            if errors:
                err_txt = '；' + '；'.join(f'{k}:{v}' for k, v in errors.items())
            jump_txt = f'（{"、".join(_jumped)} 已在浏览器打开）' if _jumped else ''
            summary = (f'共 {total} 条结果{jump_txt}{err_txt}。'
                       f'双击法规/政策文件 = 软件内预览并定位关键词，'
                       f'可「下载入库」；标准（国标/行业/地方）双击打开'
                       f'官网在线预览/详情页')
            # 同义词默认参与检索，说明白扩展了什么，免得用户以为搜串了
            syns = self._web_synonyms(kw)
            if syns:
                summary += syn_describe({kw: syns})
            status_lbl.configure(text=summary)
            if total == 0 and not errors:
                status_lbl.configure(text=_empty_hint(kw))
                return
            if pending:
                _start_verify(pending, kw, summary)

        def _empty_hint(kw):
            """零结果时说清原因，并给出正式词建议。"""
            alts = websearch.expand_keyword(kw)[1:]
            tip = (f'法规库 / 政策库 / 标准库都没有「{kw}」的结果' if not alts
                   else f'「{kw}」是俗称，正式叫法试：'
                        + '、'.join(f'「{a}」' for a in alts))
            if _jumped:
                tip += f'；{"、".join(_jumped)} 已在浏览器打开'
            return tip

        # ---- 正文核对 ----
        # 法规库只有分词模糊匹配，搜「专职安全员」会返回一大票只含「安全」
        # 的法规，光看标题分不出真假，所以抓正文实测一遍：含关键词的置顶。
        def _count_hits(text, terms):
            """正文里命中几处；-1 表示官方取不到正文（扫描件）。"""
            bd = websearch.body_of(text)
            if not bd:
                return -1
            rx = re.compile('|'.join(re.escape(t) for t in terms))
            return len(rx.findall(bd))

        def _hit_cell(n):
            if n < 0:
                return '无正文'
            return f'✓ {n} 处' if n else '✕ 无'

        def _set_cell(iid, cell):
            if iid in _iid_item and tree.exists(iid):
                tree.set(iid, 'verify', cell)

        def _start_verify(pending, kw, summary):
            seq = _vseq['n']
            n_top = min(len(pending), _VERIFY_TOP)
            terms = self._web_locate_terms(kw)

            def _work():
                hit_ids = []
                for k, (iid, item) in enumerate(pending[:n_top], 1):
                    if _vseq['n'] != seq:
                        return                   # 换关键词了，本轮作废
                    win.after(0, lambda k=k, t=n_top: status_lbl.configure(
                        text=f'{summary}　·　正文核对中… {k}/{t}'))
                    try:
                        with _fetch_lock:
                            text, _p, _b = websearch.fetch_item_text(
                                item, save_dir)
                        cell = _hit_cell(_count_hits(text, terms))
                    except Exception:
                        cell = '获取失败'
                    if cell.startswith('✓'):
                        hit_ids.append(iid)
                    win.after(0, lambda i=iid, c=cell: _set_cell(i, c))
                if _vseq['n'] != seq:
                    return
                win.after(0, lambda: _finish_verify(hit_ids, kw, summary))

            threading.Thread(target=_work, daemon=True).start()

        def _finish_verify(hit_ids, kw, summary):
            # 含关键词的搬到最前面（倒着搬，保持原有先后顺序）
            for iid in reversed(hit_ids):
                if tree.exists(iid):
                    tree.move(iid, '', 0)
            # 超出核对条数的那些保持「未核对」，免得看着像已经查过
            for iid in tree.get_children():
                if tree.set(iid, 'verify') == '核对中':
                    tree.set(iid, 'verify', '未核对')
            checked = sum(
                1 for iid in tree.get_children()
                if _iid_item.get(iid, {}).get('source') in websearch.TEXT_SOURCES
                and tree.set(iid, 'verify') not in ('核对中', '未核对'))
            tail = (f'{len(hit_ids)} 条含「{kw}」，已置顶' if hit_ids
                    else f'没有一条正文含「{kw}」（法规库/政策库按分词模糊'
                         f'匹配，这几条是噪音）')
            status_lbl.configure(
                text=f'{summary}　·　已抓取比对 {checked} 条全文：{tail}')

        def _get_selected_items():
            sel = tree.selection()
            if not sel:
                return []
            return [_iid_item[iid] for iid in sel if iid in _iid_item]

        def _open_web():
            items = _get_selected_items()
            if not items:
                status_lbl.configure(text='请先选中一条结果')
                return
            tshort = (items[0].get('title') or '')[:24]
            _open_web_item(items[0], f'已在浏览器打开《{tshort}》详情页')

        save_dir = os.path.join(DATA_ROOT, 'web_downloads')
        os.makedirs(save_dir, exist_ok=True)

        def _open_save_dir():
            try:
                os.startfile(save_dir)
            except Exception as e:
                status_lbl.configure(text=f'打开目录失败：{e}')

        def _open_item():
            """双击结果：法规/政策文件在软件内预览并定位关键词；其余走浏览器。"""
            items = _get_selected_items()
            if not items:
                status_lbl.configure(text='请先选中一条结果')
                return
            item = items[0]
            if item.get('source') not in websearch.TEXT_SOURCES:
                if item.get('source') == 'samr':
                    _open_web_item(
                        item, '国标正文是官方图片版预览，软件内取不到文字；'
                              '已在浏览器打开在线预览，可用页面里的搜索框找词'
                              '（标题命中见「命中」列）')
                elif item.get('source') in websearch.TITLE_VERIFY:
                    _open_web_item(
                        item, '已在浏览器打开官方详情页：标准正文在页内'
                              '查看/下载（部分需登录），标题命中见「命中」列')
                else:
                    _open_web_item(item, '该来源取不到正文，已在浏览器打开详情页')
                return
            if _fetching['on']:
                status_lbl.configure(text='正在抓取上一条的正文，请稍候…')
                return
            kw = _last_kw['v'] or kw_var.get().strip()
            tshort = (item.get('title') or '')[:24]
            _fetching['on'] = True

            def _prog(cur, tot):
                t = (f'正在抓取《{tshort}》正文… 第 {cur}/{tot} 页' if tot > 0
                     else f'正在抓取《{tshort}》正文…')
                win.after(0, lambda: status_lbl.configure(text=t))

            def _work():
                try:
                    with _fetch_lock:
                        text, path, body = websearch.fetch_item_text(
                            item, save_dir, progress=_prog)
                except Exception as e:
                    record_error('websearch', e, '')
                    msg = f'抓取正文失败：{e}'
                    win.after(0, lambda: status_lbl.configure(text=msg))
                    return
                finally:
                    _fetching['on'] = False
                # 顺手把这一行的「命中」补上（自动核对没轮到的行也能填）
                n = _count_hits(text, self._web_locate_terms(kw))
                iid = next((k for k, v in _iid_item.items() if v is item), None)
                if iid:
                    win.after(0, lambda: _set_cell(iid, _hit_cell(n)))
                win.after(0, lambda: self._show_web_preview(
                    item, kw, text, path, body))
                win.after(0, lambda: status_lbl.configure(
                    text=f'已在软件内打开《{tshort}》，定位到「{kw}」'
                         f'（可点「上一处 / 下一处」逐个查看）'))

            threading.Thread(target=_work, daemon=True).start()

        def _open_web_item(item, ok_text):
            url = websearch.open_url_of(item)
            if url:
                webbrowser.open(url)
                status_lbl.configure(text=ok_text)
            else:
                status_lbl.configure(text='该结果无可用链接')

        def _download(all_=False):
            items = _results if all_ else _get_selected_items()
            if not items:
                status_lbl.configure(text='请先选中一条结果')
                return
            dl_items = [it for it in items
                        if it['source'] in websearch.TEXT_SOURCES]
            if not dl_items:
                if any(it['source'] in websearch.TITLE_VERIFY for it in items):
                    status_lbl.configure(
                        text='标准类来源请在官网详情页查看/下载：选中后点'
                             '「查看网页」打开（国标是官方图片版在线预览）')
                else:
                    status_lbl.configure(
                        text='该来源仅支持网页跳转，请在浏览器中手动下载')
                return

            def _tp(it):
                return websearch.target_path_for(it, save_dir)

            def _in_lib(p):
                """该 txt 是否真的已入库。
                只判存在会把「软件内预览时缓存下来的正文」误当成已入库，
                用户点「否」后就什么也没发生（文件其实没进库）。"""
                try:
                    return is_registered(DB_PATH, p)
                except Exception:
                    return False

            # 查重：同名 txt 已存在**且已入库**才算下载过
            existing = [it for it in dl_items
                        if os.path.exists(_tp(it)) and _in_lib(_tp(it))]
            redownload = set()
            if existing:
                if all_:
                    ans = messagebox.askyesnocancel(
                        '发现已下载文件',
                        f'有 {len(existing)} 个文件此前已下载并入库。\n\n'
                        f'「是」= 全部重新下载\n'
                        f'「否」= 跳过已存在，只下载新文件\n'
                        f'「取消」= 本次不下载',
                        parent=win)
                    if ans is None:
                        return
                    if ans:
                        redownload = {id(it) for it in existing}
                else:
                    names = '、'.join(f'《{it["title"]}》'
                                      for it in existing[:3])
                    if len(existing) > 3:
                        names += f' 等 {len(existing)} 个'
                    if messagebox.askyesno(
                            '文件已下载过',
                            f'{names}已下载并入库，是否重新下载？\n\n'
                            f'选「否」则跳过。', parent=win):
                        redownload = {id(it) for it in existing}
            # 需要走网络的：本次要求重下 或 磁盘上还没有
            todo = [it for it in dl_items
                    if id(it) in redownload or not os.path.exists(_tp(it))]
            todo_ids = {id(it) for it in todo}
            # 磁盘已有又不用重下的：预览缓存下来的（未入库）直接复用入库，
            # 已入库的那批是用户选择跳过的，不再动
            reuse = [it for it in dl_items
                     if id(it) not in todo_ids and not _in_lib(_tp(it))]
            if not todo and not reuse:
                status_lbl.configure(
                    text=f'所选 {len(dl_items)} 个文件均已入库，无需重复下载')
                return

            dl_btn.configure(state='disabled')
            dl_all_btn.configure(state='disabled')
            n_total = len(todo)

            def _ui_progress(idx, tshort, cur, total):
                if total > 0:
                    status_lbl.configure(
                        text=f'下载中 {idx}/{n_total}《{tshort}》… 第 {cur}/{total} 页')
                else:
                    status_lbl.configure(
                        text=f'下载中 {idx}/{n_total}《{tshort}》… 正在解析')

            def _work():
                paths = [_tp(it) for it in reuse]   # 预览缓存过 → 免下载直接入库
                errs = []
                for idx, it in enumerate(todo, 1):
                    tshort = (it.get('title') or '')[:24]
                    try:
                        p = websearch.download_item(
                            it, save_dir,
                            progress=lambda c, n, i=idx, ts=tshort:
                                win.after(0, lambda: _ui_progress(i, ts, c, n)))
                        paths.append(p)
                    except Exception as e:
                        errs.append(f"{it['title']}: {e}")
                win.after(0, lambda: _after_download(paths, errs, len(reuse)))

            threading.Thread(target=_work, daemon=True).start()

        def _after_download(paths, errs, n_reuse=0):
            dl_btn.configure(state='normal')
            dl_all_btn.configure(state='normal')
            for e in errs:
                record_error('websearch', e, '')
            parts = []
            n_new = max(len(paths) - n_reuse, 0)
            if n_new:
                parts.append(f'新下载 {n_new} 个')
            if n_reuse:
                parts.append(f'复用已缓存的正文 {n_reuse} 个')
            if paths:
                parts.append(f'已入库 {len(paths)} 个')
            if errs:
                parts.append(f'失败 {len(errs)} 个')
            status_lbl.configure(
                text=('；'.join(parts) if parts else '没有文件被下载')
                + f'（保存目录：{save_dir}）')
            if paths:
                self._toast('联网文件已下载并入库，回主窗口即可检索全文')
                self._run_import_progress(paths, link_mode='copy',
                                          preplans={},
                                          title='下载入库')

        # 双击 = 软件内预览并定位搜索词（法规）；国标等取不到正文的来源走浏览器
        tree.bind('<Double-Button-1>', lambda e: _open_item())
        kw_entry.focus_set()

    def _db_size_text(self):
        """索引库 + 备份目录占用摘要"""
        def _dir_mb(p):
            if not os.path.isdir(p):
                return 0.0
            total = 0
            try:
                for f in os.scandir(p):
                    try:
                        if f.is_file():
                            total += f.stat().st_size
                    except OSError:
                        continue
            except OSError:
                pass
            return total / 1048576
        db_mb = 0.0
        try:
            if os.path.exists(DB_PATH):
                db_mb = os.path.getsize(DB_PATH) / 1048576
        except OSError:
            pass
        bk_mb = _dir_mb(BACKUP_DIR)
        return (f'索引 {db_mb:.0f}MB · 备份 {bk_mb:.0f}MB '
                f'（{self._max_backups()} 份）· 合计 {db_mb + bk_mb:.0f}MB')

    def _run_db_maintenance(self, win, info_lbl, size_lbl):
        """库整理：ANALYZE 生成查询计划统计 + PRAGMA optimize + VACUUM 回收空间。
        需独占写锁，放后台线程执行，期间禁用按钮避免并发写。"""
        if getattr(self, '_db_maint_running', False):
            return
        # 空间预检：VACUUM 需要约 1.2 倍库大小的空闲空间
        if os.path.exists(DB_PATH):
            free = shutil.disk_usage(os.path.dirname(DB_PATH)).free
            need = os.path.getsize(DB_PATH) * 1.2
            if free < need:
                messagebox.showwarning(
                    '磁盘空间不足',
                    f'执行库整理（VACUUM）需要约 {need/1048576:.0f}MB 空闲空间，'
                    f'当前磁盘仅剩 {free/1048576:.0f}MB。\n'
                    '请先清理磁盘空间后再试。', parent=win)
                return
        if not messagebox.askyesno(
                '库整理',
                '将对索引库执行统计与压缩（VACUUM）。\n'
                '大库可能需要数秒，期间请勿搜索或入库。\n\n确定开始？',
                parent=win):
            return
        self._db_maint_running = True
        info_lbl.configure(text='库整理中…（压缩大库可能需要数秒）')
        before = os.path.getsize(DB_PATH) if os.path.exists(DB_PATH) else 0

        def _work():
            import sqlite3
            msg = '库整理完成'
            try:
                conn = sqlite3.connect(DB_PATH, timeout=60)
                try:
                    conn.execute('PRAGMA busy_timeout=60000')
                    conn.execute('ANALYZE')          # 生成 sqlite_stat1 统计
                    conn.execute('PRAGMA optimize')  # 按需刷新统计
                    conn.commit()
                    conn.isolation_level = None      # VACUUM 不能在事务内
                    conn.execute('VACUUM')
                finally:
                    conn.close()
            except Exception as e:
                msg = f'库整理失败：{str(e)[:60]}'
            after = os.path.getsize(DB_PATH) if os.path.exists(DB_PATH) else 0

            def _done():
                self._db_maint_running = False
                saved = (before - after) / 1048576
                text = msg + (f' · 释放 {saved:.1f}MB' if saved > 0.1 else '')
                try:
                    info_lbl.configure(text=text)
                    size_lbl.configure(text=self._db_size_text())
                except Exception:
                    pass
                self.status.configure(text=text)
            self.root.after(0, _done)

        threading.Thread(target=_work, daemon=True).start()

    # -------- 搜索模板 --------


    def show_search_templates(self):
        """搜索模板管理：双击应用，选中可删除"""
        win = self._get_win('search_templates')
        if win is None:
            return
        win.title('搜索模板')
        self._center_win(win, 720, 420, modal=False)
        win.transient(self.root)
        self._dialog_header(
            win, self.IC_SEARCH, '搜索模板',
            '双击模板恢复条件并立即搜索 · 选中后可删除')
        body = ttk.Frame(win)
        body.pack(fill='both', expand=True, padx=8, pady=8)
        cols = ('name', 'kw', 'mode', 'ft', 'syn')
        tree = ttk.Treeview(body, columns=cols, show='headings')
        tree.heading('name', text='模板名')
        tree.heading('kw', text='关键词')
        tree.heading('mode', text='模式')
        tree.heading('ft', text='文件类型')
        tree.heading('syn', text='同义词')
        tree.column('name', width=160)
        tree.column('kw', width=220)
        tree.column('mode', width=70, anchor='center')
        tree.column('ft', width=90, anchor='center')
        tree.column('syn', width=70, anchor='center')
        vsb = ttk.Scrollbar(body, orient='vertical', command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        tree.pack(side='left', fill='both', expand=True)
        vsb.pack(side='right', fill='y')

        templates = self.cfg.setdefault('saved_searches', [])

        def reload_tree():
            tree.delete(*tree.get_children())
            for i, t in enumerate(templates):
                tree.insert('', 'end', iid=str(i), values=(
                    t.get('name', ''), t.get('kw', ''),
                    '模糊' if t.get('mode') == 'fuzzy' else '精确',
                    t.get('ft', '全部'),
                    '开' if t.get('syn') else '关'))

        def apply_sel(_evt=None):
            sel = tree.selection()
            if not sel:
                return
            self._apply_search_template(templates[int(sel[0])])
            win.destroy()

        def delete_sel():
            sel = tree.selection()
            if not sel:
                return
            t = templates[int(sel[0])]
            if messagebox.askyesno('确认', f'删除模板「{t.get("name")}」？',
                                   parent=win):
                del templates[int(sel[0])]
                self._save_config()
                reload_tree()

        tree.bind('<Double-1>', apply_sel)
        btns = ttk.Frame(win)
        btns.pack(fill='x', padx=8, pady=(0, 8))
        ttk.Button(btns, text='应用', style='Accent.TButton',
                   command=apply_sel).pack(side='left')
        ttk.Button(btns, text='删除', style='Danger.TButton',
                   command=delete_sel).pack(side='left', padx=6)

        def save_current():
            # 原更多菜单「保存当前搜索为模板」并入本窗口
            self._save_search_template()
            reload_tree()

        ttk.Button(btns, text='保存当前搜索为模板…',
                   command=save_current).pack(side='left', padx=6)
        ttk.Button(btns, text='关闭', command=win.destroy).pack(side='right')
        reload_tree()

    # -------- 待入库文件确认 --------
    # 支持的入库格式
    _INBOX_EXTS = {'.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx',
                   '.txt', '.md', '.csv', '.jpg', '.jpeg', '.png', '.webp',
                   '.bmp', '.gif', '.zip', '.rar', '.7z'}







    def show_inbox_review(self):
        """待入库文件确认对话框：扫描 inbox + 下载目录，多选入库"""
        win = self._get_win('inbox_review')
        if win is None:
            return
        win.title('待入库文件')
        self._center_win(win, 780, 480, modal=False)
        win.transient(self.root)
        self._dialog_header(
            win, self.IC_DOWNLOAD, '待入库文件',
            '勾选要入库的文件（Ctrl/Shift 多选）· 入库后自动索引并分类')

        body = ttk.Frame(win)
        body.pack(fill='both', expand=True, padx=8, pady=(0, 4))
        cols = ('name', 'size', 'mtime', 'source')
        tree = ttk.Treeview(body, columns=cols, show='headings',
                            selectmode='extended')
        tree.heading('name', text='文件名')
        tree.heading('size', text='大小')
        tree.heading('mtime', text='修改时间')
        tree.heading('source', text='来源')
        tree.column('name', width=340)
        tree.column('size', width=80, anchor='e')
        tree.column('mtime', width=110, anchor='center')
        tree.column('source', width=90, anchor='center')
        vsb = ttk.Scrollbar(body, orient='vertical', command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        tree.pack(side='left', fill='both', expand=True)
        vsb.pack(side='right', fill='y')

        all_files = []

        def reload_tree():
            tree.delete(*tree.get_children())
            all_files.clear()
            inbox_files = self._scan_inbox_files()
            dl_files = self._scan_download_files()
            all_files.extend(inbox_files + dl_files)
            for i, f in enumerate(all_files):
                tree.insert('', 'end', iid=str(i), values=(
                    f['name'], fmt_size(f['size']),
                    fmt_mtime(f['mtime']), f['source']))
            # 默认全选
            tree.selection_set(tree.get_children())

        def _import_selected():
            sel = tree.selection()
            if not sel:
                self.status.configure(text='未选择任何文件')
                return
            paths = [all_files[int(i)] for i in sel]
            win.destroy()
            self.status.configure(
                text=f'正在入库 {len(paths)} 个文件…')
            self.root.update_idletasks()

            def _do_import():
                ok = fail = 0
                fails = []
                for f in paths:
                    try:
                        _, status, *_ = import_file_to_library(
                            f['path'], DB_PATH)
                        if status == 'ok':
                            ok += 1
                            # 入库成功后删除 inbox 原件（下载目录不动）
                            if f['source'] != '下载':
                                try:
                                    os.remove(f['path'])
                                except OSError:
                                    pass
                        else:
                            fail += 1
                            fails.append(f['name'])
                    except Exception:
                        fail += 1
                        fails.append(f['name'])
                msg = f'入库完成：{ok} 成功'
                if fail:
                    msg += f' · {fail} 失败'
                self.root.after(0, lambda m=msg: (
                    self.status.configure(text=m),
                    self._refresh_err_count()))

            threading.Thread(target=_do_import, daemon=True).start()

        def _delete_selected():
            sel = tree.selection()
            if not sel:
                self.status.configure(text='未选择任何文件')
                return
            names = [all_files[int(i)]['name'] for i in sel]
            if not messagebox.askyesno(
                    '确认删除',
                    f'将删除 {len(sel)} 个文件：\n'
                    + '\n'.join(names[:5])
                    + ('\n…' if len(names) > 5 else ''),
                    parent=win):
                return
            for i in sel:
                f = all_files[int(i)]
                try:
                    os.remove(f['path'])
                except OSError:
                    pass
            reload_tree()
            self.status.configure(text=f'已删除 {len(sel)} 个文件')

        def _select_all():
            tree.selection_set(tree.get_children())

        def _select_none():
            tree.selection_set()

        btns = ttk.Frame(win)
        btns.pack(fill='x', padx=8, pady=(0, 8))
        ttk.Button(btns, text='全选', width=6,
                   command=_select_all).pack(side='left')
        ttk.Button(btns, text='全不选', width=6,
                   command=_select_none).pack(side='left', padx=(4, 12))
        ttk.Button(btns, text='入库选中', style='Accent.TButton',
                   command=_import_selected).pack(side='left')
        ttk.Button(btns, text='删除选中', style='Danger.TButton',
                   command=_delete_selected).pack(side='left', padx=6)
        ttk.Button(btns, text='忽略本次',
                   command=win.destroy).pack(side='right')
        reload_tree()
        if not tree.get_children():
            win.destroy()
            self.status.configure(text='没有待入库的文件')

    def _export_bookmark_digest(self, bms):
        """收藏条款一键整理成《常用制度摘编》Word，可直接打印下发。
        按文件分组：文件名 + 状态/文号/施行日期，每条带章节定位和完整条款。"""
        if not bms:
            messagebox.showinfo('提示', '还没有收藏条款')
            return
        fn = filedialog.asksaveasfilename(
            initialdir=self._export_dir(),
            initialfile=f'常用制度摘编_{time.strftime("%Y%m%d")}.docx',
            defaultextension='.docx',
            filetypes=[('Word 文档', '*.docx')])
        if not fn:
            return
        sections = []
        for path, items in by_path.items():
            sections.append((path, [
                (b['title'] or f'第 {b["line_no"]} 行', b['line_no'])
                for b in items]))
        subtitle = (time.strftime('整理日期：%Y-%m-%d')
                    + f' · 共 {len(bms)} 条收藏 · 由 镜仔搜索 自动生成')
        if not self._write_digest_docx(fn, '常用制度摘编', subtitle, sections):
            return
        self.status.configure(text=f'摘编已导出：{os.path.basename(fn)}')
        messagebox.showinfo(
            '导出成功', f'《常用制度摘编》已保存到：\n{fn}\n\n可直接打印或下发')



    # -------- 引用关系 --------
    def show_refs(self):
        """当前预览文件的引用关系窗口（引用了谁 / 被谁引用）
        强化：显示目标状态，废止/被替代目标红标 + 底部预警条"""
        if not self._cur_path:
            return
        path = self._cur_path
        win = self._get_win('refs')
        if win is None:
            return
        win.title(f'引用关系 · {os.path.basename(path)}')
        self._center_win(win, 980, 540, modal=False)   # 浏览型，不锁主界面
        win.transient(self.root)
        nb = ttk.Notebook(win)
        nb.pack(fill='both', expand=True, padx=8, pady=8)

        status_map = {'active': '有效', 'void': f'{self.IC_WARN}废止', 'replaced': f'{self.IC_REFRESH}已替代'}
        n_dead = 0

        def make_tab(tab_name, rows, direction):
            nonlocal n_dead
            frame = ttk.Frame(nb)
            nb.add(frame, text=f'{tab_name}（{len(rows)}）')
            cols = ('name', 'clause', 'status', 'line')
            tree = ttk.Treeview(frame, columns=cols, show='headings')
            if direction == 'out':
                tree.heading('name', text='引用的文件')
                tree.heading('clause', text='条款')
                tree.heading('status', text='对方状态')
                tree.heading('line', text='定位')
            else:
                tree.heading('name', text='来源文件')
                tree.heading('clause', text='引用条款')
                tree.heading('status', text='来源状态')
                tree.heading('line', text='出处行')
            tree.column('name', width=420)
            tree.column('clause', width=120, anchor='center')
            tree.column('status', width=80, anchor='center')
            tree.column('line', width=100, anchor='center')
            vsb = ttk.Scrollbar(frame, orient='vertical', command=tree.yview)
            tree.configure(yscrollcommand=vsb.set)
            tree.pack(side='left', fill='both', expand=True)
            vsb.pack(side='right', fill='y')
            tree.tag_configure('deadrow', foreground='#C13A28')
            for i, r in enumerate(rows):
                if direction == 'out':
                    name = os.path.basename(r['target_path'])
                    st_lbl = status_map.get(r.get('target_status', 'active'),
                                            '有效')
                    loc = (f"第 {r['target_line']} 行"
                           if r['target_line'] else '（未定位）')
                    dead = r.get('target_status') in ('void', 'replaced')
                    if dead:
                        n_dead += 1
                    tree.insert('', 'end', iid=str(i),
                                tags=('deadrow',) if dead else (),
                                values=(name, r['clause'] or '（仅文件）',
                                        st_lbl, loc))
                else:
                    st_lbl = status_map.get(r.get('status', 'active'), '有效')
                    tree.insert('', 'end', iid=str(i),
                                values=(r['name'], r['clause'] or '（仅文件）',
                                        st_lbl, f"第 {r['line_no']} 行"))

            def _row_from_iid(iid):
                """按 tree iid 取原始数据行"""
                if not iid:
                    return None
                try:
                    return rows[int(iid)]
                except Exception:
                    return None

            def on_open_evt(evt=None):
                """单击/双击 = 打开原文件（用 identify_row 拿行，避开 selection 时序问题）"""
                iid = None
                if evt is not None:
                    # 从事件坐标精确取行（最可靠）
                    iid = tree.identify_row(evt.y)
                if not iid:
                    # 回退：已选中行
                    sel = tree.selection()
                    if sel:
                        iid = sel[0]
                r = _row_from_iid(iid)
                if not r:
                    return
                if direction == 'out':
                    path = r['target_path']
                    line = r['target_line'] or 1
                else:
                    path = r['path']
                    line = r['line_no']
                if not os.path.exists(path):
                    messagebox.showinfo('提示',
                        f'文件不存在：\n{path}\n（可能已被移动或删除）')
                    return
                self._open_located(path, line)
                return 'break'  # 阻止 Treeview 默认单击选中行为

            def on_goto_evt(evt=None):
                """右键菜单→在预览中跳转"""
                iid = None
                if evt is not None:
                    iid = tree.identify_row(evt.y)
                if not iid:
                    sel = tree.selection()
                    if sel:
                        iid = sel[0]
                r = _row_from_iid(iid)
                if not r:
                    return
                if direction == 'out':
                    self._show_preview(r['target_path'],
                                       r['target_line'] or 1)
                else:
                    self._show_preview(r['path'], r['line_no'])
                self.root.lift()

            def _ctx_menu(evt):
                sel = tree.identify_row(evt.y)
                if not sel:
                    return
                tree.selection_set(sel)
                r = _row_from_iid(sel)
                if not r:
                    return
                ctx = tk.Menu(tree, tearoff=0)
                if direction == 'out':
                    target_path = r['target_path']
                    target_name = os.path.basename(target_path)
                    if os.path.exists(target_path):
                        ctx.add_command(
                            label=f'{self.IC_FOLDER} 打开《{target_name}》原文件',
                            command=lambda: on_open_evt())
                    else:
                        ctx.add_command(
                            label=f'{self.IC_FOLDER} 《{target_name}》（文件不存在）',
                            state='disabled')
                    ctx.add_command(
                        label=f'{self.IC_LINK} 在预览中跳转',
                        command=lambda: on_goto_evt())
                else:
                    p = r['path']
                    n = r['name']
                    if os.path.exists(p):
                        ctx.add_command(
                            label=f'{self.IC_FOLDER} 打开《{n}》原文件',
                            command=lambda: on_open_evt())
                    ctx.add_command(
                        label=f'{self.IC_LINK} 在预览中跳转',
                        command=lambda: on_goto_evt())
                ctx.tk_popup(evt.x_root, evt.y_root)

            # 只绑单击：双击时 Button-1 会先触发两次，重复绑定会打开两个标签
            tree.bind('<Button-1>', on_open_evt)
            tree.bind('<Button-3>', _ctx_menu)
            return tree

        outs = outgoing_refs(DB_PATH, path)
        ins = incoming_refs(DB_PATH, path)
        make_tab('引用的文件', outs, 'out')
        make_tab('被这些文件引用', ins, 'in')
        if not outs and not ins:
            ttk.Label(win, text='该文件暂未发现库内引用关系',
                      foreground='gray').place(relx=0.5, rely=0.5,
                                               anchor='center')
        # 引用预警条：本文件引用了已失效文件时红色提示
        if n_dead:
            bar = tk.Label(win, text=f'{self.IC_WARN} 预警：本文件有 {n_dead} 处引用指向'
                                     f'已废止/被替代的文件（红行），依据可能失效',
                           bg='#C00000', fg='white', anchor='w', padding=6)
            bar.pack(fill='x', side='bottom')

    # -------- 引用预警（全库扫描） --------
    def _check_incoming_bg(self, paths, win):
        """标记废止后，后台扫描谁引用了这些文件（一次全表扫），
        避免大库时主线程 LIKE 扫描冻结界面"""
        def work():
            try:
                pairs = incoming_refs_multi(DB_PATH, paths)
            except Exception:
                pairs = []
            self.root.after(0, lambda: self._incoming_warned(pairs, win))
        threading.Thread(target=work, daemon=True).start()


    def show_ref_warnings(self, parent_win=None):
        """全库引用预警：引用已废止文件 / 引用未入库文件，双击跳转出处"""
        if not os.path.exists(DB_PATH):
            messagebox.showinfo('提示', '库为空，请先导入文件')
            return
        w = ref_network_warnings(DB_PATH)
        win = self._get_win('refwarn')
        if win is None:
            return
        win.title('引用预警')
        self._center_win(win, 1050, 594, modal=False)   # 浏览型，不锁主界面
        win.transient(parent_win or self.root)
        self._dialog_header(
            win, self.IC_WARN, '引用预警',
            '引用已废止/已替代文件，或引用了库外文件 · 双击跳转出处')
        nb = ttk.Notebook(win)
        nb.pack(fill='both', expand=True, padx=8, pady=(8, 0))

        status_map = {'void': f'{self.IC_WARN}废止', 'replaced': f'{self.IC_REFRESH}已替代'}

        def make_tab(tab_name, rows, kind):
            frame = ttk.Frame(nb)
            nb.add(frame, text=f'{tab_name}（{len(rows)}）')
            cols = ('src', 'title', 'clause', 'status', 'line')
            tree = ttk.Treeview(frame, columns=cols, show='headings')
            tree.heading('src', text='来源文件')
            tree.heading('title', text='引用的《文件》')
            tree.heading('clause', text='条款')
            tree.heading('status', text='对方状态' if kind == 'dead' else '提示')
            tree.heading('line', text='出处行')
            tree.column('src', width=300)
            tree.column('title', width=330)
            tree.column('clause', width=90, anchor='center')
            tree.column('status', width=90, anchor='center')
            tree.column('line', width=70, anchor='center')
            vsb = ttk.Scrollbar(frame, orient='vertical', command=tree.yview)
            tree.configure(yscrollcommand=vsb.set)
            tree.pack(side='left', fill='both', expand=True)
            vsb.pack(side='right', fill='y')
            tree.tag_configure('deadrow', foreground='#C13A28')
            tree.tag_configure('dangrow', foreground='#C05621')
            for i, r in enumerate(rows):
                if kind == 'dead':
                    st = status_map.get(r['tgt_status'], r['tgt_status'])
                    tree.insert('', 'end', iid=str(i), tags=('deadrow',),
                                values=(r['src_name'], f'《{r["title"]}》',
                                        r['clause'] or '（仅文件）', st,
                                        r['line_no']))
                else:
                    tree.insert('', 'end', iid=str(i), tags=('dangrow',),
                                values=(r['src_name'], f'《{r["title"]}》',
                                        r['clause'] or '（仅文件）',
                                        '未入库', r['line_no']))

            def on_jump(_evt=None):
                sel = tree.selection()
                if not sel:
                    return
                r = rows[int(sel[0])]
                self._show_preview(r['src_path'], r['line_no'])
                self.root.lift()

            tree.bind('<Double-1>', on_jump)
            return tree

        make_tab(f'{self.IC_WARN} 引用已废止/被替代文件', w['dead'], 'dead')
        make_tab(f'{self.IC_WARN} 引用未入库文件', w['dangling'], 'dangling')
        # 底部：说明 + 导出
        bottom = ttk.Frame(win, padding=(8, 6))
        bottom.pack(fill='x')
        ttk.Label(bottom, foreground='gray', text=(
            '风险说明：红页 = 引用的依据文件已失效，引用方需同步修订；'
            '橙页 = 引用的文件不在库中，请核对名称或补充入库。'
            '双击行跳转出处。')).pack(side='left')

        def export_csv():
            import csv as _csv
            fn = filedialog.asksaveasfilename(
                title='导出预警清单', defaultextension='.csv',
                initialdir=self._export_dir(),
                initialfile='引用预警.csv',
                filetypes=[('CSV 文件', '*.csv')])
            if not fn:
                return
            try:
                with open(fn, 'w', newline='', encoding='utf-8-sig') as f:
                    wr = _csv.writer(f)
                    wr.writerow(['类别', '来源文件', '引用的《文件》', '条款',
                                 '对方状态', '出处行'])
                    for r in w['dead']:
                        wr.writerow(['引用已废止/被替代文件', r['src_name'],
                                     f'《{r["title"]}》', r['clause'] or '',
                                     status_map.get(r['tgt_status'],
                                                    r['tgt_status']),
                                     r['line_no']])
                    for r in w['dangling']:
                        wr.writerow(['引用未入库文件', r['src_name'],
                                     f'《{r["title"]}》', r['clause'] or '',
                                     '未入库', r['line_no']])
                self.status.configure(text=f'预警清单已导出: {fn}')
            except Exception as e:
                messagebox.showerror('导出失败', str(e))

        ttk.Button(bottom, text='导出 CSV', style='Accent.TButton',
                   command=export_csv).pack(side='right')
        ttk.Button(bottom, text='关闭',
                   command=win.destroy).pack(side='right', padx=4)
        if not w['dead'] and not w['dangling']:
            ttk.Label(win, text='✓ 未发现引用风险',
                      foreground='#0A7A2F').place(relx=0.5, rely=0.5,
                                                  anchor='center')

    def _show_compliance_gap(self, parent_win=None):
        """合规 Gap 扫描：输入关键词 → 拉库内相关文件的所有引用 → 对比已入库 vs 缺失"""
        kw = simpledialog.askstring(
            '合规 Gap 扫描',
            '请输入关键词（如：深基坑、高处作业、脚手架、吊装）：',
            parent=parent_win or self.root)
        if not kw:
            return
        self.status.configure(text=f'正在扫描「{kw}」相关合规缺口…')
        self.root.update_idletasks()
        try:
            res = compliance_gap_scan(DB_PATH, kw)
        except Exception as e:
            messagebox.showerror('扫描失败', str(e))
            self.status.configure(text='')
            return
        self.status.configure(text='')
        if not res or res['n_files'] == 0:
            messagebox.showinfo('结果',
                f'库内未找到包含「{kw}」的文件。')
            return
        win = self._get_win('gap')
        if win is None:
            return
        win.title(f'合规 Gap 扫描：「{kw}」')
        self._center_win(win, 900, 560, modal=False)
        win.transient(parent_win or self.root)

        # 顶部统计条
        top = ttk.Frame(win, padding=(10, 8))
        top.pack(fill='x')
        nf = res['n_files']
        nu = res['n_unique']
        ni = res['n_in_lib']
        nm = res['n_missing']
        pct = (ni / nu * 100) if nu > 0 else 0
        ttk.Label(top, text=(
            f'{self.IC_SEARCH} 关键词「{kw}」：库内 {nf} 个文件关联，'
            f'外部引用 {nu} 个，已入库 {ni}，缺失 {nm} '
            + (f'（覆盖率 {pct:.0f}%）' if nu else '（未检测到外部引用，暂无覆盖率）')),
            font=('Microsoft YaHei UI', 10)).pack(side='left')

        # 两个 Tab：已入库 / 缺失
        nb = ttk.Notebook(win)
        nb.pack(fill='both', expand=True, padx=8, pady=(0, 8))

        status_labels = {'active': f'{self.IC_CHECK}已入库', 'void': f'{self.IC_WARN}废止', 'replaced': f'{self.IC_REFRESH}已替代'}

        def make_tab(title, refs, show_search_btn=False):
            frame = ttk.Frame(nb)
            nb.add(frame, text=f'{title}（{len(refs)}）')
            cols = ('title', 'clause', 'n_sources', 'status')
            tree = ttk.Treeview(frame, columns=cols, show='headings')
            tree.heading('title', text='引用的《文件》')
            tree.heading('clause', text='引用条款')
            tree.heading('n_sources', text='被引用次数')
            tree.heading('status', text='状态')
            tree.column('title', width=380)
            tree.column('clause', width=200)
            tree.column('n_sources', width=90, anchor='center')
            tree.column('status', width=100, anchor='center')
            vsb = ttk.Scrollbar(frame, orient='vertical', command=tree.yview)
            tree.configure(yscrollcommand=vsb.set)
            tree.pack(side='left', fill='both', expand=True)
            vsb.pack(side='right', fill='y')

            tree.tag_configure('miss', foreground='#CC0000')
            tree.tag_configure('dead', foreground='#CC6600')
            tree.tag_configure('alt', foreground='#888')

            for i, r in enumerate(refs):
                tag = ''
                if not r['in_lib']:
                    tag = 'miss'
                elif r['tgt_status'] in ('void', 'replaced'):
                    tag = 'dead'
                clauses = '、'.join(r['clauses']) if r['clauses'] else '（仅文件名引用）'
                st = status_labels.get(r['tgt_status'], r['tgt_status']) if r['in_lib'] else f'{self.IC_CAP_CLOSE} 未入库'
                tree.insert('', 'end', iid=str(i), tags=(tag,) if tag else (),
                            values=(f'《{r["title"]}》', clauses,
                                    f'{r["n_sources"]} 个文件', st))

            # 底部按钮行
            btns = ttk.Frame(frame)
            btns.pack(fill='x', pady=4, padx=4)

            def search_missing():
                sel = tree.selection()
                if not sel:
                    return
                r = refs[int(sel[0])]
                webbrowser.open(
                    f'https://www.baidu.com/s?wd={quote(r["title"])}')

            def goto_sources():
                sel = tree.selection()
                if not sel:
                    return
                r = refs[int(sel[0])]
                if r['sources']:
                    self._show_preview(r['sources'][0], 1)
                    self.root.lift()

            if show_search_btn:
                ttk.Button(btns, text=f'{self.IC_SEARCH} 搜官网下载', style='Accent.TButton',
                           command=search_missing).pack(side='right')
            ttk.Button(btns, text=f'{self.IC_PIN} 查看引用来源文件',
                       command=goto_sources).pack(side='right', padx=4)
            return tree

        missing_refs = [r for r in res['refs_found'] if not r['in_lib']]
        inlib_refs = [r for r in res['refs_found'] if r['in_lib']]
        make_tab(f'{self.IC_CAP_CLOSE} 缺失（需补入库）', missing_refs, show_search_btn=True)
        make_tab(f'{self.IC_CHECK} 已入库', inlib_refs)

        # 底部说明
        bottom = ttk.Frame(win, padding=(8, 4))
        bottom.pack(fill='x')
        ttk.Label(bottom, foreground='gray', text=(
            '说明：红色 = 引用了你库里没有的标准/文件，建议下载入库；'
            '橙色 = 对方已废止/被替代；'
            '左边 = 哪些文件在引用它。')).pack(side='left')

    def _show_std_update(self, parent_win=None):
        """检查标准更新（本地自查 + 一键跳转官网搜新版）"""
        self.status.configure(text='正在扫描标准文件状态…')
        self.root.update_idletasks()
        try:
            res = _scan_local(DB_PATH)
        except Exception as e:
            messagebox.showerror('扫描失败', str(e))
            self.status.configure(text='')
            return
        self.status.configure(text='')
        win = self._get_win('stdupdate')
        if win is None:
            return
        win.title('🔔 检查标准更新')
        self._center_win(win, 900, 560, modal=False)
        win.transient(parent_win or self.root)

        # 顶部统计
        top = ttk.Frame(win, padding=(10, 8))
        top.pack(fill='x')
        ttk.Label(top, text=(
            f'库内共 {res["n_total"]} 个文件，其中 {res["n_with_std"]} 个带标准号。'
            f'已废止 {res["n_void"]} 个，已替代 {res["n_replaced"]} 个。\n'
            '这里显示库内标记，是否有修订版本需在官网人工核验。\n'
            f'点击行 → 底部「{self.IC_SEARCH} 搜官网」在浏览器打开搜索结果页，'
            f'人工判断是否有新版；双击行 → 预览该文件。'),
            font=('Microsoft YaHei UI', 10)).pack(side='left', anchor='w')

        # Treeview
        cols = ('std_code', 'name', 'status')
        tree = ttk.Treeview(win, columns=cols, show='headings')
        tree.heading('std_code', text='标准号')
        tree.heading('name', text='文件名')
        tree.heading('status', text='库内状态')
        tree.column('std_code', width=160, anchor='center')
        tree.column('name', width=540)
        tree.column('status', width=100, anchor='center')
        vsb = ttk.Scrollbar(win, orient='vertical', command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        tree.pack(side='left', fill='both', expand=True, padx=(8, 0))
        vsb.pack(side='right', fill='y')

        tree.tag_configure('void', foreground='#CC0000')
        tree.tag_configure('replaced', foreground='#CC6600')
        tree.tag_configure('active', foreground='#222')

        status_labels = {'active': f'{self.IC_CHECK} 有效', 'void': f'{self.IC_WARN} 废止', 'replaced': f'{self.IC_REFRESH} 已替代'}
        for i, f in enumerate(res['files']):
            tag = f['status']
            tree.insert('', 'end', iid=str(i), tags=(tag,),
                        values=(f['std_code'].upper() if f['std_code'] else '-',
                                f['name'],
                                status_labels.get(f['status'], f['status'])))

        # 底部按钮
        bottom = ttk.Frame(win, padding=(8, 6))
        bottom.pack(fill='x')

        def search_selected():
            sel = tree.selection()
            if not sel:
                return
            f = res['files'][int(sel[0])]
            if f['std_code']:
                webbrowser.open(_build_search_url(f['std_code']))
            else:
                webbrowser.open(
                    f'https://www.baidu.com/s?wd={quote(f["name"])} 新版 修订')

        def batch_search_active():
            """明确数量后再打开官网搜索，不将本地状态当作在线核验。"""
            eligible=[f for f in res['files'] if f['status']=='active' and f['std_code']][:10]
            if eligible and not messagebox.askyesno('打开官网搜索',f'将打开 {len(eligible)} 个搜索标签页，供你人工核验新版。继续？',parent=win):
                return
            count = 0
            for f in res['files']:
                if f['status'] == 'active' and f['std_code']:
                    webbrowser.open(_build_search_url(f['std_code']))
                    count += 1
                    if count >= 10:  # 最多开 10 个标签页，防爆炸
                        break
            if count == 0:
                messagebox.showinfo('提示',
                    '已没有有效状态的标准需要检查（或都超过 10 个了）')

        def goto_preview():
            sel = tree.selection()
            if not sel:
                return
            f = res['files'][int(sel[0])]
            self._show_preview(f['path'], 1)
            self.root.lift()

        ttk.Button(bottom, text=f'{self.IC_GLOBE} 批量搜官网（最多10个）', style='Accent.TButton',
                   command=batch_search_active).pack(side='right', padx=4)
        ttk.Button(bottom, text=f'{self.IC_SEARCH} 搜官网（选中项）',
                   command=search_selected).pack(side='right', padx=4)
        ttk.Button(bottom, text=f'{self.IC_PIN} 预览文件',
                   command=goto_preview).pack(side='right')
        ttk.Button(bottom, text='关闭',
                   command=win.destroy).pack(side='left')

        # 双击 = 预览
        tree.bind('<Double-1>', lambda e: goto_preview())

        if res['n_void'] == 0 and res['n_replaced'] == 0 and res['n_with_std'] > 0:
            ttk.Label(win,
                      text='✓ 库内所有标准文件都标记为有效状态',
                      foreground='#0A7A2F').place(relx=0.5, rely=0.3,
                                                  anchor='center')

    # -------- 同义词库设置 --------
    def show_synonym_settings(self):
        """自定义同义词维护：填空式行编辑（主词 = 别名1, 别名2），存 config.json"""
        from synonyms import parse_custom
        win = self._get_win('synonym')
        if win is None:
            return
        win.title('同义词库设置')
        self._center_win(win, 600, 460)
        win.transient(self.root)
        self._dialog_header(
            win, self.IC_SWAP, '同义词库设置',
            '搜索时自动把关键词扩展为同义词一起命中（状态栏会提示）\n'
            '每行：主词 = 别名1, 别名2（逗号分隔）；内置 16 组常用词已自动生效')

        # 可滚动行编辑区
        outer = ttk.Frame(win)
        outer.pack(fill='both', expand=True, padx=10)
        canvas = tk.Canvas(outer, highlightthickness=0, height=300)
        sb = ttk.Scrollbar(outer, orient='vertical', command=canvas.yview)
        rows_fr = ttk.Frame(canvas)
        canvas.create_window((0, 0), window=rows_fr, anchor='nw')
        rows_fr.bind('<Configure>',
                     lambda e: canvas.configure(
                         scrollregion=canvas.bbox('all')))
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side='left', fill='both', expand=True)
        sb.pack(side='right', fill='y')
        canvas.bind('<MouseWheel>', lambda e: canvas.yview_scroll(
            -1 if e.delta > 0 else 1, 'units'))

        rows = []   # [(main_var, syn_var, frame), ...]

        def add_row(main='', syns=''):
            fr = ttk.Frame(rows_fr)
            fr.pack(fill='x', pady=2)
            mv = tk.StringVar(value=main)
            sv = tk.StringVar(value=syns)
            main_entry=ttk.Entry(fr, textvariable=mv, width=16)
            main_entry.pack(side='left')
            ttk.Label(fr, text=' = ').pack(side='left')
            syn_entry=ttk.Entry(fr, textvariable=sv)
            syn_entry.pack(side='left', fill='x',expand=True,padx=(0,6))
            for entry,variable in ((main_entry,mv),(syn_entry,sv)):
                entry.bind('<Enter>',lambda _,entry=entry,variable=variable:self._show_btn_tip(entry,variable.get()))
                entry.bind('<Leave>',self._hide_tree_tip)
            def _del(fr=fr):
                fr.destroy()
                rows[:] = [r for r in rows if r[2] is not fr]
            ttk.Button(fr, text='删除', width=5, command=_del).pack(side='right')
            rows.append((mv, sv, fr))

        for grp in parse_custom(self.cfg.get('custom_synonyms', '')):
            add_row(grp[0], ', '.join(grp[1:]))
        if not rows:
            add_row()

        bar = ttk.Frame(win, padding=(10, 4))
        bar.pack(fill='x')
        ttk.Button(bar, text='＋ 添加同义词', style='Accent.TButton',
                   command=lambda: (add_row(),
                                    canvas.yview_moveto(1.0))
                   ).pack(side='left')

        def show_builtin_syn():
            """查看内置同义词组（只读）"""
            from synonyms import BUILTIN_GROUPS
            bwin = tk.Toplevel(win)
            bwin.title(f'内置同义词组（自动生效，共 {len(BUILTIN_GROUPS)} 组）')
            self._center_win(bwin, 480, 484)
            bwin.transient(win)
            self._dialog_header(
                bwin, self.IC_SWAP, '内置同义词组',
                f'开箱即用、不可修改，共 {len(BUILTIN_GROUPS)} 组')
            txt = tk.Text(bwin, wrap='word', font=('Microsoft YaHei UI', 10))
            txt.pack(fill='both', expand=True, padx=10, pady=8)
            for main, *syns in BUILTIN_GROUPS:
                txt.insert('end', f'{main} = {", ".join(syns)}\n')
            txt.insert('end', '\n以上词组开箱即用、不可修改；'
                              '如需增删，可在自定义行里按同样写法补充。')
            txt.configure(state='disabled')
            ttk.Button(bwin, text='关闭',
                       command=bwin.destroy).pack(pady=(0, 8))

        ttk.Button(bar, text='内置词组…',
                   command=show_builtin_syn).pack(side='left', padx=(8, 0))

        def save():
            lines, skipped = [], 0
            for mv, sv, _ in rows:
                main = mv.get().strip()
                syns = [s.strip() for s in
                        sv.get().replace('，', ',').replace('、', ',').split(',')
                        if s.strip()]
                if main and syns:
                    lines.append(f'{main} = {", ".join(syns)}')
                elif main or syns:
                    skipped += 1   # 只填了一半的行跳过
            if skipped:
                messagebox.showwarning('同义词未填完整',f'{skipped} 行只有主词或别名，请补全或清空该行后再保存。',parent=win)
                return
            self.cfg['custom_synonyms'] = '\n'.join(lines)
            self.cfg['synonym'] = bool(self.syn_var.get())
            save_config(self.cfg)
            win.destroy()
            msg = f'同义词库已保存（{len(lines)} 组），重新搜索后生效'
            if skipped:
                msg += f'，{skipped} 组没填完整已忽略'
            self.status.configure(text=msg)

        win._rows, win._add_row, win._save = rows, add_row, save  # 测试/调试钩子

        btns = ttk.Frame(win, padding=8)
        btns.pack(fill='x')
        ttk.Button(btns, text='保存', style='Accent.TButton', command=save).pack(side='right')
        ttk.Button(btns, text='取消',
                   command=win.destroy).pack(side='right', padx=4)

    # -------- 版本对比 --------
    def show_diff(self, parent_win=None):
        """选库内两个文件做条款级差异对比"""
        items = list_library(DB_PATH)
        if len(items) < 2:
            messagebox.showinfo('提示', '库中至少需要 2 个文件')
            return
        sel_win = tk.Toplevel(self.root)
        sel_win.title('版本对比 · 选择文件')
        self._center_win(sel_win, 540, 360)
        sel_win.transient(parent_win or self.root)
        self._dialog_header(sel_win, self.IC_DOC, '版本对比 · 选择文件',
                            '选择要对比的新旧两个版本')
        names = [d['name'] for d in items]
        ttk.Label(sel_win, text='旧版文件:').pack(anchor='w', padx=12, pady=(10, 0))
        cb1 = ttk.Combobox(sel_win, values=names, state='readonly')
        cb1.pack(fill='x', padx=12)
        ttk.Label(sel_win, text='新版文件:').pack(anchor='w', padx=12, pady=(8, 0))
        cb2 = ttk.Combobox(sel_win, values=names, state='readonly')
        cb2.pack(fill='x', padx=12)
        # 默认选最后两个（通常是新旧版）
        cb1.current(len(names) - 2)
        cb2.current(len(names) - 1)

        def run():
            i1, i2 = cb1.current(), cb2.current()
            if i1 < 0 or i2 < 0 or i1 == i2:
                messagebox.showwarning('提示', '请选择两个不同的文件')
                return
            sel_win.destroy()
            self._render_diff(items[i1]['path'], items[i2]['path'])

        ttk.Button(sel_win, text='开始对比', style='Accent.TButton', command=run).pack(pady=14)

    # -------- 新旧版本一键对比 --------

    def _choose_version_dialog(self, path, sibs, parent_win):
        """多个候选版本时的选择对话框"""
        pw = parent_win or self.root
        win = tk.Toplevel(pw)
        win.title('选择要对比的版本')
        self._center_win(win, 720, 410)
        win.transient(pw)
        self._dialog_header(win, self.IC_DOC, '选择要对比的版本',
                            f'当前文件：{os.path.basename(path)}')
        body = ttk.Frame(win)
        body.pack(fill='both', expand=True, padx=12)
        cols = ('name', 'doc_no', 'date', 'status', 'reason')
        tv = ttk.Treeview(body, columns=cols, show='headings',
                          selectmode='browse')
        tv.heading('name', text='文件名')
        tv.heading('doc_no', text='文号')
        tv.heading('date', text='生效日期')
        tv.heading('status', text='状态')
        tv.heading('reason', text='配对依据')
        tv.column('name', width=300)
        tv.column('doc_no', width=140, anchor='center')
        tv.column('date', width=100, anchor='center')
        tv.column('status', width=70, anchor='center')
        tv.column('reason', width=80, anchor='center')
        vsb = ttk.Scrollbar(body, orient='vertical', command=tv.yview)
        tv.configure(yscrollcommand=vsb.set)
        tv.pack(side='left', fill='both', expand=True)
        vsb.pack(side='right', fill='y')
        status_lbl = {'active': '有效', 'void': '废止',
                      'replaced': '已替代'}
        for s in sibs:
            tv.insert('', 'end', iid=s['path'], values=(
                s['name'], s['doc_no'], s['effective_date'],
                status_lbl.get(s['status'], s['status']), s['reason']))
        if tv.get_children():
            tv.selection_add(tv.get_children()[0])
            tv.focus(tv.get_children()[0])

        def _do(_evt=None):
            sel = tv.selection()
            if not sel:
                return
            other = sel[0]
            win.destroy()
            self._open_version_diff(path, other)

        tv.bind('<Double-1>', _do)
        tv.bind('<Return>', _do)
        ttk.Button(win, text='对比选中版本', style='Accent.TButton', command=_do).pack(pady=10)



    def _render_diff(self, path_a, path_b):
        """版本对比结果窗：左右双栏，红删/绿增/黄改，滚动同步"""
        # 单例：再次对比别的版本时，旧对比窗关掉重开，始终只保留一个
        old = self._wins.pop('diff', None)
        if old is not None:
            try:
                if old.winfo_exists():
                    old.destroy()
            except Exception:
                pass
        win = self._get_win('diff')
        if win is None:
            return
        blocks = diff_documents(DB_PATH, path_a, path_b)
        win.title('版本对比')
        self._center_win(win, 1200, 700, modal=False)   # 对比工作台，不锁主界面
        win.transient(self.root)
        top = ttk.Frame(win, padding=8)
        top.pack(fill='x')
        # 旧版/新版标签随主题换色（深色下用高亮红/绿，避免写死色刺眼）
        _fg_old = '#FF7B7B' if self.dark.get() else '#C42B1C'
        _fg_new = '#7FD69B' if self.dark.get() else '#0A7B33'
        ttk.Label(top, text='旧版: ', foreground=_fg_old).pack(side='left')
        ttk.Label(top, text=os.path.basename(path_a),
                  font=('Microsoft YaHei UI', 9, 'bold')).pack(side='left')
        ttk.Label(top, text='    新版: ', foreground=_fg_new).pack(side='left')
        ttk.Label(top, text=os.path.basename(path_b),
                  font=('Microsoft YaHei UI', 9, 'bold')).pack(side='left')

        # 统计
        n_del = sum(len(b['a']) for b in blocks if b['type'] == 'delete')
        n_ins = sum(len(b['b']) for b in blocks if b['type'] == 'insert')
        n_rep = sum(len(b['a']) for b in blocks if b['type'] == 'replace')
        ttk.Label(top, text=f'   删除 {n_del} 行 · 新增 {n_ins} 行 · '
                            f'修改 {n_rep} 行', foreground='#666').pack(side='left')

        body = ttk.Frame(win)
        body.pack(fill='both', expand=True, padx=8, pady=(0, 8))
        ta = tk.Text(body, wrap='none', font=('Microsoft YaHei UI', 10),
                     bg='#F4F6F9')
        tb = tk.Text(body, wrap='none', font=('Microsoft YaHei UI', 10),
                     bg='#F4F6F9')
        sb = ttk.Scrollbar(body, orient='vertical')
        ta.configure(yscrollcommand=lambda *a: (sb.set(*a),
                                                tb.yview_moveto(a[0])))
        tb.configure(yscrollcommand=lambda *a: (sb.set(*a),
                                                ta.yview_moveto(a[0])))
        sb.configure(command=lambda *a: (ta.yview(*a), tb.yview(*a)))
        ta.pack(side='left', fill='both', expand=True)
        tb.pack(side='left', fill='both', expand=True)
        sb.pack(side='right', fill='y')
        for t in (ta, tb):
            t.tag_configure('del', background='#FDE2E2', foreground='#B00')
            t.tag_configure('ins', background='#E2F6E2', foreground='#060')
            t.tag_configure('rep_a', background='#FFF4CE')
            t.tag_configure('rep_b', background='#FFF4CE')
            t.tag_configure('gap', foreground='#999',
                            background='#F0F0F0', justify='center')
            t.tag_configure('equal', foreground='#555')
            t.configure(state='disabled')

        def ins_a(s, tag=None):
            ta.configure(state='normal')
            ta.insert('end', s, tag)
            ta.configure(state='disabled')

        def ins_b(s, tag=None):
            tb.configure(state='normal')
            tb.insert('end', s, tag)
            tb.configure(state='disabled')

        for b in blocks:
            ty = b['type']
            if ty == 'equal':
                for ln, txt in b['a']:
                    ins_a(f'{ln:>5} │ {txt}\n', 'equal')
                for ln, txt in b['b']:
                    ins_b(f'{ln:>5} │ {txt}\n', 'equal')
            elif ty == 'gap':
                sa, sb_n = b['skip']
                ins_a(f'  … 省略 {sa} 行相同内容 …\n', 'gap')
                ins_b(f'  … 省略 {sb_n} 行相同内容 …\n', 'gap')
            elif ty == 'delete':
                for ln, txt in b['a']:
                    ins_a(f'{ln:>5} │ {txt}\n', 'del')
                ins_b('\n')
            elif ty == 'insert':
                ins_a('\n')
                for ln, txt in b['b']:
                    ins_b(f'{ln:>5} │ {txt}\n', 'ins')
            elif ty == 'replace':
                for ln, txt in b['a']:
                    ins_a(f'{ln:>5} │ {txt}\n', 'rep_a')
                for ln, txt in b['b']:
                    ins_b(f'{ln:>5} │ {txt}\n', 'rep_b')

    def _highlight_fuzzy_row(self, row, kw):
        """模糊命中行：用正则定位一字之差的实际文本片段并高亮"""
        import re as _re
        # 预览正文已顶格（无行号前缀），从第 0 列开始
        txt = self.preview.get(f'{row}.0', f'{row}.end-1c')
        if not txt or len(kw) < 2:
            return
        cands = []
        for i in range(len(kw)):
            # 错字/多打一字：前缀 + 任意 1 字 + 后缀
            cands.append(_re.escape(kw[:i]) + '.' + _re.escape(kw[i + 1:]))
            # 少打一字：前缀 + 后缀直接相连
            cands.append(_re.escape(kw[:i] + kw[i + 1:]))
        if not cands:
            return
        pat = '|'.join(cands)
        for m in _re.finditer(pat, txt):
            self.preview.tag_add('hl', f'{row}.{m.start()}',
                                 f'{row}.{m.end()}')









    # -------- 打开文件并定位（Word 高亮 / PDF 翻页 / 文本跳行） --------

    @staticmethod
    def _find_acrobat():
        """查找 Adobe Acrobat/Reader（打开 PDF 时可翻页+高亮搜索词）"""
        return find_acrobat()

    @staticmethod
    def _find_sumatra():
        """查找 SumatraPDF（绿色免费 PDF 阅读器，支持 -page N -search 高亮）。
        优先级：程序目录内捆绑版 → 常见安装路径 → 注册表 App Paths。"""
        return find_sumatra(APP_DIR, LIBRARY_DIR)

    def _open_pdf_located(self, path, page, kw):
        """PDF：优先 SumatraPDF（翻页+高亮），其次 Adobe（翻页+高亮），
        再次 Edge（仅翻页），最后默认程序"""
        if page:
            # SumatraPDF: -page N -search "kw" 一步到位
            suma = self._find_sumatra()
            if suma:
                args = [suma, '-page', str(page)]
                if kw:
                    args += ['-search', kw]
                args.append(path)
                subprocess.Popen(args)
                return True
            # Adobe Acrobat: /A page=N&search=kw
            acro = self._find_acrobat()
            if acro:
                subprocess.Popen([acro, '/A', f'page={page}&search={kw}', path])
                return True
            # Edge: 仅翻页
            for edge in (r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
                         r'C:\Program Files\Microsoft\Edge\Application\msedge.exe'):
                if os.path.exists(edge):
                    from pathlib import Path
                    url = Path(path).as_uri() + f'#page={page}'
                    subprocess.Popen([edge, '--new-window', url])
                    return True
        os.startfile(path)
        return True


    def _open_excel_located(self, path, sheet, cell, kw):
        """Excel：COM 自动化——激活工作表 + 选中单元格，可选搜索关键词"""
        try:
            import win32com.client
        except Exception:
            return False
        xl = None
        for prog in ('Excel.Application', 'ket.Application', 'KWET.Application'):
            try:
                xl = win32com.client.DispatchEx(prog)
                break
            except Exception:
                continue
        if xl is None:
            return False
        try:
            xl.Visible = True
            wb = xl.Workbooks.Open(os.path.abspath(path), ReadOnly=True)
            ws = None
            try:
                ws = wb.Worksheets(sheet)
            except Exception:
                ws = wb.ActiveSheet
            ws.Activate()
            try:
                ws.Range(cell).Select()
            except Exception:
                pass
            if kw:
                try:
                    found = ws.UsedRange.Find(kw)
                    if found:
                        found.Select()
                except Exception:
                    pass
            return True
        except Exception:
            # 失败时退出进程，避免残留看不见的 Excel.exe
            try:
                xl.Quit()
            except Exception:
                pass
            return False

    def _open_ppt_located(self, path, slide, kw):
        """PPT：COM 自动化——打开演示文稿并跳转到指定幻灯片"""
        try:
            import win32com.client
        except Exception:
            return False
        ppt = None
        for prog in ('PowerPoint.Application', 'KWPP.Application'):
            try:
                ppt = win32com.client.DispatchEx(prog)
                break
            except Exception:
                continue
        if ppt is None:
            return False
        try:
            ppt.Visible = True
            pres = ppt.Presentations.Open(os.path.abspath(path))
            if slide:
                try:
                    ppt.ActiveWindow.View.GotoSlide(int(slide))
                except Exception:
                    try:
                        pres.Slides(int(slide)).Select()
                    except Exception:
                        pass
            return True
        except Exception:
            # 失败时退出进程，避免残留看不见的 POWERPNT.EXE
            try:
                ppt.Quit()
            except Exception:
                pass
            return False

    def _open_word_highlight(self, path, kw):
        """Word 文档：COM 自动化——全部命中词标黄 + 光标跳到首个命中处"""
        word = None
        doc = None
        try:
            import win32com.client
            # 优先 MS Word，其次 WPS（API 兼容）
            for prog in ('Word.Application', 'KWPS.Application'):
                try:
                    word = win32com.client.DispatchEx(prog)
                    break
                except Exception:
                    continue
            if word is None:
                return False
            word.Visible = True
            doc = word.Documents.Open(os.path.abspath(path), ReadOnly=True)
            # 全部命中词标黄（仅内存中，不落盘）
            rng = doc.Content
            f = rng.Find
            f.ClearFormatting()
            f.Replacement.ClearFormatting()
            f.Replacement.Highlight = True
            f.Text = kw
            f.Forward = True
            f.Wrap = 1        # wdFindContinue
            f.Execute(Replace=2)   # wdReplaceAll
            # 光标跳到首个命中处（窗口自动滚动过去）
            sel = word.Selection
            sel.HomeKey(Unit=6)    # wdStory
            sf = sel.Find
            sf.ClearFormatting()
            sf.Text = kw
            sf.Forward = True
            sf.Wrap = 1
            sf.Execute()
            doc.Saved = True       # 关闭时不弹「是否保存」
            return True
        except Exception:
            # 失败时关闭文档并退出进程，避免残留看不见的 WINWORD.EXE
            if doc is not None:
                try:
                    doc.Close(False)
                except Exception:
                    pass
            if word is not None:
                try:
                    word.Quit()
                except Exception:
                    pass
            return False



    # -------- P1-12: 导出 CSV --------
    def export_csv(self):
        if not self.results:
            messagebox.showinfo('提示', '无结果可导出，请先搜索')
            return
        path = filedialog.asksaveasfilename(
            title='导出 CSV',
            defaultextension='.csv',
            initialdir=self._export_dir(),
            filetypes=[('CSV 文件', '*.csv'), ('所有文件', '*.*')],
            initialfile=f'search_{time.strftime("%Y%m%d_%H%M%S")}.csv')
        if not path:
            return
        try:
            # P1-14: 上下文行数与预览一致
            ctx_n = CTX_LINES
            with open(path, 'w', encoding='utf-8-sig', newline='') as f:
                w = csv.writer(f)
                w.writerow(['文件路径', '文件名', '行号', '匹配内容', '上下文'])
                for r in self.results:
                    ctx = get_context(DB_PATH, r['path'], r['line_no'],
                                      before=ctx_n, after=ctx_n)
                    ctx_text = '\n'.join(f'{ln}: {txt}' for ln, txt in ctx)
                    w.writerow([r['path'], r['name'], r['line_no'],
                                r['line_text'], ctx_text])
            messagebox.showinfo('导出成功',
                                f'已导出 {len(self.results)} 条，文件在：\n'
                                f'{path}\n\n可以直接打开查看了')
        except Exception as e:
            record_error(DB_PATH, 'export_csv', e)
            messagebox.showerror('导出失败', str(e))
            self._refresh_err_count()

    # -------- P1-15: 配置持久化 --------



    # -------- 通用：指针是否落在指定控件（树）内 --------

    def _bind_hover_close(self, win, key=None, keep=(), delay=160):
        """给浮层挂「鼠标移出即收回」。

        做法：窗体本身与它的每个后代都绑同一套 Enter/Leave——
        指针从子控件移到另一个子控件时，父控件会先收到一次 Leave，
        所以 Leave 不能立刻收，得延时复查指针的真实落点。
        """
        keep_paths = [str(w) for w in keep] + [str(win)]
        timer = {'t': None}

        def _cancel():
            if timer['t'] is not None:
                try:
                    self.root.after_cancel(timer['t'])
                except Exception:
                    pass
                timer['t'] = None

        def _on_enter(_e=None):
            _cancel()

        def _on_leave(_e=None):
            _cancel()
            timer['t'] = self.root.after(delay, _check)

        def _check():
            timer['t'] = None
            try:
                if not win.winfo_exists():
                    return
            except Exception:
                return
            if self._pointer_in_path(*keep_paths):
                return
            if key is not None:
                self._wins.pop(key, None)
            try:
                win.destroy()
            except Exception:
                pass

        def _walk(w):
            for e in ('<Enter>', '<Leave>'):
                try:
                    w.bind(e, _on_enter if e == '<Enter>' else _on_leave,
                           add='+')
                except Exception:
                    pass
            for ch in w.winfo_children():
                _walk(ch)

        _walk(win)

    # -------- 搜索记录下拉：鼠标移出即收回 --------




    def _show_syntax_card(self):
        """搜索语法速查卡：点击显现，再点一次或鼠标移出即收回。
        每行 目的 + 可直接抄的实例；点实例填入搜索框。"""
        old = self._wins.get('syntax')
        if old is not None:
            try:
                if old.winfo_exists():
                    old.destroy()          # 再点一次 = 收回
                    self._wins.pop('syntax', None)
                    return
            except Exception:
                pass
        win = self._get_win('syntax')
        if win is None:
            return
        win.title('搜索语法速查')
        win.resizable(False, False)
        rows = [
            ('两个词都要出现（空格）', '安全生产 检查'),
            ('任一词出现即可', '安全生产|环保'),
            ('不要包含某个词', '安全生产 -培训'),
            ('按文号搜', '文号:GB 50016'),
            ('按分类搜', '分类:01'),
            ('按生效年份搜', '日期:2024'),
            ('只搜文件名称', '名称:管理办法'),
            ('只看现行有效', '状态:有效'),
        ]
        c = self._theme_colors()
        win.configure(bg=c['preview_bg'])
        tk.Label(win, text='点右侧实例直接填入搜索框', bg=c['preview_bg'],
                 fg=c['meta_fg'], font=self.FONT_HINT
                 ).pack(anchor='w', padx=12, pady=(10, 4))
        for desc, ex in rows:
            r = tk.Frame(win, bg=c['preview_bg'])
            r.pack(fill='x', padx=12, pady=1)
            tk.Label(r, text=desc, bg=c['preview_bg'], fg=c['preview_fg'],
                     font=self.FONT_UI, width=22, anchor='w'
                     ).pack(side='left')
            b = tk.Label(r, text=ex, bg=c['tree_odd_bg'], fg=c['chap_fg'],
                         font=('Consolas', 9), cursor='hand2', padx=8, pady=1)
            b.pack(side='left')
            def _fill(_e, val=ex):
                self.keyword.set(val)
                self.kw_entry.configure(foreground='')
                win.lift(self.root)
            b.bind('<Button-1>', _fill)
        win.update_idletasks()
        x = self._syntax_btn.winfo_rootx() + 12
        y = self._syntax_btn.winfo_rooty() + self._syntax_btn.winfo_height() + 4
        win.geometry(f'+{x}+{y}')
        # 鼠标移出速查卡即收回；「?」按钮算范围内，方便移回去再点一次收回
        self._bind_hover_close(win, key='syntax', keep=(self._syntax_btn,))






    def _restore_pane_sash(self):
        """启动后恢复 PanedWindow 分栏比（首次打开时设合理默认值，
        防止左边两个框被挤没）。
        坑1：combined 是经典 tk.PanedWindow，没有 ttk 版的 sashpos，
             取值用 sash_coord、设值用 sash_place，写错会静默抛异常，
             导致分栏比从不恢复。
        坑2：脏配置（pane_main=0）会让 ttk.PanedWindow 把左栏 unmap 且
             持续重叠（sashpos 返回 0），必须合法性校验 + 自愈重排。"""
        def _clamp(v, lo, hi):
            return max(lo, min(hi, v))

        def _valid_int(v):
            try:
                return int(v)
            except (TypeError, ValueError):
                return None

        def _apply(retry=6):
            try:
                self.root.update_idletasks()
                if not hasattr(self, 'main_pane'):
                    return
                mp = self.main_pane
                pw = mp.winfo_width()
                if pw <= 200:
                    if retry > 0:
                        self.root.after(200, lambda: _apply(retry - 1))
                    return
                # 主水平分栏（ttk）：左栏合法区间 [180, pw-320]，右栏至少 320
                lo, hi = 180, pw - 320
                if lo < hi:
                    saved = _valid_int(self.cfg.get('pane_main'))
                    if saved is None or not (lo <= saved <= hi):
                        saved = int(pw * 0.38)   # 首次/脏值 → 38% : 62%
                        self.cfg.pop('pane_main', None)
                    mp.sashpos(0, _clamp(saved, lo, hi))

                # 左区垂直分栏（经典 tk.PanedWindow）：合法区间 [100, ph-100]
                cp = self.combined_pane
                ph = cp.winfo_height()
                if ph > 100 and cp.panes():
                    vlo, vhi = 100, ph - 100
                    if vlo < vhi:
                        saved = _valid_int(self.cfg.get('pane_combined'))
                        if saved is None or not (vlo <= saved <= vhi):
                            saved = int(ph * 0.68)  # 结果 68% : 章节 32%
                            self.cfg.pop('pane_combined', None)
                        # sash_place(index, x, y)：vertical orient 下 y 生效
                        cp.sash_place(0, 0, _clamp(saved, vlo, vhi))

                # 等 Tk 完成本轮布局后，检查左栏是否被 unmap 卡死
                self.root.after(160, _ensure_mapped)
            except Exception:
                if retry > 0:
                    self.root.after(200, lambda: _apply(retry - 1))

        def _ensure_mapped():
            """脏 sashpos/几何协商失败时，ttk.PanedWindow 会让左栏处于
            '在 panes() 里但 winfo_ismapped=0' 的卡死态；forget 后按
            原顺序 insert 回去即可强制重排。"""
            try:
                mp = self.main_pane
                cp = self.combined_pane
                if mp.winfo_ismapped() and not cp.winfo_ismapped():
                    mp.forget(cp)
                    mp.insert(0, cp, weight=2)
                    self.root.update_idletasks()
                    pw = mp.winfo_width()
                    if pw > 200:
                        saved = _valid_int(self.cfg.get('pane_main'))
                        if saved is None or not (180 <= saved <= pw - 320):
                            saved = int(pw * 0.38)
                        mp.sashpos(0, _clamp(saved, 180, pw - 320))
            except Exception:
                pass

        _apply()


    def _backup_db(self):
        """退出时自动备份 index.db（VACUUM INTO 生成一致快照），份数可在
        「更多 → 索引体检 → 自动备份份数」配置（0 = 不备份）"""
        keep = self._max_backups()
        if keep <= 0 or not os.path.exists(DB_PATH):
            return
        try:
            import sqlite3
            import glob as _glob
            os.makedirs(BACKUP_DIR, exist_ok=True)
            olds = sorted(_glob.glob(os.path.join(BACKUP_DIR, 'index_*.db')))
            # 数据库自上次备份后无变化则跳过
            if olds and os.path.getmtime(DB_PATH) <= os.path.getmtime(olds[-1]):
                return
            dest = os.path.join(
                BACKUP_DIR, 'index_%s.db' % time.strftime('%Y%m%d_%H%M%S'))
            conn = sqlite3.connect(DB_PATH, timeout=30)
            try:
                conn.execute('VACUUM INTO ?', (dest,))
            finally:
                conn.close()
            for old in sorted(_glob.glob(
                    os.path.join(BACKUP_DIR, 'index_*.db')))[:-keep]:
                try:
                    os.remove(old)
                except OSError:
                    pass
        except Exception as e:
            # 备份静默失败 → 记录到 config，下次启动时红字告警
            try:
                self.cfg['backup_failed'] = str(e)[:100]
                self._save_config()
            except Exception:
                pass

    # -------- 库 --------
    def show_library(self):
        """弹出库窗口：列表 + 统计 + 删除"""
        if not os.path.exists(DB_PATH):
            messagebox.showinfo('提示', '库为空，请先导入文件或扫描目录')
            return
        win = self._get_win('library')   # tkdnd 方法已挂在 BaseWidget，可直接注册
        if win is None:
            return
        win.title('库管理')
        self._center_win(win, 1250, 620, modal=False)   # 管理工作台，不锁主界面
        win._cat_filter = ''
        win._multi_mode = False
        win._checked = set()   # 多选模式下已勾选的文件路径（跨过滤切换保留）
        win._missing = set()   # 磁盘已缺失的文件路径（后台健康检查填充）
        self._lib_win = win
        win.protocol('WM_DELETE_WINDOW',
                     lambda: (self._wins.pop('library', None),
                              setattr(self, '_lib_win', None), win.destroy()))
        # 库窗口同样支持拖文件入库
        if _DND_AVAILABLE:
            win.drop_target_register(DND_FILES)
            win.dnd_bind('<<Drop>>', self._on_drop)

        # 顶部统计 → 统一头部副标题
        stats = library_stats(DB_PATH)
        info_text = self._library_info_text(win, stats)
        _band, _tl, info_lbl = self._dialog_header(
            win, self.IC_BOOKS, '库管理', info_text)
        win._info = info_lbl
        bar = ttk.Frame(win, padding=(8, 4, 8, 4))
        bar.pack(fill='x')

        ttk.Button(bar, text='刷新',
                   command=lambda: self._refresh_library_win(win)).pack(side='right',
                                                                        padx=4)
        ttk.Button(bar, text='导出清单',
                   command=lambda: self._export_library_ledger(win)).pack(
                       side='right', padx=4)
        # 高级工具：查重/版本对比/引用预警 合并到一个下拉菜单
        tools_btn = ttk.Menubutton(bar, text='高级工具', direction='below')
        tools_menu = tk.Menu(tools_btn, tearoff=0)
        tools_btn.configure(menu=tools_menu)
        tools_menu.add_command(label='查重清理',
                               command=lambda: self.show_dedupe(win))
        tools_menu.add_command(label='版本对比',
                               command=lambda: self.show_diff(win))
        tools_menu.add_command(label='引用预警',
                               command=lambda: self.show_ref_warnings(win))
        tools_menu.add_command(label='合规 Gap 扫描…',
                               command=lambda: self._show_compliance_gap(win))
        tools_menu.add_command(label='🔔 检查标准更新…',
                               command=lambda: self._show_std_update(win))
        tools_menu.add_separator()
        tools_menu.add_command(label='分类自查',
                               command=lambda: self._show_category_audit(win))
        tools_menu.add_command(label='分类管理…',
                               command=lambda: self._show_category_manager(win))
        tools_menu.add_separator()
        tools_menu.add_command(label=f'{self.IC_PACKAGE} 一键打包库…',
                               command=lambda: self._package_library(win))
        tools_menu.add_command(label=f'{self.IC_PACKAGE} 打包整套（软件+库）…',
                               command=lambda: self._package_all(win))
        tools_menu.add_command(label=f'{self.IC_DOWNLOAD} 从压缩包还原库…',
                               command=lambda: self._restore_library(win))
        tools_menu.add_separator()
        tools_menu.add_command(label=f'{self.IC_WARN} 问题文件管理…',
                               command=lambda: self.show_problem_files(win))
        tools_menu.add_command(label='打开库目录',
                               command=lambda: self._open_library_dir())
        tools_btn.pack(side='right', padx=4)
        # 入库模式：仅登记路径不复制文件（原在更多菜单，属库级偏好，移到此处）
        ttk.Checkbutton(bar, text='入库仅登记路径（不复制文件）',
                        variable=self.link_only,
                        command=self._save_config
                        ).pack(side='right', padx=4)
        # 多选操作按钮（仅多选模式显示）
        selall_btn = ttk.Button(bar, text='全选/清空',
                                command=lambda: self._multi_all(win, tree))
        invert_btn = ttk.Button(bar, text='反选',
                                command=lambda: self._multi_invert(win, tree))
        multi_btn = ttk.Button(bar, text='多选模式',
                               command=lambda: self._toggle_multi_select(
                                   win, tree, multi_btn,
                                   selall_btn, invert_btn))
        multi_btn.pack(side='right', padx=4)
        win._multi_extra_btns = (selall_btn, invert_btn)
        ttk.Button(bar, text='删除', style='Danger.TButton',
                   command=lambda: self._delete_selected(win, tree)).pack(
                       side='right', padx=4)

        # ---------- 颜色图例（info bar 与 filter bar 之间） ----------
        _c = self._theme_colors()
        legend = ttk.Frame(win, padding=(8, 2, 8, 2))
        legend.pack(fill='x')
        _LEGEND_ITEMS = [
            (_c['void_fg'],    '废止'),
            (_c['replaced_fg'], '已替代'),
            (_c['notext_fg'],   f'{self.IC_WARN}无文本'),
            (_c['missing_fg'],  f'{self.IC_WARN}文件缺失'),
        ]
        tk.Label(legend, text='  文件名颜色说明：',
                 foreground='#666').pack(side='left')
        for _fg, _lbl in _LEGEND_ITEMS:
            tk.Label(legend, text='●', foreground=_fg).pack(side='left')
            tk.Label(legend, text=_lbl, foreground=_fg).pack(side='left',
                                                             padx=(0, 10))

        # 文件名/文档号快速筛选条（250ms 防抖，与分类树叠加）
        fbar = ttk.Frame(win, padding=(8, 0, 8, 4))
        fbar.pack(fill='x')
        ttk.Label(fbar, text='筛选:').pack(side='left')
        filter_e = ttk.Entry(fbar, width=28)
        filter_e.pack(side='left', padx=4)
        ttk.Label(fbar, text='按文件名/文档号，空格分隔多个词',
                  foreground='#888').pack(side='left')
        win._filter_entry = filter_e
        win._name_filter = ''
        win._filter_after = None

        def _on_filter_key(evt=None):
            if evt and evt.keysym in (
                    'Up', 'Down', 'Left', 'Right', 'Home', 'End',
                    'Control_L', 'Control_R', 'Shift_L', 'Shift_R', 'Alt_L'):
                return
            if win._filter_after:
                win.after_cancel(win._filter_after)
            win._filter_after = win.after(250, _apply_filter)

        def _apply_filter():
            win._filter_after = None
            win._name_filter = filter_e.get().strip()
            self._fill_library_tree(tree, win._cat_filter)
        filter_e.bind('<KeyRelease>', _on_filter_key)
        ttk.Button(fbar, text='清除',
                   command=lambda: (filter_e.delete(0, 'end'),
                                    _apply_filter())).pack(side='left', padx=4)

        # 列排序状态：默认按修改时间倒序
        win._sort_col = 'mtime'
        win._sort_rev = True

        # 左侧分类树栏 + 右侧文件列表
        win._cat_filter = ''
        body = ttk.Frame(win)
        body.pack(fill='both', expand=True)
        cat_fr = ttk.Frame(body, width=185)
        cat_fr.pack(side='left', fill='y', padx=(4, 6), pady=(0, 4))
        cat_tree = ttk.Treeview(cat_fr, show='tree', selectmode='browse')
        cat_sb = ttk.Scrollbar(cat_fr, orient='vertical',
                               command=cat_tree.yview)
        cat_tree.configure(yscrollcommand=cat_sb.set)
        cat_tree.pack(side='left', fill='both', expand=True)
        cat_sb.pack(side='right', fill='y')
        win._cat_tree = cat_tree

        def _on_cat_select(_e=None):
            sel = cat_tree.selection()
            if not sel:
                return
            iid = sel[0]
            win._cat_filter = ('' if iid == '__all__'
                               else '__none__' if iid == '__none__' else iid)
            self._fill_library_tree(tree, win._cat_filter)
        cat_tree.bind('<<TreeviewSelect>>', _on_cat_select)
        cat_tree.bind('<Double-1>', lambda e: 'break')   # 防双击展开抖动

        list_fr = ttk.Frame(body)
        list_fr.pack(side='left', fill='both', expand=True)
        cols = ('sel', 'name', 'ext', 'ftype', 'size', 'lines', 'status',
                'doc_no', 'category', 'mtime', 'path')
        tree = ttk.Treeview(list_fr, columns=cols, show='headings')
        tree.heading('sel', text='选')
        tree.column('sel', width=36, anchor='center', stretch=False)
        head_text = {'name': '文件名', 'ext': '扩展名', 'ftype': '类型',
                     'size': '大小', 'lines': '行数', 'status': '状态',
                     'doc_no': '文档号', 'category': '分类',
                     'mtime': '修改时间', 'path': '路径'}
        for c in [x for x in cols if x != 'sel']:
            tree.heading(c, text=head_text[c],
                         command=lambda c=c: self._sort_library_tree(
                             win, tree, c))
        tree.column('name', width=210)
        tree.column('ext', width=50, anchor='center')
        tree.column('ftype', width=60, anchor='center')
        tree.column('size', width=65, anchor='e')
        tree.column('lines', width=50, anchor='e')
        tree.column('status', width=65, anchor='center')
        tree.column('doc_no', width=120, anchor='center')
        tree.column('category', width=90, anchor='center')
        tree.column('mtime', width=125, anchor='center')
        tree.column('path', width=290)
        tree.tag_configure('void', foreground='#98A1AC')
        tree.tag_configure('replaced', foreground='#9C7C3C')
        tree.tag_configure('missing', foreground='#C13A28')
        tree.tag_configure('notext', foreground='#C05621')
        vsb = ttk.Scrollbar(list_fr, orient='vertical', command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        tree.pack(side='left', fill='both', expand=True)
        vsb.pack(side='right', fill='y')

        # 普通模式：Ctrl/Shift 点选，Ctrl+A 选中全部行；
        # 多选模式：Ctrl+A 全选/清空☑
        def _ctrl_a(_e):
            if getattr(win, '_multi_mode', False):
                self._multi_all(win, tree)
            else:
                tree.selection_set(tree.get_children())
            return 'break'
        tree.bind('<Control-a>', _ctrl_a)
        tree.bind('<Control-A>', _ctrl_a)
        # 多选模式下点击行切换☐/☑（普通模式放行默认选中行为）
        tree.bind('<Button-1>',
                  lambda e: self._handle_tree_click(win, tree, e))

        # 右键菜单（编辑元数据 / 设置分类 / 删除 / 打开目录）
        ctx = tk.Menu(tree, tearoff=0)
        def _ctx_menu(evt):
            if getattr(win, '_multi_mode', False):
                # 多选模式：取☑行；右键点中的行未勾选则只对该行操作
                row = tree.identify_row(evt.y)
                sel = tuple(p for p in win._checked
                            if tree.exists(p))
                if row and row not in sel:
                    sel = (row,)
            else:
                sel = tree.selection()
            if not sel:
                return
            if getattr(win, '_multi_mode', False):
                # 让沿用 tree.selection() 的菜单命令在多选模式同样生效
                tree.selection_set(sel)
            ctx.delete(0, 'end')
            ctx.add_command(label='编辑元数据…',
                            command=lambda: self._edit_metadata(win, tree))
            if len(sel) == 1:
                ctx.add_command(label='重命名文件…',
                                command=lambda: self._rename_selected(
                                    win, tree, sel[0]))
            cat_menu = tk.Menu(ctx, tearoff=0)
            for cname in self._category_names() + ['未分类']:
                val = '' if cname == '未分类' else cname

                def _set(pths=tuple(sel), v=val, nm=cname):
                    n = apply_categories(DB_PATH, {p: v for p in pths})
                    self._refresh_library_win(win)
                    self.status.configure(text=f'已把 {n} 个文件设为分类「{nm}」')
                cat_menu.add_command(label=cname, command=_set)
            ctx.add_cascade(label='设置分类', menu=cat_menu)
            ctx.add_command(label='批量改状态/日期…',
                            command=lambda: self._batch_edit_metadata(
                                win, tree, tuple(sel)))
            ctx.add_command(label='与旧版对比',
                            command=lambda: self._compare_with_version(
                                tree.selection()[0], win))
            ctx.add_separator()
            ctx.add_command(label='仅从索引删除（保留磁盘文件）',
                            command=lambda: self._del_from_lib(win, tree, False))
            ctx.add_command(label='从库删除（移入回收站）',
                            command=lambda: self._del_from_lib(win, tree, True))
            ctx.add_separator()
            ctx.add_command(label='重新索引此文件',
                            command=lambda: self._reindex_selected(win, tree))
            ctx.add_command(label='打开所在目录',
                            command=lambda: self._open_in_explorer(tree))
            ctx.tk_popup(evt.x_root, evt.y_root)
        tree.bind('<Button-3>', _ctx_menu)
        # 双击用系统默认程序打开
        tree.bind('<Double-1>', lambda e: self._open_in_default(tree))

        win._tree = tree
        win._info = info_lbl
        self._fill_library_tree(tree)
        self._fill_cat_tree(win)
        self._start_health_check(win)
        self._start_lib_live_refresh(win)
        # tkdnd 按控件注册目标，树/分类栏也要能接收拖入
        if _DND_AVAILABLE:
            for wgt in (tree, cat_tree):
                wgt.drop_target_register(DND_FILES)
                wgt.dnd_bind('<<Drop>>', self._on_drop)

    def _start_health_check(self, win):
        """后台扫描磁盘缺失文件，完成后刷新标记（避免大库/网络盘卡UI）"""
        def _work():
            try:
                missing = {it['path'] for it in list_library(DB_PATH)
                           if not os.path.exists(it['path'])}
            except Exception:
                return
            try:
                self.root.after(0, lambda: self._on_health_done(win, missing))
            except RuntimeError:
                pass   # 窗口/程序正在关闭

        threading.Thread(target=_work, daemon=True).start()


    def _start_lib_live_refresh(self, win):
        """库窗口顶部统计栏实时刷新：每 15s 刷统计文字，每 60s 刷健康检查。
        窗口关闭时自动停止（winfo_exists 检测）。"""
        win._live_hc_tick = 0   # 健康检查累计 tick

        def _tick():
            if not win.winfo_exists():
                return
            try:
                # 15s 刷一次 info 统计（轻量，纯 DB 查询）
                stats = library_stats(DB_PATH)
                win._info.config(
                    text=self._library_info_text(win, stats))
                win._live_hc_tick += 1
                # 每 4 个 tick（60s）刷一次健康检查（涉及磁盘 I/O）
                if win._live_hc_tick >= 4:
                    win._live_hc_tick = 0
                    self._start_health_check(win)
            except Exception:
                pass
            self.root.after(15000, _tick)

        self.root.after(15000, _tick)


    def show_problem_files(self, parent_win=None):
        """问题文件管理：分类展示 缺失/无文本/已替代/废止，支持批量删除。"""
        if not os.path.exists(DB_PATH):
            messagebox.showinfo('提示', '库为空')
            return

        win = tk.Toplevel(self.root)
        win.title(f'{self.IC_WARN} 问题文件管理')
        self._center_win(win, 1060, 614)
        if parent_win is not None:
            win.transient(parent_win)
        self._dialog_header(
            win, self.IC_WARN, '问题文件管理',
            '文件缺失 / 无文本 / 已废止 / 已替代，集中处理库内异常条目')

        # ---- 数据准备 ----
        def _collect_problems():
            """扫描所有条目，按问题类型分类。"""
            all_items = list_library(DB_PATH)
            missing_paths = {it['path'] for it in all_items
                             if not os.path.exists(it['path'])}
            problems = []  # [(problem_type, item), ...]
            for it in all_items:
                ptype = None
                if it['path'] in missing_paths:
                    ptype = 'missing'
                elif (it.get('line_count') or 0) == 0:
                    ptype = 'notext'
                elif it.get('status') == 'void':
                    ptype = 'void'
                elif it.get('status') == 'replaced':
                    ptype = 'replaced'
                if ptype:
                    problems.append((ptype, it))
            return problems

        _PTYPE_LABEL = {
            'missing':  (f'{self.IC_WARN} 文件缺失',  # 显示标签
                         self._theme_colors()['missing_fg']),
            'notext':   (f'{self.IC_WARN} 无文本',
                         self._theme_colors()['notext_fg']),
            'void':     ('废止',
                         self._theme_colors()['void_fg']),
            'replaced': ('已替代',
                         self._theme_colors()['replaced_fg']),
        }

        # ---- 顶部：分类按钮 + 搜索 ----
        top = ttk.Frame(win, padding=(8, 8, 8, 4))
        top.pack(fill='x')
        tk.Label(top, text='分类：', width=6).pack(side='left')

        _current_filter = ['all']   # all / missing / notext / void / replaced
        _keyword = ['']

        filter_btns = {}

        def _apply_filter():
            """重刷 Treeview。"""
            tree.delete(*tree.get_children())
            visible = _current_filter[0]
            kw = _keyword[0].strip().lower()
            shown = 0
            for ptype, it in win._problems:
                if visible != 'all' and ptype != visible:
                    continue
                if kw and kw not in it['name'].lower() and \
                        kw not in (it.get('doc_no') or '').lower():
                    continue
                label, fg = _PTYPE_LABEL[ptype]
                tags = (ptype,)
                tree.insert('', 'end', iid=it['path'], tags=tags, values=(
                    '☐', it['name'], label,
                    it.get('doc_no', '') or '',
                    fmt_size(it['size'] or 0),
                    time.strftime('%Y-%m-%d',
                                  time.localtime(it['mtime'])) if it['mtime'] else '',
                    it['path'],
                ))
                shown += 1
            _update_count(shown)

        def _update_count(n):
            lbl_parts = []
            for t in ('missing', 'notext', 'void', 'replaced'):
                c = sum(1 for p, _ in win._problems if p == t)
                lbl_parts.append(f'{_PTYPE_LABEL[t][0]}:{c}')
            count_lbl.config(
                text=f'共 {len(win._problems)} 个问题文件'
                     + (f' · 当前筛选 {n} 个' if n != len(win._problems) else '')
                     + ' | ' + ' · '.join(lbl_parts))

        def _set_filter(btn, key):
            _current_filter[0] = key
            for b in filter_btns.values():
                b.configure(state='normal')
            btn.configure(state='disabled')
            _apply_filter()

        for key in ('all', 'missing', 'notext', 'void', 'replaced'):
            txt = '全部' if key == 'all' else _PTYPE_LABEL[key][0]
            b = ttk.Button(top, text=txt, width=10)
            b.configure(command=lambda b=b, k=key: _set_filter(b, k))
            filter_btns[key] = b
            b.pack(side='left', padx=2)
        filter_btns['all'].configure(state='disabled')

        ttk.Label(top, text='搜索:').pack(side='left', padx=(16, 2))
        kw_e = ttk.Entry(top, width=22)
        kw_e.pack(side='left')

        def _on_kw(_=None):
            _keyword[0] = kw_e.get()
            _apply_filter()
        kw_e.bind('<KeyRelease>', _on_kw)

        count_lbl = ttk.Label(top, text='')
        count_lbl.pack(side='right')

        # ---- Treeview ----
        cols = ('chk', 'name', 'ptype', 'docno', 'size', 'mtime', 'path')
        tree = ttk.Treeview(win, columns=cols, show='headings',
                            selectmode='extended')
        tree.heading('chk', text='')
        tree.heading('name', text='文件名')
        tree.heading('ptype', text='问题类型')
        tree.heading('docno', text='文档号')
        tree.heading('size', text='大小')
        tree.heading('mtime', text='修改时间')
        tree.heading('path', text='路径')
        tree.column('chk', width=32, anchor='center', stretch=False)
        tree.column('name', width=260, anchor='w')
        tree.column('ptype', width=110, anchor='center')
        tree.column('docno', width=130, anchor='w')
        tree.column('size', width=80, anchor='e')
        tree.column('mtime', width=110, anchor='center')
        tree.column('path', width=320, anchor='w')

        _c = self._theme_colors()
        tree.tag_configure('missing',  foreground=_c['missing_fg'])
        tree.tag_configure('notext',   foreground=_c['notext_fg'])
        tree.tag_configure('void',     foreground=_c['void_fg'])
        tree.tag_configure('replaced', foreground=_c['replaced_fg'])

        # 点击勾选框切换
        def _toggle_chk(evt=None):
            sel = tree.selection()
            if not sel:
                return
            for iid in sel:
                vals = list(tree.item(iid, 'values'))
                vals[0] = '☑' if vals[0] == '☐' else '☐'
                tree.item(iid, values=vals)

        # 只保留单击切换：双击会产生两次 ButtonRelease（切回原状），
        # 旧版同时绑 Double-1 导致连切三次、状态与预期相反
        tree.bind('<ButtonRelease-1>',
                  lambda e: (tree.identify_region(e.x, e.y) == 'cell'
                             and _toggle_chk()))

        vsb = ttk.Scrollbar(win, orient='vertical', command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        tree.pack(side='top', fill='both', expand=True,
                  padx=8, pady=(0, 4))
        vsb.place(relx=1.0, rely=0.08, relheight=0.82, anchor='ne')

        # ---- 底部按钮 ----
        bar = ttk.Frame(win, padding=8)
        bar.pack(side='bottom', fill='x')

        def _sel_all():
            for iid in tree.get_children():
                vals = list(tree.item(iid, 'values'))
                vals[0] = '☑'
                tree.item(iid, values=vals)

        def _sel_inv():
            for iid in tree.get_children():
                vals = list(tree.item(iid, 'values'))
                vals[0] = '☐' if vals[0] == '☑' else '☑'
                tree.item(iid, values=vals)

        ttk.Button(bar, text='全选', command=_sel_all).pack(side='left',
                                                            padx=(0, 4))
        ttk.Button(bar, text='反选', command=_sel_inv).pack(side='left')
        ttk.Button(bar, text='刷新',
                   command=lambda: (_reload(),
                                    filter_btns['all'].configure(state='disabled'),
                                    _apply_filter())).pack(side='left', padx=(12, 4))

        def _do_delete(delete_file):
            """批量删除：delete_file=True 从库删除（移入回收站），False 仅从索引删除"""
            to_del = []
            for iid in tree.get_children():
                vals = tree.item(iid, 'values')
                if vals[0] == '☑':
                    to_del.append(iid)
            if not to_del:
                messagebox.showinfo('提示', '请先勾选要处理的文件', parent=win)
                return
            if not messagebox.askyesno(
                    '确认删除',
                    f'将处理 {len(to_del)} 个问题文件。\n\n'
                    + ('磁盘文件将移入回收站（可恢复）。\n\n'
                       if delete_file else
                       '仅删除索引记录，保留磁盘文件。\n\n')
                    + '确定继续？',
                    icon='warning', parent=win):
                return
            ok = fail = 0
            for p in to_del:
                rc = remove_from_library(DB_PATH, p,
                                         delete_file=delete_file)
                if rc == 'ok':
                    ok += 1
                else:
                    fail += 1
                    tree.item(p, tags=())   # 删不掉就去掉 tag 以示区别
            messagebox.showinfo(
                '完成',
                f'已处理 {ok} 个问题文件'
                + (f'（{fail} 个失败：{fail} 个被占用或已不在库）'
                   if fail else ''),
                parent=win)
            _reload()
            _apply_filter()
            if parent_win is not None:
                self._refresh_library_win(parent_win)

        ttk.Button(bar, text='仅从索引删除',
                   command=lambda: _do_delete(delete_file=False)
                   ).pack(side='right', padx=(4, 0))
        ttk.Button(bar, text='从库删除（移入回收站）', style='Danger.TButton',
                   command=lambda: _do_delete(delete_file=True)
                   ).pack(side='right')

        # ---- 初始化加载 ----
        def _reload():
            win._problems = _collect_problems()

        _reload()
        _apply_filter()


    def _reindex_selected(self, win, tree):
        """重新解析建索引（OCR 可能耗时，后台线程执行），支持多选"""
        if getattr(win, '_multi_mode', False):
            paths = [p for p in win._checked if tree.exists(p)]
        else:
            paths = list(tree.selection())
        if not paths:
            return
        if getattr(win, '_reindexing', False):
            messagebox.showinfo('提示', '已有重新索引任务在执行，请稍候。',
                                parent=win)
            return
        if len(paths) > 1 and not messagebox.askyesno(
                '重新索引', f'将重新解析 {len(paths)} 个文件，扫描件 OCR '
                '可能耗时数分钟。\n期间可正常使用搜索，是否继续？', parent=win):
            return
        win._reindexing = True
        self.status.configure(text='开始重新索引…')

        def _work():
            ok = still_empty = 0
            total = len(paths)
            for i, p in enumerate(paths, 1):
                if not os.path.exists(p):
                    continue
                self.root.after(0,
                    lambda i=i, t=total, p=p: self.status.configure(
                        text=f'重新索引 {i}/{t}: {os.path.basename(p)}'))
                try:
                    st = index_single_file(p, DB_PATH)
                    if st == 'ok':
                        ok += 1
                        meta = get_metadata(DB_PATH, p)
                        if meta and meta.get('line_count', 0) == 0:
                            still_empty += 1
                except Exception:
                    pass
            def _done():
                if not win.winfo_exists():
                    win._reindexing = False
                    return
                win._reindexing = False
                self._refresh_library_win(win)
                self._refresh_err_count()
                msg = f'重新索引完成：{ok}/{total} 成功'
                if still_empty:
                    msg += f'；{still_empty} 个仍无文本（可能是加密/损坏/纯图片 OCR 失败）'
                self.status.configure(text=msg)
                messagebox.showinfo('完成', msg, parent=win)
            try:
                self.root.after(0, _done)
            except RuntimeError:
                pass
        threading.Thread(target=_work, daemon=True).start()






    def _fill_cat_tree(self, win):
        """左侧分类树栏：全部文件 / 各分类 / 未分类（带数量）"""
        ct = win._cat_tree
        try:
            counts = category_counts(DB_PATH)
        except Exception:
            counts = {}
        total = sum(counts.values())
        cur = getattr(win, '_cat_filter', '') or '__all__'
        ct.delete(*ct.get_children())
        ct.insert('', 'end', iid='__all__', open=True,
                  text=f'全部文件 ({total})')
        shown = set()
        for n in self._category_names():
            ct.insert('', 'end', iid=n,
                      text=f'{n} ({counts.get(n, 0)})')
            shown.add(n)
        # 库里存在但规则里没有的分类（历史遗留）也列出来
        for c, n in sorted(counts.items(), key=lambda x: -x[1]):
            if c and c not in shown:
                ct.insert('', 'end', iid=c, text=f'{c} ({n})')
                shown.add(c)
        ct.insert('', 'end', iid='__none__',
                  text=f'未分类 ({counts.get("", 0)})')
        # 恢复选中（不触发事件）
        sel_iid = cur if cur in ('__all__', '__none__') else cur
        try:
            ct.selection_set(sel_iid)
        except tk.TclError:
            ct.selection_set('__all__')


    _LIB_HEAD_TEXT = {'name': '文件名', 'ext': '扩展名', 'ftype': '类型',
                      'size': '大小', 'lines': '行数', 'status': '状态',
                      'doc_no': '文档号', 'category': '分类',
                      'mtime': '修改时间', 'path': '路径'}





    def _edit_metadata(self, win, tree):
        """编辑选中文件的元数据（文档号/生效日期/状态/分类）"""
        sel = tree.selection()
        if not sel:
            return
        path = sel[0]
        meta = get_metadata(DB_PATH, path)
        dlg = tk.Toplevel(self.root)
        dlg.title('编辑元数据')
        self._center_win(dlg, 480, 420)
        dlg.transient(win)
        self._dialog_header(dlg, self.IC_DOC, '编辑元数据',
                            os.path.basename(path))
        frm = ttk.Frame(dlg, padding=12)
        frm.pack(fill='x')
        ttk.Label(frm, text='文档号:').grid(row=0, column=0, sticky='w', pady=4)
        doc_no_e = ttk.Entry(frm, width=36)
        doc_no_e.insert(0, meta['doc_no'])
        doc_no_e.grid(row=0, column=1, pady=4)
        ttk.Label(frm, text='生效日期:').grid(row=1, column=0, sticky='w', pady=4)
        date_e = ttk.Entry(frm, width=36)
        date_e.insert(0, meta['effective_date'])
        date_e.grid(row=1, column=1, pady=4, sticky='w')
        ttk.Label(frm, text='格式 2021-09-01，可留空',
                  foreground='#888').grid(row=2, column=1, sticky='w')
        ttk.Label(frm, text='状态:').grid(row=3, column=0, sticky='w', pady=4)
        st_var = tk.StringVar(
            value=self.STATUS_LABELS.get(meta['status'], '有效'))
        st_box = ttk.Combobox(frm, state='readonly', width=20, textvariable=st_var,
                              values=['有效', '废止', '已替代'])
        st_box.grid(row=3, column=1, pady=4, sticky='w')
        ttk.Label(frm, text='分类:').grid(row=4, column=0, sticky='w', pady=4)
        cat_var = tk.StringVar(value=meta.get('category', '') or '未分类')
        cat_box = ttk.Combobox(frm, width=32, textvariable=cat_var,
                               values=self._category_names() + ['未分类'])
        cat_box.grid(row=4, column=1, pady=4, sticky='w')

        def save():
            date_in = date_e.get().strip()
            if date_in and not valid_date(date_in):
                messagebox.showwarning(
                    '日期格式不对',
                    '生效日期需为 YYYY-MM-DD 格式（如 2021-09-01），\n'
                    '且必须是真实日期；不需要请清空。', parent=dlg)
                date_e.focus_set()
                return
            old_status = meta['status']
            new_status = self.STATUS_VALUES.get(st_var.get(), 'active')
            cat_in = cat_var.get().strip()
            new_cat = '' if cat_in in ('', '未分类') else cat_in
            update_metadata(DB_PATH, path,
                             doc_no=doc_no_e.get().strip() or '',
                             effective_date=date_in,
                             status=new_status, category=new_cat)
            dlg.destroy()
            self._refresh_library_win(win)
            self.status.configure(text=f'已更新元数据: {os.path.basename(path)}')
            # 引用预警：文件标记为废止/被替代时，后台检查还有谁在引用它
            if (old_status == 'active'
                    and new_status in ('void', 'replaced')):
                self._check_incoming_bg([path], win)

        btns = ttk.Frame(dlg, padding=8)
        btns.pack(fill='x')
        ttk.Button(btns, text='保存', style='Accent.TButton', command=save).pack(side='right')
        ttk.Button(btns, text='取消', command=dlg.destroy).pack(side='right', padx=4)
        dlg.bind('<Return>', lambda e: save())

    def _batch_edit_metadata(self, win, tree, paths):
        """批量改状态/生效日期（分类仍用右键「设置分类」）"""
        paths = [p for p in paths if p]
        if not paths:
            return
        n = len(paths)
        dlg = tk.Toplevel(self.root)
        dlg.title('批量改状态/日期')
        self._center_win(dlg, 480, 410)
        dlg.transient(win)
        self._dialog_header(dlg, self.IC_DOC, '批量改状态/日期',
                            f'将对选中的 {n} 个文件统一修改')
        frm = ttk.Frame(dlg, padding=14)
        frm.pack(fill='x')
        ttk.Label(frm, text='状态:').grid(row=0, column=0, sticky='w', pady=6)
        st_var = tk.StringVar(value='不修改')
        ttk.Combobox(frm, state='readonly', width=16, textvariable=st_var,
                     values=['不修改', '有效', '废止', '已替代']).grid(
                         row=0, column=1, sticky='w', pady=6)
        ttk.Label(frm, text='生效日期:').grid(row=1, column=0, sticky='nw', pady=6)
        date_mode = tk.IntVar(value=0)
        dbox = ttk.Frame(frm)
        dbox.grid(row=1, column=1, sticky='w')
        ttk.Radiobutton(dbox, text='不修改', variable=date_mode,
                        value=0).pack(anchor='w')
        row2 = ttk.Frame(dbox)
        row2.pack(anchor='w', pady=2)
        ttk.Radiobutton(row2, text='统一设为', variable=date_mode,
                        value=1).pack(side='left')
        date_e = ttk.Entry(row2, width=14)
        date_e.pack(side='left', padx=4)
        ttk.Label(row2, text='(2021-09-01)', foreground='#888').pack(side='left')
        ttk.Radiobutton(dbox, text='清空生效日期', variable=date_mode,
                        value=2).pack(anchor='w')

        def apply():
            new_status = self.STATUS_VALUES.get(st_var.get())
            dm = date_mode.get()
            date_in = date_e.get().strip()
            if new_status is None and dm == 0:
                messagebox.showwarning('提示', '请至少选择一项要修改的内容。',
                                       parent=dlg)
                return
            if dm == 1:
                if not valid_date(date_in):
                    messagebox.showwarning(
                        '日期格式不对',
                        '生效日期需为 YYYY-MM-DD 格式（如 2021-09-01）。',
                        parent=dlg)
                    return
            dead_paths = []
            for p in paths:
                try:
                    old = get_metadata(DB_PATH, p)
                    kw = {}
                    if new_status is not None:
                        kw['status'] = new_status
                        if old.get('status') == 'active' and \
                                new_status in ('void', 'replaced'):
                            dead_paths.append(p)
                    if dm == 1:
                        kw['effective_date'] = date_in
                    elif dm == 2:
                        kw['effective_date'] = ''
                    if kw:
                        update_metadata(DB_PATH, p, **kw)
                except Exception:
                    pass
            dlg.destroy()
            self._refresh_library_win(win)
            self.status.configure(text=f'已批量更新 {n} 个文件的元数据')
            if dead_paths:
                self._check_incoming_bg(dead_paths, win)

        bar = ttk.Frame(dlg, padding=10)
        bar.pack(side='bottom', fill='x')
        ttk.Button(bar, text='应用', style='Accent.TButton', command=apply).pack(side='right')
        ttk.Button(bar, text='取消', command=dlg.destroy).pack(side='right',
                                                                padx=4)

    def _library_info_text(self, win, stats):
        """库顶部统计：总数/大小/类型 + 状态分布/无文本/缺失"""
        size_str = fmt_size(stats['total_size'])
        by_type = stats['by_type']
        top_types = ', '.join(f'{k}:{v}' for k, v in
                              sorted(by_type.items(), key=lambda x: -x[1])[:8])
        text = (f'共 {stats["files"]} 个文件 · 总大小 {size_str}'
                f'    类型分布: {top_types if top_types else "无"}')
        try:
            items = list_library(DB_PATH)
        except Exception:
            return text
        n_act = sum(1 for it in items if it.get('status', 'active') == 'active')
        n_void = sum(1 for it in items if it.get('status') == 'void')
        n_rep = sum(1 for it in items if it.get('status') == 'replaced')
        n_notext = sum(1 for it in items if (it.get('line_count') or 0) == 0)
        n_miss = len(getattr(win, '_missing', set()))
        text += (f'    状态: 有效{n_act} · 废止{n_void} · 已替代{n_rep}')
        if n_notext:
            text += f'    {self.IC_WARN}无文本{n_notext}'
        if n_miss:
            text += f'    {self.IC_WARN}文件缺失{n_miss}'
        return text


    def _export_library_ledger(self, win):
        """导出全库清单 CSV：序号/文件名/文档号/分类/状态/生效日期/入库时间/路径"""
        items = list_library(DB_PATH)
        if not items:
            messagebox.showinfo('提示', '库中没有文件', parent=win)
            return
        default_fn = time.strftime('文件清单_%Y%m%d.csv')
        fn = filedialog.asksaveasfilename(
            parent=win, title='导出文件清单',
            initialdir=self._export_dir(),
            initialfile=default_fn,
            defaultextension='.csv',
            filetypes=[('CSV 文件', '*.csv')])
        if not fn:
            return
        # 分类排序（未分类最后），同分类按文件名
        cat_order = self._category_names()

        def _sort_key(it):
            cat = it.get('category') or ''
            idx = cat_order.index(cat) if cat in cat_order else len(cat_order)
            return (idx, cat, it['name'])
        items.sort(key=_sort_key)
        try:
            with open(fn, 'w', encoding='utf-8-sig', newline='') as f:
                w = csv.writer(f)
                w.writerow(['序号', '文件名', '文档号', '分类', '状态',
                            '生效日期', '入库时间', '存放路径'])
                for n, it in enumerate(items, 1):
                    indexed = ''
                    if it.get('indexed_at'):
                        indexed = time.strftime(
                            '%Y-%m-%d %H:%M:%S',
                            time.localtime(it['indexed_at']))
                    w.writerow([
                        n, it['name'], it.get('doc_no', ''),
                        it.get('category', '') or '未分类',
                        self.STATUS_LABELS.get(it.get('status', 'active'), '有效'),
                        it.get('effective_date', ''), indexed, it['path'],
                    ])
        except Exception as e:
            messagebox.showerror('导出失败', f'写入清单失败：\n{e}', parent=win)
            return
        self.status.configure(text=f'已导出文件清单（{len(items)} 份）: {fn}')
        messagebox.showinfo('完成', f'已导出 {len(items)} 份文件的清单。',
                            parent=win)


    def _ask_categories_popup(self, paths):
        """自动归类失败 → 弹窗给每个文件选分类（可全部设为同一个）"""
        cats = self._category_names()
        choices = cats + ['未分类']
        win = tk.Toplevel(self.root)
        win.title('为文件选择分类')
        n = len(paths)
        # 统一头部（双行副标题）替换原顶部说明，净增约 34px
        self._center_win(win, 620, min(154 + n * 36 + 60, 674))
        win.transient(self.root)
        self._dialog_header(
            win, self.IC_FOLDER, '为文件选择分类',
            f'有 {n} 个文件没能自动判断分类，请为它们选择归属\n'
            '选「未分类」或不选就保持未分类')
        tip = ttk.Label(win, padding=(14, 4, 14, 0), foreground='#888',
                        text='全部设为：')
        tip.pack(anchor='w')

        rows = []   # [(path, var), ...]
        all_var = tk.StringVar()

        def _apply_all():
            v = all_var.get()
            if not v:
                return
            for _p, var in rows:
                var.set(v)
        af = ttk.Frame(win, padding=(14, 2))
        af.pack(anchor='w')
        ttk.Combobox(af, state='readonly', width=17, textvariable=all_var,
                     values=choices).pack(side='left')
        ttk.Button(af, text='应用到全部', command=_apply_all).pack(
            side='left', padx=6)

        outer = ttk.Frame(win, padding=(14, 6))
        outer.pack(fill='both', expand=True)
        canvas = tk.Canvas(outer, highlightthickness=0)
        sb = ttk.Scrollbar(outer, orient='vertical', command=canvas.yview)
        rows_fr = ttk.Frame(canvas)
        canvas.create_window((0, 0), window=rows_fr, anchor='nw')
        rows_fr.bind('<Configure>',
                     lambda e: canvas.configure(
                         scrollregion=canvas.bbox('all')))
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side='left', fill='both', expand=True)
        sb.pack(side='right', fill='y')
        canvas.bind('<MouseWheel>', lambda e: canvas.yview_scroll(
            -1 if e.delta > 0 else 1, 'units'))
        # 不固定 canvas 高度 → 随 outer（fill='both', expand=True）自适应，
        # 高 DPI 或小屏时 outer 被压缩、按钮始终可见
        for p in paths:
            fr = ttk.Frame(rows_fr)
            fr.pack(fill='x', pady=3)
            var = tk.StringVar(value='未分类')
            ttk.Label(fr, text=os.path.basename(p), width=38,
                      anchor='w').pack(side='left')
            ttk.Combobox(fr, state='readonly', width=17,
                         textvariable=var, values=choices).pack(side='left',
                                                                padx=8)
            rows.append((p, var))

        def _save():
            mapping = {p: ('' if var.get() == '未分类' else var.get())
                       for p, var in rows}
            try:
                cnt = apply_categories(DB_PATH, mapping)
            except Exception as e:
                messagebox.showerror('选择分类', f'保存失败：{e}', parent=win)
                return
            win.destroy()
            self.status.configure(text=f'已给 {cnt} 个文件指定分类')
            # 库窗口开着就刷新（分类树 + 列表）
            for w in self.root.winfo_children():
                if isinstance(w, tk.Toplevel) and hasattr(w, '_cat_tree') \
                        and w.winfo_exists():
                    self._refresh_library_win(w)

        def _on_return(_e):
            # 焦点在下拉框上时 Enter 留给 Combobox 选值，避免选一行就误触发保存
            if isinstance(win.focus_get(), ttk.Combobox):
                return
            _save()

        # Enter=保存、Esc=跳过（焦点在分类下拉框时 Enter 只选值不保存）
        win.bind('<Return>', _on_return)
        win.bind('<Escape>', lambda _e: win.destroy())
        # 底部按钮先 pack 到 bottom，高 DPI 或小屏窗口被夹矮时
        # 中间滚动区被压缩、按钮始终可见
        btns = ttk.Frame(win, padding=(14, 8))
        btns.pack(fill='x', side='bottom')
        ttk.Button(btns, text='保存', style='Accent.TButton', command=_save).pack(side='right')
        ttk.Button(btns, text='跳过', command=win.destroy).pack(
            side='right', padx=6)

    def _show_category_manager(self, lib_win=None):
        """分类管理：填空式行编辑（分类名 = 关键词1, 关键词2），存索引库"""
        win = tk.Toplevel(self.root)
        win.title('分类管理（自动归类规则）')
        self._center_win(win, 640, 500)
        win.transient(self.root)
        self._dialog_header(
            win, self.IC_FOLDER, '分类管理（自动归类规则）',
            '新文件入库时按规则自动归入分类（从上到下先中先得）\n'
            '每行：分类名 = 关键词（逗号隔开，匹配文件名/文档号/正文开头）')

        outer = ttk.Frame(win)
        outer.pack(fill='both', expand=True, padx=10)
        canvas = tk.Canvas(outer, highlightthickness=0, height=300)
        sb = ttk.Scrollbar(outer, orient='vertical', command=canvas.yview)
        rows_fr = ttk.Frame(canvas)
        canvas.create_window((0, 0), window=rows_fr, anchor='nw')
        rows_fr.bind('<Configure>',
                     lambda e: canvas.configure(
                         scrollregion=canvas.bbox('all')))
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side='left', fill='both', expand=True)
        sb.pack(side='right', fill='y')
        canvas.bind('<MouseWheel>', lambda e: canvas.yview_scroll(
            -1 if e.delta > 0 else 1, 'units'))

        rows = []   # [(name_var, kw_var, frame), ...]

        def add_row(name='', kws=''):
            fr = ttk.Frame(rows_fr)
            fr.pack(fill='x', pady=2)
            nv = tk.StringVar(value=name)
            kv = tk.StringVar(value=kws)
            name_entry=ttk.Entry(fr,textvariable=nv,width=18)
            name_entry.pack(side='left')
            ttk.Label(fr, text=' = ').pack(side='left')
            rule_entry=ttk.Entry(fr,textvariable=kv)
            rule_entry.pack(side='left',fill='x',expand=True,padx=(0,6))
            for entry,variable in ((name_entry,nv),(rule_entry,kv)):
                entry.bind('<Enter>',lambda _,entry=entry,variable=variable:self._show_btn_tip(entry,variable.get()))
                entry.bind('<Leave>',self._hide_tree_tip)

            def _del(fr=fr):
                fr.destroy()
                if (nv, kv, fr) in rows:
                    rows.remove((nv, kv, fr))
            ttk.Button(fr, text='删除', width=5, command=_del).pack(side='left')
            rows.append((nv, kv, fr))
        for r in get_category_rules(DB_PATH):
            if isinstance(r, (list, tuple)) and len(r) >= 2:
                add_row(str(r[0]),
                        ', '.join(str(k) for k in r[1:]))
        ttk.Button(win, text='＋ 添加分类',
                   command=lambda: add_row()).pack(anchor='w', padx=10,
                                                   pady=(6, 0))

        def _save():
            rules = []
            for nv, kv, _fr in rows:
                name = nv.get().strip()
                kws = [k.strip() for k in kv.get().replace('，', ',').split(',')
                       if k.strip()]
                if bool(name)!=bool(kws):
                    messagebox.showwarning('分类规则未填完整','分类名称和关键词需同时填写；请补全或清空不完整的行。',parent=win)
                    return
                if name and kws:
                    rules.append([name] + kws)
            if not rules:
                messagebox.showwarning('分类管理', '至少保留一个带关键词的分类。',
                                       parent=win)
                return
            res = save_category_rules(DB_PATH, rules)
            if res is True:
                win.destroy()
                if lib_win is not None and lib_win.winfo_exists():
                    self._refresh_library_win(lib_win)
                self._toast('分类规则已保存，新入库文件将自动归类')
            else:
                messagebox.showerror('分类管理', f'保存失败：{res}', parent=win)
        btns = ttk.Frame(win, padding=(10, 8))
        btns.pack(fill='x')
        ttk.Button(btns, text='保存', style='Accent.TButton', command=_save).pack(side='right')
        ttk.Button(btns, text='取消', command=win.destroy).pack(
            side='right', padx=6)

    def _show_category_audit(self, lib_win=None):
        """分类自查：统计各分类数量，列出未分类与归类可能不对的文件，可一键修正"""
        win = tk.Toplevel(self.root)
        win.title('分类自查')
        self._center_win(win, 900, 614)
        win.transient(self.root)
        self._dialog_header(
            win, self.IC_BOOKS, '分类自查',
            '统计各分类数量，列出未分类与归类存疑的文件')
        stat_lbl = ttk.Label(win, padding=(10, 6), text='正在自查…')
        stat_lbl.pack(anchor='w')
        bar = ttk.Frame(win, padding=(10, 0))
        bar.pack(fill='x')
        body = ttk.Frame(win, padding=(10, 6))
        body.pack(fill='both', expand=True)
        cols = ('name', 'current', 'suggested', 'path')
        tree = ttk.Treeview(body, columns=cols, show='headings')
        tree.heading('name', text='文件名')
        tree.heading('current', text='当前分类')
        tree.heading('suggested', text='建议分类')
        tree.heading('path', text='路径')
        tree.column('name', width=240)
        tree.column('current', width=100, anchor='center')
        tree.column('suggested', width=100, anchor='center')
        tree.column('path', width=400)
        vsb = ttk.Scrollbar(body, orient='vertical', command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        tree.pack(side='left', fill='both', expand=True)
        vsb.pack(side='right', fill='y')

        def _work():
            try:
                res = category_audit(DB_PATH)
            except Exception as e:
                win.after(0, lambda: stat_lbl.configure(text=f'自查失败：{e}'))
                return
            win.after(0, lambda: _fill(res))

        def _fill(res):
            counts = res['counts']
            total = res['total']
            # 按 01~10 编号顺序展示，空分类也列出
            parts = []
            for cat in CATEGORY_FOLDERS:
                n = counts.get(cat, 0)
                if n:
                    parts.append(f'{cat}: {n}')
            n_uncat = counts.get('', 0)
            if n_uncat:
                parts.append(f'未分类: {n_uncat}')
            ignored = res.get('ignored_count', 0)
            line1 = f'共 {total} 个文件'
            if ignored:
                line1 += f'（另有 {ignored} 个已忽略不参与自查）'
            # 多行展示分类分布（每行 4 个分类）
            stat_lines = [line1, '分类分布：']
            for i in range(0, len(parts), 4):
                stat_lines.append('    '.join(parts[i:i + 4]))
            stat_lbl.configure(text='\n'.join(stat_lines), justify='left')
            tree.delete(*tree.get_children())
            for it in res['uncategorized']:
                tree.insert('', 'end', iid=it['path'], tags=('none',), values=(
                    it['name'], '未分类', it['suggested'] or '（无建议）',
                    it['path']))
            for it in res['mismatch']:
                tree.insert('', 'end', iid=it['path'], tags=('diff',), values=(
                    it['name'], it['current'], it['suggested'], it['path']))
            tree.tag_configure('none', foreground='#c0392b')
            tree.tag_configure('diff', foreground='#C05621')
            n_fix = sum(1 for t in tree.get_children()
                        if 'none' in tree.item(t, 'tags')
                        and tree.set(t, 'suggested') not in ('', '（无建议）'))
            fix_btn.configure(state='normal' if n_fix else 'disabled',
                              text=f'修正未分类（{n_fix}）')
            undo_path = os.path.join(APP_DIR, 'category_undo.json')
            undo_btn.configure(
                state='normal' if os.path.exists(undo_path) else 'disabled')

        def _ignore_selected():
            """将选中文件标记为'忽略分类'，不再参与自查"""
            sels = tree.selection()
            if not sels:
                messagebox.showinfo('提示', '请先选中要忽略的文件', parent=win)
                return
            paths = [tree.set(iid, 'path') for iid in sels]
            n = set_category_ignored(DB_PATH, paths, True)
            for iid in sels:
                tree.delete(iid)
            self._toast(f'已忽略 {n} 个文件的分类（不再参与自查）')
            # 刷新统计
            _work()

        def _fix():
            # 只处理「未分类」且有建议的行；存疑行（diff）不参与批量修正
            mapping = {}
            preview = []
            for iid in tree.get_children():
                if 'none' not in tree.item(iid, 'tags'):
                    continue
                sug = tree.set(iid, 'suggested')
                if sug and sug != '（无建议）':
                    mapping[iid] = sug
                    preview.append(f'{tree.set(iid, "name")}  →  {sug}')
            if not mapping:
                return
            # 预览前 10 条搬迁路径
            preview_txt = '\n'.join(preview[:10])
            if len(preview) > 10:
                preview_txt += f'\n… 以及另外 {len(preview) - 10} 条'
            if not messagebox.askyesno(
                    '确认批量修正',
                    f'将把 {len(mapping)} 个未分类文件搬到建议分类文件夹：\n\n'
                    f'{preview_txt}\n\n确定执行？',
                    icon='warning', parent=win):
                return
            snapshot_db(DB_PATH, BACKUP_DIR, 'pre_catfix')
            recs = apply_categories_ex(DB_PATH, mapping)
            # 存撤销记录
            try:
                import json
                undo_path = os.path.join(APP_DIR, 'category_undo.json')
                with open(undo_path, 'w', encoding='utf-8') as f:
                    json.dump({'time': time.time(), 'records': recs}, f,
                              ensure_ascii=False)
                undo_btn.configure(state='normal')
            except Exception:
                pass
            tree.delete(*tree.get_children())
            fix_btn.configure(state='disabled', text='已修正')
            if lib_win is not None and lib_win.winfo_exists():
                self._refresh_library_win(lib_win)
            self._toast(f'已按规则修正 {len(recs)} 个文件的分类，可点「撤销」回退')

        def _undo_fix():
            """撤销上次批量修正：把文件搬回原位置并恢复原分类"""
            undo_path = os.path.join(APP_DIR, 'category_undo.json')
            if not os.path.exists(undo_path):
                return
            try:
                import json
                with open(undo_path, 'r', encoding='utf-8') as f:
                    recs = json.load(f).get('records', [])
            except Exception as e:
                messagebox.showerror('撤销失败', f'读取撤销记录出错：{e}',
                                     parent=win)
                return
            if not recs:
                return
            if not messagebox.askyesno(
                    '撤销修正',
                    f'将撤销上次批量修正（{len(recs)} 个文件），'
                    '搬回原文件夹并恢复原分类，确定？',
                    icon='warning', parent=win):
                return
            ok, skip = revert_categories(DB_PATH, recs)
            try:
                os.remove(undo_path)
            except OSError:
                pass
            undo_btn.configure(state='disabled')
            if lib_win is not None and lib_win.winfo_exists():
                self._refresh_library_win(lib_win)
            self._toast(f'已撤销 {ok} 个修正'
                        + (f'（{skip} 个跳过：文件已移动/被占用）' if skip else ''))
            _work()   # 重新自查刷新列表

        btns = ttk.Frame(win, padding=(10, 8))
        btns.pack(fill='x')
        fix_btn = ttk.Button(btns, text='修正未分类', state='disabled',
                             style='Accent.TButton', command=_fix)
        fix_btn.pack(side='right')
        undo_btn = ttk.Button(btns, text='撤销上次修正', state='disabled',
                              command=_undo_fix)
        undo_btn.pack(side='right', padx=6)
        ttk.Button(btns, text='忽略选中项',
                   command=_ignore_selected).pack(side='right', padx=6)
        ttk.Button(btns, text='重新自查',
                   command=lambda: (stat_lbl.configure(text='正在自查…'),
                                    threading.Thread(target=_work,
                                                     daemon=True).start())
                   ).pack(side='right', padx=6)
        ttk.Button(btns, text='关闭', command=win.destroy).pack(side='right')
        threading.Thread(target=_work, daemon=True).start()

    def show_dedupe(self, parent_win=None):
        """查重清理窗口：每组保留最新的，其余可一键删除"""
        if not os.path.exists(DB_PATH):
            messagebox.showinfo('提示', '库为空')
            return
        groups = find_duplicates(DB_PATH)
        win = tk.Toplevel(self.root)
        win.title('查重清理')
        self._center_win(win, 1000, 574)
        if parent_win is not None:
            win.transient(parent_win)
        self._dialog_header(
            win, self.IC_DOC, '查重清理',
            '每组保留修改时间最新的一个；内容重复可一键删，同名旧版本需人工确认')

        # 分组：内容重复（hash 相同）可一键删；同名旧版本（内容不同）需人工确认
        content_dups = [d['path'] for g in groups if g['reason'] == '内容重复'
                        for d in g['dups']]
        name_dups = [d['path'] for g in groups if g['reason'] == '同名旧版本'
                     for d in g['dups']]
        total_dups = len(content_dups) + len(name_dups)
        if groups:
            parts = []
            if content_dups:
                parts.append(f'内容重复 {len(content_dups)} 个（可一键删除）')
            if name_dups:
                parts.append(f'同名旧版本 {len(name_dups)} 个'
                             '（内容不同，请逐组人工勾选确认）')
            info = f'发现 {len(groups)} 组重复：' + '；'.join(parts)
        else:
            info = '未发现重复文件'
        ttk.Label(win, text=info, padding=(8, 6)).pack(anchor='w')

        cols = ('reason', 'mtime', 'size', 'path')
        tree = ttk.Treeview(win, columns=cols, show='tree headings')
        tree.heading('#0', text='文件')
        tree.heading('reason', text='类型')
        tree.heading('mtime', text='修改时间')
        tree.heading('size', text='大小')
        tree.heading('path', text='路径')
        tree.column('#0', width=240, anchor='w')
        tree.column('reason', width=90, anchor='center')
        tree.column('mtime', width=140, anchor='center')
        tree.column('size', width=80, anchor='e')
        tree.column('path', width=380, anchor='w')

        def _mtime_str(ts):
            return time.strftime('%Y-%m-%d %H:%M:%S',
                                 time.localtime(ts)) if ts else ''

        for gi, g in enumerate(groups):
            k = g['keep']
            is_name_grp = g['reason'] == '同名旧版本'
            parent = tree.insert('', 'end',
                text=f'{self.IC_CHECK} {k["name"]}（保留）',
                values=(g['reason'], _mtime_str(k['mtime']),
                        fmt_size(k['size'] or 0), k['path']),
                open=True, tags=('keep',))
            for d in g['dups']:
                if is_name_grp:
                    tree.insert(parent, 'end',
                        text=f'⚠ {d["name"]}（同名旧版本，请确认后再删）',
                        values=('需人工确认', _mtime_str(d['mtime']),
                                fmt_size(d['size'] or 0), d['path']),
                        tags=('dupname',))
                else:
                    tree.insert(parent, 'end', text=f'🗑 {d["name"]}（旧）',
                        values=('待删除', _mtime_str(d['mtime']),
                                fmt_size(d['size'] or 0), d['path']),
                        tags=('dup',))
        tree.tag_configure('keep', background='#EAF7EA',
                           font=('Microsoft YaHei UI', 9, 'bold'))
        tree.tag_configure('dup', background='#FDECEC')
        tree.tag_configure('dupname', background='#FDF3DC')
        vsb = ttk.Scrollbar(win, orient='vertical', command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        tree.pack(side='top', fill='both', expand=True, padx=8)
        vsb.place(relx=1.0, rely=0.06, relheight=0.86, anchor='ne')

        def _do_clean(only_content=True):
            # only_content=True → 只删内容重复组；False → 选中项（树里人工勾）
            if only_content:
                dup_paths = [d['path'] for g in groups
                             if g['reason'] == '内容重复' for d in g['dups']]
            else:
                sel = tree.selection()
                dup_paths = []
                for iid in sel:
                    # dup 子节点才有 path 可删（keep 节点不要动）
                    val = tree.set(iid, 'path')
                    if val and tree.set(iid, 'reason') != '保留':
                        dup_paths.append(val)
            if not dup_paths:
                return
            snapshot_db(DB_PATH, BACKUP_DIR, 'pre_dedupe')
            if not messagebox.askyesno(
                    '确认清理',
                    f'将删除 {len(dup_paths)} 个旧文件（移入回收站，可恢复），'
                    '确定继续？', icon='warning'):
                return
            ok = fail = 0
            for p in dup_paths:
                rc = remove_from_library(DB_PATH, p, delete_file=True)
                if rc == 'ok':
                    ok += 1
                else:
                    fail += 1
                    # 失败项在树上标红字，提醒用户
                    for iid in tree.get_children():
                        for child in tree.get_children(iid):
                            if tree.set(child, 'path') == p:
                                tree.item(child, tags=('fail',))
                                break
            tree.tag_configure('fail', foreground='#c0392b')
            messagebox.showinfo(
                '完成', f'已删除 {ok} 个旧文件'
                + (f'（{fail} 个失败：回收站不可用或被占用）' if fail else ''))
            if not fail:
                win.destroy()
                if parent_win is not None:
                    self._refresh_library_win(parent_win)
                self._refresh_err_count()

        btn_bar = ttk.Frame(win, padding=8)
        btn_bar.pack(side='bottom', fill='x')
        if content_dups:
            ttk.Button(btn_bar,
                       text=f'删除内容重复旧文件（{len(content_dups)}）',
                       style='Danger.TButton',
                       command=lambda: _do_clean(True)).pack(side='right')
        if name_dups:
            ttk.Button(btn_bar,
                       text='删除选中的旧文件（同名版本）',
                       command=lambda: _do_clean(False)).pack(
                           side='right', padx=6)
        ttk.Button(btn_bar, text='关闭',
                   command=win.destroy).pack(side='right', padx=4)





    # -------- 库整体打包 / 还原 --------


    def _package_all(self, parent=None):
        """打包 exe + 库（library + index.db + OCR缓存 + 配置），
        生成一个 zip，对方解压后直接双击 exe 即可用。"""
        if not os.path.exists(DB_PATH):
            messagebox.showinfo('提示', '库为空，没有可打包的内容',
                                parent=parent)
            return
        default_name = '镜仔搜索_含程序_%s.zip' % time.strftime('%Y%m%d_%H%M%S')
        zp = filedialog.asksaveasfilename(
            title='打包整套（软件+库），保存到',
            initialdir=self._pkg_default_dir(),
            initialfile=default_name,
            defaultextension='.zip',
            filetypes=[('压缩包', '*.zip')], parent=parent)
        if not zp:
            return
        if os.path.exists(zp):
            if not messagebox.askyesno(
                    '覆盖确认', f'{os.path.basename(zp)} 已存在，覆盖吗？',
                    parent=parent):
                return
        if not messagebox.askyesno(
                '开始打包',
                '将把以下内容打成一个压缩包：\n\n'
                '· 镜仔搜索.exe（程序本体）\n'
                '· library 文件夹（全部库内文件）\n'
                '· index.db 索引库（换电脑后无需重新识别）\n'
                '· OCR 缓存、手机上传收件箱、软件配置\n\n'
                '对方解压后双击 exe 即可直接使用，无需任何配置。\n'
                '开始吗？', parent=parent):
            return

        from indexer import package_library
        pw, bar, lbl, pct = self._pkg_progress_win('正在打包整套（软件+库）', 100)

        def cb(done, total, name):
            def u():
                bar.configure(maximum=total or 1, value=done)
                lbl.configure(text=name or '')
                pct.configure(text='%d / %d' % (done, total) if total else '')
            self.root.after(0, u)

        def work():
            try:
                r = package_library(zp, DB_PATH, APP_DIR,
                                    progress=cb, include_exe=True)
                self.root.after(0, lambda: self._pkg_done(pw, r))
            except Exception as e:
                self.root.after(0, lambda: self._pkg_fail(pw, zp, e))
        threading.Thread(target=work, daemon=True).start()

    def _package_library(self, parent=None):
        """一键打包库：library 全部文件 + index.db + OCR缓存/收件箱/配置"""
        if not os.path.exists(DB_PATH):
            messagebox.showinfo('提示', '库为空，没有可打包的内容', parent=parent)
            return
        default_name = '镜仔库备份_%s.zip' % time.strftime('%Y%m%d_%H%M%S')
        zp = filedialog.asksaveasfilename(
            title='库打包成压缩包，保存到',
            initialdir=self._pkg_default_dir(),
            initialfile=default_name,
            defaultextension='.zip',
            filetypes=[('压缩包', '*.zip')], parent=parent)
        if not zp:
            return
        if os.path.exists(zp):
            if not messagebox.askyesno(
                    '覆盖确认', f'{os.path.basename(zp)} 已存在，覆盖吗？',
                    parent=parent):
                return
        if not messagebox.askyesno(
                '开始打包',
                '将把以下内容打成一个压缩包：\n\n'
                '· library 文件夹（全部库内文件）\n'
                '· index.db 索引库（换电脑后无需重新识别）\n'
                '· OCR 缓存、手机上传收件箱、软件配置\n\n'
                '打包不影响正常使用，完成后可随时用「还原库」恢复。\n'
                '开始吗？', parent=parent):
            return

        from indexer import package_library
        pw, bar, lbl, pct = self._pkg_progress_win('正在打包库', 100)

        def cb(done, total, name):
            def u():
                bar.configure(maximum=total or 1, value=done)
                lbl.configure(text=name or '')
                pct.configure(text='%d / %d' % (done, total) if total else '')
            self.root.after(0, u)

        def work():
            try:
                r = package_library(zp, DB_PATH, APP_DIR, progress=cb)
                self.root.after(0, lambda: self._pkg_done(pw, r))
            except Exception as e:
                self.root.after(0, lambda: self._pkg_fail(pw, zp, e))
        threading.Thread(target=work, daemon=True).start()



    def _restore_library(self, parent=None):
        """从压缩包还原库（准备→重启切换）"""
        from indexer import inspect_package
        zp = filedialog.askopenfilename(
            title='选择镜仔库压缩包',
            initialdir=self._pkg_default_dir(),
            filetypes=[('库压缩包', '*.zip *.7z *.rar'),
                       ('所有文件', '*.*')],
            parent=parent)
        if not zp:
            return
        try:
            mf = inspect_package(zp)
        except Exception as e:
            messagebox.showerror('无法还原', str(e), parent=parent)
            return
        size_mb = (mf.get('total_size') or 0) / 1024 / 1024
        if not messagebox.askyesno(
                '确认还原',
                '压缩包信息：\n'
                '· 打包时间：%s\n· 文档记录：%s 篇\n· 库文件总大小：约 %.0f MB\n\n'
                '还原将替换当前整个库；现有的库会先挪进 backup 文件夹保留，'
                '不会直接删除。\n\n'
                '还原需要软件自动重启一次，确定继续吗？'
                % (mf.get('created_at', '未知'),
                   mf.get('doc_count', '?'), size_mb), parent=parent):
            return

        from indexer import restore_library, discard_restore
        pw, bar, lbl, pct = self._pkg_progress_win('正在准备还原', 100)

        def cb(done, total, name):
            def u():
                bar.configure(maximum=total or 1, value=done)
                lbl.configure(text=name or '')
                pct.configure(text='%d / %d' % (done, total) if total else '')
            self.root.after(0, u)

        def work():
            try:
                r = restore_library(zp, APP_DIR, progress=cb)
                self.root.after(0, lambda: self._restore_ready(pw, r))
            except Exception as e:
                self.root.after(0, lambda: self._restore_fail(pw, e))
        threading.Thread(target=work, daemon=True).start()

    def _restore_ready(self, pw, r):
        pw.destroy()
        from indexer import finish_restore_bat
        try:
            bat = finish_restore_bat(r['staging'], APP_DIR,
                                     wait_pid=os.getpid())
        except Exception as e:
            messagebox.showerror('还原失败', '生成重启任务失败：%s' % e)
            return
        messagebox.showinfo(
            '准备就绪',
            '还原已准备好（%s 篇，已改写 %s 条本机路径）。\n\n'
            '点击确定后软件自动关闭、完成替换并重新打开，'
            '请先保存好其他窗口的内容。'
            % (r['doc_count'], r['rewritten_paths']))
        try:
            subprocess.Popen(['cmd.exe', '/c', 'start', '', bat],
                             cwd=APP_DIR, close_fds=True,
                             creationflags=getattr(subprocess,
                                                   'CREATE_NO_WINDOW', 0))
        except Exception:
            # 兜底：直接运行 bat（会闪一下命令行窗）
            subprocess.Popen([bat], cwd=APP_DIR, shell=True)
        self._shutdown_for_restore()






    # -------- 使用说明 --------
    def show_help(self):
        """使用说明窗口（大白话版）"""
        # 已开且主题没变 → 只提到前台；主题变了就重建，避免深色下白底刺眼
        old = self._wins.get('help')
        if old is not None:
            try:
                if old.winfo_exists():
                    if getattr(old, '_jz_dark', None) == self.dark.get():
                        old.lift()
                        old.focus_force()
                        return
                    old.destroy()
            except Exception:
                pass
            self._wins.pop('help', None)
        win = self._get_win('help')
        if win is None:
            return
        win._jz_dark = self.dark.get()
        pal = self.palette()
        win.title('使用说明')
        self._center_win(win, 620, 660, modal=False)   # 阅读型，不锁主界面
        win.transient(self.root)
        win.configure(bg=pal['bg'])

        # 中文没有空格，wrap='word' 只会在「→」「Ctrl+Alt+J」这些少数空格处断行，
        # 造成忽长忽短的胡乱分行；改成按字符断行，每行都能铺满
        txt = tk.Text(win, wrap='char', font=('Microsoft YaHei UI', 10),
                      padx=18, pady=14, background=pal['bg'],
                      foreground=pal['title_fg'], bd=0, highlightthickness=0,
                      insertbackground=pal['title_fg'])
        sb = ttk.Scrollbar(win, orient='vertical', command=txt.yview)
        txt.configure(yscrollcommand=sb.set)
        txt.pack(side='left', fill='both', expand=True)
        sb.pack(side='right', fill='y')

        # 样式：色值全部取设计令牌，跟随亮/深色
        txt.tag_configure('h1', font=('Microsoft YaHei UI', 14, 'bold'),
                          foreground=pal['brand'], spacing1=8, spacing3=6)
        txt.tag_configure('h2', font=('Microsoft YaHei UI', 11, 'bold'),
                          foreground=pal['brand'], spacing1=12, spacing3=3)
        txt.tag_configure('tip', font=('Microsoft YaHei UI', 10),
                          foreground=pal['title_fg'], lmargin1=16,
                          lmargin2=16, spacing1=1, spacing3=1)
        txt.tag_configure('dim', foreground=pal['sub_fg'], spacing3=8)

        def h1(s): txt.insert('end', s + '\n', 'h1')
        def h2(s): txt.insert('end', s + '\n', 'h2')
        def tip(s): txt.insert('end', s + '\n', 'tip')
        def dim(s): txt.insert('end', s + '\n', 'dim')

        h1('镜仔搜索 · 使用说明')
        dim('一句话：把文件拖进来，输个词，就能找到内容在哪一页、哪一条。')

        h2('★ 最有特色的功能（这几个最好用，先看）')
        tip('1. 引用预警：库管理 → 高级工具 → 引用预警，自动揪出「引用了已废止文件」的条款，'
            '防止还在用过期规定糊弄事')
        tip('3. 迷你速查窗：写材料时按 Ctrl+Alt+J，弹出小窗输入即搜，'
            '回车把《文件名》第X条 引用直接粘进当前文档，写报告超快。'
            '结果两行显示（文件名+章节位置+正文片段，命中词标色），'
            'Ctrl+C 只复制不粘贴、Ctrl+O 跳回软件定位、Esc 关窗；'
            '空输入按 ↑↓ 翻搜索历史，窗内可直接勾「仅看有效/同义扩展」，'
            '小窗可拖动缩放，位置尺寸会记住')
        tip('4. 剪贴板快存：看到有用的段落复制后按 Ctrl+Alt+M，'
            '自动存成文本并建好索引，立刻能搜到，还记下当时在哪个窗口')
        tip('5. 同义扩展：搜「安全员」连「专职安全管理员」「安全管理人员」'
            '一起找到，换个叫法也不漏（顶部勾选「同义扩展」）')
        tip('6. 十大分类自动归档：文件一拖进来，按文件名自动分到'
            '01国家法律 ~ 10其他资料 十个文件夹，库目录永远整整齐齐')
        tip('7. 条款级定位：结果直接带「第几章第几条」，'
            '点「复制引用」一键生成《文件名》第X条 的标准格式')
        tip('8. 废止管理：过期文件自动标灰、默认搜不到，'
            '标记废止时还会提醒哪些文件引用了它')
        tip('9. 命中分布图：搜索后顶部柱状图显示哪个文件命中最多，'
            '柱子越高越相关，点柱子直接跳到那个文件')

        h2('一、三步上手')
        tip('1. 把文件拖进软件窗口（Word、PDF、Excel、PPT、图片、txt 都行）')
        tip('2. 上面的框里输要找的词——不用按回车，停下来就自动搜')
        tip('3. 单击结果看原文段落，双击直接打开原文件到那一页')

        h2('二、搜索小技巧')
        tip('0. 记不住语法：点搜索框右侧的「?」，打开带实例的速查卡，'
            '点实例直接填入')
        tip('1. 一行里同时有两个词：空格隔开，如 安全 培训')
        tip('2. 两个词找任意一个：中间加竖线，如 安全 | 环保')
        tip('3. 不要某个词：前面加减号，如 安全 -费用')
        tip('4. 字打错了也不怕：搜索类型选「模糊」，错一个字也能找到')
        tip('5. 结果太多：结果上方的小框再输个词，立刻再筛一遍')
        tip('6. 同义词命中显示橙色（普通关键词是黄色），'
            '状态栏会提示扩展了哪些叫法')

        h2('三、按文号/状态/日期/分类直接筛')
        tip('1. 按文号：文号:12号（写片段即可，如 文号:安监12号）')
        tip('2. 按状态：状态:现行 / 状态:废止 / 状态:替代；'
            '多个用竖线：状态:废止|替代')
        tip('3. 按生效日期：日期:2024（整年）、日期:2022..2024（区间）、'
            '日期:2023..（某年及以后）、日期:..2023（某年及以前）、'
            '日期:2024-03-01（精确到天）')
        tip('4. 按文件名：名称:差旅（只在文件名里找，不搜正文）')
        tip('5. 按分类：分类:05-国家标准GB；多个用竖线：'
            '分类:01-国家法律|02-行政法规')
        tip('6. 只输前缀不输正文词 = 直接列出符合条件的文件；'
            '也能和正文词混用，如：差旅 状态:现行 日期:2023..')
        tip('7. 冒号中英文都行；前缀词不会被高亮')

        h2('四、结果怎么看')
        tip('1. 同一文件里连着几行都命中，会自动合并成一条，标「N 行相邻命中」')
        tip('2. PDF、PPT 的命中行带页码（位置如 L45 · P5 = 第 45 行、第 5 页），双击直接跳到那一页')
        tip('3. 点结果：右边显示完整段落，左边显示属于哪一章哪一条')
        tip('4. 顶部柱状图：每根柱子是一个文件，越高命中越多；'
            '灰色柱子是已废止文件；点柱子跳到该文件首条命中')

        h2('五、废止和有效文件')
        tip('1. 文件入库时，软件自动识别文档号和生效日期，不用手填')
        tip(f'2. 过期文件标「{self.IC_WARN}废止」、灰色显示，默认搜不到')
        tip('3. 勾上「仅看有效」，只搜现行有效的文件')
        tip('4. 标记某文件为废止时，软件会提醒哪些文件引用了它')
        tip('5. 识别错了能改：库管理 → 右键文件 → 编辑元数据')

        h2('六、文件存哪里')
        tip('1. 拖进来的文件会复制一份，放在软件旁边的 library 文件夹，'
            '原来的删了也不影响')
        tip('2. 拖入压缩包（zip/7z）会自动拆开，包里每个文件独立入库、'
            '各自按内容归类到 01~10 文件夹；包内文件与库里已有的相同内容'
            '会自动跳过；压缩包本身不保留（内容已全部入库）')
        tip('3. 按十大分类分别放在 01~10 的子文件夹里，不会散在根目录')
        tip('4. 一键打包库：库管理 → 高级工具 → 一键打包库，整个库（文件+索引+'
            'OCR识别结果）压成一个 zip，软件本身不带数据、体积不变；'
            '换电脑时在新软件里选「从压缩包还原库」，自动重启后整库恢复，'
            '不用重新识别，原来的库会自动留在 backup 文件夹兜底')
        tip('5. 换电脑：把软件文件夹整个拷走也行，或用上面的打包/还原')
        tip('6. 扫描件和图片要逐字识别（OCR），大文件请耐心等一会')

        h2('七、常用快捷键')
        tip('1. Ctrl+Alt+J  呼出「镜仔速查」迷你窗：Enter 粘引用、Ctrl+C 只复制、'
            'Ctrl+O 定位、↑↓ 选择/翻历史、Esc 关闭')
        tip('2. Ctrl+Alt+M  把剪贴板文字快存入库（自动建索引，立刻可搜）')
        tip('3. Ctrl+F    回到搜索框')
        tip('4. F5        重新扫描文件夹')
        tip('5. Ctrl+E    把搜索结果导出成表格')
        tip('6. F1        打开本说明')
        tip('7. 全局热键可在 ⚙ → 热键设置 里改；被占用会提示更换')

        h2('八、其他好用功能')
        tip('1. 收藏：预览时点「☆ 收藏此条」，顶部「书签」按钮随时找回')
        tip('2. 收藏汇编：书签窗口 → 导出汇编，收藏的条款自动整理成'
            '《常用制度摘编》（带文号、施行日期、章节定位），可直接打印下发')
        tip('3. 结果汇编：搜完点结果上方「导出汇编」，命中条款自动成稿'
            '（带文号、施行日期、章节定位，关键词黄底标注），可直接打印')
        tip('4. 版本对比：库管理 → 高级工具 → 版本对比，挑两个文件左右对照；'
            '也可在库管理或搜索结果里右键 → 与旧版对比，自动认出同名旧版本并排打开，'
            '删改之处红绿标出')
        tip('5. 引用跳转：正文里蓝色的《文件名》点一下，跳到被引用的文件；'
            '红色说明对方已废止')
        tip('6. 分类自查：库管理 → 高级工具 → 分类自查，列出未分类和可能归错的文件，'
            '一键修正，文件自动搬进对应文件夹')
        tip('7. 查重清理：库管理 → 高级工具 → 查重清理，自动找出重复文件，保留最新的')
        tip('8. 自定义同义词：⚙ → 同义词库设置，加本单位叫法'
            '（如 安环部 = 安全环保部）；「内置词组…」可看自带的 16 组')
        tip('9. 命令行唤起：镜仔搜索.exe /s "关键词" 启动即搜；'
            '重复启动不会开第二个窗口，自动转给已运行的实例')
        tip('10. 开机自启动：⚙ → 勾选「开机自启动」，开电脑自动运行')
        tip('11. 网页唤起：软件启动时已自动注册 jingzai:// 协议，'
             '浏览器打开 jingzai://search?kw=关键词 即可在软件里搜索')
        tip('12. 扫描入库：点「扫描入库」选文件夹，里面所有文件自动入库；'
             'F5 随时重扫')
        tip('13. 深色模式：⚙ 菜单勾选「深色模式」；预览按住 Ctrl+滚轮缩放字号；'
             '底部「自适应换行」可把 PDF/Word 硬断行重排铺满窗格，关掉即逐行原样')
        tip('14. 引用关系图：⚙ → 生成引用关系图（HTML），离线网页不依赖网络。'
             '默认「分层看前后」——依据在最上层、细则在最下层，箭头指向被引用文件；'
             '可切「力导向看聚类」看同分类抱团。单击节点高亮它的依据/引用者，'
             '右侧名称继续点能沿引用链追溯；双击节点只展开它 1~3 层引用链，'
             '再双击空白恢复全库；滚轮缩放、拖拽平移，放大后自动显示文件名')

        h2('九、关闭与后台运行')
        tip('1. 点右上角的叉：本次启动第一次会先问「最小化到托盘」还是'
            '「直接退出」，选完这次就一直照这个来，不会再问')
        tip('2. 只影响本次运行：彻底退出程序后，下次打开还会重新问一次')
        tip('3. 选「最小化到托盘」：窗口收进系统托盘、后台继续运行，'
            '手滑点了也不会把正在跑的入库任务关掉；'
            '双击托盘图标，或右键托盘图标 → 显示主窗口，即可唤回')
        tip('4. 选「直接退出」：结束程序；若入库等长任务还在跑，'
            '会先确认一次再退')
        tip('5. 询问框上直接关掉（或按 Esc）＝取消，什么也不做')
        tip('6. 想立刻彻底退出、不想等问答：右键托盘图标 → 退出程序')
        tip('7. 收进托盘后全局热键照常可用；用 镜仔搜索.exe /s "关键词" '
            '或浏览器打开 jingzai:// 链接，会自动把主窗口调出来')

        h2('十、免责声明')
        tip('1. 本软件仅用于办公文档的整理与检索，搜索结果、条款摘录、'
            '分类与有效性标注均由程序自动处理，仅供参考')
        tip('2. 软件输出不能作为法律依据或执法依据。办理具体事项时，'
            '请以正式发布的法律法规、规章及规范性文件的原文为准')
        tip('3. 因文件版本更新、OCR 识别误差、元数据识别偏差等造成的'
            '内容出入，请以权威来源的现行有效文本为准')
        dim('使用本软件即表示您已知悉并接受上述说明。')

        txt.configure(state='disabled')







if __name__ == '__main__':
    # 批2：命令行/协议参数 + 单实例转发（第二实例带参则转发后退出）
    _startup_kw = ipc.read_command(sys.argv)
    if not ipc.acquire_mutex():
        if _startup_kw:
            ipc.forward_to_primary(APP_DIR, _startup_kw)
        sys.exit(0)
    # 高 DPI 屏幕清晰显示（必须在创建 Tk 窗口前调用）
    # 否则 Windows 会位图拉伸导致界面模糊
    if sys.platform.startswith('win'):
        import ctypes
        # 任务栏图标归组：必须在窗口创建前设置 AppUserModelID，
        # 否则任务栏显示默认 Tk 图标而不是 exe 自带图标
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                'DragonStarFlower.JingzaiSearch.1')
        except Exception:
            pass
        try:
            # Win8.1+: 每显示器 DPI 感知（最清晰）
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            try:
                # Win Vista+: 系统 DPI 感知（兜底）
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass
    # DnD 可用时用 tkinterdnd2.Tk()，否则回退到 tk.Tk()
    if _DND_AVAILABLE:
        root = TkinterDnD.Tk()
    else:
        root = tk.Tk()
    # 按系统 DPI 调整 Tk 缩放因子，避免开启 DPI 感知后字体变小
    if sys.platform.startswith('win'):
        try:
            import ctypes
            dpi = ctypes.windll.user32.GetDpiForSystem()
            # Tk scaling 单位是 points/pixel，96 DPI 时为 1.333（即 72/96 倒数）
            root.tk.call('tk', 'scaling', dpi / 72.0)
        except Exception:
            pass
    # 启动淡入（与启动图衔接；减弱动效时直接全显）
    _startup_fade = anim is not None and not anim.reduced()
    if _startup_fade:
        root.attributes('-alpha', 0.0)
    app = App(root)
    # 首帧强制绘制：Tk 惰性绘制，窗口第一次显示前若从未画过，会先露出一帧
    # 未初始化的黑色表面（“黑一下才变正常”）。趁 alpha=0 时先把首帧画完，
    # 淡入/显示时就直接是界面底色，不再闪黑。
    try:
        root.update_idletasks()
        root.update()
    except Exception:
        pass
    # 上面的 update() 只跑 Tk 事件循环，不强制 Win32 同步绘制：Tk 给每个
    # 子控件都建了独立 HWND，分层窗口在真正 WM_PAINT 前仍是黑色表面。
    # 逐个 HWND 发同步 RedrawWindow，把首帧像素真正按到屏幕上。
    try:
        if winchrome is not None:
            winchrome.force_paint(root, deep=True, passes=2)
    except Exception:
        pass
    if _startup_fade:
        root.after(80, lambda: anim.fade_in(root, 280))
    # 批2：单实例本地转发服务（第二实例的 /s、jingzai:// 参数走这里）
    ipc_server = None
    try:
        ipc_server = ipc.IPCServer(APP_DIR, app.accept_remote_keyword,
                                   on_quit=root.destroy)
        ipc_server.serve_forever_daemon()
        app.ipc_server = ipc_server
    except Exception:
        ipc_server = None
    # 固化默认行为：静默注册 jingzai:// 协议 + 条件满足时挂文件监听
    app._auto_register_protocol()
    app._start_watcher_silent()
    # 首实例带参启动：主窗就绪后执行搜索并置顶
    if _startup_kw:
        root.after(500, lambda: app._remote_search(_startup_kw))
    # 语义索引不再启动时自动跑，改为首次打开 AI 镜仔智答窗时后台构建
    def _on_close():
        # 关闭语义（会话级记忆，不落盘）：首次点叉弹框询问「最小化到托盘 /
        # 直接退出」，选完本次内沿用；托盘右键「退出程序」置 _force_quit，
        # 直接退出、绝不询问。详见 App._resolve_close_choice。
        choice = app._resolve_close_choice()
        if choice is None:
            return                      # 关掉询问框 = 取消，什么也不做
        if choice == 'tray' and app._hide_to_tray():
            return
        # 入库等长任务进行中 → 确认后再退（未完成部分中断）
        if getattr(app, '_task_busy', False):
            cur = ''
            try:
                cur = str(app.status.cget('text'))[:40]
            except Exception:
                pass
            if not messagebox.askokcancel(
                    '任务还在进行',
                    f'{cur}\n\n现在退出会中断任务（已完成的文件保留），确定退出？'):
                return
        app._stop_hotkeys()
        # 清理托盘图标（若处于后台运行状态）
        if getattr(app, '_tray', None) is not None:
            try:
                app._tray.del_icon()
                app._tray.stop()
            except Exception:
                pass
            app._tray = None
            app._bg_mode = False
        if app.watcher:
            try:
                app.watcher.stop()
            except Exception:
                pass
        app._save_config()
        app._backup_db()
        if ipc_server:
            ipc_server.shutdown_clean()
        root.destroy()
    root.protocol('WM_DELETE_WINDOW', _on_close)
    root.mainloop()
