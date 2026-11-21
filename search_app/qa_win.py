# -*- coding: utf-8 -*-
"""T9 GUI：离线问答窗（ttk，深色模式随主窗）。

- 环境检测/一键拉起 Ollama、模型设置、语义索引构建（进度+取消）
- 流式答案 + 出处卡片（点击复用主窗 _open_located 定位原文）
- 全程仅与本机 Ollama 通信；Ollama 缺失时显示引导而非崩溃
"""
import os
import re
import threading
import queue
import time
import webbrowser

import tkinter as tk
from tkinter import ttk, messagebox, filedialog

import ai_qa

OLLAMA_URL = 'https://ollama.com/download'

# 模型设置下拉推荐（全部免费开源，可编辑，自动合并本机已装模型）
CHAT_SUGGEST = [
    ('qwen2.5:7b-instruct', '通义千问2.5 7B，默认均衡'),
    ('qwen2.5:3b-instruct', '通义千问2.5 3B，低配机器'),
    ('qwen2.5:14b-instruct', '通义千问2.5 14B，高配机器'),
    ('qwen3:8b', '通义千问3 8B'),
    ('deepseek-r1:8b', '深度求索R1 8B 推理'),
    ('llama3.1:8b', ''),
]
EMBED_SUGGEST = [
    ('bge-m3', '智源BGE-M3 向量，默认'),
    ('nomic-embed-text', ''),
]

# 常见开源模型家族中文名（前缀匹配，用于给本机已装模型自动标注）
MODEL_ZH = [
    ('qwen', '通义千问'), ('deepseek', '深度求索'), ('glm', '智谱GLM'),
    ('chatglm', '智谱GLM'), ('bge', '智源BGE'), ('bce', '网易BCE'),
    ('gemma', '谷歌Gemma'), ('phi', '微软Phi'), ('yi-', '零一万物'),
    ('minicpm', '面壁MiniCPM'), ('internlm', '书生浦语'),
    ('baichuan', '百川'), ('moonshot', '月之暗面'), ('hunyuan', '腾讯混元'),
]


def zh_of(model_name):
    low = (model_name or '').lower()
    for p, zh in MODEL_ZH:
        if low.startswith(p):
            return zh
    return ''


def _disp(item):
    """('name', '中文名') 或 'name' → 显示文本"""
    name, zh = (item, zh_of(item)) if isinstance(item, str) else item
    return f'{name}（{zh}）' if zh else name


def _pure(text):
    """剥掉尾部中文标注括号，还原纯模型名"""
    return re.sub(r'（[^（）]*）\s*$', '', (text or '').strip()).strip()


