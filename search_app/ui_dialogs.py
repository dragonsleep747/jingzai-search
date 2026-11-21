# -*- coding: utf-8 -*-
"""GUI 对话框混入层：从 main.py 原样迁出的 App 方法。

只做代码搬家：方法体逐字保留，引用仍通过 self 解析，行为不变。
后续拆分 main.py 时优先从这里迁出/改写。
"""
from core import (
    APP_DIR,
    CTX_LINES,
    DEFAULT_FEEDBACK_URL,
    KW_HINT,
    TYPE_OPTIONS,
    _resource_path,
    build_citation,
    kw_terms,
    save_config,
)
from export import build_search_hits, write_digest_docx
from indexer import (
    OTHER_CATEGORY,
    add_bookmark,
    clear_errors,
    count_errors,
    get_category_rules,
    get_error_traceback,
    get_errors,
    get_metadata,
    index_batch_files,
    is_bookmarked,
    library_stats,
    list_library,
    remove_bookmark,
    remove_from_library,
    rename_in_library,
    save_reading_position,
)
from runtime import DB_PATH, LIBRARY_DIR, MAX_BACKUPS
from searcher import (
    _PAGE_RE,
    _norm_variants,
    extract_page,
    find_version_siblings,
    get_chapter_chain_str,
    get_line_range,
    get_line_text,
    get_paragraph,
    list_chapters,
)
from service import (
    eff_sort_key,
    fmt_size,
    is_table_row,
    line_path_from_tags,
    parse_dropped_paths,
    parse_excel_loc,
    plan_row_cost,
    prepare_filter_cache,
)
from synonyms import expand_map
from urllib.parse import quote
from thirdparty import (
    _HOTKEY_OK,
    _QA_OK,
    _SVTTK,
    _TRAY_OK,
    _tray_mod,
    anim,
    qa_win,
    quickwin,
    sv_ttk,
    websearch,
    winchrome,
)
from tkinter import filedialog, messagebox, simpledialog, ttk
from watcher import FileWatcher
import ipc
import os
import queue
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
import webbrowser