class QAWindow:
    def __init__(self, app):
        self.app = app
        self.cfg = app.cfg
        self.q = queue.Queue()
        self._build_cancel = threading.Event()
        self._hits = []
        self._building = False
        self._asking = False
        self._stop_ask = threading.Event()   # 停止生成
        self._ask_id = 0                     # 问答代次：停止/重问即作废旧流程
        self._gen = None                     # 当前生成器引用（强制断开用）
        self._gen_eta = None                 # 预计生成秒数（生成阶段显示）
        self._gen_t0 = 0.0                   # 本次生成开始时间戳
        self._last_query = ''                # 最近一次问题（供重新生成）
        self._last_search_q = ''           # 最近一次有效检索式（供轮次记录）
        self._last_hits = []                 # 最近一次检索结果（供重新生成）
        self._ctx_hits = None                # 搜索上下文（RAG 联动；None=全库检索）
        self._qa_turns = []                  # 最近问答轮次（供多轮上下文）
        # 问答记录保存目录（跟随主库 DB_PATH 所在目录，而非源码目录）
        try:
            _db_root = os.path.dirname(self.app._qa_db())
        except Exception:
            _db_root = os.path.dirname(
                os.path.dirname(os.path.abspath(__file__)))
        self._qa_dir = os.path.join(_db_root, 'qa_history')
        os.makedirs(self._qa_dir, exist_ok=True)

        w = tk.Toplevel(app.root)
        self.win = w
        w.title('AI 镜仔智答 · 本机离线（Ollama）')
        w.minsize(680, 560)
        w.transient(app.root)
        w.protocol('WM_DELETE_WINDOW', self._on_close)
        self._apply_geometry()
        # 几何记忆：拖动/缩放实时写入内存，销毁时落盘。
        # 不复用 _on_close——主程序 show_qa_window 把关闭协议改成了裸 destroy。
        w.bind('<Configure>', self._on_geo_change, add='+')
        w.bind('<Destroy>', self._on_geo_save, add='+')
        self._build_ui()
        self._poll()
        self.check_env()

    def _apply_geometry(self):
        """恢复上次的大小与位置；无记录则居中 820x680"""
        w = self.win
        m = re.match(r'(\d+)x(\d+)\+(-?\d+)\+(-?\d+)',
                     str(self.app.cfg.get('qa_geo') or ''))
        if m:
            gw, gh, gx, gy = (int(v) for v in m.groups())
            w.geometry(f'{max(gw, 680)}x{max(gh, 560)}+{gx}+{gy}')
            return
        w.update_idletasks()
        sw, sh = w.winfo_screenwidth(), w.winfo_screenheight()
        x, y = max((sw - 820) // 2, 0), max((sh - 680) // 2, 0)
        w.geometry(f'820x680+{x}+{y}')

    def _on_geo_change(self, evt):
        if evt.widget is not self.win:
            return
        try:
            ww, hh = self.win.winfo_width(), self.win.winfo_height()
            if ww <= 1 or hh <= 1:
                return
            self.app.cfg['qa_geo'] = (
                f'{ww}x{hh}+{self.win.winfo_x()}+{self.win.winfo_y()}')
        except Exception:
            pass

    def _on_geo_save(self, evt):
        if evt.widget is not self.win:
            return
        try:
            self.app._save_config()
        except Exception:
            pass

    def set_search_context(self, hits, kw, total_files):
        """主界面搜索结果联动：后续提问只基于这些命中片段回答。
        hits: build_messages 兼容格式（name/status/score/text…）"""
        self._ctx_hits = hits
        n = len(hits)
        extra = f'（共 {total_files} 个，已截取前 {n} 个）' \
            if total_files > n else ''
        self._ctx_lbl.configure(
            text=f'基于搜索「{kw[:24]}」· {n} 个文件{extra}'
                 f'——提问将只依据这些结果回答')
        self._ctx_bar.pack(fill='x', before=self.qf)
        self.q.put(('sys', f'已载入搜索上下文：{n} 个文件\n'))

    def _clear_search_context(self):
        """清除搜索上下文，恢复全库语义检索"""
        if self._ctx_hits is None:
            return
        self._ctx_hits = None
        self._ctx_bar.pack_forget()
        self.q.put(('sys', '已恢复全库检索模式\n'))

    def _on_close(self):
        """关窗即作废进行中的问答并断开 Ollama，避免生成槽被占"""
        self._cursor_on = False
        job = getattr(self, '_cursor_job', None)
        if job is not None:
            try:
                self.win.after_cancel(job)
            except Exception:
                pass
        rj = getattr(self, '_reflow_job', None)
        if rj is not None:
            try:
                self.win.after_cancel(rj)
            except Exception:
                pass
        self._stop_ask.set()
        self._ask_id += 1
        g = self._gen
        self._gen = None
        if g is not None:
            try:
                g.close()   # 断开 HTTP → Ollama 取消生成、释放生成槽
            except Exception:
                pass
        self.win.destroy()

    def _center(self, win, w, h):
        """先构建子窗，再在事件循环显示；不在建窗中重入布局队列。"""
        try:
            x = (win.winfo_screenwidth() - w) // 2
            y = (win.winfo_screenheight() - h) // 2
            win.geometry(f'{w}x{h}+{max(x, 0)}+{max(y, 0)}')
        except Exception:
            win.geometry(f'{w}x{h}')
        try:
            self._apply_modal(win)
        except Exception:
            pass

    # ---------- 弹窗等级模态（与主界面同规则） ----------

    def _apply_modal(self, dlg):
        """使用 Tk 的窗口抓取管理模态关系，不向父窗插入遮罩。"""
        if not hasattr(self, '_modal_state'):
            self._modal_state = {}
            self._modal_overlays = {}
        previous = dlg.grab_current()
        origin = self.win.focus_get()
        def show():
            if not dlg.winfo_exists():
                return
            dlg.deiconify()
            dlg.attributes('-alpha', 1.0)
            dlg.lift()
            dlg.grab_set()
            dlg.focus_set()
        def release(event):
            if event.widget is not dlg:
                return
            try:
                if previous is not None and previous.winfo_exists():
                    previous.grab_set()
                if origin is not None and origin.winfo_exists():
                    origin.focus_set()
            except tk.TclError:
                pass
        dlg.bind('<Destroy>', release, add='+')
        dlg.bind('<Escape>', lambda _: dlg.destroy())
        dlg.after_idle(show)

    def _deny_close(self, p, dlg):
        p.bell()
        try:
            dlg.lift()
            dlg.focus_force()
        except Exception:
            pass

    def _release_modal(self, event):
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
                            p.tk.call('wm', 'protocol', str(p),
                                      'WM_DELETE_WINDOW', old)
                        else:
                            p.protocol('WM_DELETE_WINDOW', p.destroy)
                    except Exception:
                        pass
                    self._modal_state.pop(p, None)

    # ---------- UI ----------

    def _build_ui(self):
        w = self.win
        # ---------- 顶部紧凑状态条（可折叠展开设置面板） ----------
        top = ttk.Frame(w, padding=(10, 6))
        top.pack(fill='x')
        self.env_lbl = ttk.Label(top, text='Ollama：检测中…')
        self.env_lbl.pack(side='left')
        ttk.Button(top, text='设置…', command=self._toggle_setup).pack(
            side='right')
        self.idx_lbl = ttk.Label(top, text='', foreground='gray')
        self.idx_lbl.pack(side='right', padx=(0, 10))

        # 搜索上下文横幅（默认隐藏；主界面「AI 分析这些结果」联动时显示）
        self._ctx_bar = tk.Frame(w, bg='#2F5E8C')
        self._ctx_lbl = tk.Label(
            self._ctx_bar, text='', bg='#2F5E8C', fg='#FFFFFF',
            font=('Microsoft YaHei UI', 9), anchor='w', padx=10, pady=4)
        self._ctx_lbl.pack(side='left', fill='x', expand=True)
        _ctx_x = tk.Label(self._ctx_bar, text='✕', bg='#2F5E8C',
                          fg='#FFFFFF', cursor='hand2', padx=10,
                          font=('Microsoft YaHei UI', 9))
        _ctx_x.pack(side='right')
        _ctx_x.bind('<Button-1>', lambda _e: self._clear_search_context())

        # 可折叠设置面板（Ollama + 语义索引），默认收起
        self._setup_visible = False
        self.setup_frame = ttk.LabelFrame(w, text='环境与索引', padding=(10, 6))
        row = ttk.Frame(self.setup_frame)
        row.pack(fill='x')
        ttk.Button(row, text='重新检测 Ollama',
                   command=self.check_env).pack(side='left')
        ttk.Button(row, text='启动本机 Ollama',
                   command=self.launch_ollama).pack(side='left', padx=(6, 0))
        ttk.Button(row, text='模型设置…',
                   command=self.show_settings).pack(side='right')
        row2 = ttk.Frame(self.setup_frame)
        row2.pack(fill='x', pady=(8, 0))
        self.build_btn = ttk.Button(row2, text='构建 / 更新语义索引',
                                    style='Accent.TButton',
                                    command=self.start_build)
        self.build_btn.pack(side='left')
        self.cancel_btn = ttk.Button(row2, text='取消', state='disabled',
                                     command=self.cancel_build)
        self.cancel_btn.pack(side='left', padx=(6, 0))
        self.pbar = ttk.Progressbar(row2, mode='determinate', length=200)
        self.pbar.pack(side='left', padx=(12, 0), fill='x', expand=True)
        self.pct_lbl = ttk.Label(row2, text='', width=22, anchor='e')
        self.pct_lbl.pack(side='left', padx=(8, 0))

        # 空库 / 未建索引提示（默认隐藏；ttk.Label 随主题换底纹）
        self._qa_hint = ttk.Label(w, text='', foreground='#c0392b',
                                  font=('Microsoft YaHei UI', 10),
                                  anchor='w', padding=(10, 4))
        # ---------- 问题输入：两行布局 ----------# 问题输入：两行布局
        qf = ttk.Frame(w, padding=(10, 6))
        qf.pack(fill='x')
        self.qf = qf
        self.qvar = tk.StringVar()
        self.ent = ttk.Combobox(qf, textvariable=self.qvar,
                                font=('Microsoft YaHei UI', 11),
                                values=self.cfg.get('qa_history') or [])
        self.ent.pack(fill='x')
        self.ent.bind('<Return>', lambda _e: self.start_ask())
        qrow = ttk.Frame(qf)
        qrow.pack(fill='x', pady=(4, 0))
        self.only_active = tk.BooleanVar(
            value=bool(self.cfg.get('qa_only_active', True)))
        ttk.Checkbutton(qrow, text='仅现行有效', variable=self.only_active
                        ).pack(side='left')
        ttk.Button(qrow, text='模型测速', command=self.model_bench
                   ).pack(side='left', padx=(8, 0))
        self.ask_btn = ttk.Button(qrow, text='问一问', style='Accent.TButton',
                                  command=self.start_ask)
        self.ask_btn.pack(side='right')
        self.ent.focus_set()

        # ---------- 回答 + 出处：PanedWindow 可拖拽分割 ----------
        pw = ttk.PanedWindow(w, orient='vertical')
        pw.pack(fill='both', expand=True, padx=10, pady=(0, 10))

        # 回答区
        af = ttk.LabelFrame(pw, text='回答', padding=(4, 2))
        pw.add(af, weight=5)
        # 回答区工具栏（右对齐）
        atb = ttk.Frame(af)
        atb.pack(fill='x', padx=2, pady=(2, 0))
        self.loading_lbl = ttk.Label(atb, text='')
        self.loading_lbl.pack(side='left')
        self.regen_btn = ttk.Button(atb, text='重新生成', state='disabled',
                                    command=self.regenerate)
        self.regen_btn.pack(side='right', padx=(4, 0))
        self.copy_btn = ttk.Button(atb, text='复制答案', command=self.copy_answer)
        self.copy_btn.pack(side='right')
        self.hist_btn = ttk.Button(atb, text='历史', command=self.show_history)
        self.hist_btn.pack(side='right', padx=(0, 4))
        self.export_btn = ttk.Button(atb, text='导出',
                                     command=self.export_qa)
        self.export_btn.pack(side='right', padx=(0, 4))
        # 回答文本（气泡会话流：Text 仅作嵌入容器，消息以 window_create 挂入）
        abody = ttk.Frame(af)
        abody.pack(fill='both', expand=True)
        _pal = self._qa_palette()
        self.answer = tk.Text(abody, wrap='none', relief='flat',
                              background=_pal['page'], borderwidth=0,
                              highlightthickness=0, padx=6, pady=8,
                              cursor='arrow', takefocus=0)
        sb = ttk.Scrollbar(abody, command=self.answer.yview)
        self.answer.configure(yscrollcommand=sb.set)
        sb.pack(side='right', fill='y')
        self.answer.pack(side='left', fill='both', expand=True)
        self.answer.bind('<Key>', lambda _e: 'break')
        # 嵌入气泡所在行的横向对齐（window_create 的 -align 只管纵向）
        self.answer.tag_configure('right', justify='right')
        self.answer.tag_configure('center', justify='center')
        # 气泡会话状态
        self._chat_items = []          # {'kind','w'|'fr'+'t'} 供重排/换色
        self._cur_ai = None            # 本轮 AI 气泡内部 Text
        self._cur_ai_fr = None
        self._rep_lbl = None           # 拉取模型等原地刷新的状态行
        self._cursor_on = False
        self._cursor_job = None
        from tkinter import font as _tkfont
        self._qa_font = ('Microsoft YaHei UI', 10)
        self._qa_font_ls = _tkfont.Font(
            family='Microsoft YaHei UI', size=10).metrics('linespace')
        self.answer.bind('<Configure>', lambda _e: self._reflow_chat())
        self.answer.bind('<Map>', lambda _e: self._schedule_reflow(), add='')
        self._reflow_job = None

        # 出处区
        sf = ttk.LabelFrame(pw, text='出处条款（点击跳转原文）', padding=(6, 4))
        pw.add(sf, weight=2)
        canvas = tk.Canvas(sf, height=100, highlightthickness=0)
        sb = ttk.Scrollbar(sf, orient='vertical', command=canvas.yview)
        self.src_inner = ttk.Frame(canvas)
        self.src_inner.bind(
            '<Configure>',
            lambda e: canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.create_window((0, 0), window=self.src_inner, anchor='nw')
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side='left', fill='both', expand=True)
        sb.pack(side='right', fill='y')
        canvas.bind('<MouseWheel>',
            lambda e: canvas.yview_scroll(int(-1 * (e.delta / 120)), 'units'))
        # 底部来源计数
        self.src_pos_lbl = ttk.Label(sf, text='')
        self.src_pos_lbl.pack(anchor='e', padx=(8, 0))

        style = ttk.Style(w)
        style.configure('Dead.TButton', foreground='#c0392b')
        style.configure('Warn.TButton', foreground='#b8860b')
        style.configure('Active.TButton', foreground='#2e7d32')
        # 出处卡片左对齐：ttk.Button 不支持 anchor/justify 参数（会直接
        # TclError 崩掉事件泵），必须通过样式设置
        style.configure('SrcActive.TButton', anchor='w', justify='left')
        style.configure('SrcWarn.TButton', anchor='w', justify='left')
        style.configure('SrcDead.TButton', anchor='w', justify='left')
        self._add_status('输入问题后，答案与引用条款将显示在这里。',
                         'note', center=True)
        self.refresh_index_label()

    def _toggle_setup(self):
        """展开/收起顶部环境与索引面板"""
        if self._setup_visible:
            self.setup_frame.pack_forget()
            self._setup_visible = False
        else:
            self.setup_frame.pack(fill='x', padx=10, before=self.qf)
            self._setup_visible = True

    def refresh_index_label(self):
        try:
            st = ai_qa.semantic_stats(self.app._qa_db())
            if st['chunks']:
                self.idx_lbl.configure(
                    text=f"已索引 {st['chunks']} 个条款 / {st['docs']} 个文件"
                         + (f"（模型 {st['model']}）" if st['model'] else ''))
            else:
                self.idx_lbl.configure(text='尚未构建')
        except Exception:
            self.idx_lbl.configure(text='索引状态未知')
        self._update_gate()

    def _update_gate(self):
        """空库/未建索引时禁用提问输入并显示引导；可用后自动恢复。"""
        import sqlite3 as _sq
        hint = ''
        enabled = True
        try:
            db = self.app._qa_db()
            if not os.path.exists(db):
                hint, enabled = '还没入库文档：请先在主界面点「扫描入库」' \
                                '或「导入文件」。', False
            else:
                conn = _sq.connect(db, timeout=5)
                try:
                    docs = conn.execute(
                        'SELECT COUNT(*) FROM documents').fetchone()[0]
                finally:
                    conn.close()
                if docs == 0:
                    hint, enabled = '还没入库文档：请先在主界面点「扫描入库」' \
                                    '或「导入文件」。', False
                else:
                    st = ai_qa.semantic_stats(db)
                    if st['chunks'] == 0:
                        hint, enabled = '语义索引尚未构建：展开上方「环境与索引」' \
                                        '→「构建 / 更新语义索引」，' \
                                        '完成后再提问。', False
        except Exception:
            pass   # 读取失败不拦提问，交给问答流程自身报错
        try:
            state = 'normal' if enabled else 'disabled'
            self.ent.configure(state=state)
            self.ask_btn.configure(state=state)
            if hint:
                self._qa_hint.configure(text=hint)
                self._qa_hint.pack(fill='x', before=self.qf)
            else:
                self._qa_hint.pack_forget()
        except Exception:
            pass

    # ---------- 气泡会话流 ----------

    def _qa_palette(self):
        """聊天气泡配色（随主窗深色/浅色）"""
        if getattr(self.app, 'dark', None) is not None and self.app.dark.get():
            return dict(
                page='#252629', ai_bg='#333438', ai_fg='#ECECEC',
                ai_border='#333438', ai_hw=0,
                user_bg='#2889DE', user_fg='#FFFFFF',
                sys_fg='#9A9DA2', note_fg='#8A8DA2', err_fg='#FF7B7B',
                cite_fg='#8EC5FF', brand='#4FA3F0')
        return dict(
            page='#F4F6F9', ai_bg='#FFFFFF', ai_fg='#222222',
            ai_border='#DFE4EA', ai_hw=1,
            user_bg='#2F5E8C', user_fg='#FFFFFF',
            sys_fg='#7A7F87', note_fg='#90949A', err_fg='#C0392B',
            cite_fg='#2F5E8C', brand='#2F5E8C')

    def _bubble_width(self):
        """气泡最大宽：容器宽 74%，上限 600，未映射时给保底值"""
        try:
            w = self.answer.winfo_width() - 36
        except Exception:
            w = 420
        if w <= 20:
            w = 420
        return max(220, min(600, int(w * 0.74)))

    def _embed(self, widget, side='left', padx=8, pady=4):
        """把控件作为气泡嵌入会话流并换行。
        window_create 只能纵向对齐，横向对齐靠所在行的 justify tag。"""
        self.answer.window_create('end', window=widget, align='top',
                                  padx=padx, pady=pady)
        line = self.answer.index('end-1c linestart')
        self.answer.insert('end', '\n')
        if side in ('right', 'center'):
            self.answer.tag_add(side, line, line + ' lineend')
        self.answer.see('end')
        self._schedule_reflow()

    def _add_user_bubble(self, text):
        """用户问题：右侧品牌蓝气泡"""
        pal = self._qa_palette()
        lbl = tk.Label(
            self.answer, text=text, bg=pal['user_bg'], fg=pal['user_fg'],
            font=self._qa_font, wraplength=self._bubble_width(),
            justify='left', anchor='w', padx=12, pady=7, bd=0)
        self._embed(lbl, 'right', padx=10, pady=6)
        self._chat_items.append({'kind': 'user', 'w': lbl})
        self.answer.see('end')

    def _add_ai_bubble(self):
        """AI 回答：左侧浅卡片气泡（内嵌 Text，保留加粗/可点引用）"""
        pal = self._qa_palette()
        bw = self._bubble_width()
        fr = tk.Frame(self.answer, bd=0, bg=pal['ai_bg'],
                      highlightthickness=pal['ai_hw'],
                      highlightbackground=pal['ai_border'],
                      highlightcolor=pal['ai_border'],
                      width=bw, height=40)
        fr.pack_propagate(False)
        t = tk.Text(fr, wrap='word', relief='flat', borderwidth=0,
                    highlightthickness=0, bg=pal['ai_bg'], fg=pal['ai_fg'],
                    font=self._qa_font, padx=12, pady=8,
                    cursor='arrow', takefocus=0, height=1)
        t.pack(fill='both', expand=True)
        t.bind('<Key>', lambda _e: 'break')
        t.tag_configure('bold', font=('Microsoft YaHei UI', 10, 'bold'))
        t.tag_configure('num', font=('Microsoft YaHei UI', 10, 'bold'))
        t.tag_configure('cite', foreground=pal['cite_fg'], underline=True)
        t.tag_configure('cursor', foreground=pal['brand'])
        self._embed(fr, 'left', padx=10, pady=5)
        self._chat_items.append({'kind': 'ai', 'fr': fr, 't': t})
        self._cur_ai, self._cur_ai_fr = t, fr
        self._sync_ai_height()
        return t

    def _add_status(self, text, kind='sys', center=False):
        """系统/提示/错误：无气泡底色的小灰字行"""
        s = (text or '').strip('\n')
        if not s:
            return
        pal = self._qa_palette()
        fg = {'sys': pal['sys_fg'], 'note': pal['note_fg'],
              'err': pal['err_fg']}.get(kind, pal['sys_fg'])
        font = (('Microsoft YaHei UI', 9, 'italic') if kind == 'sys'
                else ('Microsoft YaHei UI', 9))
        lbl = tk.Label(
            self.answer, text=s, bg=pal['page'], fg=fg, font=font,
            wraplength=max(240, self._bubble_width() + 60),
            justify='left', anchor='w', bd=0)
        self._embed(lbl, 'center' if center else 'left',
                    padx=14, pady=3)
        self._chat_items.append({'kind': kind, 'w': lbl})
        self.answer.see('end')

    def _set_rep(self, text):
        """拉取模型等进度：原地刷新同一状态行，不堆字"""
        pal = self._qa_palette()
        if self._rep_lbl is None:
            self._rep_lbl = tk.Label(
                self.answer,
                text=(text or '').strip(), bg=pal['page'],
                fg=pal['sys_fg'], font=('Microsoft YaHei UI', 9, 'italic'),
                wraplength=max(240, self._bubble_width() + 60),
                justify='left', anchor='w', bd=0)
            self._embed(self._rep_lbl, 'left', padx=14, pady=3)
            self._chat_items.append({'kind': 'rep', 'w': self._rep_lbl})
        else:
            self._rep_lbl.configure(text=(text or '').strip())
        self.answer.see('end')

    def _clear_rep(self):
        lbl = self._rep_lbl
        if lbl is None:
            return
        self._rep_lbl = None
        self._chat_items = [it for it in self._chat_items
                           if it.get('w') is not lbl]
        try:
            lbl.destroy()
        except Exception:
            pass

    def _sync_ai_height(self, item=None):
        """内嵌 Text 不自动撑高：按显示行数反算 Frame 高度。
        注意 count 的选项必须在索引前，且末字符滚出可视区后 bbox 为 None，
        所以优先用 -displaylines -update 强制重排计数。"""
        if item is None:
            item = None if self._cur_ai_fr is None else {
                'fr': self._cur_ai_fr, 't': self._cur_ai}
        if item is None:
            return
        fr, t = item['fr'], item['t']
        try:
            t.update_idletasks()
            n = int(t.tk.call(
                t._w, 'count', '-displaylines', '-update',
                '1.0', 'end'))
            fr.configure(height=max(1, n) * self._qa_font_ls + 18)
        except Exception:
            try:
                bb = t.bbox('end-2c')
                if bb:
                    fr.configure(height=bb[1] + bb[3] + 12)
            except Exception:
                pass

    def _ai_append(self, delta):
        t = self._cur_ai
        if t is None:
            t = self._add_ai_bubble()
        try:
            has_cur = t.get('end-2c', 'end-1c') == '\u258d'
        except Exception:
            has_cur = False
        if has_cur:
            t.insert('end-2c', delta)
        else:
            t.insert('end', delta)
        self._ensure_cursor(t)
        self._sync_ai_height()
        self._schedule_reflow()
        self.answer.see('end')

    def _ensure_cursor(self, t=None):
        """流式光标 ▍：挂在 AI 气泡末尾，500ms 闪烁"""
        t = t or self._cur_ai
        if t is None:
            return
        try:
            if t.get('end-2c', 'end-1c') != '\u258d':
                t.insert('end-1c', '\u258d', 'cursor')
        except Exception:
            return
        if not self._cursor_on:
            self._cursor_on = True
            self._blink_cursor()

    def _blink_cursor(self):
        if not self._cursor_on:
            return
        t = self._cur_ai
        if t is not None:
            pal = self._qa_palette()
            cur = str(t.tag_cget('cursor', 'foreground')).lower()
            try:
                t.tag_configure(
                    'cursor',
                    foreground=pal['ai_bg'] if cur != pal['ai_bg'].lower()
                    else pal['brand'])
            except Exception:
                pass
        self._cursor_job = self.win.after(500, self._blink_cursor)

    def _finish_cursor(self):
        """生成结束：移除光标并停闪"""
        self._cursor_on = False
        job = self._cursor_job
        self._cursor_job = None
        if job is not None:
            try:
                self.win.after_cancel(job)
            except Exception:
                pass
        t = self._cur_ai
        if t is not None:
            try:
                if t.get('end-2c', 'end-1c') == '\u258d':
                    t.delete('end-2c', 'end-1c')
                self._sync_ai_height()
            except Exception:
                pass

    def _answer_text(self):
        """当前 AI 气泡纯文本（供复制/导出/历史）"""
        t = self._cur_ai
        if t is None:
            return ''
        try:
            txt = t.get('1.0', 'end-1c')
        except Exception:
            return ''
        return txt[:-1].strip() if txt.endswith('\u258d') else txt.strip()

    def _schedule_reflow(self):
        """布局/映射可能尚未稳定（宽度变化导致行数变化），防抖重排一次"""
        job = self._reflow_job
        if job is not None:
            try:
                self.win.after_cancel(job)
            except Exception:
                pass
        self._reflow_job = self.win.after(30, self._do_scheduled_reflow)

    def _do_scheduled_reflow(self):
        self._reflow_job = None
        self._reflow_chat()

    def _reflow_chat(self):
        """容器宽度变化：重算所有气泡宽高与换行宽"""
        bw = self._bubble_width()
        for it in self._chat_items:
            try:
                if it['kind'] == 'ai':
                    it['fr'].configure(width=bw)
                    self._sync_ai_height(it)
                elif it['kind'] == 'user':
                    it['w'].configure(wraplength=bw)
                elif it['kind'] == 'band':
                    wl = min(bw + 40, 640) - 24
                    for lbl in it.get('labels', ()):
                        lbl.configure(wraplength=wl)
                else:
                    it['w'].configure(wraplength=max(240, bw + 60))
            except Exception:
                continue

    def apply_theme(self):
        """主窗切换深色/浅色后同步会话区配色"""
        pal = self._qa_palette()
        try:
            self.answer.configure(bg=pal['page'])
        except Exception:
            pass
        kind_fg = {'sys': pal['sys_fg'], 'note': pal['note_fg'],
                   'err': pal['err_fg'], 'rep': pal['sys_fg']}
        for it in self._chat_items:
            try:
                k = it['kind']
                if k == 'ai':
                    fr, t = it['fr'], it['t']
                    fr.configure(bg=pal['ai_bg'],
                                 highlightbackground=pal['ai_border'],
                                 highlightcolor=pal['ai_border'])
                    t.configure(bg=pal['ai_bg'], fg=pal['ai_fg'])
                    t.tag_configure('cite', foreground=pal['cite_fg'])
                    t.tag_configure('cursor', foreground=pal['brand'])
                elif k == 'user':
                    it['w'].configure(bg=pal['user_bg'], fg=pal['user_fg'])
                elif k == 'band':
                    continue   # 横幅配色保持生成时的主题（会话中途切主题不重染）
                else:
                    it['w'].configure(bg=pal['page'],
                                      fg=kind_fg.get(k, pal['sys_fg']))
            except Exception:
                continue

    # ---------- 队列轮询（Tk 只在主线程操作） ----------

    def _poll(self):
        try:
            while True:
                kind, *args = self.q.get_nowait()
                handler = getattr(self, '_on_' + kind, None)
                if handler:
                    try:
                        handler(*args)
                    except Exception:
                        # 单条事件处理失败绝不能带死轮询链——否则后续答案、
                        # 结束信号全部堆在队列里没人消费，界面永远转圈
                        import traceback
                        traceback.print_exc()
                        try:
                            self._add_status(
                                '[界面事件处理出错，已跳过并继续]', 'sys')
                        except Exception:
                            pass
        except queue.Empty:
            pass
        self.win.after(100, self._poll)

    def _on_env(self, tags):
        if tags is None:
            self.env_lbl.configure(
                text='Ollama：未连接（已尝试自动启动仍失败，可点「启动本机 '
                     'Ollama」重试）', foreground='#c0392b')
            self._show_guide()
        else:
            self.env_lbl.configure(
                text=f'Ollama：在线（{len(tags)} 个模型）', foreground='')

    def _on_warm_start(self):
        self.env_lbl.configure(
            text='Ollama：模型预热中（首次较慢，预热后提问秒回）…',
            foreground='#b8860b')

    def _on_warm_done(self):
        self.env_lbl.configure(text='Ollama：模型已就绪（可直接提问）',
                               foreground='#2e7d32')

    def _on_prog(self, frac, msg):
        if frac is None:
            self.pbar.configure(mode='indeterminate')
            self.pbar.start(12)
        else:
            if str(self.pbar.cget('mode')) == 'indeterminate':
                self.pbar.stop()
                self.pbar.configure(mode='determinate')
            self.pbar.configure(value=max(0, min(1, frac)) * 100)
        self.pct_lbl.configure(text=(msg or '')[:26])

    def _on_build_done(self, info):
        self._building = False
        self.build_btn.configure(state='normal')
        self.cancel_btn.configure(state='disabled')
        self.pbar.stop()
        self.pbar.configure(mode='determinate', value=100)
        self.pct_lbl.configure(text='完成')
        self.refresh_index_label()
        if info:
            skipped = info.get('skipped') or []
            msg = (f"完成：{info.get('docs', 0)} 个文件、"
                   f"{info.get('chunks', 0)} 个条款已向量化。")
            if skipped:
                names = '、'.join(skipped[:5])
                more = f' 等{len(skipped)}个' if len(skipped) > 5 else ''
                msg += (f"\n\n{len(skipped)} 个文件无文本未索引"
                        f"（可能是加密PDF或扫描件OCR失败）：\n{names}{more}\n"
                        f"可在库管理中右键「重新索引此文件」处理。")
            messagebox.showinfo('语义索引', msg, parent=self.win)

    def _on_note(self, text, tag='note'):
        self._add_status(text, tag)

    def _on_user_q(self, q):
        self._add_user_bubble(q)

    def _on_ans(self, delta):
        self._clear_rep()
        self._ai_append(delta)

    def _on_sys(self, delta):
        """系统运行文案（灰色斜体），与AI回答正文区分"""
        self._add_status(delta, 'sys')

    def _on_link_cites(self):
        """把回答正文里的 [n] 编号渲染成可点击引用，点击跳转对应出处原文"""
        t = self._cur_ai
        if t is None:
            return
        txt = t.get('1.0', 'end-1c')
        hits = self._hits or []
        for m in re.finditer(r'\[(\d{1,2})\]', txt):
            n = int(m.group(1))
            if not (1 <= n <= len(hits)):
                continue
            start = t.index(f'1.0 + {m.start()} chars')
            end = t.index(f'1.0 + {m.end()} chars')
            uniq = f'cite_{n}_{m.start()}'
            t.tag_add(uniq, start, end)
            t.tag_add('cite', start, end)
            hit = hits[n - 1]
            t.tag_bind(
                uniq, '<Button-1>',
                lambda _e, h=hit: self._locate(h))
            t.tag_bind(
                uniq, '<Enter>',
                lambda _e: t.configure(cursor='hand2'))
            t.tag_bind(
                uniq, '<Leave>',
                lambda _e: t.configure(cursor='arrow'))

    def _on_ans_replace(self, text):
        """拉取模型等进度：原地刷新同一状态行，不堆字（灰色斜体）"""
        self._set_rep(text)

    def _on_rep_end(self):
        self._clear_rep()

    def _on_ans_clear(self):
        self._finish_cursor()
        self._clear_rep()
        if self._reflow_job is not None:
            try:
                self.win.after_cancel(self._reflow_job)
            except Exception:
                pass
            self._reflow_job = None
        self.answer.delete('1.0', 'end')
        self._chat_items = []
        self._cur_ai = None
        self._cur_ai_fr = None

    def _on_srcs(self, hits):
        self._hits = hits
        for child in self.src_inner.winfo_children():
            child.destroy()
        if not hits:
            ttk.Label(self.src_inner, text='未检索到相关条款。').pack(
                anchor='w')
            self.src_pos_lbl.configure(text='')
            return
        status_cfg = {
            'void':     ('⚠ 废止', 'Dead.TButton'),
            'replaced': ('⚠ 已替代', 'Warn.TButton'),
            'active':   ('● 现行', 'Active.TButton'),
        }
        for i, h in enumerate(hits, 1):
            status = h.get('status') or 'active'
            badge, st = status_cfg.get(status, ('● 现行', 'Active.TButton'))
            doc_no = f"· {h['doc_no']}" if h.get('doc_no') else ''
            eff = f"· 生效 {h['effective_date']}" if h.get('effective_date') else ''
            chap = f" {h['chapter']}" if h.get('chapter') else ''
            try:
                pct = f"· {h['score'] * 100:.0f}%"
            except Exception:
                pct = ''
            head = f"[{i}] {badge} 《{h['name']}》{chap} {doc_no} {eff} {pct}"
            # 条款预览（前2行）
            preview_lines = (h.get('text') or '').splitlines()[:2]
            preview = '  '.join(l.strip() for l in preview_lines if l.strip())
            preview = preview[:80] + ('…' if len(preview) > 80 else '')
            btn = ttk.Button(
                self.src_inner, text=head + ('\n  ' + preview if preview else ''),
                style='Src' + st.split('.')[0] + '.TButton',
                command=lambda x=h: self._locate(x))
            btn.pack(pady=2, fill='x')
        n = len(hits)
        self.src_pos_lbl.configure(text=f'共 {n} 条来源')

    def _on_busy(self, asking):
        self._asking = asking
        if asking:
            self.ask_btn.configure(text='■ 停止', command=self.stop_ask)
            self.regen_btn.configure(state='disabled')
        else:
            self.ask_btn.configure(text='问一问', command=self.start_ask)
            self.regen_btn.configure(
                state='normal' if self._last_hits else 'disabled')
            self._gen_eta = None
            self._finish_cursor()
            self._stop_loading()

    def stop_ask(self):
        """中断当前问答生成：作废代次 + 强制断开 HTTP，
        让 Ollama 立刻取消生成、释放生成槽（否则模型会把长回答
        生成完才让位，后续提问全部排队，表现为"无反应"）"""
        self._ask_id += 1          # 作废当前问答流程（检索阶段也会终止）
        self._stop_ask.set()
        g = self._gen
        self._gen = None
        if g is not None:
            try:
                g.close()
            except Exception:
                pass
        self._add_status('[已停止]', 'sys')
        self._on_busy(False)

    def _start_loading(self):
        """回答区左上角显示闪烁加载点"""
        self._loading_dots = 0
        self._loading_job = self.win.after(0, self._tick_loading)

    def _tick_loading(self):
        if not self._asking:
            return
        self._loading_dots = (self._loading_dots + 1) % 4
        txt = '●' * self._loading_dots + '○' * (3 - self._loading_dots)
        # 生成阶段在动画旁显示预计时间（基于上次实测速度校准）
        if self._gen_eta:
            waited = max(0, int(time.time() - self._gen_t0))
            txt += f'  预计约{self._gen_eta}秒（已等{waited}秒）'
        self.loading_lbl.configure(text=txt)
        self._loading_job = self.win.after(400, self._tick_loading)

    def _stop_loading(self):
        if hasattr(self, '_loading_job'):
            try:
                self.win.after_cancel(self._loading_job)
            except Exception:
                pass
        self.loading_lbl.configure(text='')

    def _on_error(self, msg):
        self._add_status(msg, 'err')
        self._on_busy(False)

    # ---------- 动作 ----------

    def _cfg(self, key, default):
        return self.cfg.get(key) or default

    def check_env(self):
        self.env_lbl.configure(text='Ollama：检测中…', foreground='')

        def _work():
            base = self._cfg('ollama_base', 'http://127.0.0.1:11434')
            tags = None
            try:
                tags = ai_qa.list_models(base)
            except Exception:
                # 服务没响应：已安装就自动拉起 ollama serve，等待就绪后再试一次
                if ai_qa.try_launch_ollama() and ai_qa.wait_ready(base):
                    try:
                        tags = ai_qa.list_models(base)
                    except Exception:
                        tags = None
            self.q.put(('env', tags))
            # 在线即后台预热双模型：提问时免等模型加载（首次可省 1-2 分钟）
            if tags is not None:
                self.q.put(('warm_start',))
                ai_qa.warm_up(
                    base,
                    self._cfg('ollama_chat', 'qwen2.5:7b-instruct'),
                    self._cfg('ollama_embed', 'bge-m3'))
                self.q.put(('warm_done',))
        threading.Thread(target=_work, daemon=True).start()

    def launch_ollama(self):
        base = self._cfg('ollama_base', 'http://127.0.0.1:11434')
        # 已在线：直接刷新状态并提示，不重复拉起
        try:
            tags = ai_qa.list_models(base)
        except Exception:
            tags = None
        if tags is not None:
            self.env_lbl.configure(
                text=f'Ollama：在线（{len(tags)} 个模型），无需启动',
                foreground='')
            return
        if not ai_qa.try_launch_ollama():
            messagebox.showinfo(
                '未找到 Ollama',
                '本机未检测到 Ollama。请到官网下载安装（免费）：\n'
                + OLLAMA_URL + '\n\n安装后重新打开本窗口即可。',
                parent=self.win)
            webbrowser.open(OLLAMA_URL)
            return
        self.env_lbl.configure(text='Ollama：正在启动…', foreground='')

        def _work():
            ok = ai_qa.wait_ready(base)
            try:
                tags = ai_qa.list_models(base) if ok else None
            except Exception:
                tags = None
            self.q.put(('env', tags))
            if not ok:
                self.q.put(('error',
                            'Ollama 启动超时：未在 10 秒内就绪。'
                            '请确认已正确安装（' + OLLAMA_URL + '），'
                            '或先在开始菜单启动 Ollama 后重试。'))
        threading.Thread(target=_work, daemon=True).start()

    def show_settings(self):
        dlg = tk.Toplevel(self.win)
        dlg.title('模型设置')
        self._center(dlg, 500, 494)
        dlg.transient(self.win)
        dlg.resizable(True, True)
        self.app._dialog_header(
            dlg, self.app.IC_GEAR, '模型设置',
            '本机 Ollama 接口地址与问答 / 向量模型')
        frm = ttk.Frame(dlg, padding=14)
        frm.pack(fill='x')
        frm.columnconfigure(1, weight=1)
        fields = [
            ('ollama_base', '接口地址', 'http://127.0.0.1:11434', None),
            ('ollama_chat', '问答模型', 'qwen2.5:7b-instruct', CHAT_SUGGEST),
            ('ollama_embed', '向量模型', 'bge-m3', EMBED_SUGGEST),
        ]
        vars_ = {}
        combos = []
        for i, (key, label, default, suggests) in enumerate(fields):
            ttk.Label(frm, text=label).grid(row=i, column=0, sticky='e',
                                            pady=4)
            v = tk.StringVar(
                value=_disp(self._cfg(key, default))
                if suggests else self._cfg(key, default))
            if suggests is None:
                ttk.Entry(frm, textvariable=v, width=34).grid(
                    row=i, column=1, padx=8, pady=4)
            else:
                cb = ttk.Combobox(frm, textvariable=v, width=32)
                cb['values'] = [_disp(x) for x in suggests]
                cb.grid(row=i, column=1, padx=8, pady=4)
                combos.append((cb, suggests))
            vars_[key] = v
        ttk.Label(frm, text='检索条数').grid(row=3, column=0, sticky='e',
                                             pady=4)
        kv = tk.StringVar(value=str(self.cfg.get('qa_topk') or 8))
        ttk.Entry(frm, textvariable=kv, width=10).grid(row=3, column=1,
                                                       sticky='w', padx=8)
        # 生成温度（越低越保守，制度问答推荐 0.2~0.4）
        ttk.Label(frm, text='生成温度').grid(row=4, column=0, sticky='e',
                                             pady=4)
        tv = tk.DoubleVar(value=float(self.cfg.get('qa_temperature') or 0.3))
        tframe = ttk.Frame(frm)
        tframe.grid(row=4, column=1, sticky='w', padx=8)
        ttk.Scale(tframe, from_=0.0, to=1.0, variable=tv,
                  length=150, command=lambda v: tval_lbl.configure(
                      text=f'{float(v):.2f}')).pack(side='left')
        tval_lbl = ttk.Label(tframe, text=f'{tv.get():.2f}', width=5)
        tval_lbl.pack(side='left', padx=(6, 0))
        ttk.Label(frm, text='模型未安装时，首次问答/构建会自动拉取'
                            '（较大的模型下载较久）；温度越低越保守',
                  foreground='#888',wraplength=470).grid(row=5, column=0, columnspan=2,
                                          sticky='w', padx=8)

        def _refresh_models():
            """下拉选项 = 推荐列表 + 本机已装模型（去重，带中文标注）"""
            try:
                base = vars_['ollama_base'].get().strip() or \
                    'http://127.0.0.1:11434'
                tags = ai_qa.list_models(base)
            except Exception:
                return
            vals = [t for t in tags if t]
            dlg.after(0, lambda: [
                cb.configure(values=[_disp(v) for v in vals] +
                             [_disp(x) for x in sugg])
                for cb, sugg in combos])

        threading.Thread(target=_refresh_models, daemon=True).start()

        def _save():
            for key, v in vars_.items():
                self.cfg[key] = _pure(v.get()) if key != 'ollama_base' \
                    else v.get().strip()
            try:
                self.cfg['qa_topk'] = max(1, min(20, int(kv.get())))
            except ValueError:
                self.cfg['qa_topk'] = 8
            self.cfg['qa_temperature'] = round(max(0.0, min(1.0,
                                          float(tv.get()))), 2)
            from main import save_config
            save_config(self.cfg)
            dlg.destroy()
        ttk.Button(frm, text='保存', style='Accent.TButton', command=_save).grid(
            row=6, column=1, sticky='w', padx=8, pady=(8, 0))

    def start_build(self):
        if self._building:
            return
        self._building = True
        self.build_btn.configure(state='disabled')
        self.cancel_btn.configure(state='normal')
        self.pbar.configure(mode='determinate', value=0)
        self.pct_lbl.configure(text='准备中…')
        self._build_cancel.clear()
        base = self._cfg('ollama_base', 'http://127.0.0.1:11434')
        embed_model = self._cfg('ollama_embed', 'bge-m3')

        def _cb(done, total, msg):
            frac = done / total if total else None
            self.q.put(('prog', frac, msg))

        def _work():
            try:
                info = ai_qa.build_semantic_index(
                    self.app._qa_db(), base, embed_model,
                    progress_cb=_cb, cancel_event=self._build_cancel)
                self.q.put(('build_done', info))
            except ai_qa.BuildCancelled:
                self.q.put(('prog', None, '已取消'))
                self.q.put(('build_done', None))
            except ai_qa.OllamaUnavailable:
                self.q.put(('build_done', None))
                self.q.put(('error', 'Ollama 未启动：请先点「启动本机 '
                                     'Ollama」或安装后再构建语义索引。'))
            except Exception as e:
                self.q.put(('build_done', None))
                self.q.put(('error', f'构建失败：{e}'))
        threading.Thread(target=_work, daemon=True).start()

    def cancel_build(self):
        self._build_cancel.set()
        self.pct_lbl.configure(text='正在取消…')

    def start_ask(self):
        if self._asking:
            return
        q = self.qvar.get().strip()
        if not q:
            return
        # 同步置位防连点：不等队列消息，连续点击/回车立刻被挡
        self._asking = True
        # 问题历史（去重置顶，最多 15 条），下拉可直接复问
        hist = [x for x in (self.cfg.get('qa_history') or []) if x != q]
        hist.insert(0, q)
        self.cfg['qa_history'] = hist[:15]
        self.ent['values'] = self.cfg['qa_history']
        from main import save_config
        save_config(self.cfg)

        self._last_query = q
        self._stop_ask.clear()
        self._ask_id += 1
        _my_id = self._ask_id   # 本轮问答的代次，停止/重问后旧流程自动作废
        self.q.put(('ans_clear',))
        self.q.put(('user_q', q))
        self._clear_sources()
        self.q.put(('busy', True))
        self._start_loading()
        self.q.put(('sys', '正在检索相关条款…（首次提问需加载向量模型，'
                    '请稍候）\n'))
        base = self._cfg('ollama_base', 'http://127.0.0.1:11434')
        chat_model = self._cfg('ollama_chat', 'qwen2.5:7b-instruct')
        embed_model = self._cfg('ollama_embed', 'bge-m3')
        top_k = int(self.cfg.get('qa_topk') or 8)
        only_active = bool(self.only_active.get())
        try:
            self.cfg['qa_only_active'] = only_active
        except Exception:
            pass
        db_path = self.app._qa_db()
        # 解析有效检索式：追问时自动继承上一轮主题
        search_q, display_q = ai_qa.resolve_query(q, self._qa_turns)
        self._last_search_q = search_q   # 供 _run_chat 保存轮次时记录
        if search_q != q:
            self.q.put(('sys', f'（结合上轮：{display_q}）\n'))

        def _work():
            def _cancelled():
                return self._ask_id != _my_id or self._stop_ask.is_set()
            try:
                # 服务没响应：自动拉起 ollama serve 并等就绪（开窗检测只做一次，
                # 若中途服务被关，这里兜底重拉，避免提问直接报"无法连接"）
                try:
                    tags = ai_qa.list_models(base)
                except Exception:
                    if not (ai_qa.try_launch_ollama()
                            and ai_qa.wait_ready(base, tries=60)):
                        raise ai_qa.OllamaUnavailable(
                            '无法连接 Ollama（已尝试自动启动）')
                    tags = ai_qa.list_models(base)
                if _cancelled():
                    return
                have = embed_model in tags or embed_model.split(':')[0] in [
                    m.split(':')[0] for m in tags]
                if not have and self._ctx_hits is None:
                    self.q.put(('sys',
                                f'向量模型 {embed_model} 未安装，自动拉取中…\n'))

                    def _ep(frac, st):
                        pct = f'{frac * 100:.0f}%' if frac is not None else ''
                        self.q.put(('ans_replace',
                                    f'（拉取向量模型 {embed_model} {pct} {st}）'))
                    ai_qa.pull_model(base, embed_model, _ep)
                    self.q.put(('rep_end',))
                # 检索提速：同义词扩展（瞬时，复用主界面同义词库）+ 一次批量向量检索。
                # 原 LLM 改写要先生成再检索、且每个说法各跑一遍全库扫描，非常慢；
                # 现改为默认关闭，配置 qa_rewrite=1 才启用（批量检索内部仍合并去重）。
                pool = top_k * 3 if only_active else top_k
                queries = [search_q]
                try:
                    from main import kw_terms
                    from synonyms import expand_map
                    syn = expand_map(kw_terms(search_q),
                                     self.cfg.get('custom_synonyms', ''))
                    added = []
                    for syns in syn.values():
                        for s in syns:
                            qv = f'{search_q} {s}'
                            if qv not in queries and len(queries) < 4:
                                queries.append(qv)
                                added.append(s)
                    if added:
                        self.q.put(('sys',
                                    f'（同义词扩展：{"、".join(added[:4])}'
                                    f'{"等" if len(added) > 4 else ""}）\n'))
                except Exception:
                    pass
                if self.cfg.get('qa_rewrite') and len(search_q) > 4:
                    self.q.put(('sys', '正在改写问题…\n'))
                    for qv in ai_qa.rewrite_query(base, chat_model, search_q):
                        if qv not in queries and len(queries) < 5:
                            queries.append(qv)
                if _cancelled():
                    return
                if self._ctx_hits is not None:
                    # 搜索上下文模式：跳过向量检索，直接用主界面传来的结果
                    hits = list(self._ctx_hits)
                    self.q.put(('sys',
                                f'（使用搜索上下文：{len(hits)} 个文件）\n'))
                else:
                    hits = ai_qa.semantic_search_multi(
                        db_path, base, embed_model, queries, top_k=pool)
                if _cancelled():
                    return
                if only_active:
                    active_hits = [h for h in hits
                                   if (h.get('status') or 'active') == 'active']
                    if len(active_hits) < top_k and hits:
                        # 现行不足，补充历史版本并提示
                        non_active = [h for h in hits
                                      if (h.get('status') or 'active') != 'active']
                        hits = active_hits + non_active[:top_k - len(active_hits)]
                        self.q.put(('sys',
                                    '（现行有效条款不足，已补充历史版本供参考）\n'))
                    else:
                        hits = active_hits
                    hits = hits[:top_k]
                self._last_hits = hits
                self.q.put(('srcs', hits))
                if not hits:
                    if self._ctx_hits is not None:
                        msg = ('搜索上下文被「仅现行有效」过滤为空：'
                               '可取消勾选重试，或点金色横幅 ✕ 恢复全库检索。')
                    elif ai_qa.semantic_stats(db_path)['chunks'] == 0:
                        msg = '语义索引为空，请先点上方「构建 / 更新语义索引」。'
                    else:
                        msg = ('没有检索到相关条款。可换个说法、取消勾选「仅现行'
                               '有效」再试，或先更新语义索引。')
                    self.q.put(('sys', msg))
                    self.q.put(('busy', False))
                    return
                # 可信度评估 + 版本/数值冲突检测（纯函数，毫秒级，无模型调用）。
                # high 且无冲突时静默（默认态不打扰）；mid/low/有冲突时出横幅
                assess = ai_qa.confidence_level(hits)
                conflicts = ai_qa.detect_conflicts(hits)
                if (assess['level'] != 'high'
                        or conflicts['version'] or conflicts['numeric']):
                    self.q.put(('assess', assess, conflicts))
                messages = ai_qa.build_messages(q, hits, turns=self._qa_turns)
                self._run_chat(base, chat_model, messages, retry=True,
                               should_stop=_cancelled)
            except ai_qa.OllamaUnavailable:
                self.q.put(('error', 'Ollama 未启动或无响应，无法问答。'
                            '请确认 Ollama 正在运行后重试。'))
            except Exception as e:
                self.q.put(('error', f'问答失败：{e}'))
            finally:
                # 任何路径都确保按钮复位，避免一次异常后提问被永久挡住；
                # 若已被新提问/停止接管（代次变了），则不越权操作
                if self._ask_id == _my_id and not self._stop_ask.is_set():
                    self.q.put(('busy', False))
        threading.Thread(target=_work, daemon=True).start()

    def export_qa(self):
        """把 问题+回答+出处条款原文 导出为 Word（.docx）或 Markdown（.md）"""
        answer = self._answer_text()
        if not answer:
            messagebox.showinfo('导出', '还没有回答内容，先提问再导出。',
                                parent=self.win)
            return
        q = self._last_query or self.qvar.get().strip() or 'AI 镜仔智答'
        hits = self._hits or []
        stamp = time.strftime('%Y%m%d_%H%M')
        safe = re.sub(r'[\\/:*?"<>|]', '_', q)[:30].strip()
        desktop = os.path.join(os.path.expanduser('~'), 'Desktop')
        fn = filedialog.asksaveasfilename(
            parent=self.win,
            initialdir=desktop if os.path.isdir(desktop) else None,
            initialfile=f'问答_{safe}_{stamp}.docx',
            defaultextension='.docx',
            filetypes=[('Word 文档', '*.docx'), ('Markdown', '*.md')])
        if not fn:
            return
        st_name = {'active': '现行有效', 'void': '已废止',
                   'replaced': '已被替代'}
        try:
            if fn.lower().endswith('.md'):
                self._write_qa_md(fn, q, answer, hits, st_name)
            else:
                self._write_qa_docx(fn, q, answer, hits, st_name)
            messagebox.showinfo('导出成功', f'已保存到：\n{fn}',
                                parent=self.win)
        except Exception as e:
            messagebox.showerror('导出失败', str(e), parent=self.win)

    def _write_qa_md(self, fn, q, answer, hits, st_name):
        lines = [f'# AI 镜仔智答记录', '',
                 f'- 问题：{q}',
                 f'- 日期：{time.strftime("%Y-%m-%d %H:%M")}',
                 f'- 来源：镜仔搜索 本机离线问答', '',
                 '## 回答', '', answer, '']
        if hits:
            lines.append('## 出处条款')
            lines.append('')
            for i, h in enumerate(hits, 1):
                st = st_name.get(h.get('status') or 'active', '')
                meta = ' · '.join(x for x in [
                    h.get('doc_no', ''), st,
                    f"生效 {h['effective_date']}" if h.get('effective_date')
                    else ''] if x)
                lines.append(f"### [{i}] 《{h['name']}》"
                             f"{h.get('chapter') or ''}")
                lines.append(f'*{meta}*')
                lines.append('')
                lines.append('> ' + (h.get('text') or '').replace('\n', '\n> '))
                lines.append('')
        with open(fn, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines))

    def _write_qa_docx(self, fn, q, answer, hits, st_name):
        from docx import Document
        from docx.shared import Pt, RGBColor
        doc = Document()
        doc.add_heading('AI 镜仔智答记录', level=0)
        p = doc.add_paragraph()
        p.add_run('问题：').bold = True
        p.add_run(q)
        doc.add_paragraph(f'日期：{time.strftime("%Y-%m-%d %H:%M")}')
        doc.add_paragraph('来源：镜仔搜索 本机离线问答')
        doc.add_heading('回答', level=1)
        for para in answer.split('\n'):
            doc.add_paragraph(para)
        if hits:
            doc.add_heading('出处条款', level=1)
            for i, h in enumerate(hits, 1):
                st = st_name.get(h.get('status') or 'active', '')
                hp = doc.add_paragraph()
                r = hp.add_run(f"[{i}] 《{h['name']}》"
                               f"{h.get('chapter') or ''}")
                r.bold = True
                r.font.size = Pt(11)
                meta = ' · '.join(x for x in [
                    h.get('doc_no', ''), st,
                    f"生效 {h['effective_date']}" if h.get('effective_date')
                    else ''] if x)
                mp = doc.add_paragraph()
                mr = mp.add_run(meta)
                mr.font.size = Pt(9)
                mr.font.color.rgb = RGBColor(0x88, 0x88, 0x88)
                for para in (h.get('text') or '').split('\n'):
                    doc.add_paragraph(para)
        doc.save(fn)

    def regenerate(self):
        """用上次的问题和检索结果重新生成回答"""
        if not self._last_hits or self._asking:
            return
        self._asking = True
        self._stop_ask.clear()
        self._ask_id += 1
        _my_id = self._ask_id
        self.q.put(('ans_clear',))
        self.q.put(('user_q', self._last_query))
        self.q.put(('busy', True))
        self._start_loading()
        self.q.put(('sys', '重新生成回答…\n'))
        base = self._cfg('ollama_base', 'http://127.0.0.1:11434')
        chat_model = self._cfg('ollama_chat', 'qwen2.5:7b-instruct')

        def _work():
            def _cancelled():
                return self._ask_id != _my_id or self._stop_ask.is_set()
            try:
                messages = ai_qa.build_messages(self._last_query,
                                                self._last_hits)
                self._run_chat(base, chat_model, messages, retry=True,
                               should_stop=_cancelled)
            except Exception as e:
                self.q.put(('error', f'重新生成失败：{e}'))
            finally:
                if self._ask_id == _my_id and not self._stop_ask.is_set():
                    self.q.put(('busy', False))
        threading.Thread(target=_work, daemon=True).start()

    def model_bench(self):
        """测速所有已装对话模型：响应正常的显示耗时，无响应的剔除，
        自动选用响应最快的模型并保存配置。向量模型不参与。"""
        base = self._cfg('ollama_base', 'http://127.0.0.1:11434')
        embed_model = self._cfg('ollama_embed', 'bge-m3')
        dlg = tk.Toplevel(self.win)
        dlg.title('模型测速')
        dlg.geometry('500x430')
        self._center(dlg, 500, 430)
        dlg.transient(self.win)
        self.app._dialog_header(
            dlg, self.app.IC_CHAT, '模型测速',
            '逐个发测试问题（每个最多等 45 秒），测完自动选用最快的')
        box = ttk.Frame(dlg, padding=(10, 0))
        box.pack(fill='both', expand=True)
        tip = ttk.Label(dlg, text='测速中…', padding=10)
        tip.pack(anchor='w')
        rows = {}
        state = {'done': False, 'results': {}}

        def refresh():
            for name, status in state['results'].items():
                if name not in rows:
                    row = ttk.Frame(box)
                    row.pack(fill='x', pady=2)
                    lbl = ttk.Label(row, text=name + '   测速中…')
                    lbl.pack(side='left')
                    rows[name] = lbl
                lbl = rows[name]
                if status == 'testing':
                    continue
                if status is None:
                    lbl.configure(text=f'{name}   ✕ 无响应（已剔除）',
                                  foreground='#c0392b')
                else:
                    cur = '（当前使用）' if name == self._cfg(
                        'ollama_chat', '') else ''
                    lbl.configure(text=f'{name}   {status} {cur}')
            if state['done']:
                pick = [(v, k) for k, v in state['results'].items()
                        if v and v != 'testing']
                if pick:
                    best = min(pick)[1]
                    if self._cfg('ollama_chat', '') == best:
                        tip.configure(text=f'当前模型已是响应最快的：{best}')
                    else:
                        self.cfg['ollama_chat'] = best
                        from main import save_config
                        save_config(self.cfg)
                        tip.configure(text=f'✓ 已自动选用响应最快的模型：{best}')
                else:
                    tip.configure(
                        text='所有对话模型均无响应，请检查 Ollama 是否已启动',
                        foreground='#c0392b')
            else:
                dlg.after(200, refresh)

        def bench_worker():
            try:
                models = ai_qa.list_models(base)
            except Exception as e:
                state['results'] = {f'Ollama 连接失败：{e}': None}
                state['done'] = True
                return
            emb_prefix = embed_model.split(':')[0]
            for m in models:
                if m.split(':')[0] == emb_prefix:
                    continue    # 向量模型不能对话，不参与
                state['results'][m] = 'testing'
                t0 = time.perf_counter()
                try:
                    ai_qa.ping_chat(base, m)
                    state['results'][m] = f'{time.perf_counter() - t0:.1f}s ✓'
                except Exception:
                    state['results'][m] = None
            state['done'] = True

        threading.Thread(target=bench_worker, daemon=True).start()
        dlg.after(200, refresh)

    def copy_answer(self):
        text = self._answer_text()
        if not text:
            return
        self.win.clipboard_clear()
        self.win.clipboard_append(text)
        self.copy_btn.configure(text='已复制')
        self.win.after(1200, lambda: self.copy_btn.configure(text='复制答案'))

    def show_history(self):
        """列出 qa_history 目录下的历史问答记录，可点击查看"""
        files = sorted(
            [f for f in os.listdir(self._qa_dir) if f.endswith('.txt')],
            reverse=True)[:50]
        if not files:
            messagebox.showinfo('历史记录', '暂无问答记录。', parent=self.win)
            return
        dlg = tk.Toplevel(self.win)
        dlg.title('问答历史记录')
        dlg.geometry('540x514')
        self._center(dlg, 540, 514)
        dlg.transient(self.win)
        self.app._dialog_header(
            dlg, self.app.IC_HISTORY, '问答历史记录',
            '最近 50 条，双击打开全文')
        lst = tk.Listbox(dlg, font=('Microsoft YaHei UI', 10))
        sb = ttk.Scrollbar(dlg, command=lst.yview)
        lst.configure(yscrollcommand=sb.set)
        sb.pack(side='right', fill='y')
        lst.pack(fill='both', expand=True, padx=8, pady=(6, 4))
        for f in files:
            lst.insert('end', f)

        def _open(_e=None):
            sel = lst.curselection()
            if not sel:
                return
            fpath = os.path.join(self._qa_dir, files[sel[0]])
            try:
                with open(fpath, encoding='utf-8') as f:
                    content = f.read()
            except Exception as e:
                messagebox.showerror('打开失败', str(e), parent=dlg)
                return
            # 显示内容到新窗口
            vw = tk.Toplevel(dlg)
            vw.title(files[sel[0]])
            vw.geometry('620x574')
            self._center(vw, 620, 574)
            vw.transient(dlg)
            self.app._dialog_header(vw, self.app.IC_HISTORY,
                                    files[sel[0]], '问答记录全文')
            txt = tk.Text(vw, wrap='word', font=('Microsoft YaHei UI', 10))
            sb2 = ttk.Scrollbar(vw, command=txt.yview)
            txt.configure(yscrollcommand=sb2.set)
            sb2.pack(side='right', fill='y')
            txt.pack(fill='both', expand=True, padx=6, pady=6)
            txt.insert('1.0', content)
            txt.configure(state='disabled')

        lst.bind('<Double-1>', _open)
        ttk.Button(dlg, text='打开', command=_open).pack(pady=(0, 8))

    def _run_chat(self, base, model, messages, retry=True, should_stop=None):
        """流式生成；模型缺失时自动 pull 一次再重试。
        ThinkFilter 过滤推理模型的 <think> 思考段，只显示正文"""
        def _pull_progress(frac, st):
            pct = f'{frac * 100:.0f}%' if frac is not None else ''
            self.q.put(('ans_replace',
                        f'（首次使用，正在拉取模型 {model} {pct} {st}）'))
        think = ai_qa.ThinkFilter()
        try:
            # 预计生成时间：按上次实测速度校准（首次按默认估算）
            try:
                speed = float(self.cfg.get('qa_gen_speed') or 20.0)
            except (TypeError, ValueError):
                speed = 20.0
            speed = min(max(speed, 1.0), 500.0)
            try:
                est_chars = int(self.cfg.get('qa_gen_chars') or 300)
            except (TypeError, ValueError):
                est_chars = 300
            est_chars = min(max(est_chars, 50), 2000)
            self._gen_eta = max(1, round(est_chars / speed))
            self._gen_t0 = time.time()   # 供加载动画显示"已等X秒"
            # 模型冷启动时生成前有加载等待，给出可感知的分级提示；
            # chat_stream 自带停滞自断（出字后 20 秒无输出 / 首字 180 秒），
            # 因此这里只负责"提示"，不需要兜底强杀
            self.q.put(('sys', '正在生成回答…\n'))
            self._first_piece = False
            self._generating = True

            def _hint_at(sec, msg):
                def _fire():
                    if not self._first_piece:
                        self.q.put(('sys', msg))
                t = threading.Timer(sec, _fire)
                t.daemon = True
                t.start()
                return t
            _hint1 = _hint_at(20, '（模型加载中：首次提问或长时间未使用时需要'
                                  '先加载模型，可能需要 1-2 分钟，请耐心等待…）\n')
            _hint2 = _hint_at(90, '（仍在等待：Ollama 可能正被预热或自动索引'
                                  '占用；等不及可点「停止」后稍候再试）\n')
            temperature = float(self.cfg.get('qa_temperature') or 0.3)
            try:
                num_ctx = int(self.cfg.get('qa_num_ctx') or 8192)
            except (TypeError, ValueError):
                num_ctx = 8192
            gen = ai_qa.chat_stream(
                base, model, messages, temperature=temperature,
                options={'num_ctx': num_ctx}, should_stop=should_stop)
            self._gen = gen
            out_chars = 0
            try:
                for piece in gen:
                    if not self._first_piece:
                        self._first_piece = True
                    visible = think.feed(piece)
                    if visible:
                        out_chars += len(visible)
                        self.q.put(('ans', visible))
            finally:
                for _t in (_hint1, _hint2):
                    try:
                        _t.cancel()
                    except Exception:
                        pass
                self._gen = None
                self._generating = False
                gen.close()   # 停止/异常/结束都立即断开，释放 Ollama 生成槽
            cancelled = ((should_stop is not None and should_stop())
                         or self._stop_ask.is_set())
            if not cancelled:
                # 记录本次实测速度，供下次「预计约X秒」校准
                elapsed = time.time() - self._gen_t0
                if out_chars >= 20 and elapsed >= 0.5:
                    self.cfg['qa_gen_speed'] = round(
                        min(max(out_chars / elapsed, 0.5), 500.0), 1)
                    self.cfg['qa_gen_chars'] = out_chars
                    try:
                        from main import save_config
                        save_config(self.cfg)
                    except Exception:
                        pass
                    self.q.put(('sys',
                                f'\n（本次生成 {out_chars} 字，'
                                f'用时 {elapsed:.0f} 秒）'))
                tail = think.flush()
                if tail:
                    self.q.put(('ans', tail))
                # 把回答正文里的 [1][2] 渲染成可点击引用
                self.q.put(('link_cites',))
                # 保存问答记录到文件
                try:
                    full_answer = self._answer_text()
                    ts = time.strftime('%Y%m%d_%H%M%S')
                    safe_q = re.sub(r'[\\/:*?"<>|]', '_',
                                    (self.qvar.get() or 'question')[:50]).strip()
                    fname = f'{ts}_{safe_q}.txt'
                    fpath = os.path.join(self._qa_dir, fname)
                    with open(fpath, 'w', encoding='utf-8') as f:
                        f.write(f'问题：{self.qvar.get()}\n\n')
                        f.write(f'回答：\n{full_answer}\n\n')
                        if self._hits:
                            f.write('出处条款：\n')
                            for i, h in enumerate(self._hits, 1):
                                st = {'void': '⚠已废止', 'replaced': '⚠已被替代'}.get(
                                    h.get('status') or 'active', '')
                                f.write(f'[{i}] {st}《{h["name"]}》'
                                        f'{h.get("chapter","")}'
                                        f'（{h.get("doc_no","")}）\n')
                    self.q.put(('sys', f'\n（记录已保存：{fname}）'))
                except Exception:
                    pass
                # 保存最近问答轮次供多轮上下文（限 5 轮）
                if full_answer.strip():
                    self._qa_turns.append((self._last_query, full_answer,
                                           self._last_search_q))
                    self._qa_turns = self._qa_turns[-5:]
            if not cancelled:   # 被取消的旧流程不得触碰按钮状态，干扰新提问
                self.q.put(('busy', False))
        except ai_qa.OllamaModelMissing:
            if not retry:
                self.q.put(('error', f'模型 {model} 拉取后仍不可用。'))
                return
            self.q.put(('sys', f'\n模型 {model} 未下载，开始拉取…\n'))
            try:
                ai_qa.pull_model(base, model, _pull_progress)
            except Exception as e:
                self.q.put(('rep_end',))
                self.q.put(('error', f'拉取模型失败：{e}（可在模型设置里'
                                     f'换小一点的模型，如 qwen2.5:3b-instruct）'))
                return
            self.q.put(('rep_end',))
            self._run_chat(base, model, messages, retry=False,
                           should_stop=should_stop)
        except Exception as e:
            self.q.put(('error', str(e)))

    # ---------- 杂项 ----------

    def _set_answer(self, text, tag=None):
        """兼容旧调用：统一落到状态小灰字（答案正文走 _ai_append）"""
        self._add_status(
            text, tag if tag in ('sys', 'note', 'err') else 'note')

    def _clear_sources(self):
        for child in self.src_inner.winfo_children():
            child.destroy()
        self.src_pos_lbl.configure(text='')

    def _show_guide(self):
        self._add_status(
            '使用方法（全程离线，数据不出本机）：\n'
            '1. 安装并启动 Ollama：' + OLLAMA_URL + '\n'
            '2. 在命令行执行一次：ollama pull qwen2.5:7b-instruct '
            '（约 4.7GB；配置低可改 3b）与 ollama pull bge-m3\n'
            '   也可直接点「构建/问答」，程序会自动拉取并显示进度。\n'
            '3. 点「构建 / 更新语义索引」，把库内条款转向量（文件更新后增量补齐）。\n'
            '4. 输入问题，答案仅依据库内条款生成，下方可点击条款跳转原文。',
            'note')

    def _on_assess(self, assess, conflicts):
        """可信度横幅 + 版本/数值冲突提示块（答案气泡上方，先于回答出现）。
        high 档无冲突时 worker 不会发此事件，默认态保持安静。"""
        top_pct = round(assess.get('top', 0) * 100)
        lv = assess.get('level')
        if lv == 'low':
            self._add_band(
                'danger',
                f'⚠ 未找到直接依据（最高相关度 {top_pct}%）',
                '库内没有匹配的条款，以下内容可能是模型推断而非资料原文，'
                '请务必核实，或换个关键词再问。')
        elif lv == 'mid':
            self._add_band(
                'warn',
                f'相关度中等（最高 {top_pct}%）',
                '库内有相关制度但匹配不够精确，回答请结合下方条款原文核实。')
        st_lbl = {'void': '已废止', 'replaced': '已被替代', 'active': '现行'}
        for g in conflicts.get('version', []):
            links = []
            for x in g['items']:
                links.append((f"《{x['name']}》{x['year']}年"
                              f"（{st_lbl.get(x['status'], x['status'])}）",
                              x['idx']))
            self._add_band(
                'warn', '⚠ 检索到同一标准的多个版本',
                '回答将以现行版本为准，旧版内容仅作历史参考。点击查看：',
                links)
        for g in conflicts.get('numeric', []):
            links = [(f"《{x['name']}》{x['value']}{g['unit']}", x['idx'])
                     for x in g['items']]
            self._add_band(
                'warn', f"⚠ 不同文件对「{g['topic']}」的取值不一致",
                '可能是适用对象/条件不同，请核对后再采用。点击查看条款：',
                links)

    def _add_band(self, level, title, desc='', links=None):
        """答案区彩色横幅。level: warn=琥珀(提示) / danger=橙红(强警告)。
        links: [(文本, 来源下标)]，点击跳转对应条款原文"""
        dark = bool(getattr(self.app, 'dark', None) and self.app.dark.get())
        if level == 'danger':
            bg, fg, bd = ('#3D2725', '#F0938A', '#5C3A37') if dark else \
                         ('#FBE9E7', '#A4352B', '#E8C4BF')
        else:
            bg, fg, bd = ('#3A3020', '#E3BE7E', '#5C4E33') if dark else \
                         ('#FBF0DA', '#8A5A12', '#E8D3A8')
        pal = self._qa_palette()
        bw = min(self._bubble_width() + 40, 640)
        wl = bw - 24
        fr = tk.Frame(self.answer, bg=bg, highlightthickness=1,
                      highlightbackground=bd, highlightcolor=bd, bd=0)
        band_labels = []
        title_lbl = tk.Label(fr, text=title, bg=bg, fg=fg,
                             font=('Microsoft YaHei UI', 9, 'bold'),
                             anchor='w', justify='left',
                             wraplength=wl)
        title_lbl.pack(fill='x', padx=10, pady=(7, 0))
        band_labels.append(title_lbl)
        if desc:
            d_lbl = tk.Label(fr, text=desc, bg=bg, fg=fg,
                             font=('Microsoft YaHei UI', 9),
                             anchor='w', justify='left',
                             wraplength=wl)
            d_lbl.pack(fill='x', padx=10, pady=(2, 0))
            band_labels.append(d_lbl)
        if links:
            for text, idx in links:
                def _jump(_e=None, i=idx):
                    hits = self._hits or []
                    if 0 <= i < len(hits):
                        self._locate(hits[i])
                lbl = tk.Label(fr, text='▸ ' + text, bg=bg,
                               fg=pal['cite_fg'], cursor='hand2',
                               font=('Microsoft YaHei UI', 9, 'underline'),
                               anchor='w', wraplength=wl,
                               justify='left')
                lbl.pack(fill='x', padx=10, pady=(1, 0))
                lbl.bind('<Button-1>', _jump)
                band_labels.append(lbl)
        tk.Frame(fr, bg=bg, height=6).pack()
        self._embed(fr, 'left', padx=10, pady=4)
        self._chat_items.append({'kind': 'band', 'w': fr,
                                 'labels': band_labels})

    def _locate(self, hit):
        try:
            self.app.root.deiconify()
            self.app.root.lift()
            self.app._open_located(hit['path'], hit['start_line'])
        except Exception as e:
            messagebox.showerror('定位失败', str(e), parent=self.win)