class DialogsMixin:
    def _draw_distribution_chart(self):
        """在画布上绘制全部文件的命中分布横向条形图（按命中数降序）。
            每个文件一栏：文件名 / 彩条 / 命中数；废止文件灰条。
            超出可视宽度用底部滚动条或滚轮横向查看。"""
        canvas = getattr(self, '_chart_canvas', None)
        if canvas is None:
            return
        canvas.delete('all')
        self._chart_items = []
        groups = [g for g in self.groups if g.get('hits')]
        if not groups:
            canvas.configure(scrollregion=(0, 0, 1, 1))
            return
        c = self._theme_colors()
        canvas.configure(bg=c['preview_bg'])
        colors = ['#4285F4', '#34A853', '#FBBC05', '#EA4335', '#9C27B0']
        gray = '#9E9E9E'
        max_hits = groups[0]['hits'] or 1
        PAD, BARW = (8, 78)
        name_top = 6
        bar_h, bar_r = (14, 4)
        gap_tb, gap_bn, hover_dn = (6, 6, 3)
        bar_y0 = name_top + self._chart_name_font.metrics('linespace') + gap_tb
        bar_y1 = bar_y0 + bar_h
        num_y = bar_y1 + hover_dn + gap_bn
        H = num_y + self._chart_count_font.metrics('linespace') + 6
        canvas.configure(height=H)
        for j in getattr(self, '_chart_grow_jobs', []):
            try:
                canvas.after_cancel(j)
            except Exception:
                pass
        self._chart_grow_jobs = []
        for tw in getattr(self, '_chart_tweens', []):
            try:
                tw.cancel()
            except Exception:
                pass
        self._chart_tweens = []
        self._chart_grown = not self._anim_ok()
        self._chart_hover_idx = -1
        COLW_MIN, COLW_MAX = (96, 240)
        name_ws = []
        for g in groups:
            w = self._chart_name_font.measure(g['name']) + 12
            name_ws.append(max(COLW_MIN, min(w, COLW_MAX)))
        x = PAD
        bar_infos = []
        for i, g in enumerate(groups):
            colw = name_ws[i]
            cx = x + colw // 2
            dead = g.get('status') not in ('active', None, '')
            color = gray if dead else colors[i % len(colors)]
            name = self._fit_text(self._chart_name_font, g['name'], colw - 12)
            lbl_id = canvas.create_text(cx, name_top, text=name, font=self._chart_name_font, fill=c['meta_fg'], anchor='n')
            bw = max(3, int(BARW * g['hits'] / max_hits))
            bar_id = canvas.create_polygon(self._bar_pts(cx, 0, bar_y0, bar_y1, bar_r), fill=color, width=0, smooth=True, tags=('bar', f'idx{i}'))
            num_id = canvas.create_text(cx, num_y, text=str(g['hits']), font=self._chart_count_font, fill=c['preview_bg'] if self._anim_ok() else color if not dead else c['meta_fg'], anchor='n')
            bar_infos.append(dict(id=bar_id, num=num_id, lbl=lbl_id, cx=cx, bw=bw, y0=bar_y0, y1=bar_y1, r=bar_r, hover_dn=hover_dn, color=color, dead=dead, num_fg=color if not dead else c['meta_fg']))
            self._chart_items.append({'x0': x, 'x1': x + colw, 'name': g['name'], 'hits': g['hits'], 'path': g['path'], 'line': g.get('first_line', 1), 'dead': dead})
            x += colw
        canvas.configure(scrollregion=(0, 0, x + PAD, H))
        self._chart_bar_infos = bar_infos
        if self._anim_ok():
            for i, bi in enumerate(bar_infos):
                job = canvas.after(i * 26, lambda bi=bi: self._chart_grow_one(canvas, bi))
                self._chart_grow_jobs.append(job)
        else:
            self._chart_grown = True

    def _dialog_header(self, win, icon, title, subtitle=''):
        """统一对话框头部：品牌色 MDL2 图标 + 标题 + 副标题 + 发丝分隔线。
            返回 (band, title_lbl, subtitle_lbl|None)，副标题标签可后续 config。"""
        pal = self.palette()
        band_bg = pal['band_bg']
        line_bg = pal['band_line']
        title_fg = pal['title_fg']
        sub_fg = pal['sub_fg']
        brand = pal['brand']
        band = tk.Frame(win, bg=band_bg, bd=0, highlightthickness=0)
        band.pack(fill='x')
        row = tk.Frame(band, bg=band_bg, bd=0, highlightthickness=0)
        row.pack(fill='x', padx=16, pady=(12, 10))
        ic = tk.Label(row, text=icon, bg=band_bg, fg=brand, bd=0, font=('Segoe MDL2 Assets', 15))
        ic.pack(side='left', anchor='n', padx=(0, 10))
        col = tk.Frame(row, bg=band_bg, bd=0, highlightthickness=0)
        col.pack(side='left', fill='x', expand=True)
        tl = tk.Label(col, text=title, bg=band_bg, fg=title_fg, bd=0, font=('Microsoft YaHei UI', 12, 'bold'), anchor='w', justify='left')
        tl.pack(anchor='w')
        sl = None
        if subtitle:
            sl = tk.Label(col, text=subtitle, bg=band_bg, fg=sub_fg, bd=0, font=self.FONT_HINT, anchor='w', justify='left')
            sl.pack(anchor='w', pady=(2, 0))
        hair = tk.Frame(win, height=1, bg=line_bg, bd=0, highlightthickness=0)
        hair.pack(fill='x')
        headers = getattr(self, '_dlg_headers', None)
        if headers is None:
            headers = self._dlg_headers = []
        headers.append((win, band, hair, ic, tl, sl, (row, col)))
        return (band, tl, sl)

    def _reset_results(self):
        """搜索框清空 → 结果区同步重置（结果树/统计/预览/章节目录/过滤词）"""
        if self._prev_hist:
            self._prev_hist = []
            self.back_btn.configure(state='disabled')
        if not (getattr(self, 'results', None) or getattr(self, 'groups', None)):
            return
        self.results = []
        self.groups = []
        self._last_searched = ''
        self._syn_map = {}
        self._ctx_map = {}
        self.tree.delete(*self.tree.get_children())
        self._tree_item_paths = {}
        if hasattr(self, 'filter_var'):
            self.filter_var.set('')
        self.preview.delete('1.0', 'end')
        self._cur_path = None
        self._cur_line = None
        self._cur_hit_lines = []
        self._cur_cross = None
        self._chapters_path = None
        self.chap_tree.delete(*self.chap_tree.get_children())
        self.chap_tree.insert('', 'end', text='搜索后在此显示该文件的章节结构', tags=('ph',))
        self._preview_ph.place(relx=0.5, rely=0.5, anchor='center')
        self.stats_label.configure(text='')
        self._draw_distribution_chart()
        self._refresh_bookmark_btn()
        self._set_status('就绪 · 拖拽文件到窗口直接入库')

    def _insert_cluster(self, parent, g, cl, cl_idx, kw):
        """插入一个命中片段簇行（+ 可选上下文灰字行）"""
        odd_tag = 'odd' if cl_idx % 2 else ''
        first = cl['lines'][0]
        hit_lns = [m['line_no'] for m in cl['lines']]
        cross_map = getattr(self, '_cluster_cross', None)
        first_cross = first.get('cross_word')
        if first_cross and first.get('cross_next'):
            hit_lns.append(first['cross_next'])
            if cross_map is not None:
                cross_map[g['path'], first['line_no']] = (first['cross_next'], first_cross)
        self._cluster_rows[g['path'], first['line_no']] = hit_lns
        fuzzy = any((m.get('score', 1.0) < 1.0 for m in cl['lines']))
        mark = '≈ ' if fuzzy else ''
        if first_cross and first.get('cross_next'):
            row_label = f'L{first['line_no']}-{first['cross_next']}'
            tail = '   〔跨行命中〕'
        elif cl['line_end'] > cl['line_start']:
            row_label = f'L{cl['line_start']}-{cl['line_end']}'
            tail = f'   〔{len(cl['lines'])} 行相邻命中〕'
        else:
            row_label = f'L{cl['line_start']}'
            tail = ''
        pg = self._page_label(first['line_text'], first['line_no'], g['name'])
        if pg:
            row_label += ' · ' + pg
        if first_cross:
            joined = _PAGE_RE.sub('', first['line_text'] or '', count=1).rstrip() + _PAGE_RE.sub('', first.get('cross_text') or '', count=1).lstrip()
            snippet = self._snippet(joined, first_cross) + tail
        else:
            snippet = self._snippet(first['line_text'], kw, fuzzy=fuzzy) + tail
        cl_item = self.tree.insert(parent, 'end', text='  ' + row_label, values=('', first['line_no'], mark + snippet), tags=(('fuzzyline',) if fuzzy else ()) + (('crossline', 'line') if first_cross else ('line',)) + (g['path'], odd_tag), open=True)

    def _pkg_progress_win(self, title, total):
        """打包/还原通用进度窗，返回 (win, bar, lbl, closed_flag)"""
        pw = tk.Toplevel(self.root)
        pw.title(title)
        self._center_win(pw, 480, 220, modal=True)
        pw.transient(self.root)
        pw.resizable(False, False)
        self._dialog_header(pw, self.IC_DOWNLOAD, title)
        lbl = ttk.Label(pw, text='准备中…', wraplength=410)
        lbl.pack(pady=(14, 8), padx=20)
        bar = ttk.Progressbar(pw, maximum=total if total else 100, length=400, mode='determinate', style='Brand.Horizontal.TProgressbar')
        bar.pack(pady=4)
        pct = ttk.Label(pw, text='')
        pct.pack(pady=(2, 0))
        pw.protocol('WM_DELETE_WINDOW', lambda: None)
        return (pw, bar, lbl, pct)

    def _populate_tree(self, restore=False):
        """按 self.groups 填充结果树；支持结果内二次过滤（filter_var）。
            过滤规则：文件名命中 → 保留整组；否则只保留片段含过滤词的命中行。
            组数较多时分批插入（按「行预算」切片，不是按固定组数），这样首屏
            立刻可见、主线程每帧只干一小段活；与「减弱动效」开关无关 ——
            分批是为了不卡，不是为了好看。"""
        fk = self.filter_var.get().strip().lower() if hasattr(self, 'filter_var') else ''
        kw = self.keyword.get()
        if fk:
            prepare_filter_cache(self.groups)
        self._populate_gen = getattr(self, '_populate_gen', 0) + 1
        gen = self._populate_gen
        self._tree_hover_iid = None
        if restore:
            self._capture_tree_view()
        else:
            self._clear_tree_view()
        self.tree.delete(*self.tree.get_children())
        self._tree_item_paths = {}
        self._more_map = {}
        plan = []
        for g in self.groups:
            tags = ['file']
            _status_suffix = ''
            status = g.get('status', 'active')
            if status == 'void':
                tags.append('void')
                _status_suffix = '  （已废止）'
            elif status == 'replaced':
                tags.append('replaced')
                _status_suffix = '  （已被替代）'
            name_hit = bool(fk) and fk in (g.get('_low_name') or g['name'].lower())
            visible_clusters = []
            for cl in g.get('clusters', []):
                low = cl.get('_low')
                if low is None:
                    low = [((m.get('line_text') or '').lower(), (m.get('cross_text') or '').lower()) for m in cl['lines']]
                if not fk or name_hit or any((fk in a or fk in b for a, b in low)):
                    visible_clusters.append(cl)
            if fk and (not name_hit) and (not visible_clusters):
                continue
            first_snip = ''
            if visible_clusters:
                first_snip = self._snippet(visible_clusters[0]['lines'][0]['line_text'], kw, width=60) or ''
            plan.append(dict(g=g, disp_name='▎ ' + g['name'] + _status_suffix, tags=tuple(tags), hits_lbl=f'{g['hits']} 条', first_snip=first_snip, visible=visible_clusters, kw=kw))
        state = {'shown': 0}
        if len(plan) > 8:
            self._populate_batch(plan, state, gen, 0, fk)
        else:
            for p in plan:
                self._insert_group_plan(p, state)
            self._populate_finish(fk, state['shown'])

    def _apply_pending_restore(self):
        """搜索结果渲染后，定位到上次浏览的文件行"""
        pend = getattr(self, '_pending_restore', None)
        self._pending_restore = None
        if not pend:
            return
        path, line_no = pend
        parent_iid = None
        for iid, p in self._tree_item_paths.items():
            if p == path:
                parent_iid = iid
                break
        if not parent_iid:
            return
        self.tree.item(parent_iid, open=True)
        target = None
        best_diff = None
        for child in self.tree.get_children(parent_iid):
            try:
                ln = int(self.tree.item(child, 'values')[1])
            except (ValueError, IndexError):
                continue
            d = abs(ln - line_no)
            if best_diff is None or d < best_diff:
                best_diff, target = (d, child)
        if target:
            self.tree.see(target)
            self.tree.selection_set(target)
        self._show_preview(path, line_no)

    def _reflow_content(self, content):
        """把硬断行并成自然段，返回 [(段首行号, 合并后文本)]。
            同时记录 行号→段首行号（_reflow_anchor）与 段首行号→段内全部行号
            （_reflow_alias），使命中判定、行号跳转仍能命中被并掉的后续行。"""
        self._reflow_alias = {}
        self._reflow_anchor = {}
        self._reflow_spans = {}
        if not content:
            return content
        out = []
        start = None
        lns = []
        segs = []
        cur = ''
        cur_page = ''
        for ln, txt in content:
            raw = txt or ''
            m = _PAGE_RE.match(raw)
            body = _PAGE_RE.sub('', raw, count=1)
            page = m.group(1) if m else ''
            if start is None:
                start, lns, segs = (ln, [ln], [(ln, len(body))])
                cur, cur_page = (raw, page)
                continue
            prev = _PAGE_RE.sub('', cur, count=1).rstrip()
            joinable = body.strip() and prev and (page == cur_page) and (not is_table_row(body)) and (not is_table_row(prev)) and (len(prev) >= 20) and (prev[-1] not in self._RF_END) and (not self._RF_HEAD.match(prev)) and (not self._RF_TOC.search(prev)) and (not self._RF_NEXT.match(body))
            if joinable:
                cur = cur + body.rstrip()
                lns.append(ln)
                segs.append((ln, len(body.rstrip())))
            else:
                out.append((start, cur))
                for x in lns:
                    self._reflow_anchor[x] = start
                self._reflow_alias[start] = tuple(lns)
                self._reflow_spans[start] = tuple(segs)
                start, lns, segs, cur, cur_page = (ln, [ln], [(ln, len(body))], raw, page)
        if start is not None:
            out.append((start, cur))
            for x in lns:
                self._reflow_anchor[x] = start
            self._reflow_alias[start] = tuple(lns)
            self._reflow_spans[start] = tuple(segs)
        return out

    def _cap_paint(self):
        """标题栏配色统一出口（主题切换 / hover / 失焦都走这里）"""
        c = getattr(self, '_cap', None)
        if c is None:
            return
        pal = self.palette()
        bg = str(ttk.Style().lookup('TFrame', 'background') or pal['bg'])
        ink, dim, hov = (pal['cap_ink'], pal['cap_dim'], pal['cap_hover'])
        c['bar'].configure(bg=bg)
        c['left'].configure(bg=bg)
        c['icon'].configure(bg=bg)
        c['title'].configure(bg=bg, fg=dim if not c['active'] else ink)
        for key in ('min', 'max', 'close'):
            f, l = c[key]
            f.configure(bg=bg)
            l.configure(bg=bg, fg=dim if not c['active'] else ink)
        h = c['hover'] if c['active'] else None
        if h:
            f, l = c[h]
            if h == 'close':
                f.configure(bg='#C42B1C')
                l.configure(bg='#C42B1C', fg='#FFFFFF')
            else:
                f.configure(bg=hov)
                l.configure(bg=hov)

    def _highlight_cross_seam(self):
        """跨行命中（重排关闭时）：给上行尾 + 下行首被切断的词补黄高亮。
            self._cur_cross = (下行行号, 词)；当前行为上行。"""
        b_ln, word = self._cur_cross
        a_ln = self._cur_line
        ra = self.preview.tag_nextrange(f'ln{a_ln}', '1.0')
        rb = self.preview.tag_nextrange(f'ln{b_ln}', '1.0')
        if not ra or not rb:
            return
        a_txt = self.preview.get(ra[0], ra[1]).rstrip('\n')
        b_txt = self.preview.get(rb[0], rb[1]).rstrip('\n')
        k_hit = next((k for k in range(len(word) - 1, 0, -1) if a_txt.endswith(word[:k]) and b_txt.startswith(word[k:])), None)
        if k_hit is None:
            return
        h, tlen = (word[:k_hit], len(word) - k_hit)
        self.preview.tag_add('hl', f'{ra[1]}-{len(h)}c', ra[1])
        self.preview.tag_add('hl', rb[0], f'{rb[0]}+{tlen}c')

    def on_select(self, evt):
        sel = self.tree.selection()
        if not sel:
            return
        item = sel[0]
        tags = self.tree.item(item, 'tags')
        if 'morerow' in tags:
            self._expand_more(item)
            return
        path = line_path_from_tags(tags)
        if not path:
            return
        vals = self.tree.item(item, 'values')
        try:
            line_no = int(vals[1])
        except (ValueError, IndexError):
            return
        hit_lines = self._cluster_rows.get((path, line_no))
        cross = (getattr(self, '_cluster_cross', None) or {}).get((path, line_no))
        self._show_preview(path, line_no, hit_lines=hit_lines, cross=cross)
        self._remember_position(path, line_no)

    def _capture_tree_view(self):
        """记录当前滚动位置与选中行（重建前调用）"""
        self._restore_scroll = None
        self._restore_sel_path = None
        self._restore_sel_text = None
        try:
            self._restore_scroll = self.tree.yview()[0]
        except Exception:
            pass
        try:
            sel = self.tree.selection()
        except Exception:
            sel = ()
        if not sel:
            return
        iid = sel[0]
        self._restore_sel_text = self.tree.item(iid, 'text')
        path = self._tree_item_paths.get(iid)
        if path is None:
            path = self._tree_item_paths.get(self.tree.parent(iid))
        self._restore_sel_path = path

    def _import_thread(self, paths, link_mode, ui_cb=None, preplans=None, stop_event=None):
        """后台批量入库：复制（或链接）+ 建索引"""
        result = self._import_batch_loop(paths, link_mode, stop_event=stop_event, ui_cb=ui_cb, preplans=preplans)
        if result is None:
            return
        ok, total = (result['ok'], result['total'])
        extra = result['summary_suffix']
        self.root.after(0, lambda: self.status.configure(text=f'已收好 {ok} 份文件{extra}' if ok or extra else '没有新文件入库'))
        self.root.after(0, lambda: self._set_progress(100))
        if ok:
            self.root.after(0, lambda: self.preview.delete('1.0', 'end'))
        lw = getattr(self, '_lib_win', None)
        if lw is not None and lw.winfo_exists() and ok:
            self.root.after(0, lambda: self._refresh_library_win(lw))
        self.root.after(0, lambda: self._check_uncategorized(result['imported_ok']))
        self._auto_qa_refresh(result['imported_ok'])

    def _lib_sort_key(self, it, col):
        """库列表各列排序键：数值列按数值，其余按文本"""
        st = it.get('status', 'active')
        if col == 'size':
            return it.get('size') or 0
        if col == 'lines':
            return it.get('line_count') or 0
        if col == 'mtime':
            return it.get('mtime') or 0
        if col == 'status':
            return {'active': 0, 'replaced': 1, 'void': 2}.get(st, 3)
        if col == 'name':
            return it.get('name', '').lower()
        if col == 'doc_no':
            return (it.get('doc_no') or '').lower()
        if col == 'category':
            return it.get('category', '') or '~'
        if col == 'ext':
            return (it.get('ext') or '').lower()
        if col == 'ftype':
            return (it.get('ftype') or '').lower()
        return it.get('path', '').lower()

    def _refresh_dialog_headers(self):
        """主题切换后同步所有仍打开对话框头部的配色"""
        headers = getattr(self, '_dlg_headers', None)
        if not headers:
            return
        pal = self.palette()
        band_bg = pal['band_bg']
        line_bg = pal['band_line']
        title_fg = pal['title_fg']
        sub_fg = pal['sub_fg']
        brand = pal['brand']
        alive = []
        for rec in headers:
            win = rec[0]
            try:
                if not win.winfo_exists():
                    continue
                _win, band, hair, ic, tl, sl, extras = rec
                for w in (band,) + extras:
                    w.configure(bg=band_bg)
                hair.configure(bg=line_bg)
                ic.configure(bg=band_bg, fg=brand)
                tl.configure(bg=band_bg, fg=title_fg)
                if sl is not None:
                    sl.configure(bg=band_bg, fg=sub_fg)
                alive.append(rec)
            except Exception:
                continue
        self._dlg_headers = alive

    def _restore_from_tray(self):
        """从托盘恢复：销毁托盘图标 + 显示主窗+进度窗。"""
        if not self._bg_mode:
            return
        pw = self._pw_ref
        if pw is not None:
            try:
                if pw.winfo_exists():
                    pw.deiconify()
                    pw.lift()
                    try:
                        if hasattr(self, '_progress_bg_btn') and self._progress_bg_btn is not None and self._progress_bg_btn.winfo_exists():
                            self._progress_bg_btn.configure(state='normal')
                    except Exception:
                        pass
            except Exception:
                pass
        try:
            self.root.deiconify()
            self.root.lift()
        except Exception:
            pass
        tray = self._tray
        if tray is not None:
            try:
                tray.del_icon()
                tray.stop()
            except Exception:
                pass
        self._tray = None
        self._tray_evt_q = None
        self._bg_mode = False
        self._pw_ref = None
        self._pw_stop_event = None

    def _save_config(self):
        """统一保存入口：配置 + 窗口尺寸位置 + 分栏比例"""
        self.cfg['root_dir'] = self.root_dir.get()
        self.cfg['dark'] = bool(self.dark.get())
        self.cfg['preview_font'] = getattr(self, '_pfont_size', 10)
        self.cfg['synonym'] = bool(self.syn_var.get())
        self.cfg['autowatch'] = bool(self.autowatch.get())
        self.cfg['only_active'] = bool(self.only_active.get())
        self.cfg['link_only'] = bool(self.link_only.get())
        try:
            geo = self.root.geometry()
            m = re.match('(\\d+)x(\\d+)\\+(-?\\d+)\\+(-?\\d+)', geo)
            if m:
                w, h = (int(m.group(1)), int(m.group(2)))
                if w >= 600 and h >= 400:
                    self.cfg['window'] = geo
        except Exception:
            pass
        try:
            if hasattr(self, 'main_pane'):
                pos = self.main_pane.sashpos(0)
                pw = self.main_pane.winfo_width()
                if pw > 0 and 50 < pos < pw - 50:
                    self.cfg['pane_main'] = pos
                else:
                    self.cfg.pop('pane_main', None)
        except Exception:
            pass
        try:
            if hasattr(self, 'combined_pane') and self.combined_pane.panes():
                pos = int(self.combined_pane.sash_coord(0)[1])
                ph = self.combined_pane.winfo_height()
                if ph > 0 and 30 < pos < ph - 30:
                    self.cfg['pane_combined'] = pos
                else:
                    self.cfg.pop('pane_combined', None)
        except Exception:
            pass
        save_config(self.cfg)

    def _save_search_template(self):
        """把当前搜索条件（关键词+模式+筛选）保存为模板"""
        kw = self.keyword.get().strip()
        if not kw or kw == KW_HINT:
            messagebox.showinfo('搜索模板', '请先输入关键词再保存')
            return
        from tkinter import simpledialog
        name = simpledialog.askstring('保存搜索模板', '模板名称：', initialvalue=kw, parent=self.root)
        if not name:
            return
        lst = self.cfg.setdefault('saved_searches', [])
        lst[:] = [t for t in lst if t.get('name') != name]
        lst.append({'name': name, 'kw': kw, 'mode': self.search_mode.get(), 'oa': bool(self.only_active.get()), 'cs': bool(self.cs.get()), 'ft': self.file_type.get(), 'syn': bool(self.syn_var.get())})
        self._save_config()
        self.status.configure(text=f'已保存搜索模板「{name}」')

    def _apply_search_template(self, t):
        """恢复模板条件并立即搜索"""
        self.keyword.set(t.get('kw', ''))
        self.search_mode.set(t.get('mode', 'exact'))
        self.only_active.set(bool(t.get('oa', True)))
        self.cs.set(bool(t.get('cs', False)))
        self.file_type.set(t.get('ft', '全部'))
        self.syn_var.set(bool(t.get('syn', False)))
        self._last_searched = None
        self.do_search()
        self.status.configure(text=f'已应用模板「{t.get('name', '')}」')

    def _expand_more(self, item):
        """点击「…还有 N 处命中」：就地展开被折叠的片段，不重搜"""
        packed = self._more_map.pop(item, None)
        if not packed:
            return
        g, hidden_cls, start_idx = packed
        parent = self.tree.parent(item)
        try:
            self.tree.selection_set(parent)
        except Exception:
            pass
        self.tree.delete(item)
        kw = self.keyword.get()
        for off, cl in enumerate(hidden_cls):
            self._insert_cluster(parent, g, cl, start_idx + off, kw)
        try:
            self.tree.item(parent, open=True)
        except Exception:
            pass

    def _restore_tree_view(self):
        """重建完成后还原滚动位置与选中行（找不到就退化成文件行）"""
        scroll = getattr(self, '_restore_scroll', None)
        sel_path = getattr(self, '_restore_sel_path', None)
        sel_text = getattr(self, '_restore_sel_text', None)
        self._clear_tree_view()
        target = None
        if sel_path:
            for iid, p in self._tree_item_paths.items():
                if p == sel_path:
                    target = iid
                    break
        if target is not None and sel_text:
            for c in self.tree.get_children(target):
                try:
                    if self.tree.item(c, 'text') == sel_text:
                        target = c
                        break
                except Exception:
                    break
        if target is not None:
            try:
                self.tree.selection_set(target)
            except Exception:
                pass
        if scroll:
            try:
                self.tree.yview_moveto(scroll)
            except Exception:
                pass

    def _result_right_click(self, evt):
        """结果树右键：文件行/命中行「与旧版对比」；有结果时提供「AI 分析」"""
        row = self.tree.identify_row(evt.y)
        if row:
            self.tree.selection_set(row)
        path = None
        if row:
            tags = self.tree.item(row, 'tags')
            path = self._tree_item_paths.get(row) or line_path_from_tags(tags)
        m = tk.Menu(self.tree, tearoff=0)
        if path:
            m.add_command(label='与旧版对比', command=lambda: self._compare_with_version(path))
        if self.results:
            m.add_command(label=f'{self.IC_CHAT} AI 分析这些结果', command=self._send_results_to_qa)
        if m.index('end') is None:
            return
        m.tk_popup(evt.x_root, evt.y_root)

    def _check_inbox_on_startup(self):
        """启动后延迟检查待入库文件，有则弹确认对话框。
            7天内只弹一次，避免每次启动都打扰。"""
        if not self.cfg.get('inbox_auto_check', True):
            return
        files = self._scan_inbox_files() + self._scan_download_files()
        if not files:
            return
        last = self.cfg.get('inbox_last_prompt_ts', 0)
        if time.time() - last < 7 * 86400:
            return
        self.cfg['inbox_last_prompt_ts'] = time.time()
        try:
            self._save_config()
        except Exception:
            pass
        self.show_inbox_review()

    def _insert_group_plan(self, p, state):
        """插入一个文件组（文件行 + 前 3 簇 + 折叠提示）"""
        g = p['g']
        parent = self.tree.insert('', 'end', text=p['disp_name'], values=(p['hits_lbl'], '', p['first_snip']), open=state['shown'] == 0, tags=p['tags'])
        self._tree_item_paths[parent] = g['path']
        shown_cls = p['visible'][:3]
        hidden_cls = p['visible'][3:]
        for cl_idx, cl in enumerate(shown_cls):
            self._insert_cluster(parent, g, cl, cl_idx, p['kw'])
        if hidden_cls:
            more = self.tree.insert(parent, 'end', text=f'…还有 {len(hidden_cls)} 处命中，点击展开', values=('', '', ''), tags=('morerow',))
            self._more_map[more] = (g, hidden_cls, len(shown_cls))
        state['shown'] += 1

    def _on_ref_preview_goto(self, evt):
        """内部跳转：在镜仔搜索内跳到被引用文件+行，push 历史栈"""
        tag, info = self._ref_target_at(evt)
        if not tag or not info[0]:
            return
        if self._cur_path:
            self._prev_hist.append((self._cur_path, self._cur_line or 1))
        self._show_preview(info[0], info[1])
        self.back_btn.configure(state='normal')
        try:
            for item_id in self.tree.get_children(''):
                vals = self.tree.item(item_id, 'values')
                if info[0].lower().endswith(vals[0].lower()):
                    self.tree.selection_set(item_id)
                    self.tree.see(item_id)
                    break
        except Exception:
            pass
        name = os.path.basename(info[0])
        self.status.configure(text=f'已跳转到《{name}》第 {info[1]} 行（点←返回上一个）')

    def _on_tree_motion(self, evt):
        """文件行悬停 500ms 弹完整路径 tooltip；命中行/空白区不弹"""
        iid = self.tree.identify_row(evt.y)
        if iid == getattr(self, '_tip_iid', None):
            return
        self._tip_iid = iid
        self._hide_tree_tip()
        if not iid or iid not in getattr(self, '_tree_item_paths', {}):
            return
        path = self._tree_item_paths[iid]
        rx, ry = (evt.x_root, evt.y_root)
        self._tip_job = self.root.after(500, lambda: self._show_tree_tip(path, rx, ry))

    def start_scan(self):
        if self._bg_mode:
            self._restore_from_tray()
            self.root.after(300, self.start_scan)
            return
        d = self.root_dir.get()
        if not d or not os.path.isdir(d):
            messagebox.showerror('错误', '请先选择要扫描的目录')
            return
        self.stop_event = threading.Event()
        self._task_busy = True
        self.scan_btn.config(text='停止扫描', command=self.stop_scan)
        self._set_progress(0, immediate=True)
        link_mode = self.link_only.get()
        threading.Thread(target=self._scan_walk_thread, args=(d, self.stop_event, link_mode), daemon=True).start()

    def _chart_hover(self, evt):
        """悬停柱区：柱子放大提亮 + 状态栏显示完整文件名与命中数"""
        i = self._chart_hit_index(evt)
        if i == self._chart_hover_idx:
            return
        self._chart_set_hover(i)
        if i < 0:
            return
        it = self._chart_items[i]
        tag = f'{self.IC_WARN}废止 ' if it['dead'] else ''
        self.status.configure(text=f'{tag}{it['name']} · {it['hits']} 条命中 · 点击查看')

    def _enter_reading(self, path):
        """内部阅读：隐藏左列（搜索+章节），整窗阅读该文件"""
        if not path or not os.path.exists(path):
            return
        self._read_path = path
        try:
            self.main_pane.forget(self.combined_pane)
        except Exception:
            self.combined_pane.pack_forget()
        self.read_bar.pack(side='top', fill='x', before=self.rbar)
        line_no = 1
        for g in getattr(self, 'groups', []):
            if g.get('path') == path:
                line_no = g.get('first_line') or 1
                break
        self._show_preview(path, line_no)

    def _go_back(self):
        """从历史栈弹出，回到上一个文件位置"""
        if not self._prev_hist:
            return
        path, line = self._prev_hist.pop()
        self._show_preview(path, line)
        name = os.path.basename(path)
        self.status.configure(text=f'已返回《{name}》第 {line} 行')
        try:
            for item_id in self.tree.get_children(''):
                vals = self.tree.item(item_id, 'values')
                if path.lower().endswith(vals[0].lower()):
                    self.tree.selection_set(item_id)
                    self.tree.see(item_id)
                    break
        except Exception:
            pass
        if not self._prev_hist:
            self.back_btn.configure(state='disabled')

    def _install_hist_autoclose(self):
        """给关键词下拉的弹出列表挂「鼠标移出即收回」。

            弹出列表是 Tcl 侧的独立 toplevel（不是 tkinter 对象），只能先
            用 ttk::combobox::PopdownWindow 拿到它的路径，再按路径 bind。
            """
        if getattr(self, '_hist_bound', False):
            return
        try:
            pop = str(self.kw_entry.tk.call('ttk::combobox::PopdownWindow', self.kw_entry))
        except tk.TclError:
            return
        if not pop:
            return
        self._hist_pop = pop
        try:
            _leave = self.root.register(self._hist_on_leave)
            _enter = self.root.register(self._hist_on_enter)
        except Exception:
            return
        for w in (pop, pop + '.f', pop + '.f.l'):
            try:
                self.kw_entry.tk.call('bind', w, '<Leave>', _leave)
                self.kw_entry.tk.call('bind', w, '<Enter>', _enter)
            except tk.TclError:
                pass
        self._hist_bound = True

    def _push_recent_keyword(self, kw):
        """关键词入历史，最多保留 10 条，去重；同时记住最后搜索词"""
        recent = self.cfg.get('recent_keywords', [])
        if kw in recent:
            recent.remove(kw)
        recent.insert(0, kw)
        self.cfg['recent_keywords'] = recent[:10]
        self.cfg['last_kw'] = kw
        self.kw_entry['values'] = self.cfg['recent_keywords']
        self._save_config()

    def _shutdown_for_restore(self):
        """还原切换前走一遍正常退出流程，让重启批处理接管文件替换"""
        try:
            self._stop_hotkeys()
        except Exception:
            pass
        if getattr(self, 'watcher', None):
            try:
                self.watcher.stop()
            except Exception:
                pass
        try:
            self._save_config()
        except Exception:
            pass
        srv = getattr(self, 'ipc_server', None)
        if srv:
            try:
                srv.shutdown_clean()
            except Exception:
                pass
        self.root.destroy()

    def _clear_search(self):
        """点击清空小叉：清空搜索框文字 + 重置结果 + 显示占位提示"""
        self.keyword.set('')
        self.kw_entry.configure(foreground='gray')
        self.kw_entry.focus_set()
        if hasattr(self, '_show_kw_hint'):
            self._show_kw_hint()
        if hasattr(self, '_refresh_clear_btn'):
            self._refresh_clear_btn()
        self._reset_results()

    def _on_tree_open_track(self, evt):
        """展开文件时：记录展开的父节点 + 立即 see 保证可见。"""
        iid = self.tree.focus()
        if not iid:
            return
        self._tree_expanded.add(iid)
        try:
            self.tree.see(iid)
        except Exception:
            pass
        self.root.after_idle(lambda: self.tree.see(iid) if self.tree.winfo_exists() else None)
        try:
            children = self.tree.get_children(iid)
            if children:
                self._keep_parent_visible(children[0])
        except Exception:
            pass

    def _pointer_in_path(self, *paths):
        """指针当前是否停在给定控件路径自身或其子孙上。

            弹出列表这类控件只有 Tcl 路径字符串，不能用 winfo_ 方法，
            只能用 winfo_containing 反查指针下面是谁。
            """
        paths = [p for p in paths if p]
        if not paths:
            return False
        try:
            w = self.root.winfo_containing(self.root.winfo_pointerx(), self.root.winfo_pointery())
        except Exception:
            return False
        if w is None:
            return False
        p = str(w)
        return any((p == q or p.startswith(q + '.') for q in paths))

    def _scan_download_files(self):
        """扫描下载文件夹中最近 24h 的支持格式新文件"""
        dl = self.cfg.get('download_watch_dir') or os.path.join(os.path.expanduser('~'), 'Downloads')
        if not os.path.isdir(dl):
            return []
        cutoff = time.time() - 86400
        found = []
        try:
            for f in os.scandir(dl):
                if not f.is_file():
                    continue
                ext = os.path.splitext(f.name)[1].lower()
                if ext not in self._INBOX_EXTS:
                    continue
                try:
                    st = f.stat()
                    if st.st_mtime < cutoff:
                        continue
                    found.append({'path': f.path, 'name': f.name, 'size': st.st_size, 'mtime': st.st_mtime, 'source': '下载'})
                except OSError:
                    continue
        except OSError:
            pass
        return found

    def _send_results_to_qa(self):
        """把当前搜索结果作为上下文发给 AI 问答窗口（搜索→问答 RAG 联动）"""
        if not self.results:
            return
        kw = self.keyword.get().strip()
        hits = self._build_search_hits()
        if not hits:
            return
        self.show_qa_window()
        try:
            self._qa_window.set_search_context(hits, kw, len(self.groups))
            self._qa_window.win.lift()
            self._qa_window.win.focus_force()
        except Exception:
            pass

    def _split_content_into_blocks(self, content):
        """把 content [(ln, txt), ...] 切成交替的 [普通段落块, 表格块, 普通段落块, ...]
            表格块：连续 2+ 行 is_table_row 的条目。
            返回 [(kind, items)]，kind 是 'para' 或 'table'，items 是 [(ln, txt), ...]。"""
        blocks = []
        cur_kind = None
        cur_items = []
        for ln, txt in content:
            is_t = is_table_row(txt)
            kind = 'table' if is_t else 'para'
            if kind != cur_kind:
                if cur_kind == 'table' and len(cur_items) < 2:
                    cur_kind = 'para'
                if cur_items:
                    blocks.append((cur_kind, cur_items))
                cur_kind = kind
                cur_items = [(ln, txt)]
            else:
                cur_items.append((ln, txt))
        if cur_items:
            if cur_kind == 'table' and len(cur_items) < 2:
                cur_kind = 'para'
            blocks.append((cur_kind, cur_items))
        return blocks


    def _auto_search(self):
        """停止输入后的回调：有新关键词才搜；清空即重置结果"""
        self._type_after_id = None
        kw = self.keyword.get().strip()
        if not kw or kw == KW_HINT:
            self._reset_results()
            return
        if kw == getattr(self, '_last_searched', None):
            return
        self.do_search(auto=True)

    def _cap_on_maxstate(self, _e=None):
        c = getattr(self, '_cap', None)
        if c is None:
            return
        zoomed = None
        try:
            v = getattr(_e, 'user_data', None)
            if v is not None:
                zoomed = str(v) == '1'
        except Exception:
            zoomed = None
        if zoomed is None:
            try:
                zoomed = self.root.state() == 'zoomed'
            except Exception:
                zoomed = False
        c['maxl'].configure(text=self.IC_CAP_RESTORE if zoomed else self.IC_CAP_MAX)

    def _chart_click(self, evt):
        """点击柱子 → 预览该文件首条命中处"""
        i = self._chart_hit_index(evt)
        if i < 0:
            return
        it = self._chart_items[i]
        self._show_preview(it['path'], it['line'])
        self.status.configure(text=f'已跳到: {it['name']} 首条命中')

    def _choose_download_watch_dir(self):
        """选择下载监控文件夹（默认系统下载目录）"""
        d = filedialog.askdirectory(title='选择下载监控文件夹', initialdir=self.cfg.get('download_watch_dir') or os.path.join(os.path.expanduser('~'), 'Downloads'))
        if not d:
            return
        self.cfg['download_watch_dir'] = d
        self._save_config()
        self.status.configure(text=f'下载监控文件夹：{d}')

    def _handle_tree_click(self, win, tree, event):
        """统一处理树点击：多选模式切换勾选（记入 win._checked），普通模式放行"""
        if not getattr(win, '_multi_mode', False):
            return None
        row = tree.identify_row(event.y)
        if not row:
            return None
        if row in win._checked:
            win._checked.discard(row)
            tree.set(row, 'sel', '☐')
        else:
            win._checked.add(row)
            tree.set(row, 'sel', '☑')
        return 'break'

    def _hist_close_if_out(self):
        """指针确实已离开搜索记录范围 → 收回下拉"""
        self._hist_timer = None
        pop = getattr(self, '_hist_pop', None)
        if not pop or not getattr(self, '_hist_bound', False):
            return
        if self._pointer_in_path(pop):
            return
        try:
            self.kw_entry.tk.call('ttk::combobox::Unpost', self.kw_entry)
        except tk.TclError:
            pass

    def _multi_all(self, win, tree):
        """多选模式全选/清空：当前列表已全选则清空，否则全选当前列表"""
        if not getattr(win, '_multi_mode', False):
            return
        kids = tree.get_children()
        visible = set(kids)
        if visible and visible.issubset(win._checked):
            win._checked -= visible
        else:
            win._checked |= visible
        for iid in kids:
            tree.set(iid, 'sel', '☑' if iid in win._checked else '☐')

    def _on_chapter_select(self, evt=None):
        """点击章节 → 预览显示整章内容"""
        sel = self.chap_tree.selection()
        if not sel or not self._cur_path:
            return
        vals = self.chap_tree.item(sel[0], 'values')
        try:
            start, end = (int(vals[0]), int(vals[1]))
        except (ValueError, IndexError):
            return
        self._show_chapter(self._cur_path, start, end)

    def _on_ref_miss_click(self, evt):
        """单击 refmiss（未入库引用）：弹对话框 + 直接打开百度搜官网"""
        tag, info = self._ref_target_at(evt)
        if not tag:
            return
        title = info[2] if len(info) > 2 else '未命名文件'
        webbrowser.open(f'https://www.baidu.com/s?wd={quote(title)}')
        self.status.configure(text=f'库中未找到《{title}》，已在浏览器打开搜索结果')

    def _open_in_default(self, tree):
        """双击用系统默认程序打开"""
        sel = tree.selection()
        if not sel:
            return
        path = sel[0]
        if not os.path.exists(path):
            self._show_missing_file(tree.winfo_toplevel(), path)
            return
        if sys.platform.startswith('win'):
            os.startfile(path)
        elif sys.platform == 'darwin':
            subprocess.Popen(['open', path])
        else:
            subprocess.Popen(['xdg-open', path])

    def _remember_position(self, path, line_no):
        """点击结果后防抖 800ms 落盘，避免每次点击都写配置"""
        self.cfg['last_path'] = path
        self.cfg['last_line'] = int(line_no)
        job = getattr(self, '_pos_job', None)
        if job:
            try:
                self.root.after_cancel(job)
            except Exception:
                pass
        self._pos_job = self.root.after(800, self._flush_position)

    def _scan_inbox_files(self):
        """扫描 inbox/ 目录中的待入库文件"""
        inbox = self._inbox_dir()
        if not os.path.isdir(inbox):
            return []
        found = []
        try:
            for f in os.scandir(inbox):
                if not f.is_file():
                    continue
                ext = os.path.splitext(f.name)[1].lower()
                if ext not in self._INBOX_EXTS:
                    continue
                try:
                    st = f.stat()
                    found.append({'path': f.path, 'name': f.name, 'size': st.st_size, 'mtime': st.st_mtime, 'source': '手机上传' if f.name.startswith('手机上传_') else '收件箱'})
                except OSError:
                    continue
        except OSError:
            pass
        return found

    def _toggle_preview_wrap(self):
        """切换正文自动换行，记忆偏好"""
        on = bool(self.preview_wrap_var.get())
        self.preview.configure(wrap='word' if on else 'none')
        self.cfg['preview_wrap'] = on
        self._save_config()
        if self._cur_path:
            self._refresh_preview()

    def _tree_keep_parent_after_scroll(self):
        """滚动停止后检查：选中项的父节点（文件头）是否仍在可见区。"""
        sel = self.tree.selection()
        if not sel:
            return
        try:
            parent = self.tree.parent(sel[0])
        except Exception:
            return
        if not parent:
            return
        try:
            self.tree.update_idletasks()
            if self.tree.bbox(parent) is None:
                self.tree.see(parent)
        except Exception:
            pass

    def _tree_row_hover(self, evt):
        try:
            iid = self.tree.identify_row(evt.y)
        except Exception:
            return
        if iid == self._tree_hover_iid:
            return
        self._tree_row_hover_clear()
        if not iid:
            return
        col = '#2E3137' if self.dark.get() else '#EDF3FA'
        try:
            if getattr(self, '_hoverrow_col', None) != col:
                self.tree.tag_configure('hoverrow', background=col)
                self._hoverrow_col = col
            tags = tuple(self.tree.item(iid, 'tags') or ())
            self.tree.item(iid, tags=tags + ('hoverrow',))
            self._tree_hover_iid = iid
        except Exception:
            pass

    def open_file(self, evt):
        sel = self.tree.selection()
        if not sel:
            return
        item = sel[0]
        tags = self.tree.item(item, 'tags')
        if tags and tags[0] == 'file':
            idx = self.tree.index(item)
            if 0 <= idx < len(self.groups):
                self._enter_reading(self.groups[idx]['path'])
            return
        if tags:
            path = line_path_from_tags(tags)
            if path and os.path.exists(path):
                if sys.platform.startswith('win'):
                    vals = self.tree.item(item, 'values')
                    try:
                        line_no = int(vals[1])
                    except (ValueError, IndexError):
                        line_no = None
                    self._open_located(path, line_no)
                elif sys.platform == 'darwin':
                    subprocess.Popen(['open', path])
                else:
                    subprocess.Popen(['xdg-open', path])

    def palette(self):
        """当前主题的设计令牌（dict）。标题栏/AppBar/弹层头部一律取这里。"""
        return self.PALETTE_DARK if self.dark.get() else self.PALETTE_LIGHT
    def _on_resize(self, evt):
        """P1-15: 记录窗口尺寸（仅在主窗口变化时）"""
        if evt.widget == self.root:
            self._win_size = f'{evt.width}x{evt.height}'
    def _start_hotkeys(self):
        """按当前配置注册全局热键；非法/占用情况经队列回报"""
        if not _HOTKEY_OK:
            self.status.configure(text='未检测到 pywin32，全局热键不可用（其他功能正常）')
            return
        binds = []
        for name, cfg_key, hid in (('quick', 'quick_hotkey', 1), ('clip', 'clip_hotkey', 2)):
            spec = (self.cfg.get(cfg_key) or '').strip()
            try:
                mods, vk = quickwin.parse_hotkey(spec)
            except ValueError:
                self._toast(f'热键格式无效：{spec}（示例 ctrl+alt+j）')
                continue
            binds.append({'name': name, 'id': hid, 'mods': mods, 'vk': vk, 'label': spec})
        if not binds:
            return
        t = quickwin.HotkeyThread(binds, self._hotkey_q)
        t.start()
        t.ready.wait(2)
        self._hotkey_thread = t
        self._hotkey_labels = {b['name']: b['label'] for b in binds}
    def _stop_hotkeys(self):
        if self._hotkey_thread:
            self._hotkey_thread.stop()
            self._hotkey_thread.join(timeout=1.5)
            self._hotkey_thread = None
    def _restart_hotkeys(self):
        self._stop_hotkeys()
        self._start_hotkeys()
    def _poll_hotkey(self):
        """主线程轮询热键事件（Tk 操作必须在主线程）"""
        try:
            while True:
                typ, data = self._hotkey_q.get_nowait()
                if typ == 'hotkey':
                    if data == 'quick' and self.quick_win:
                        self.quick_win.show()
                    elif data == 'clip':
                        self.quick_save_clipboard()
                elif typ == 'hotkey_failed':
                    labels = '、'.join((self._hotkey_labels.get(b['name'], b.get('label', '?')) for b in data))
                    messagebox.showwarning('全局热键注册失败', f'以下热键被其他软件占用：\n{labels}\n\n请到 ⚙ → 热键设置 改成别的组合键。')
        except queue.Empty:
            pass
        self.root.after(150, self._poll_hotkey)
    def _auto_register_protocol(self):
        """启动时静默注册 jingzai:// 协议（仅打包 exe；HKCU 无需管理员）。
            每次启动重注册一次，幂等且能在 exe 被挪动后刷新路径。
            原为更多菜单开关，2026-10-01 起固化为默认行为。"""
        if not sys.platform.startswith('win'):
            return
        if not getattr(sys, 'frozen', False):
            return
        try:
            ipc.register_protocol(sys.executable)
            self.cfg['url_protocol'] = True
            save_config(self.cfg)
        except Exception:
            pass
    def _export_dir(self):
        """所有“另存为”对话框的默认目录：用户设置 → 桌面"""
        d = self.cfg.get('export_dir') or ''
        if d and os.path.isdir(d):
            return d
        desktop = os.path.join(os.path.expanduser('~'), 'Desktop')
        return desktop if os.path.isdir(desktop) else APP_DIR
    def _choose_export_dir(self):
        d = filedialog.askdirectory(title='选择默认存档位置（汇编/CSV/图谱等都存到这里）', initialdir=self._export_dir(), mustexist=True)
        if not d:
            return
        self.cfg['export_dir'] = d
        self._save_config()
        self._toast(f'默认存档位置已设为：{d}')
    def open_feedback(self):
        """打开在线反馈表格；未配置链接时引导粘贴一次（存 config）"""
        url = (self.cfg.get('feedback_url') or '').strip()
        if not url:
            url = DEFAULT_FEEDBACK_URL
        if not url:
            url = (simpledialog.askstring('意见反馈', '粘贴 WPS 在线表格/表单的分享链接\n（设置一次后记住，下次直接打开）：', parent=self.root) or '').strip()
            if not url:
                return
            if not url.startswith(('http://', 'https://')):
                messagebox.showwarning('意见反馈', '链接需以 http:// 或 https:// 开头')
                return
            self.cfg['feedback_url'] = url
            self._save_config()
        webbrowser.open(url)
    def accept_remote_keyword(self, kw):
        """单实例/命令行/协议唤起：置顶主窗并执行搜索（由 IPC 线程调用）"""
        kw = (kw or '').strip()[:200]
        if kw:
            self.root.after(0, lambda: self._remote_search(kw))
    def _remote_search(self, kw):
        try:
            if self._bg_mode:
                self._restore_from_tray()
            self.root.deiconify()
            self.root.lift()
            self.root.attributes('-topmost', 1)
            self.root.after(400, lambda: self.root.attributes('-topmost', 0))
            self.keyword.set(kw)
            self.kw_entry.configure(foreground='')
            self.do_search()
        except Exception:
            pass
    def _qa_db(self):
        """问答窗使用的索引库（与主程序一致）"""
        return DB_PATH
    def show_qa_window(self):
        """⚙ 菜单：打开 Ollama 离线问答窗（单例）"""
        if not _QA_OK:
            messagebox.showinfo('AI 镜仔智答不可用', '问答模块加载失败，请检查 ai_qa.py / qa_win.py 是否完整。')
            return
        if self._qa_window is not None and tk.Toplevel.winfo_exists(self._qa_window.win):
            self._qa_window.win.deiconify()
            self._qa_window.win.lift()
            self._qa_window.check_env()
            return
        self._qa_window = qa_win.QAWindow(self)
        self._qa_window.win.protocol('WM_DELETE_WINDOW', self._qa_window.win.destroy)
        if not getattr(self, '_qa_auto_started', False):
            self._qa_auto_started = True
            self._auto_qa_refresh(['__startup__'])
    def _show_ocr_status_dlg(self, st, err=None):
        self.status.configure(text='就绪')
        if err:
            messagebox.showerror('OCR 状态', f'OCR 组件检测失败：{err}')
            return
        if st['rapid']:
            lines = ['主引擎：RapidOCR（内置中文模型，无需额外软件）', f'版本：rapidocr_onnxruntime {st['rapid_ver']}', f'模型：{len(st['models'])} 个（{', '.join(st['models'])}）', f'运行时：onnxruntime {st['onnxruntime']}（{'/'.join(st['providers'])}）']
        else:
            lines = ['主引擎：RapidOCR 不可用（打包缺少 rapidocr_onnxruntime 或 onnxruntime 组件）']
        if st['tesseract']:
            lines.append(f'回退引擎：Tesseract 已安装（{st['tesseract']}）')
        else:
            lines.append('回退引擎：Tesseract 未安装（扫描件将仅能使用 RapidOCR；如需双保险可自行安装 Tesseract-OCR）')
        messagebox.showinfo('OCR 状态', '\n'.join(lines))
    def _graph_done(self, path, stats):
        try:
            webbrowser.open('file:///' + path.replace('\\', '/'))
        except Exception:
            pass
        if stats['edges'] == 0:
            messagebox.showinfo('关系图谱已生成', f'已保存：{path}\n\n库内文件之间暂无《》引用关系；红描边节点列出了指向库外/未入库制度的引用。')
        else:
            self._toast(f'图谱已生成并打开：{stats['nodes']} 个节点、{stats['edges']} 条引用边')
    def _toggle_qa_auto(self):
        """⚙ 菜单：开关启动时自动更新语义索引。
            勾上后立即触发一次增量更新；取消则仅记住配置。"""
        self.cfg['qa_auto'] = self.qa_auto_var.get()
        try:
            save_config(self.cfg)
        except Exception:
            pass
        if self.qa_auto_var.get():
            self._auto_qa_refresh(['__manual_enable'])
    def _auto_start_semantic(self):
        """软件启动后自动后台更新语义索引（勾在⚙菜单默认开）。
            - build_plan 比对 documents.mtime vs qa_chunks.mtime，没变动秒退
            - Ollama 未运行会自动拉起（try_launch_ollama + wait_ready）
            - 正在手动构建/流式问答则跳过，让前台操作优先"""
        if not self.qa_auto_var.get():
            return
        self._auto_qa_refresh(['__startup__'])
    def _anim_ok(self):
        return anim is not None and (not anim.reduced())
    def _qa_btn_colors(self):
        c = self._theme_colors()
        return (c['qa_bg'], c['qa_active'])
    def _qa_safe_bg(self, col):
        if col == getattr(self, '_qa_last_bg', None):
            return
        self._qa_last_bg = col
        try:
            self.qa_btn.configure(bg=col)
        except Exception:
            pass
    def _qa_breathe_on_visibility(self, visible):
        """窗口最小化/隐藏时停呼吸（后台不做装饰重绘），恢复时继续"""
        if visible:
            if not getattr(self, '_qa_hovered', False) and self._anim_ok():
                job = getattr(self, '_qa_resume_job', None)
                if job:
                    try:
                        self.root.after_cancel(job)
                    except Exception:
                        pass
                self._qa_breathe_start()
        else:
            b = getattr(self, '_qa_breathe', None)
            if b is not None:
                b.stop()
    def _qa_breathe_pause(self):
        self._qa_hovered = True
        b = getattr(self, '_qa_breathe', None)
        if b is not None:
            b.stop()
    def _qa_breathe_resume(self):
        self._qa_hovered = False
        job = getattr(self, '_qa_resume_job', None)
        if job:
            try:
                self.root.after_cancel(job)
            except Exception:
                pass
        self._qa_resume_job = self.root.after(220, self._qa_breathe_start)
    def _anim_palette(self, kind):
        """四类按钮的 常态/hover/按下 配色（与 _configure_control_styles 保持一致）"""
        dark = self.dark.get()
        st = ttk.Style()
        frame_bg = st.lookup('TFrame', 'background') or ('#202020' if dark else '#F4F6F9')
        if kind == 'accent':
            if dark:
                return dict(bg='#2889DE', hover='#3D9AE8', press='#1E6FBE', fg='#FFFFFF', font=self.FONT_UI_B, padding=(12, 0))
            return dict(bg='#2F5E8C', hover='#3A6FA2', press='#274D74', fg='#FFFFFF', font=self.FONT_UI_B, padding=(12, 0))
        if kind == 'danger':
            if dark:
                return dict(bg=frame_bg, hover='#4A2726', press='#5A2E2D', fg='#FF99A4', hover_fg='#FFB3BB', font=self.FONT_UI, padding=(12, 0))
            return dict(bg=frame_bg, hover='#FBE9E5', press='#F6CECA', fg='#C13A28', hover_fg='#A61F10', font=self.FONT_UI, padding=(12, 0))
        if kind == 'link':
            if dark:
                return dict(bg=frame_bg, hover='#3A3A3C', press='#45464A', fg=None, hover_fg=None, font=self.FONT_UI, padding=(12, 0))
            return dict(bg=frame_bg, hover='#E9EDF2', press='#DFE4EA', fg=None, hover_fg=None, font=self.FONT_UI, padding=(12, 0))
        base = st.lookup('TButton', 'background') or frame_bg
        mix = '#FFFFFF' if dark else '#000000'
        return dict(bg=base, hover=anim.hex_lerp(base, mix, 0.1) if anim else base, press=anim.hex_lerp(base, mix, 0.18) if anim else base, fg=None, hover_fg=None, font=self.FONT_UI, padding=(12, 0))
    def _btn_cur_bg(self, info):
        try:
            return ttk.Style().lookup(info['style'], 'background') or self._anim_palette(info['kind'])['bg']
        except Exception:
            return self._anim_palette(info['kind'])['bg']
    def _btn_set_bg(self, info, color):
        try:
            ttk.Style().configure(info['style'], background=color, bordercolor=color)
        except Exception:
            pass
    def _btn_update_target(self, info, ms):
        """根据 hover/press/disabled 状态计算目标色并补间"""
        try:
            disabled = str(info['btn']['state']) == 'disabled'
        except Exception:
            disabled = False
        pal = self._anim_palette(info['kind'])
        if disabled:
            target = pal['bg']
        elif info['press']:
            target = pal['press']
        elif info['hover']:
            target = pal['hover']
        else:
            target = pal['bg']
        self._btn_to(info, target, ms)
    def _btn_hover(self, info, on):
        info['hover'] = on
        if info['press']:
            return
        self._btn_update_target(info, 140)
    def _btn_press(self, info, on):
        info['press'] = on
        self._btn_update_target(info, 70 if on else 120)
    def _refresh_btn_anims(self):
        """主题切换后：把所有挂接按钮的样式重置为当前主题的常态色"""
        if anim is None:
            return
        for info in getattr(self, '_anim_btns', []):
            try:
                if not info['btn'].winfo_exists():
                    continue
                pal = self._anim_palette(info['kind'])
                opts = dict(background=pal['bg'], bordercolor=pal['bg'], font=info.get('font', pal['font']), padding=pal['padding'])
                if pal.get('fg'):
                    opts['foreground'] = pal['fg']
                ttk.Style().configure(info['style'], **opts)
                src = info.get('layout_src')
                if src:
                    lay = self.root.tk.call('ttk::style', 'layout', src)
                    if lay:
                        self.root.tk.call('ttk::style', 'layout', info['style'], lay)
                info['hover'] = info['press'] = False
                info['tw'].cancel()
            except Exception:
                pass
        self._refresh_input_focus_styles()
        if getattr(self, '_qa_breathe', None) is not None and self._anim_ok():
            self._qa_breathe_pause()
            self._qa_hovered = False
            self.root.after(220, self._qa_breathe_start)
    def _toggle_reduce_motion(self):
        """「减弱动效」开关：即时生效并写入配置"""
        v = bool(self.reduce_motion_var.get())
        self.cfg['reduce_motion'] = v
        try:
            self._save_config()
        except Exception:
            pass
        if anim is not None:
            anim.set_reduce_motion(v)
        if v:
            self._refresh_btn_anims()
    def _bind_global_popup_anim(self):
        """委托 bind_all：任何普通 Toplevel 首次 Map 时自动播放弹入动画。
            overrideredirect（菜单/tooltip/下拉/Toast）与主窗本身跳过。"""
        self._popup_seen = set()
        self.root.bind_all('<Map>', self._on_toplevel_map, add='+')
    def _on_toplevel_map(self, evt):
        if not self._anim_ok():
            return
        w = evt.widget
        try:
            if not isinstance(w, tk.Toplevel) or w is self.root:
                return
            if w.wm_overrideredirect():
                return
            wid = id(w)
            if wid in self._popup_seen:
                return
            self._popup_seen.add(wid)
            if winchrome is not None:
                winchrome.apply_window(w, self.dark.get())
            anim.popup_in(w)
            w.bind('<Unmap>', lambda _e: self._popup_seen.discard(wid), add='+')
            w.bind('<Destroy>', lambda _e: self._popup_seen.discard(wid), add='+')
        except Exception:
            pass
    def _refresh_input_focus_styles(self):
        """按当前主题重配克隆输入框样式（主题切换后由刷新钩子调用）"""
        if anim is None or not getattr(self, '_input_focus', None):
            return
        st = ttk.Style()
        dark = self.dark.get()
        normal, brand, fieldbg = ('#3A3B40', '#4FA3F0', '#2D2E32') if dark else ('#DFE4EA', '#2F5E8C', '#FFFFFF')
        fg = st.lookup('TEntry', 'foreground') or None
        for info in self._input_focus:
            opts = dict(fieldbackground=fieldbg, bordercolor=normal)
            opts.update(info['extra'])
            if fg:
                opts['foreground'] = fg
            try:
                st.configure(info['style'], **opts)
                st.map(info['style'], fieldbackground=[('focus', fieldbg), ('!focus', fieldbg)])
            except Exception:
                pass
            info['normal'], info['brand'] = (normal, brand)
            info['focused'] = False
            info['tw'].cancel()
        try:
            focused = self.root.focus_get()
            for info in self._input_focus:
                if info['w'] is focused:
                    st.configure(info['style'], bordercolor=info['brand'])
                    info['focused'] = True
        except Exception:
            pass
    def _bind_tree_row_hover(self):
        self._tree_hover_iid = None
        self.tree.bind('<Motion>', self._tree_row_hover, add='+')
        self.tree.bind('<Leave>', lambda _e: self._tree_row_hover_clear(), add='+')
    def _tree_row_hover_clear(self):
        iid = self._tree_hover_iid
        if iid:
            try:
                tags = tuple((t for t in self.tree.item(iid, 'tags') if t != 'hoverrow'))
                self.tree.item(iid, tags=tags)
            except Exception:
                pass
            self._tree_hover_iid = None
    def _sash_initial_bg(self):
        """分栏线初始色（_configure_control_styles 运行前使用）"""
        return '#3A3B3F' if self.dark.get() else '#E3E5E8'
    def _cap_on_hover(self, which):
        c = self._cap
        if c['hover'] == which:
            return
        c['hover'] = which
        self._cap_paint()
    def _cap_on_leave(self, _e=None):
        c = self._cap
        if not c['hover']:
            return
        c['hover'] = None
        self._cap_paint()
    def _cap_on_active(self, on):
        c = self._cap
        c['active'] = bool(on)
        if not on:
            c['hover'] = None
        self._cap_paint()
    def _hide_guide(self):
        if hasattr(self, 'guide'):
            self.guide.place_forget()
        fl = getattr(self, '_guide_float', None)
        if fl is not None:
            fl.stop()
    def _check_empty_library(self):
        """库为空 → 显示居中大拖放卡片；非空 → 确保卡片隐藏"""
        try:
            empty = True
            if os.path.exists(DB_PATH):
                from indexer import library_stats
                empty = library_stats(DB_PATH)['files'] == 0
        except Exception:
            empty = True
        if empty:
            self._show_guide()
        else:
            self._hide_guide()
    def _on_drop(self, evt):
        """拖拽回调：分析→预览确认→入库（按当前 link_only 模式）"""
        paths = parse_dropped_paths(evt.data)
        if not paths:
            return
        self._start_import_flow(paths, self.link_only.get())
    def _build_cover(self):
        """预建置顶无边框遮罩窗（含一个铺满的 Label），常驻映射在屏幕外。

            刻意不 withdraw：撤掉的窗口重新映射时 Windows 会新建一张未初始化的
            表面，第一帧是纯黑 —— 探针实测这正是残留的那 1 帧黑（62ms）。改成一直
            映射在屏幕外（x=-40000），表面始终有效、冻结帧也早已画在上面，还原
            当刻只需 SetWindowPos 挪进来，没有建窗 + 首帧绘制的延迟。"""
        try:
            bg = '#F4F6F9'
            try:
                bg = str(ttk.Style().lookup('TFrame', 'background') or bg)
            except Exception:
                pass
            cv = tk.Toplevel(self.root)
            cv.overrideredirect(True)
            cv.configure(bg=bg)
            lb = tk.Label(cv, bd=0, highlightthickness=0, bg=bg)
            lb.pack(fill='both', expand=True)
            try:
                cv.attributes('-topmost', True)
            except Exception:
                pass
            cv.geometry('1x1+-40000+-40000')
            self._cover = cv
            self._cover_label = lb
            try:
                if winchrome is not None:
                    winchrome.set_cover_window(int(cv.winfo_id()))
            except Exception:
                pass
        except Exception:
            self._cover = None
    def _show_cover(self):
        """还原当刻贴上冻结帧（位置与真窗口完全重合）。

        冻结帧抓取失败时仍把遮罩盖上去 —— 纯色纸底也比露黑帧强。"""
        if self._cover_dirty:
            self._prepare_cover_photo()
        if self._cover is None or (not self._cover_rect):
            return
        try:
            if winchrome is not None:
                winchrome.set_cover_window(int(self._cover.winfo_id()))
        except Exception:
            pass
        try:
            w, h, x, y = self._cover_rect
            self._cover.geometry('%dx%d+%d+%d' % (w, h, x, y))
            self._cover.attributes('-topmost', True)
            self._cover.lift()
        except Exception:
            pass
    def _hide_cover(self):
        """挪回屏幕外（不 withdraw，理由见 _build_cover）"""
        if self._cover is None:
            return
        try:
            self._cover.geometry('+-40000+-40000')
        except Exception:
            pass
    def _restore_hwnd(self):
        """主窗真正带标题栏的 Win32 句柄（缓存，别每 10ms 都 GetAncestor）"""
        if self._restore_hw is None:
            try:
                self._restore_hw = winchrome._caption_hwnd(self.root) or 0
            except Exception:
                self._restore_hw = 0
        return self._restore_hw
    def _force_paint(self):
        """还原当刻连续 update()：让 Tk 尽早把内容画进重定向表面。"""
        try:
            self.root.update()
        except Exception:
            pass
    def _minimize_to_tray(self, pw, stop_event, tip=None):
        """隐藏主窗 + 进度窗 → 启动托盘图标。
            返回 True 表示托盘已正常启动；False 表示失败（调用方应回退）。
            pw: 当前进度窗；stop_event: 进度窗的 stop_event（托盘右键"取消"用）
            tip: 托盘悬停文案；None 表示后台入库场景
            """
        if not _TRAY_OK or self._bg_mode:
            return False
        try:
            self._tray_evt_q = queue.Queue()
            self._tray = _tray_mod.TrayThread(self._tray_evt_q)
            self._tray.start()
            if not self._tray.wait_ready(2.0):
                try:
                    self._tray.stop()
                except Exception:
                    pass
                self._tray = None
                self._tray_evt_q = None
                return False
        except Exception:
            self._tray = None
            self._tray_evt_q = None
            return False
        self._pw_ref = pw
        self._pw_stop_event = stop_event
        self._bg_mode = True
        try:
            self._save_config()
        except Exception:
            pass
        icon_path = _resource_path('app_icon.ico')
        try:
            self._tray.add_icon(icon_path, tip or '镜仔搜索 · 后台入库中')
        except Exception:
            pass
        try:
            pw.withdraw()
        except Exception:
            pass
        try:
            if hasattr(self, '_cover'):
                self._grab_restore_cover()
        except Exception:
            pass
        try:
            self.root.withdraw()
        except Exception:
            pass
        self.root.after(100, self._poll_tray_events)
        return True
    def _hide_to_tray(self):
        """点关闭叉：隐藏主窗到托盘而非退出。
            返回 True 表示已隐藏；False 表示托盘不可用（调用方回退到退出流程）。
            """
        if not _TRAY_OK or self._bg_mode:
            return False
        if not self._minimize_to_tray(None, None, tip='镜仔搜索 · 后台运行（双击显示，右键退出）'):
            return False
        self._toast('已隐藏到系统托盘 · 双击托盘图标可恢复')
        return True
    def _resolve_close_choice(self):
        """点关闭叉时该做什么：返回 'tray' / 'exit' / None（取消，什么都不做）。

            会话级记忆：本次启动第一次点叉弹框询问，选完写进 self._close_choice
            并在本次进程内一直沿用；不落盘 —— 彻底退出后下次启动重新询问。
            托盘右键「退出程序」会置 _force_quit，直接返回 'exit' 不再询问。
            """
        if getattr(self, '_force_quit', False):
            return 'exit'
        choice = getattr(self, '_close_choice', None)
        if choice is None:
            choice = self._ask_close_choice()
            if choice is None:
                return None
            self._close_choice = choice
        return choice
    def _poll_tray_events(self):
        """主线程定期消费托盘事件队列。后台状态结束则停止轮询。"""
        if not self._bg_mode or self._tray_evt_q is None:
            return
        try:
            while True:
                try:
                    evt = self._tray_evt_q.get_nowait()
                except queue.Empty:
                    break
                kind = evt[0]
                if kind in ('lclick', 'lclick2'):
                    self._restore_from_tray()
                    return
                elif kind == 'rclick':
                    self._tray_menu(evt[1], evt[2])
                elif kind == 'balloon_click':
                    self._restore_from_tray()
                    return
        except Exception:
            pass
        self.root.after(100, self._poll_tray_events)
    def _tray_balloon(self, title, info, kind='info'):
        """托盘气泡通知（仅在后台运行时有效）。"""
        if self._tray is not None:
            try:
                self._tray.show_balloon(title, info, kind)
            except Exception:
                pass
    def _deny_close(self, p, dlg):
        """弹窗未关闭时上级窗口请求关闭：响铃并弹回弹窗，拒绝关闭"""
        p.bell()
        try:
            dlg.lift()
            dlg.focus_force()
        except Exception:
            pass
    def _release_modal(self, event):
        """弹窗销毁时解锁其全部上级窗口"""
        overlays = self._modal_overlays.pop(id(event.widget), None)
        if not overlays:
            return
        for p, ov in overlays:
            try:
                ov.destroy()
            except Exception:
                pass
            st = self._modal_state.get(p)
            if st:
                st['depth'] -= 1
                if st['depth'] <= 0:
                    old = st['orig']
                    try:
                        if old:
                            p.tk.call('wm', 'protocol', str(p), 'WM_DELETE_WINDOW', old)
                        else:
                            p.protocol('WM_DELETE_WINDOW', p.destroy)
                    except Exception:
                        pass
                    self._modal_state.pop(p, None)
    def _center_win(self, win, w, h, modal=True):
        """弹窗在屏幕居中，所有自定义弹窗统一调用；
            modal=True 时按弹窗等级锁定上级窗口（点不动+响铃+禁越级关闭）；
            高 DPI 下字体随 tk scaling 放大而窗口几何是物理像素，
            不同步放大会把底部按钮挤出视口 → 按 DPI 比例放大并夹到工作区"""
        try:
            win.update_idletasks()
            try:
                scale = float(win.tk.call('tk', 'scaling')) / (96.0 / 72.0)
            except Exception:
                scale = 1.0
            scale = max(scale, 1.0)
            w = int(w * scale)
            h = int(h * scale)
            sw = win.winfo_screenwidth()
            sh = win.winfo_screenheight()
            w = min(w, int(sw * 0.96))
            h = min(h, int(sh * 0.92))
            x = (sw - w) // 2
            y = (sh - h) // 2
            win.geometry(f'{w}x{h}+{max(x, 0)}+{max(y, 0)}')
        except Exception:
            win.geometry(f'{w}x{h}')
        if modal:
            try:
                self._apply_modal(win)
            except Exception:
                pass
        if anim is not None:
            try:
                win.after(10, lambda: anim.popup_in(win, duration_ms=160, rise=8))
            except Exception:
                pass
    def _import_batch_loop(self, files, link_mode, stop_event=None, ui_cb=None, preplans=None):
        """批量入库核心（工作线程内执行），拖拽与目录扫描共用。

            优先调用 index_batch_files（并行解析 + 批量事务 + 快速去重）。
            link_mode 下 index_batch_files 只做解析+建索引（不复制文件），
            已在 index_batch_files 内部处理。

            返回统计 dict（含 skipped_detail 给主线程弹报告用）；
            被 stop_event 中断返回 None"""
        result = index_batch_files(files, DB_PATH, stop_event=stop_event, ui_cb=ui_cb, parse_timeout=120, batch_commit=50, workers=4)
        if stop_event is not None and stop_event.is_set():
            return None
        return {'ok': result['ok'], 'dup': result['dup'], 'skipped': result['skip'], 'failed': result['failed'] + result['timeout'], 'imported_ok': result['imported_ok'], 'skipped_detail': result['skipped_detail'], 'total': result['total']}
    def import_files_dialog(self):
        """「导入文件」按钮：文件选择对话框 → 预览确认 → 入库"""
        if self._bg_mode:
            self._restore_from_tray()
            self.root.after(300, self.import_files_dialog)
            return
        files = filedialog.askopenfilenames(title='选择要导入库的文件', filetypes=[('所有文件', '*.*')])
        if not files:
            return
        self._start_import_flow(list(files), self.link_only.get())
    def _on_type_change(self, evt=None):
        """类型下拉选择 → 更新 file_type 并自动重新搜索"""
        lbl = self.type_cb.get()
        for code, label in TYPE_OPTIONS:
            if label == lbl:
                self.file_type.set(code)
                break
        self.do_search()
    def _scan_action(self):
        """扫描入库：未选目录时先选（选完自动扫），否则重扫当前目录"""
        d = self.root_dir.get()
        if d and os.path.isdir(d):
            self.start_scan()
        else:
            self.choose_dir()
    def _focus_search(self, evt=None):
        """Ctrl+F：聚焦搜索框并全选"""
        self.kw_entry.focus_set()
        self.kw_entry.select_range(0, 'end')
        return 'break'
    def _fill_chapters(self, path):
        """按文件层级填充章节树。

            防重入：同一 path 在短时间内被多次调时，只执行第一次。
            去重：list_chapters SQL DISTINCT + 此处 (start_line,title) 二次去重。
            """
        if getattr(self, '_chap_filling_path', None) == path:
            return
        self._chap_filling_path = path
        try:
            self._chapters_path = path
            self.chap_tree.delete(*self.chap_tree.get_children())
            try:
                chapters = list_chapters(DB_PATH, path)
            except Exception:
                chapters = []
            if not chapters:
                self.chap_tree.insert('', 'end', text='（无章节结构）', values=('', ''))
                return
            seen = set()
            unique = []
            for ch in chapters:
                key = (ch['start_line'], ch['title'])
                if key in seen:
                    continue
                seen.add(key)
                unique.append(ch)
            chapters = unique
            last = {}
            for ch in chapters:
                parent = ''
                for lv in range(ch['level'] - 1, -1, -1):
                    if lv in last:
                        parent = last[lv]
                        break
                _lvl_tag = f'lv{min(ch['level'], 3)}'
                item = self.chap_tree.insert(parent, 'end', text=ch['title'], values=(ch['start_line'], ch['end_line']), tags=(_lvl_tag,))
                last = {lv: v for lv, v in last.items() if lv < ch['level']}
                last[ch['level']] = item
        finally:
            if getattr(self, '_chap_filling_path', None) == path:
                self._chap_filling_path = None
    def _on_chap_click(self, evt=None):
        """点击已选中的章节时 <<TreeviewSelect>> 不触发，这里补一次跳转"""
        if evt is None:
            return
        iid = self.chap_tree.identify_row(evt.y)
        if iid and iid in self.chap_tree.selection():
            self._on_chapter_select()
    def _show_chapter(self, path, start, end):
        """章节跳转：预览显示整章内容，章首标题高亮"""
        self._cur_path = path
        self._cur_line = start
        if self._chapters_path != path:
            self._fill_chapters(path)
        rows = get_line_range(DB_PATH, path, start, end)
        self._preview_ph.place_forget()
        self.preview.delete('1.0', 'end')
        name = os.path.basename(path)
        self.preview.insert('end', f'{name}  ', 'chap')
        self.preview.insert('end', f'· 章节范围 L{start}-L{end}\n', 'meta')
        self.preview.insert('end', '章节浏览模式 · 点底部「整段 / 上下文」可回到命中片段\n\n', 'meta')
        if rows:
            body_start = self.preview.index('end')
            self.preview.insert('end', f'{rows[0][1]}\n', 'chap')
            for _ln, text in rows[1:]:
                self.preview.insert('end', text + '\n')
            self.preview.tag_add('body', body_start, 'end-1c')
        self.preview.mark_set('insert', '1.0')
        self.preview.see('1.0')
    def choose_dir(self):
        """「扫描目录」按钮：选目录后立即开始扫描入库"""
        d = filedialog.askdirectory(title='选择要扫描入库的目录')
        if d:
            self.root_dir.set(d)
            self._save_config()
            self.start_scan()
    def stop_scan(self):
        if self.stop_event:
            self.stop_event.set()
            self.status.configure(text='正在停止...')
            self.scan_btn.config(state='disabled')
    def _scan_finished(self):
        self.scan_btn.config(text='扫描入库', command=self.start_scan, state='normal')
        self.stop_event = None
        self._refresh_err_count()
        self._start_watcher_silent()
    def _refresh_err_count(self):
        try:
            n = count_errors(DB_PATH) if os.path.exists(DB_PATH) else 0
        except Exception:
            n = 0
        if n:
            self.err_btn.config(text=f'{self.IC_WARN} 错误 {n}')
            if not self.err_btn.winfo_ismapped():
                self.err_btn.pack(side='left')
                self._badge_pop(self.err_btn, family=self.FONT_ICON_S[0], base=8)
        else:
            self.err_btn.pack_forget()
        self._refresh_prob_badge()
    def _apply_prob_badge(self, n):
        if n:
            self.prob_btn.config(text=f'{self.IC_WARN} {n} 个文件需处理')
            if not self.prob_btn.winfo_ismapped():
                self.prob_btn.pack(side='left')
                self._badge_pop(self.prob_btn, family=self.FONT_ICON_S[0], base=8)
        else:
            self.prob_btn.pack_forget()
    def _on_tree_col_resized(self, _evt=None):
        """用户拖过列宽 → 防抖 400ms 存配置，下次启动恢复"""
        job = getattr(self, '_col_job', None)
        if job:
            try:
                self.root.after_cancel(job)
            except Exception:
                pass
        self._col_job = self.root.after(400, self._save_tree_cols)
    def _save_tree_cols(self):
        self._col_job = None
        try:
            widths = {}
            for cid in ('#0', 'hits', 'text'):
                widths[cid] = int(self.tree.column(cid, 'width'))
            self.cfg['tree_cols'] = widths
            self._save_config()
        except Exception:
            pass
    def _flush_position(self):
        self._pos_job = None
        try:
            self._save_config()
        except Exception:
            pass
    def _restore_session(self):
        """启动后恢复上次关键词 → 但保持空态不自动搜索，避免旧结果占屏。
            搜索框预填上次关键词，用户按回车即可继续。"""
        kw = self.cfg.get('last_kw')
        path = self.cfg.get('last_path')
        if not kw or kw == KW_HINT or (not path):
            return
        try:
            if not os.path.exists(DB_PATH):
                return
            from indexer import library_stats
            if library_stats(DB_PATH)['files'] == 0:
                return
        except Exception:
            return
        self.keyword.set(kw)
        self.kw_entry.configure(foreground='')
        self._pending_restore = (path, int(self.cfg.get('last_line') or 1))
    def _max_backups(self):
        """退出自动备份保留份数（0 = 不备份）"""
        try:
            return max(0, min(10, int(self.cfg.get('max_backups', MAX_BACKUPS))))
        except (TypeError, ValueError):
            return MAX_BACKUPS
    def _choose_max_backups(self):
        """设置索引库自动备份保留份数"""
        from tkinter import simpledialog
        try:
            per = os.path.getsize(DB_PATH) / 1048576 if os.path.exists(DB_PATH) else 0
        except OSError:
            per = 0
        n = simpledialog.askinteger('自动备份份数', f'退出时保留最近几份索引备份？（0 = 不备份）\n当前库约 {per:.0f}MB/份，备份存于 backup\\ 目录', initialvalue=self._max_backups(), minvalue=0, maxvalue=10, parent=self.root)
        if n is None:
            return
        self.cfg['max_backups'] = int(n)
        self._save_config()
        self.status.configure(text=f'自动备份份数已设为 {n}' + ('（不备份）' if n == 0 else ''))
    def _start_watcher_silent(self):
        """自动监听已固化为默认：条件满足时静默启动（无目录/未建库则跳过）。
            启动后与扫描完成时调用，不弹任何错误框。"""
        if self.watcher and self.watcher.is_running():
            return
        d = self.root_dir.get()
        if not d or not os.path.isdir(d) or (not os.path.exists(DB_PATH)):
            return
        try:
            self.watcher = FileWatcher(d, DB_PATH, on_event=self._on_watch_event)
            self.watcher.start()
        except Exception:
            self.watcher = None
    def _auto_qa_refresh(self, imported_ok):
        """新文件入库/变更后，后台自动增量更新语义索引。
            - 只构建 mtime 变化的文件（增量），完成后轻提示，不打断操作
            - Ollama 未运行会自动拉起；向量模型缺失或彻底连不上则静默跳过
            - 问答窗开着时同步刷新索引计数"""
        if not imported_ok or not _QA_OK or (not os.path.exists(DB_PATH)):
            return
        w = getattr(self, '_qa_window', None)
        if w is not None and w.win.winfo_exists():
            if getattr(w, '_building', False):
                return
            if getattr(w, '_asking', False):
                self._qa_auto_pending = True
                return
        if not self._QA_AUTO_LOCK.acquire(blocking=False):
            self._qa_auto_pending = True
            return
        threading.Thread(target=self._auto_qa_work, daemon=True).start()
    def _on_watch_event(self, status, path):
        """watcher 回调：在主线程更新状态栏"""
        try:
            name = os.path.basename(path)
            self.root.after(0, lambda: self.status.configure(text=f'监听更新: {name} ({status})'))
        except Exception:
            pass
        if status in ('ok', 'deleted'):
            self._auto_qa_refresh([path])
    def _stop_search_spinner(self):
        self._spinner_on = False
        job = getattr(self, '_spinner_job', None)
        if job:
            try:
                self.root.after_cancel(job)
            except Exception:
                pass
            self._spinner_job = None
        try:
            self.search_btn.configure(text=f'{self.IC_SEARCH} 搜索')
        except Exception:
            pass
    def _on_kw_typed(self, evt=None):
        """输入即搜：停止输入 300ms 后自动搜索；功能键不触发，避免双发"""
        if evt and evt.keysym in ('Return', 'KP_Enter', 'Escape', 'Tab', 'Up', 'Down', 'Left', 'Right', 'Home', 'End', 'Shift_L', 'Shift_R', 'Control_L', 'Control_R', 'Alt_L', 'Alt_R'):
            return
        if getattr(self, '_type_after_id', None):
            self.root.after_cancel(self._type_after_id)
        self._type_after_id = self.root.after(300, self._auto_search)
    def _on_filter_change(self, evt=None):
        """结果内过滤输入 → 防抖 250ms 后统一重刷，避免每个按键全量重插树+重渲染预览"""
        job = getattr(self, '_filter_job', None)
        if job:
            try:
                self.root.after_cancel(job)
            except Exception:
                pass
        self._filter_job = self.root.after(250, self._apply_filter)
    def _apply_filter(self):
        self._filter_job = None
        self._populate_tree(restore=True)
        self._refresh_preview()
    def _populate_batch(self, plan, state, gen, idx, fk=''):
        """按行预算分批插入；新一轮填充/过滤会立即打断旧批次"""
        if gen != getattr(self, '_populate_gen', 0):
            return
        budget = self._BATCH_ROWS if state['shown'] else self._BATCH_FIRST
        while idx < len(plan) and budget > 0:
            p = plan[idx]
            budget -= plan_row_cost(p)
            self._insert_group_plan(p, state)
            idx += 1
        if idx < len(plan):
            self.root.after(1, lambda: self._populate_batch(plan, state, gen, idx, fk))
        else:
            self._populate_finish(fk, state['shown'])
    def _clear_tree_view(self):
        self._restore_scroll = None
        self._restore_sel_path = None
        self._restore_sel_text = None
    def _populate_finish(self, fk, shown_groups):
        """填充收尾：还原视图 + 过滤提示 + 导出按钮可用态"""
        self._restore_tree_view()
        if fk:
            n = len(self.tree.get_children())
            self.status.configure(text=f'结果内过滤「{self.filter_var.get()}」→ {n} 个文件')
        if hasattr(self, 'digest_btn'):
            self.digest_btn.configure(state='normal' if shown_groups else 'disabled')
    def _chart_hit_index(self, evt):
        """画布点击/悬停位置 → 文件栏索引（-1 表示不在柱区内）"""
        cx = self._chart_canvas.canvasx(evt.x)
        for i, it in enumerate(getattr(self, '_chart_items', [])):
            if it['x0'] <= cx < it['x1']:
                return i
        return -1
    def _build_search_hits(self, max_files=8, max_chars=6000):
        """把当前搜索结果转成 ai_qa.build_messages 可吃的 hits 列表。
            每文件取前 4 条命中行（±1 行上下文，复用搜索时已取的 _ctx_map），
            按命中数排序截断；score 按文件序递减，模拟相关度。"""
        return build_search_hits(self.groups, self._ctx_map, max_files, max_chars)
    def _tree_yscroll_set(self, *args):
        """tree 垂直滚动回调：滚动条 set + 防抖延迟保持父节点可见。"""
        try:
            self._tree_sb.set(*args)
        except Exception:
            pass
        if hasattr(self, '_tree_keep_parent_job'):
            try:
                self.root.after_cancel(self._tree_keep_parent_job)
            except Exception:
                pass
        self._tree_keep_parent_job = self.root.after(150, self._tree_keep_parent_after_scroll)
    def _show_btn_tip(self, widget, text):
        self._hide_tree_tip()
        try:
            x = widget.winfo_rootx()
            y = widget.winfo_rooty() + widget.winfo_height() + 4
            self._show_tree_tip(text, x, y)
        except Exception:
            pass
    def _show_tree_tip(self, text, rx, ry):
        """显示悬停提示。窗口只建一次、之后复用 —— 悬停是高频动作，
            每次都新建 Toplevel+Label 再销毁，等于反复创建/销毁原生窗口。"""
        self._hide_tree_tip()
        try:
            win = getattr(self, '_tree_tip_win', None)
            if win is None or not tk.Toplevel.winfo_exists(win):
                win = tk.Toplevel(self.root)
                win.wm_overrideredirect(True)
                lbl = tk.Label(win, background='#FFFBE6', fg='#333', relief='solid', borderwidth=1, wraplength=420, justify='left', font=('Microsoft YaHei UI', 8), padx=6, pady=3)
                lbl.pack()
                self._tree_tip_win = win
                self._tree_tip_lbl = lbl
            self._tree_tip_lbl.configure(text=text)
            win.wm_geometry(f'+{rx + 14}+{ry + 10}')
            self._tree_tip = win
            if anim is not None and (not anim.reduced()):
                win.attributes('-alpha', 0.0)
                win.deiconify()
                anim.fade_in(win, 110)
            else:
                win.deiconify()
        except Exception:
            pass
    def _hide_tree_tip(self, _evt=None):
        if getattr(self, '_tip_job', None):
            try:
                self.root.after_cancel(self._tip_job)
            except Exception:
                pass
            self._tip_job = None
        win = getattr(self, '_tree_tip', None)
        if win is not None:
            try:
                win.withdraw()
                win.attributes('-alpha', 1.0)
            except Exception:
                pass
            self._tree_tip = None
    def _show_preview(self, path, line_no, hit_lines=None, cross=None):
        """记录当前预览对象，触发刷新；同时保存阅读位置。
            hit_lines: 合并行片段簇的全部命中行号；传入后预览自动展开覆盖整段。
            cross: (下行行号, 被换行切断的词)，跨行命中时在预览接缝处补高亮。"""
        self._cur_path = path
        self._cur_line = line_no
        self._cur_hit_lines = sorted(set(hit_lines)) if hit_lines else [line_no]
        self._cur_cross = cross or None
        try:
            save_reading_position(DB_PATH, path, line_no)
        except Exception:
            pass
        self._refresh_preview()
    def _ln_hit(self, ln, hit_set):
        """重排后：段内任一真实行号命中，整段就算命中"""
        return any((x in hit_set for x in self._reflow_alias.get(ln, (ln,))))
    def _ln_anchor(self, ln):
        """行号 → 它在预览里所属段的行号（重排后可能不是自已）"""
        return self._reflow_anchor.get(ln, ln)
    def _tag_hit_spans(self, ln, r0, row_end, hit_set):
        """给命中行打底色。重排后一段可能由多行并成，只给命中的那几行
            所占的字符区间上色（未重排时就是整行，行为不变）。"""
        segs = self._reflow_spans.get(ln)
        if not segs or len(segs) == 1:
            self.preview.tag_add('hit', r0, row_end)
            return
        off = 0
        for sln, slen in segs:
            if sln in hit_set and slen:
                self.preview.tag_add('hit', self.preview.index(f'{r0}+{off}c'), self.preview.index(f'{r0}+{off + slen}c'))
            off += slen
    def _render_table_block(self, table_items, hit_set, mode_label):
        """渲染一个表格块：表头加粗深色底 + 斑马纹 + 对齐 tab + 引用链接支持"""
        for row_idx, (ln, txt) in enumerate(table_items):
            r0 = self.preview.index('end-1c linestart')
            is_head = row_idx == 0
            is_hit = self._ln_hit(ln, hit_set)
            cells = [c.strip() for c in txt.split(' | ')]
            row_tag = []
            if is_head:
                row_tag.append('tbl_header')
            elif is_hit:
                row_tag.append('tbl_hit')
                row_tag.append('hit')
            elif row_idx % 2 == 1:
                row_tag.append('tbl_row_alt')
            else:
                row_tag.append('tbl_row')
            tag_tuple = tuple(row_tag)
            for col_idx, cell in enumerate(cells):
                if col_idx > 0:
                    self.preview.insert('end', '\t', tag_tuple)
                self._insert_with_refs(_PAGE_RE.sub('', cell or '', count=1), extra_tags=row_tag)
            self.preview.insert('end', '\n')
            row_end = f'{r0.split('.')[0]}.end'
            self.preview.tag_add(f'ln{ln}', r0, row_end)
    def _refresh_preview(self):
        """根据 preview_mode 渲染预览区"""
        if not self._cur_path or not self._cur_line:
            return
        path = self._cur_path
        line_no = self._cur_line
        if self._chapters_path != path:
            self._fill_chapters(path)
        self._ref_resolve_cache = {}
        self._ref_clause_cache = {}
        self.preview.delete('1.0', 'end')
        self._preview_ph.place_forget()
        hit_lines = self._cur_hit_lines or [line_no]
        hit_set = set(hit_lines)
        hit_start, hit_end = (min(hit_lines), max(hit_lines))
        multi_hit = hit_end > hit_start
        expanded = False
        if self.preview_mode.get() == 'paragraph':
            content = get_paragraph(DB_PATH, path, line_no)
            mode_label = '整段'
            if multi_hit and content:
                para_last = content[-1][0]
                if para_last < hit_end:
                    extra = get_line_range(DB_PATH, path, para_last + 1, hit_end + 3)
                    content = content + [r for r in extra if r[0] > para_last]
                    expanded = True
        else:
            ctx_n = CTX_LINES
            content = get_line_range(DB_PATH, path, hit_start - ctx_n, hit_end + ctx_n)
            if multi_hit:
                mode_label = f'上下文 ±{ctx_n} 行（已展开覆盖 L{hit_start}-L{hit_end}）'
            else:
                mode_label = f'上下文 ±{ctx_n} 行'
        raw_content = content
        self._reflow_alias = {}
        self._reflow_anchor = {}
        self._reflow_spans = {}
        if self.preview_wrap_var.get():
            content = self._reflow_content(content)
        content_map = dict(raw_content)
        page = extract_page(content_map.get(hit_start, ''))
        name = os.path.basename(path)
        self.preview.insert('end', name + '  ', 'chap')
        loc = f'L{hit_start}-L{hit_end}' if multi_hit else f'L{line_no}'
        self.preview.insert('end', '· ' + loc + (f' · P{page}' if page else '') + '\n', 'meta')
        meta = get_metadata(DB_PATH, path)
        status_map = {'active': '有效', 'void': f'{self.IC_WARN}废止', 'replaced': f'{self.IC_REFRESH}已替代'}
        st_lbl = status_map.get(meta.get('status', 'active'), '有效')
        line2 = [st_lbl]
        if meta.get('doc_no'):
            line2.append(meta['doc_no'])
        if meta.get('effective_date'):
            line2.append('生效 ' + meta['effective_date'])
        fuzzy_lines = [ln for ln in hit_lines if (sc := self._score_map.get((path, ln))) is not None and sc < 1.0]
        if fuzzy_lines:
            line2.append('模糊命中')
        if getattr(self, '_cur_cross', None):
            line2.append('跨行命中')
        if expanded:
            mode_label += '（已补齐相邻命中行）'
        line2.append(mode_label)
        chap_chain = get_chapter_chain_str(DB_PATH, path, line_no)
        if chap_chain:
            line2.append(chap_chain)
        self.preview.insert('end', ' · '.join(line2) + '\n\n', 'meta')
        self._ref_targets = {}
        self._ref_seq = 0
        self._ref_status_cache = {}
        first_hit_idx = None
        blocks = self._split_content_into_blocks(content)
        body_start = self.preview.index('end')
        for kind, items in blocks:
            if kind == 'table':
                self._render_table_block(items, hit_set, mode_label)
                for ln, _ in items:
                    if self._ln_hit(ln, hit_set):
                        rng = self.preview.tag_nextrange(f'ln{ln}', '1.0')
                        if rng and first_hit_idx is None:
                            first_hit_idx = rng[0]
                            break
            else:
                for ln, txt in items:
                    r0 = self.preview.index('end-1c linestart')
                    self._insert_with_refs(_PAGE_RE.sub('', txt or '', count=1))
                    self.preview.insert('end', '\n')
                    row_end = f'{r0.split('.')[0]}.end'
                    self.preview.tag_add(f'ln{ln}', r0, row_end)
                    if self._ln_hit(ln, hit_set):
                        self._tag_hit_spans(ln, r0, row_end, hit_set)
                        if first_hit_idx is None:
                            first_hit_idx = r0
        self.preview.tag_add('body', body_start, 'end-1c')
        self.preview.insert('end', '\n')
        self.preview.insert('end', f'共 {len(raw_content)} 行 · 双击左侧树打开文件\n', 'meta')
        terms = kw_terms(self.keyword.get())
        if terms:
            nocase = not self.cs.get()
            for term in terms:
                start = '1.0'
                while True:
                    idx = self.preview.search(term, start, stopindex='end', nocase=nocase)
                    if not idx:
                        break
                    end = f'{idx}+{len(term)}c'
                    self.preview.tag_add('hl', idx, end)
                    start = end
        if not self.preview_wrap_var.get() and getattr(self, '_cur_cross', None):
            self._highlight_cross_seam()
        syn_terms = []
        for _tok, syns in (getattr(self, '_syn_map', None) or {}).items():
            for s in syns:
                if s not in terms and s not in syn_terms:
                    syn_terms.append(s)
        if syn_terms:
            nocase = not self.cs.get()
            for term in syn_terms:
                start = '1.0'
                while True:
                    idx = self.preview.search(term, start, stopindex='end', nocase=nocase)
                    if not idx:
                        break
                    end = f'{idx}+{len(term)}c'
                    self.preview.tag_add('syn', idx, end)
                    start = end
        if terms and fuzzy_lines:
            for fln in fuzzy_lines:
                rng = self.preview.tag_nextrange(f'ln{self._ln_anchor(fln)}', '1.0')
                if rng:
                    row = int(rng[0].split('.')[0])
                    for term in terms:
                        try:
                            self._highlight_fuzzy_row(row, term)
                        except Exception:
                            pass
        fk = self.filter_var.get().strip() if hasattr(self, 'filter_var') else ''
        if fk:
            start = '1.0'
            while True:
                idx = self.preview.search(fk, start, stopindex='end', nocase=True)
                if not idx:
                    break
                end = f'{idx}+{len(fk)}c'
                self.preview.tag_add('fhl', idx, end)
                start = end
        if first_hit_idx:
            self._smooth_see(first_hit_idx)
        self._fade_hit_highlight()
        self._refresh_bookmark_btn()
    def _ref_hover(self, evt, tag):
        """鼠标悬停在《文件》引用上：改光标 + 状态栏提示 + 链接颜色渐变"""
        if tag is None:
            self.preview.configure(cursor='')
            last = getattr(self, '_ref_hover_tag', None)
            if last:
                self._ref_link_color(last, False)
            self._ref_hover_tag = None
            last_inst = getattr(self, '_ref_hover_inst', None)
            if last_inst:
                self._ref_bg(last_inst, False)
            self._ref_hover_inst = None
            self.status.configure(text='')
            return
        loc = f'@{evt.x},{evt.y}'
        for t in self.preview.tag_names(loc):
            if t.startswith('ref_') and t in self._ref_targets:
                info = self._ref_targets[t]
                self.preview.configure(cursor='hand2')
                if getattr(self, '_ref_hover_inst', None) != t:
                    old = getattr(self, '_ref_hover_inst', None)
                    if old:
                        self._ref_bg(old, False)
                    self._ref_hover_inst = t
                    self._ref_bg(t, True)
                if tag in ('reflink', 'refmiss', 'refdead') and getattr(self, '_ref_hover_tag', None) != tag:
                    if getattr(self, '_ref_hover_tag', None):
                        self._ref_link_color(self._ref_hover_tag, False)
                    self._ref_hover_tag = tag
                    self._ref_link_color(tag, True)
                if info[0]:
                    name = os.path.basename(info[0])
                    self.status.configure(text=f'{self.IC_LINK} 单击打开《{name}》原文件（已定位到第 {info[1]} 行）')
                elif tag == 'refdead':
                    self.status.configure(text=f'{self.IC_WARN} 此处引用的文件已废止/被替代')
                else:
                    title = info[2] if len(info) > 2 else '未命名文件'
                    self.status.configure(text=f'{self.IC_WARN}《{title}》未入库，点击查看详情')
                return
    def _ref_target_at(self, evt):
        """返回事件坐标处的 ref_xxx tag 及其 info；无则返回 (None, None)"""
        loc = f'@{evt.x},{evt.y}'
        for t in self.preview.tag_names(loc):
            if t.startswith('ref_') and t in self._ref_targets:
                return (t, self._ref_targets[t])
        return (None, None)
    def _on_ref_open(self, evt):
        """单击 reflink/refdead：用系统默认程序打开原文件并定位"""
        tag, info = self._ref_target_at(evt)
        if not tag or not info[0]:
            return
        self._open_located(info[0], info[1])
    def _on_preview_key(self, evt):
        """预览只读：只对「会改内容的按键」提示，Ctrl+C/方向键等不管"""
        if evt.keysym in ('BackSpace', 'Delete') or (evt.char and evt.char.isprintable()):
            self.status.configure(text='预览为只读，正文不能直接编辑；如需修改请点「打开文件」')
    def _refresh_bookmark_btn(self):
        """按当前行是否已收藏刷新按钮文字；无预览内容时禁用操作按钮。
            未收藏→已收藏的瞬间播放星星弹跳（uiverse 点赞动效）。"""
        has = bool(self._cur_path and self._cur_line)
        if not has:
            self.bm_btn.configure(text=f'{self.IC_STAR} 收藏此条', state='disabled')
            self.open_file_btn.configure(state='disabled')
            self._bm_was = None
            return
        try:
            bm = is_bookmarked(DB_PATH, self._cur_path, self._cur_line)
        except Exception:
            bm = None
        self.bm_btn.configure(text=f'{self.IC_STAR_FILL} 已收藏（点击取消）' if bm else f'{self.IC_STAR} 收藏此条', state='normal')
        self.open_file_btn.configure(state='normal')
        if bm and self._bm_was is False:
            self._bm_pop()
        self._bm_was = bool(bm)
    def _toggle_bookmark(self):
        """收藏/取消收藏当前预览行"""
        if not self._cur_path or not self._cur_line:
            return
        bm = is_bookmarked(DB_PATH, self._cur_path, self._cur_line)
        if bm:
            remove_bookmark(DB_PATH, bookmark_id=bm)
            self.status.configure(text='已取消收藏')
        else:
            title = get_chapter_chain_str(DB_PATH, self._cur_path, self._cur_line) or ''
            if not title:
                lt = get_line_text(DB_PATH, self._cur_path, self._cur_line)
                title = lt[:30]
            add_bookmark(DB_PATH, self._cur_path, self._cur_line, title)
            self.status.configure(text='已收藏此条，可点顶部「书签」查看')
        self._refresh_bookmark_btn()
    def _web_synonyms(self, keyword):
        """联网检索用的同义词表：复用本地检索那套（内置组 + 自定义别名）。

            与本地检索共用同一个开关（self.syn_var，默认开），不额外加界面元素。
            搜「安全员」时法规正文里写的是「专职安全生产管理人员」，只查原词会漏。
            """
        if not getattr(self, 'syn_var', None) or not self.syn_var.get():
            return []
        try:
            return list(expand_map([keyword], self.cfg.get('custom_synonyms', '')).get(keyword, []))
        except Exception:
            return []
    def _web_locate_terms(self, keyword):
        """联网结果定位用的词表：原词 + 行话别名 + 同义词 + 写法变体。

            搜「塔吊」时法规正文里写的是「塔式起重机」，只高亮原词会 0 处命中，
            所以别名一并纳入；同义词走本地检索那套（安全员 ↔ 专职安全管理员）；
            写法变体复用本地检索的归一化，避免「GB/T 3608」与「GB／T 3608」
            互相定位不到。
            """
        try:
            base = websearch.expand_keyword(keyword) or [keyword]
        except Exception:
            base = [keyword]
        base = base + self._web_synonyms(keyword)
        terms = []
        for t in base:
            for v in _norm_variants(t) or [t]:
                v = (v or '').strip()
                if v and v not in terms:
                    terms.append(v)
        terms.sort(key=len, reverse=True)
        return terms
    def _inbox_dir(self):
        """库目录下的统一收件箱路径"""
        return os.path.join(os.path.dirname(os.path.abspath(DB_PATH)), 'inbox')
    def _write_digest_docx(self, fn, title, subtitle, sections, highlight_terms=(), context_n=0):
        """汇编 docx 通用构建器（书签汇编/搜索结果汇编共用）。
            sections: [(path, [(位置标题, 命中行号), ...])]
            highlight_terms: 需黄色高亮的关键词。
            context_n>0（制度汇编模式）：不整篇摘抄，只导出命中行上下各 N 行
                重点片段，相邻片段自动合并，命中行加粗。成功返回 True。"""
        ok, err = write_digest_docx(DB_PATH, fn, title, subtitle, sections, highlight_terms, context_n)
        if not ok:
            messagebox.showerror('导出失败', err)
            self._refresh_err_count()
        return ok
    def _export_results_digest(self):
        """把当前搜索结果按文件汇编成 Word（命中词黄色高亮）"""
        if not getattr(self, 'groups', None):
            messagebox.showinfo('提示', '当前没有搜索结果，请先搜索')
            return
        sections, n_clauses = ([], 0)
        for g in self.groups:
            locs, seen_para = ([], set())
            for cl in g.get('clusters', []):
                first_ln = cl['lines'][0]['line_no']
                para = get_paragraph(DB_PATH, g['path'], first_ln)
                if not para:
                    continue
                key = para[0][0]
                if key in seen_para:
                    continue
                seen_para.add(key)
                chain = get_chapter_chain_str(DB_PATH, g['path'], first_ln)
                locs.append((chain or f'第 {first_ln} 行', first_ln))
            if locs:
                sections.append((g['path'], locs))
                n_clauses += len(locs)
        if not sections:
            messagebox.showinfo('提示', '当前结果没有可导出的条款')
            return
        fn = filedialog.asksaveasfilename(initialdir=self._export_dir(), initialfile=f'制度重点汇编_{time.strftime('%Y%m%d_%H%M')}.docx', defaultextension='.docx', filetypes=[('Word 文档', '*.docx')])
        if not fn:
            return
        terms = [t for t in kw_terms(self.keyword.get()) if t not in ('OR',)]
        subtitle = time.strftime('导出日期：%Y-%m-%d') + f' · 关键词：{self.keyword.get()}' + f' · 共 {len(sections)} 个文件 {n_clauses} 处命中' + ' · 仅摘录命中行前后各 4 行重点，非全文' + ' · 由 镜仔搜索 自动生成'
        if self._write_digest_docx(fn, '制度重点汇编', subtitle, sections, highlight_terms=terms, context_n=4):
            self.status.configure(text=f'重点汇编已导出：{os.path.basename(fn)}（{len(sections)} 个文件）')
            messagebox.showinfo('导出成功', f'《制度重点汇编》已保存到：\n{fn}')
    def _incoming_warned(self, pairs, win):
        ins = [r for _, refs in pairs for r in refs]
        if not ins:
            return
        srcs = '、'.join(dict.fromkeys((r['name'] for r in ins)))
        if messagebox.askyesno('引用预警', f'有 {len(ins)} 处引用来自:\n{srcs[:200]}\n\n这些文件依据可能失效，是否查看预警详情？', parent=win):
            self.show_ref_warnings(win)
    def _compare_with_version(self, path, parent_win=None):
        """自动寻找同文号/同名版本：唯一候选直接对比，多个弹窗选择"""
        sibs = find_version_siblings(DB_PATH, path)
        if not sibs:
            messagebox.showinfo('未找到其他版本', '没有找到与该文件同文号、或名称高度相似的其他文件。\n可使用库中的「版本对比」手动选择两个文件。', parent=parent_win or self.root)
            return
        if len(sibs) == 1:
            self._open_version_diff(path, sibs[0]['path'])
            return
        self._choose_version_dialog(path, sibs, parent_win)
    def _open_version_diff(self, path_a, path_b):
        """按生效日期（缺省退回 mtime）判定新旧，旧版在左、新版在右"""
        try:
            da = get_metadata(DB_PATH, path_a).get('effective_date', '')
            db_ = get_metadata(DB_PATH, path_b).get('effective_date', '')
        except Exception:
            da = db_ = ''
        ka = eff_sort_key(da)
        kb = eff_sort_key(db_)
        if ka == kb:
            try:
                ka = os.path.getmtime(path_a)
                kb = os.path.getmtime(path_b)
            except OSError:
                ka = kb = 0
        old, new = (path_a, path_b) if ka <= kb else (path_b, path_a)
        self._render_diff(old, new)
    def _copy_paragraph(self):
        """复制预览区内容到剪贴板"""
        content = self.preview.get('1.0', 'end-1c')
        self.root.clipboard_clear()
        self.root.clipboard_append(content)
        self.status.configure(text='已复制预览内容到剪贴板')
    def _copy_citation(self):
        """生成标准引用格式并复制：《文件名》章节链（文号）: 命中行原文"""
        if not self._cur_path or not self._cur_line:
            return
        cite = build_citation(DB_PATH, self._cur_path, self._cur_line)
        self.root.clipboard_clear()
        self.root.clipboard_append(cite)
        self.status.configure(text='已复制引用到剪贴板')
    def _copy_paragraph_evt(self, evt):
        """Ctrl+C 事件：如果选中文本则复制选中，否则复制全段"""
        if self.preview.tag_ranges('sel'):
            self.preview.event_generate('<<Copy>>')
        else:
            self._copy_paragraph()
        return 'break'
    def _open_current_file(self):
        """打开当前预览的文件（带定位：高亮/翻页/跳行）"""
        if not self._cur_path or not os.path.exists(self._cur_path):
            return
        if sys.platform.startswith('win'):
            self._open_located(self._cur_path, self._cur_line)
        elif sys.platform == 'darwin':
            subprocess.Popen(['open', self._cur_path])
        else:
            subprocess.Popen(['xdg-open', self._cur_path])
    def _open_current_dir(self):
        """打开当前预览文件所在目录"""
        if not self._cur_path or not os.path.exists(self._cur_path):
            return
        d = os.path.dirname(self._cur_path)
        if sys.platform.startswith('win'):
            subprocess.Popen(['explorer', d])
        elif sys.platform == 'darwin':
            subprocess.Popen(['open', d])
        else:
            subprocess.Popen(['xdg-open', d])
    def _exit_reading(self):
        if self._read_path is None:
            return
        self._read_path = None
        self.read_bar.pack_forget()
        try:
            self.main_pane.insert(0, self.combined_pane, weight=2)
        except Exception:
            try:
                self.combined_pane.pack(side='left', fill='y')
            except Exception:
                pass
    def _first_term(self):
        """取搜索词里第一个有效词（供打开文件时高亮定位）"""
        kw = self.keyword.get().strip()
        for part in kw.replace('|', ' ').replace('｜', ' ').split():
            p = part.strip()
            if not p or p.upper() in ('OR', 'AND', 'NOT') or p.startswith('-'):
                continue
            return p
        return kw
    def _open_text_at_line(self, path, line_no):
        """文本类：直接用默认程序打开（记事本不支持 /g 跳行）"""
        try:
            os.startfile(path)
            return True
        except Exception:
            try:
                subprocess.Popen(['notepad', path])
                return True
            except Exception:
                return False
    def _open_located(self, path, line_no):
        """打开文件并尽量定位到命中位置：
            docx/doc → Word 全部高亮+跳转；PDF → 按页码翻页（Adobe 还高亮）；
            文本类 → 记事本跳行；其余 → 默认程序。"""
        if self._bg_mode:
            self._restore_from_tray()
        try:
            norm = os.path.normcase(os.path.abspath(path))
        except Exception:
            norm = path
        now = time.time()
        last = getattr(self, '_last_open_file', None)
        if last and last[0] == norm and (now - last[1] < 1.0):
            return
        self._last_open_file = (norm, now)
        kw = self._first_term()
        ext = os.path.splitext(path)[1].lower()
        full_line = get_line_text(DB_PATH, path, line_no) if line_no else ''
        page = extract_page(full_line)
        located = ''
        try:
            if ext in ('.docx', '.doc') and kw:
                if self._open_word_highlight(path, kw):
                    located = f' · 已高亮「{kw}」并跳转'
                else:
                    os.startfile(path)
            elif ext == '.pdf' and page:
                self._open_pdf_located(path, int(page), kw or '')
                located = f' · 已跳到第 {page} 页'
                if kw and (self._find_sumatra() or self._find_acrobat()):
                    located = f' · 已跳到第 {page} 页并高亮「{kw}」'
            elif ext == '.pdf' and (not page):
                if kw and self._find_sumatra():
                    subprocess.Popen([self._find_sumatra(), '-search', kw, path])
                    located = f' · 已搜索「{kw}」'
                else:
                    os.startfile(path)
            elif ext in ('.xlsx', '.xls') and line_no:
                xl_loc = parse_excel_loc(full_line)
                if xl_loc and self._open_excel_located(path, xl_loc[0], xl_loc[1], kw):
                    located = f' · 已定位到 {xl_loc[0]}!{xl_loc[1]}'
                    if kw:
                        located += f' 并高亮「{kw}」'
                else:
                    os.startfile(path)
            elif ext in ('.pptx', '.ppt') and page:
                if self._open_ppt_located(path, page, kw):
                    located = f' · 已跳到第 {page} 张幻灯片'
                else:
                    os.startfile(path)
            elif line_no and ext in ('.txt', '.md', '.csv', '.json', '.log', '.xml', '.html', '.htm', '.ini', '.bat', '.py', '.js', '.css'):
                if self._open_text_at_line(path, line_no):
                    located = f' · 已跳到第 {line_no} 行'
                else:
                    os.startfile(path)
            else:
                os.startfile(path)
        except Exception:
            try:
                if sys.platform.startswith('win'):
                    os.startfile(path)
                elif sys.platform == 'darwin':
                    subprocess.Popen(['open', path])
                else:
                    subprocess.Popen(['xdg-open', path])
            except Exception:
                pass
        self.status.config(text=f'已打开: {os.path.basename(path)}{located}')
    def _remove_keyword(self, kw):
        """删除单条搜索历史"""
        recent = self.cfg.get('recent_keywords', [])
        if kw in recent:
            recent.remove(kw)
            self.cfg['recent_keywords'] = recent
            self.kw_entry['values'] = recent
            self._save_config()
            self.status.configure(text=f'已删除搜索历史「{kw[:20]}」')
    def _clear_keywords(self):
        """清除全部搜索历史"""
        if not self.cfg.get('recent_keywords'):
            return
        if messagebox.askyesno('确认', '清除全部搜索历史？'):
            self.cfg['recent_keywords'] = []
            self.kw_entry['values'] = []
            self._save_config()
            self.status.configure(text='已清除全部搜索历史')
    def _hist_on_enter(self, *_a):
        """指针回到下拉范围内：取消待收回"""
        t = getattr(self, '_hist_timer', None)
        if t is not None:
            try:
                self.root.after_cancel(t)
            except Exception:
                pass
            self._hist_timer = None
    def _hist_on_leave(self, *_a):
        """指针离开下拉：延时复查后再决定收不收"""
        self._hist_on_enter()
        self._hist_timer = self.root.after(140, self._hist_close_if_out)
    def _theme_colors(self):
        """亮/暗两套自定义控件配色（sv_ttk 管不到的 tk.Text 和行标签）"""
        if self.dark.get():
            return dict(preview_bg='#1E1E1E', preview_fg='#CFCFCF', preview_sel='#3A5A8C', hl_bg='#8F7500', hl_fg='#FFFFFF', chap_fg='#5CB3FF', meta_fg='#999999', hit_bg='#5A4A18', hit_fg='#EFE6C0', fuzzymeta_fg='#D4A017', fhl_bg='#1E5B30', fhl_fg='#B8F0C2', syn_bg='#8A5200', syn_fg='#FFD9A8', reflink_fg='#5CB3FF', refmiss_fg='#777777', refdead_fg='#FF7B7B', weblink_fg='#5CB3FF', tbl_head_bg='#2D3A4A', tbl_head_fg='#FFFFFF', tbl_row_bg='#262626', tbl_alt_bg='#2A2A2A', tbl_hit_bg='#5A4A18', tbl_hit_fg='#EFE6C0', tbl_sep_fg='#555555', tbl_sep_bg='#1E1E1E', tree_file_bg='#333438', tree_file_fg='#9CC4F0', tree_line_bg='#252629', tree_odd_bg='#2D2E31', tree_fuzzy_bg='#453A1D', tree_fuzzy_fg='#F0DFAE', void_fg='#808080', replaced_fg='#C9A87C', missing_fg='#FF6B6B', notext_fg='#E0A83C', qa_bg='#3E76B0', qa_active='#4E88C4', qa_fg='#FFFFFF')
        return dict(preview_bg='#FCFDFE', preview_fg='#1B2632', preview_sel='#D6E3F0', hl_bg='#FBDCD2', hl_fg='#9C1F10', chap_fg='#2F5E8C', meta_fg='#7B8694', hit_bg='#FBE3DC', hit_fg='#6B2014', fuzzymeta_fg='#C05621', fhl_bg='#D6EFDC', fhl_fg='#14532D', syn_bg='#FBE5C8', syn_fg='#7A4A10', reflink_fg='#2F5E8C', refmiss_fg='#9AA3AE', refdead_fg='#C13A28', weblink_fg='#2F5E8C', tbl_head_bg='#E9EFF5', tbl_head_fg='#1B2632', tbl_row_bg='#FFFFFF', tbl_alt_bg='#F7F9FB', tbl_hit_bg='#FBE3DC', tbl_hit_fg='#6B2014', tbl_sep_fg='#C0C8D1', tbl_sep_bg='#F0F2F5', tree_file_bg='#EAF0F6', tree_file_fg='#244C78', tree_line_bg='#FCFDFE', tree_odd_bg='#F7F9FB', tree_fuzzy_bg='#FBEDE7', tree_fuzzy_fg='#C05621', void_fg='#98A1AC', replaced_fg='#9C7C3C', missing_fg='#C13A28', notext_fg='#C05621', qa_bg='#2F5E8C', qa_active='#3A6FA2', qa_fg='#FFFFFF')
    def _apply_ui_theme(self):
        """切换亮/暗主题：sv_ttk + 自定义标签色（标签改配置即时生效，无需重刷）"""
        if _SVTTK:
            sv_ttk.set_theme('dark' if self.dark.get() else 'light')
        try:
            self._configure_control_styles()
        except Exception:
            pass
        c = self._theme_colors()
        try:
            self._cap_paint()
        except Exception:
            pass
        try:
            self._refresh_dialog_headers()
        except Exception:
            pass
        _qw = getattr(self, '_qa_window', None)
        if _qw is not None:
            try:
                _qw.apply_theme()
            except Exception:
                pass
        qb = getattr(self, 'qa_btn', None)
        if qb is not None:
            qb.configure(background=c['qa_bg'], foreground=c['qa_fg'], activebackground=c['qa_active'], activeforeground=c['qa_fg'])
        self.tree.tag_configure('file', background=c['tree_file_bg'], foreground=c['tree_file_fg'], font=self.FONT_UI_B)
        self.tree.tag_configure('line', background=c['tree_line_bg'])
        self.tree.tag_configure('odd', background=c['tree_odd_bg'])
        self.tree.tag_configure('fuzzyline', background=c['tree_fuzzy_bg'], foreground=c['tree_fuzzy_fg'])
        self.tree.tag_configure('crossline', foreground=self.palette()['brand'])
        self.tree.tag_configure('void', foreground=c['void_fg'])
        self.tree.tag_configure('replaced', foreground=c['replaced_fg'])
        ct = getattr(self, 'chap_tree', None)
        if ct is not None:
            ct.tag_configure('lv1', font=self.FONT_UI_B, foreground=c['chap_fg'])
            ct.tag_configure('lv2', font=self.FONT_UI)
            ct.tag_configure('lv3', font=self.FONT_HINT, foreground=c['meta_fg'])
            ct.tag_configure('ph', foreground=c['meta_fg'], font=self.FONT_HINT)
        lw = getattr(self, '_lib_win', None)
        if lw is not None and lw.winfo_exists():
            lt = getattr(lw, '_tree', None)
            if lt is not None:
                lt.tag_configure('missing', foreground=c['missing_fg'])
                lt.tag_configure('notext', foreground=c['notext_fg'])
                lt.tag_configure('void', foreground=c['void_fg'])
                lt.tag_configure('replaced', foreground=c['replaced_fg'])
        self.preview.configure(background=c['preview_bg'], foreground=c['preview_fg'], selectbackground=c['preview_sel'], insertbackground=c['preview_fg'])
        try:
            self._preview_ph.configure(bg=c['preview_bg'], fg=c['meta_fg'])
        except Exception:
            pass
        self.preview.tag_configure('hl', background=c['hl_bg'], foreground=c['hl_fg'])
        self.preview.tag_configure('chap', foreground=c['chap_fg'])
        self.preview.tag_configure('meta', foreground=c['meta_fg'])
        self.preview.tag_configure('hit', background=c['hit_bg'], foreground=c['hit_fg'])
        self.preview.tag_configure('fuzzymeta', foreground=c['fuzzymeta_fg'])
        self.preview.tag_configure('fhl', background=c['fhl_bg'], foreground=c['fhl_fg'])
        self.preview.tag_configure('syn', background=c['syn_bg'], foreground=c['syn_fg'])
        self.preview.tag_configure('reflink', foreground=c['reflink_fg'])
        self.preview.tag_configure('refmiss', foreground=c['refmiss_fg'])
        self.preview.tag_configure('refdead', foreground=c['refdead_fg'])
        self.preview.tag_configure('weblink', foreground=c['weblink_fg'])
        self.preview.tag_configure('tbl_header', background=c['tbl_head_bg'], foreground=c['tbl_head_fg'])
        self.preview.tag_configure('tbl_row', background=c['tbl_row_bg'])
        self.preview.tag_configure('tbl_row_alt', background=c['tbl_alt_bg'])
        self.preview.tag_configure('tbl_hit', background=c['tbl_hit_bg'], foreground=c['tbl_hit_fg'])
        self._draw_distribution_chart()
        if hasattr(self, 'guide') and self.guide.winfo_ismapped():
            fl = getattr(self, '_guide_float', None)
            if fl is not None:
                fl.stop()
                self._guide_float = None
            self.guide.destroy()
            del self.guide
            self.root.after(60, self._show_guide)
        self.cfg['dark'] = bool(self.dark.get())
        self._save_config()
    def _preview_zoom(self, evt):
        """Ctrl+滚轮 缩放预览正文字号（8-26，记忆到配置）"""
        step = 1 if evt.delta > 0 else -1
        size = max(8, min(26, self._pfont_size + step))
        if size != self._pfont_size:
            self._pfont_size = size
            self._pfont.configure(size=size)
            self.cfg['preview_font'] = size
            self.status.configure(text=f'预览字号: {size}')
        return 'break'
    def _on_health_done(self, win, missing):
        if not win.winfo_exists():
            return
        win._missing = missing
        tree = win._tree
        yv = tree.yview()
        sel = tree.selection()
        self._fill_library_tree(tree, getattr(win, '_cat_filter', ''))
        try:
            win._info.config(text=self._library_info_text(win, library_stats(DB_PATH)))
        except Exception:
            pass
        try:
            tree.yview_moveto(yv[0])
            tree.selection_set([s for s in sel if tree.exists(s)])
        except tk.TclError:
            pass
    def _cleanup_missing(self, win):
        """清理磁盘已不存在的失效索引"""
        missing = [it['path'] for it in list_library(DB_PATH) if not os.path.exists(it['path'])]
        if not missing:
            messagebox.showinfo('提示', '没有发现失效索引，所有文件均在位。', parent=win)
            return
        if not messagebox.askyesno('清理失效索引', f'发现 {len(missing)} 个文件在磁盘上已不存在（被移动或删除）。\n仅移除它们的索引记录，不会删除任何磁盘文件。\n\n是否清理？', parent=win):
            return
        n = 0
        for p in missing:
            if remove_from_library(DB_PATH, p, delete_file=False) == 'ok':
                n += 1
        self._refresh_library_win(win)
        self._start_health_check(win)
        self.status.configure(text=f'已清理 {n} 条失效索引')
    def _rename_selected(self, win, tree, path):
        """右键重命名：改磁盘文件名 + 同步库路径（不重建索引，搜索不受影响）"""
        old_name = os.path.basename(path)
        stem, ext = os.path.splitext(old_name)
        new_stem = simpledialog.askstring('重命名文件', '输入新的文件名（扩展名保持不变）：', initialvalue=stem, parent=win)
        if new_stem is None:
            return
        new_stem = new_stem.strip()
        if not new_stem or new_stem == stem:
            return
        new_name = new_stem + ext
        rc = rename_in_library(DB_PATH, path, new_name)
        if rc == 'ok':
            self._refresh_library_win(win)
            self.status.configure(text=f'已重命名为：{new_name}（索引无需重建）')
        elif rc == 'exists':
            messagebox.showerror('重命名失败', f'同目录下已存在「{new_name}」，请换个名字。', parent=win)
        elif rc == 'invalid':
            messagebox.showerror('重命名失败', '文件名不能为空，且不能包含 \\ / : * ? " < > | 等字符。', parent=win)
        elif rc.startswith('error'):
            messagebox.showerror('重命名失败', rc, parent=win)
    def _toggle_multi_select(self, win, tree, btn, selall_btn=None, invert_btn=None):
        """切换多选模式：进入时所有行显示☐，退出时清除勾选与辅助按钮"""
        if getattr(win, '_multi_mode', False):
            win._multi_mode = False
            if btn:
                btn.configure(text='多选模式')
            win._checked = set()
            for iid in tree.get_children():
                tree.set(iid, 'sel', '')
            tree.configure(selectmode='extended')
            if selall_btn:
                selall_btn.pack_forget()
                invert_btn.pack_forget()
        else:
            win._multi_mode = True
            if btn:
                btn.configure(text='退出多选')
            win._checked = set()
            for iid in tree.get_children():
                tree.set(iid, 'sel', '☐')
            tree.configure(selectmode='none')
            if selall_btn:
                invert_btn.pack(side='right', padx=4)
                selall_btn.pack(side='right', padx=4)
    def _multi_invert(self, win, tree):
        """多选模式反选当前列表（不影响其他分类的勾选）"""
        if not getattr(win, '_multi_mode', False):
            return
        for iid in tree.get_children():
            if iid in win._checked:
                win._checked.discard(iid)
                tree.set(iid, 'sel', '☐')
            else:
                win._checked.add(iid)
                tree.set(iid, 'sel', '☑')
    def _delete_selected(self, win, tree):
        """删除选中文件：多选模式取☑勾选（跨分类保留），普通模式取选中行"""
        if getattr(win, '_multi_mode', False):
            sel = [p for p in win._checked if tree.exists(p)]
        else:
            sel = tree.selection()
        if not sel:
            messagebox.showinfo('提示', '请先选择要删除的文件', parent=win)
            return
        self._del_from_lib(win, tree, True, sel)
    def _category_names(self):
        """当前分类名列表（按规则顺序，末尾固定追加 10-其他资料）"""
        try:
            names = [str(r[0]).strip() for r in get_category_rules(DB_PATH) if r and str(r[0]).strip()]
        except Exception:
            names = []
        if OTHER_CATEGORY not in names:
            names.append(OTHER_CATEGORY)
        return names
    def _update_lib_headings(self, win, tree):
        """列头显示当前排序方向箭头"""
        sort_col = getattr(win, '_sort_col', 'mtime')
        rev = getattr(win, '_sort_rev', True)
        for c, txt in self._LIB_HEAD_TEXT.items():
            arrow = (' ▼' if rev else ' ▲') if c == sort_col else ''
            tree.heading(c, text=txt + arrow)
    def _sort_library_tree(self, win, tree, col):
        """点列头：同列切换升降序，新列默认升序（时间/大小默认降序）"""
        if getattr(win, '_sort_col', 'mtime') == col:
            win._sort_rev = not win._sort_rev
        else:
            win._sort_col = col
            win._sort_rev = col in ('size', 'lines', 'mtime')
        self._fill_library_tree(tree, getattr(win, '_cat_filter', ''))
    def _fill_library_tree(self, tree, cat_filter=''):
        items = list_library(DB_PATH)
        win_top = tree.winfo_toplevel()
        missing = getattr(win_top, '_missing', set())
        terms = [t for t in (getattr(win_top, '_name_filter', '') or '').lower().split() if t]
        filtered = []
        for it in items:
            cat = it.get('category', '') or ''
            if cat_filter == '__none__' and cat:
                continue
            if cat_filter and cat_filter != '__none__' and (cat != cat_filter):
                continue
            if terms:
                hay = (it['name'] + ' ' + (it.get('doc_no') or '')).lower()
                if not all((t in hay for t in terms)):
                    continue
            filtered.append(it)
        sort_col = getattr(win_top, '_sort_col', 'mtime')
        rev = getattr(win_top, '_sort_rev', True)
        try:
            filtered.sort(key=lambda it: self._lib_sort_key(it, sort_col), reverse=rev)
        except Exception:
            pass
        tree.delete(*tree.get_children())
        status_lbl = {'active': '有效', 'void': '废止', 'replaced': '已替代'}
        for it in filtered:
            cat = it.get('category', '') or ''
            st = it.get('status', 'active')
            name_disp = it['name']
            if it['path'] in missing:
                tags = ('missing',)
                name_disp += f'  {self.IC_WARN}文件缺失'
            elif (it['line_count'] or 0) == 0:
                tags = ('notext',)
                name_disp += f'  {self.IC_WARN}无文本'
            elif st == 'void':
                tags = ('void',)
            elif st == 'replaced':
                tags = ('replaced',)
            else:
                tags = ()
            tree.insert('', 'end', iid=it['path'], tags=tags, values=('', name_disp, it['ext'], it['ftype'], fmt_size(it['size'] or 0), it['line_count'] or 0, status_lbl.get(st, st), it.get('doc_no', '') or '', cat, time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(it['mtime'])) if it['mtime'] else '', it['path']))
        self._update_lib_headings(win_top, tree)
        if getattr(win_top, '_multi_mode', False):
            all_paths = {it['path'] for it in items}
            win_top._checked &= all_paths
            for iid in tree.get_children():
                tree.set(iid, 'sel', '☑' if iid in win_top._checked else '☐')
    def _refresh_library_win(self, win):
        stats = library_stats(DB_PATH)
        win._info.config(text=self._library_info_text(win, stats))
        if hasattr(win, '_cat_tree'):
            self._fill_cat_tree(win)
        self._fill_library_tree(win._tree, getattr(win, '_cat_filter', ''))
        if hasattr(win, '_tree'):
            self._start_health_check(win)
    def _check_uncategorized(self, paths):
        """入库完成后：筛出自动归类失败的文件，弹窗让用户选分类"""
        if not paths or not os.path.exists(DB_PATH):
            return
        uncat = []
        for p in paths:
            try:
                if not get_metadata(DB_PATH, p).get('category'):
                    uncat.append(p)
            except Exception:
                pass
        if uncat:
            self._ask_categories_popup(uncat)
    def _del_from_lib(self, win, tree, delete_file, paths=None):
        """从库删除选中项（支持多选批量）

            paths 非空时直接使用，否则取 tree.selection()。
            """
        sel = paths if paths is not None else tree.selection()
        if not sel:
            return
        msg = f'确认删除 {len(sel)} 个文件？\n' + ('此操作会将文件移入回收站（可恢复）！' if delete_file else '仅从索引删除，磁盘文件保留。')
        if len(sel) <= 5:
            msg += '\n\n' + '\n'.join(('· ' + os.path.basename(p) for p in sel))
        if not messagebox.askyesno('确认删除', msg, icon='warning'):
            return
        failed = []
        for path in sel:
            status = remove_from_library(DB_PATH, path, delete_file=delete_file)
            if status != 'ok':
                failed.append(f'{os.path.basename(path)}: {status}')
        if failed:
            messagebox.showwarning('部分失败', f'{len(sel) - len(failed)} 个成功，{len(failed)} 个失败:\n' + '\n'.join(failed))
        self._refresh_library_win(win)
        self._refresh_err_count()
    def _show_missing_file(self, win, path):
        """文件不在原位置：提示并可一键移除失效索引"""
        if messagebox.askyesno('文件不在原位置', f'文件已被移动或删除，无法打开：\n{path}\n\n「是」从索引中移除此条记录（不删除任何文件）\n「否」保留记录', parent=win):
            remove_from_library(DB_PATH, path, delete_file=False)
            if win.winfo_exists() and hasattr(win, '_tree'):
                self._refresh_library_win(win)
                self._start_health_check(win)
            self.status.configure(text=f'已移除失效索引: {os.path.basename(path)}')
    def _open_in_explorer(self, tree):
        sel = tree.selection()
        if not sel:
            return
        path = sel[0]
        if not os.path.exists(path):
            self._show_missing_file(tree.winfo_toplevel(), path)
            return
        if sys.platform.startswith('win'):
            subprocess.Popen(['explorer', '/select,', path])
        elif sys.platform == 'darwin':
            subprocess.Popen(['open', '-R', path])
        else:
            subprocess.Popen(['xdg-open', os.path.dirname(path)])
    def _pkg_default_dir(self):
        d = os.path.join(os.path.expanduser('~'), 'Desktop')
        return d if os.path.isdir(d) else APP_DIR
    def _pkg_done(self, pw, r):
        pw.destroy()
        mb = os.path.getsize(r['zip_path']) / 1024 / 1024
        skip_note = ''
        if r.get('skipped'):
            skip_note = f'\n{self.IC_WARN} 有 %d 个文件当时被占用未能打入（包内有 SKIPPED.txt 清单），可关闭占用后重打。\n' % len(r['skipped'])
        if messagebox.askyesno('打包完成', '库已打包完成：\n\n· 文件数：%d 个\n· 文档记录：%d 篇\n· 压缩包大小：%.0f MB\n%s\n把这个 zip 拷到新电脑，在软件里选「从压缩包还原库」即可。\n\n打开压缩包所在文件夹吗？' % (r['file_count'], r['doc_count'], mb, skip_note)):
            try:
                os.startfile(os.path.dirname(r['zip_path']))
            except Exception:
                pass
    def _pkg_fail(self, pw, zp, e):
        pw.destroy()
        try:
            if zp and os.path.exists(zp):
                os.remove(zp)
        except OSError:
            pass
        messagebox.showerror('打包失败', str(e) or '未知错误')
    def _restore_fail(self, pw, e):
        pw.destroy()
        messagebox.showerror('还原失败', str(e) or '未知错误')
    def _open_library_dir(self):
        """打开 library 目录"""
        if not os.path.exists(LIBRARY_DIR):
            os.makedirs(LIBRARY_DIR, exist_ok=True)
        if sys.platform.startswith('win'):
            os.startfile(LIBRARY_DIR)
        elif sys.platform == 'darwin':
            subprocess.Popen(['open', LIBRARY_DIR])
        else:
            subprocess.Popen(['xdg-open', LIBRARY_DIR])
    def show_errors(self):
        if not os.path.exists(DB_PATH):
            messagebox.showinfo('提示', '索引库不存在')
            return
        errors = get_errors(DB_PATH)
        if not errors:
            messagebox.showinfo('错误日志', '无错误记录')
            return
        existed = 'errors' in self._wins
        win = self._get_win('errors')
        if win is None:
            return
        if existed:
            self._refresh_errors_win(win)
            return
        win.title(f'错误日志（共 {len(errors)} 条）')
        self._center_win(win, 1000, 560, modal=False)
        bar = ttk.Frame(win, padding=6)
        bar.pack(fill='x')
        ttk.Button(bar, text='刷新', command=lambda: self._refresh_errors_win(win)).pack(side='left')
        ttk.Button(bar, text='清空', command=lambda: self._clear_errors_win(win)).pack(side='left', padx=4)
        info = ttk.Label(bar, text=f'共 {len(errors)} 条 · 双击查看完整堆栈')
        info.pack(side='left', padx=12)
        body = ttk.Frame(win)
        body.pack(fill='both', expand=True)
        cols = ('path', 'stage', 'error', 'time')
        tree = ttk.Treeview(body, columns=cols, show='headings')
        tree.heading('path', text='文件路径')
        tree.heading('stage', text='阶段')
        tree.heading('error', text='错误信息')
        tree.heading('time', text='时间')
        tree.column('path', width=360)
        tree.column('stage', width=80, anchor='center')
        tree.column('error', width=400)
        tree.column('time', width=140, anchor='center')
        vsb = ttk.Scrollbar(body, orient='vertical', command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        tree.pack(side='left', fill='both', expand=True)
        vsb.pack(side='right', fill='y')
        tree.bind('<Double-1>', lambda e: self._show_traceback(win))
        win._tree = tree
        win._info = info
        self._fill_errors_tree(tree, errors)
    def _fill_errors_tree(self, tree, errors):
        tree.delete(*tree.get_children())
        for e in errors:
            tree.insert('', 'end', iid=str(e['id']), values=(e['path'], e['stage'], e['error'], time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(e['ts']))))
    def _refresh_errors_win(self, win):
        errors = get_errors(DB_PATH)
        self._fill_errors_tree(win._tree, errors)
        win._info.config(text=f'共 {len(errors)} 条 · 双击查看完整堆栈')
        win.title(f'错误日志（共 {len(errors)} 条）')
    def _clear_errors_win(self, win):
        if not messagebox.askyesno('确认', '清空所有错误日志？'):
            return
        clear_errors(DB_PATH)
        self._refresh_errors_win(win)
        self._refresh_err_count()
    def _show_traceback(self, win):
        sel = win._tree.selection()
        if not sel:
            return
        eid = int(sel[0])
        tb = get_error_traceback(DB_PATH, eid)
        if not tb:
            return
        sub = tk.Toplevel(win)
        sub.title(f'错误 #{eid} 完整堆栈')
        self._center_win(sub, 800, 548)
        self._dialog_header(sub, self.IC_WARN, f'错误 #{eid} 完整堆栈')
        txt = tk.Text(sub, wrap='none')
        txt.pack(fill='both', expand=True)
        txt.insert('1.0', tb)
        txt.config(state='disabled')
        sb = ttk.Scrollbar(sub, orient='vertical', command=txt.yview)
        txt.configure(yscrollcommand=sb.set)
        sb.pack(side='right', fill='y')
