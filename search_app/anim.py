# -*- coding: utf-8 -*-
"""镜仔搜索 · 轻量动效工具箱

纯 Tkinter 标准库实现，零外部依赖。
提供缓动函数、颜色插值、补间器、全局减弱动效开关。
所有动画失败时静默降级，不影响主程序功能。
"""
from __future__ import annotations
import json
import os

# 全局配置缓存
_CFG_PATH = os.path.join(os.path.dirname(__file__), 'config.json')
_reduce_motion = None


def _load_reduce_motion() -> bool:
    """读取 config.json 里的 reduce_motion，默认 False"""
    global _reduce_motion
    if _reduce_motion is not None:
        return _reduce_motion
    try:
        with open(_CFG_PATH, 'r', encoding='utf-8') as f:
            cfg = json.load(f)
    except Exception:
        cfg = {}
    _reduce_motion = bool(cfg.get('reduce_motion', False))
    return _reduce_motion


def set_reduce_motion(flag: bool):
    """运行时切换减弱动效（不入盘，仅当前进程生效）"""
    global _reduce_motion
    _reduce_motion = bool(flag)


def reduced() -> bool:
    """当前是否处于「减弱动效」模式"""
    return _load_reduce_motion()


# ---------- 缓动函数 ----------
def ease_linear(t: float) -> float:
    return t


def ease_in_quad(t: float) -> float:
    return t * t


def ease_out_quad(t: float) -> float:
    return 1 - (1 - t) * (1 - t)


def ease_in_out_quad(t: float) -> float:
    return 2 * t * t if t < 0.5 else 1 - (-2 * t + 2) ** 2 / 2


def ease_out_cubic(t: float) -> float:
    return 1 - (1 - t) ** 3


def ease_out_back(t: float) -> float:
    c1, c3 = 1.70158, 2.70158
    return 1 + c3 * (t - 1) ** 3 + c1 * (t - 1) ** 2


_EASINGS = {
    'linear': ease_linear,
    'ease_in_quad': ease_in_quad,
    'ease_out_quad': ease_out_quad,
    'ease_in_out_quad': ease_in_out_quad,
    'ease_out_cubic': ease_out_cubic,
    'ease_out_back': ease_out_back,
}

# ---------- 颜色工具 ----------

def _hex_to_rgb(h: str):
    h = h.lstrip('#')
    if len(h) == 3:
        h = ''.join(c * 2 for c in h)
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _rgb_to_hex(r: int, g: int, b: int) -> str:
    return f'#{r:02x}{g:02x}{b:02x}'


def hex_lerp(c1: str, c2: str, t: float) -> str:
    """在两种 hex 颜色之间线性插值，t∈[0,1]"""
    t = max(0.0, min(1.0, t))
    r1, g1, b1 = _hex_to_rgb(c1)
    r2, g2, b2 = _hex_to_rgb(c2)
    return _rgb_to_hex(
        int(r1 + (r2 - r1) * t),
        int(g1 + (g2 - g1) * t),
        int(b1 + (b2 - b1) * t),
    )


def rgb_lerp(c1: tuple, c2: tuple, t: float) -> tuple:
    """在两种 RGB 元组之间线性插值"""
    t = max(0.0, min(1.0, t))
    return tuple(int(a + (b - a) * t) for a, b in zip(c1, c2))


# ---------- 补间器 ----------

class Tweener:
    """Tkinter 补间器：用 widget.after 驱动单属性/颜色过渡。

    用法示例（颜色渐变）：
        def on_step(t, color):
            widget.configure(bg=color)
        tween_color('#0F6CBD', '#2889E0', 150, on_step)

    用法示例（数值过渡）：
        def on_step(t, val):
            widget.configure(padx=int(val))
        tween(10, 14, 150, on_step)
    """

    def __init__(self, widget, after_fn=None):
        self.widget = widget
        self._after_fn = after_fn or widget.after
        self._job = None

    def cancel(self):
        if self._job is not None:
            try:
                self.widget.after_cancel(self._job)
            except Exception:
                pass
            self._job = None

    def tween(self, start, end, duration_ms: int, on_step, on_done=None,
              easing='ease_out_quad'):
        """数值补间（start/end 须为数字）。"""
        if _load_reduce_motion() or duration_ms <= 0:
            on_step(1.0, end)
            if on_done:
                on_done()
            return
        ease = _EASINGS.get(easing, ease_out_quad)
        start_t = self.widget.winfo_toplevel().tk.call('clock', 'milliseconds')
        frame_ms = 16  # ~60fps

        def _tick():
            now = self.widget.winfo_toplevel().tk.call('clock', 'milliseconds')
            elapsed = now - start_t
            t = min(1.0, elapsed / duration_ms)
            val = start + (end - start) * ease(t)
            try:
                on_step(t, val)
            except Exception:
                pass
            if t < 1.0:
                self._job = self._after_fn(frame_ms, _tick)
            else:
                self._job = None
                if on_done:
                    try:
                        on_done()
                    except Exception:
                        pass

        self.cancel()
        _tick()

    def tween_color(self, c1: str, c2: str, duration_ms: int, on_step,
                    on_done=None, easing='ease_out_quad'):
        """Hex 颜色补间。"""
        if _load_reduce_motion() or duration_ms <= 0:
            on_step(1.0, c2)
            if on_done:
                on_done()
            return
        ease = _EASINGS.get(easing, ease_out_quad)
        r1, g1, b1 = _hex_to_rgb(c1)
        r2, g2, b2 = _hex_to_rgb(c2)
        start_t = self.widget.winfo_toplevel().tk.call('clock', 'milliseconds')
        frame_ms = 16

        def _tick():
            now = self.widget.winfo_toplevel().tk.call('clock', 'milliseconds')
            t = min(1.0, (now - start_t) / duration_ms)
            et = ease(t)
            col = _rgb_to_hex(
                int(r1 + (r2 - r1) * et),
                int(g1 + (g2 - g1) * et),
                int(b1 + (b2 - b1) * et),
            )
            try:
                on_step(t, col)
            except Exception:
                pass
            if t < 1.0:
                self._job = self._after_fn(frame_ms, _tick)
            else:
                self._job = None
                if on_done:
                    try:
                        on_done()
                    except Exception:
                        pass

        self.cancel()
        _tick()


# ---------- 便捷函数 ----------

def tween(widget, start, end, duration_ms: int, on_step, on_done=None,
          easing='ease_out_quad', after_fn=None):
    """单发数值补间（不保存 Tweener 实例，无法取消）。"""
    t = Tweener(widget, after_fn)
    t.tween(start, end, duration_ms, on_step, on_done, easing)


def tween_color(widget, c1: str, c2: str, duration_ms: int, on_step,
                on_done=None, easing='ease_out_quad', after_fn=None):
    """单发颜色补间。"""
    t = Tweener(widget, after_fn)
    t.tween_color(c1, c2, duration_ms, on_step, on_done, easing)


# ---------- 按钮 hover 颜色渐变（tk.Button 专用） ----------

def bind_hover_bg(widget: 'tk.Widget', normal_bg: str, hover_bg: str,
                  normal_fg: str | None = None, hover_fg: str | None = None,
                  duration_ms: int = 150):
    """给 tk.Button / tk.Label 等绑定 hover 颜色渐变。
    静默处理所有异常，不影响原有功能。"""
    tw = Tweener(widget)
    _state = {'in': False}

    def _enter(_e=None):
        _state['in'] = True
        tw.tween_color(normal_bg, hover_bg, duration_ms,
                       lambda _t, c: _state['in'] and _safe_bg(c))
        if normal_fg is not None and hover_fg is not None:
            tween_color(widget, normal_fg, hover_fg, duration_ms,
                        lambda _t, c: _state['in'] and _safe_fg(c))

    def _leave(_e=None):
        _state['in'] = False
        tw.tween_color(hover_bg, normal_bg, duration_ms,
                       lambda _t, c: not _state['in'] and _safe_bg(c))
        if normal_fg is not None and hover_fg is not None:
            tween_color(widget, hover_fg, normal_fg, duration_ms,
                        lambda _t, c: not _state['in'] and _safe_fg(c))

    def _safe_bg(c):
        try:
            widget.configure(bg=c)
        except Exception:
            pass

    def _safe_fg(c):
        try:
            widget.configure(fg=c)
        except Exception:
            pass

    widget.bind('<Enter>', _enter, add='+')
    widget.bind('<Leave>', _leave, add='+')


# ---------- 通用淡入/淡出 ----------

def fade_in(widget, duration_ms: int = 150, on_done=None):
    """Toplevel/Window 淡入（alpha 0→1）。Windows 上需先确保窗口已显示。"""
    if _load_reduce_motion() or duration_ms <= 0:
        try:
            widget.attributes('-alpha', 1.0)
        except Exception:
            pass
        if on_done:
            on_done()
        return
    tw = Tweener(widget)
    tw.tween(0.0, 1.0, duration_ms,
             lambda _t, v: _safe_alpha(widget, v), on_done)


def fade_out(widget, duration_ms: int = 150, on_done=None):
    """Toplevel/Window 淡出（alpha 1→0），完成后自动调用 on_done。"""
    if _load_reduce_motion() or duration_ms <= 0:
        try:
            widget.attributes('-alpha', 0.0)
        except Exception:
            pass
        if on_done:
            on_done()
        return
    tw = Tweener(widget)
    tw.tween(1.0, 0.0, duration_ms,
             lambda _t, v: _safe_alpha(widget, v), on_done)


def _safe_alpha(w, v):
    try:
        w.attributes('-alpha', max(0.0, min(1.0, v)))
    except Exception:
        pass


# ---------- 骨架屏 shimmer ----------

class SkeletonShimmer:
    """预览区骨架屏：灰色条 + 白色微光从左到右扫过。

    用法：
        sk = SkeletonShimmer(parent_frame, dark=False)
        sk.show()
        # ... 数据加载完成 ...
        sk.hide()
    """

    def __init__(self, parent, dark: bool = False, lines: int = 6):
        self.parent = parent
        self.dark = dark
        self.lines = lines
        self._cv = None
        self._job = None
        self._running = False
        self._tag_ids = []

    def show(self):
        if self._cv is not None:
            return
        self._running = True
        bg = '#1E1E1E' if self.dark else '#F4F6F9'
        bar = '#333333' if self.dark else '#DFE4EA'
        self._cv = tk.Canvas(self.parent, bg=bg, highlightthickness=0)
        self._cv.place(relx=0, rely=0, relwidth=1, relheight=1)
        # 画骨架条
        self._tag_ids = []
        w = 400  # 占位宽，实际随窗口
        y = 30
        for i in range(self.lines):
            # 长短交替，更像真实文本
            bw = w * (0.92 if i % 3 != 2 else 0.55)
            rid = self._cv.create_rectangle(20, y, 20 + bw, y + 14,
                                             fill=bar, outline='')
            self._tag_ids.append(rid)
            y += 28
        # shimmer 遮罩（白色半透明矩形，从左到右扫）
        self._shimmer = self._cv.create_rectangle(
            -100, 0, 0, 1000, fill='white', outline='', stipple='gray50')
        self._cv.lower(self._shimmer)
        self._anim_shimmer()

    def _anim_shimmer(self):
        if not self._running or self._cv is None:
            return
        try:
            pw = self.parent.winfo_width()
        except Exception:
            pw = 600
        duration = 1200  # 一个周期 ms
        start = -150
        end = pw + 150
        tw = Tweener(self._cv)

        def _step(_t, x):
            if self._cv is None:
                return
            try:
                self._cv.coords(self._shimmer, x, 0, x + 120, 1000)
            except Exception:
                pass

        def _loop():
            if self._running:
                self._anim_shimmer()

        tw.tween(start, end, duration, _step, _loop, 'ease_in_out_quad')
        self._job = tw._job

    def hide(self):
        self._running = False
        if self._job is not None:
            try:
                self._cv.after_cancel(self._job)
            except Exception:
                pass
            self._job = None
        if self._cv is not None:
            try:
                self._cv.destroy()
            except Exception:
                pass
            self._cv = None


# ---------- 呼吸动画（用于状态圆点等） ----------

class Breathe:
    """周期呼吸：颜色/大小在两个值之间来回过渡。

    用法：
        b = Breathe(widget)
        b.start(lambda t, v: widget.itemconfig(dot, fill=v),
                '#0F6CBD', '#6BB5FF', 800)
        b.stop()
    """

    def __init__(self, widget):
        self.widget = widget
        self._tw = Tweener(widget)
        self._running = False
        self._forward = True

    def start(self, on_step, c1: str, c2: str, half_period_ms: int = 800,
              easing='ease_in_out_quad'):
        if _load_reduce_motion():
            on_step(1.0, c2)
            return
        self._running = True
        self._forward = True

        def _one_way():
            if not self._running:
                return
            a, b = (c1, c2) if self._forward else (c2, c1)
            self._tw.tween_color(a, b, half_period_ms,
                                 lambda _t, c: self._running and on_step(_t, c),
                                 lambda: self._running and _flip())

        def _flip():
            self._forward = not self._forward
            _one_way()

        _one_way()

    def stop(self, final_val=None):
        self._running = False
        self._tw.cancel()


# ---------- 脉冲缩放（用于清空按钮等） ----------

def pulse_scale(widget, start_size: int, peak_size: int, duration_ms: int = 100):
    """字体大小先放大再还原（用于 tk.Button / tk.Label）。"""
    if _load_reduce_motion() or duration_ms <= 0:
        return
    tw = Tweener(widget)
    # 先放大
    tw.tween(start_size, peak_size, duration_ms // 2,
             lambda _t, s: _safe_font(widget, int(s)),
             lambda: tw.tween(peak_size, start_size, duration_ms // 2,
                              lambda _t, s: _safe_font(widget, int(s))))


def _safe_font(widget, size: int):
    try:
        cur = widget.cget('font')
        # 简单替换 size：假设 font 是元组 (family, size, ...)
        if isinstance(cur, tuple) and len(cur) >= 2:
            new = (cur[0], size) + cur[2:]
        elif isinstance(cur, str):
            # 字符串形式："family size ..."
            parts = cur.split()
            if len(parts) >= 2:
                parts[1] = str(size)
                new = ' '.join(parts)
            else:
                new = cur
        else:
            new = cur
        widget.configure(font=new)
    except Exception:
        pass


# tk 别名（SkeletonShimmer 需要）
import tkinter as tk


# ---------- 弹窗弹入（淡入 + 轻微上移，替代硬出现） ----------

def popup_in(win, duration_ms: int = 160, rise: int = 10):
    """Toplevel 打开时：alpha 淡入 + 向上浮入 rise 像素。
    须在窗口已 map（有真实坐标）后调用；静默降级。"""
    if _load_reduce_motion() or duration_ms <= 0:
        return
    try:
        win.update_idletasks()
        x, y = win.winfo_x(), win.winfo_y()
        win.attributes('-alpha', 0.0)
        win.geometry(f'+{x}+{y + rise}')
        tw = Tweener(win)
        tw.tween(0.0, 1.0, duration_ms,
                 lambda _t, v: _safe_alpha(win, v),
                 easing='ease_out_cubic')
        tw2 = Tweener(win)
        tw2.tween(y + rise, y, duration_ms,
                  lambda _t, ny: win.geometry(f'+{x}+{int(ny)}'),
                  easing='ease_out_cubic')
    except Exception:
        try:
            win.attributes('-alpha', 1.0)
        except Exception:
            pass


# ---------- 数字滚动（counter） ----------

def count_up(widget, set_value, start: float, end: float,
             duration_ms: int = 300, easing='ease_out_cubic'):
    """数字从 start 滚动到 end；set_value(v) 负责回写控件文本。"""
    if _load_reduce_motion() or duration_ms <= 0:
        set_value(end)
        return
    tw = Tweener(widget)
    tw.tween(float(start), float(end), duration_ms,
             lambda _t, v: set_value(v), easing=easing)


# ---------- 漂浮循环（空状态图标上下浮动等） ----------

class FloatLoop:
    """周期性上下漂浮：每帧把偏移量交给 on_offset(dy)。"""

    def __init__(self, widget, on_offset, amplitude: int = 6,
                 period_ms: int = 1800):
        self.widget = widget
        self.on_offset = on_offset
        self.amplitude = amplitude
        self.period_ms = period_ms
        self._running = False
        self._job = None
        self._t0 = None

    def start(self):
        if _load_reduce_motion():
            return
        try:
            self._t0 = self.widget.winfo_toplevel().tk.call(
                'clock', 'milliseconds')
        except Exception:
            return   # 宿主已销毁（如主题切换重建引导页）
        self._running = True
        self._tick()

    def _tick(self):
        if not self._running:
            return
        import math
        try:
            now = self.widget.winfo_toplevel().tk.call(
                'clock', 'milliseconds')
        except Exception:
            self._running = False
            return
        phase = ((now - self._t0) % self.period_ms) / self.period_ms
        dy = self.amplitude * (math.sin(phase * 2 * math.pi - math.pi / 2) + 1) / 2
        try:
            self.on_offset(dy)
        except Exception:
            pass
        self._job = self.widget.after(32, self._tick)

    def stop(self):
        self._running = False
        if self._job is not None:
            try:
                self.widget.after_cancel(self._job)
            except Exception:
                pass
            self._job = None


# ---------- 惯性平滑滚动（Mousewheel 动量衰减） ----------

class SmoothScroller:
    """给 Treeview/Text/Canvas/Listbox 加惯性滚动。

    用法：SmoothScroller(widget, axis='y')
    Windows 滚轮 delta=±120；触摸板会有小幅连续事件。

    手感：一格滚轮先立即滚 step 行（确定、跟手），惯性只额外滑行
    _coast/(1-decay) 行后收住 —— 合计约 4 行，接近系统默认的 3 行。
    早期版本把惯性当主体（step*3 起始速度 + 0.82 慢衰减），一格实际滚
    ~37 行、超过一页，完全看不到中间内容。
    """

    def __init__(self, widget, axis: str = 'y', step: float = 2.0):
        self.widget = widget
        self.axis = axis
        self.step = step
        self._coast = 0.8    # 惯性滑行系数（起始速度 = 一格 * _coast）
        self._decay = 0.6    # 每帧衰减：尾巴短、停得快
        self._vel = 0.0
        self._job = None
        self._last_dir = 0
        self._remain = 0.0   # 亚像素余量，避免衰减末期 int 吞量
        widget.bind('<MouseWheel>', self._on_wheel, add='+')

    def _scroll(self, amount):
        w = self.widget
        try:
            if self.axis == 'y':
                w.yview_scroll(int(amount), 'units')
            else:
                w.xview_scroll(int(amount), 'units')
        except Exception:
            pass

    def _scroll_frac(self, amount):
        """按小数行滚：攒够一整行才真正滚动，避免小步长被 int 吞掉"""
        self._remain += amount
        whole = int(self._remain)
        if whole:
            self._scroll(whole)
            self._remain -= whole

    def _on_wheel(self, evt):
        if _load_reduce_motion():
            self._scroll(-evt.delta // 120 * 3)
            return 'break'
        # 反向滚动立即抵消惯性
        direction = 1 if evt.delta > 0 else -1
        if self._last_dir and direction != self._last_dir:
            self._vel = 0.0
            self._remain = 0.0
        self._last_dir = direction
        notches = -evt.delta / 120.0     # 一格 = ±1；触摸板是更小的连续值
        # 立即滚基础量：手感确定、与惯性无关
        self._scroll_frac(notches * self.step)
        # 惯性尾巴：只额外滑行一点点，快速收住
        self._vel = max(-3.0, min(3.0, self._vel + notches * self._coast))
        if self._job is None:
            self._tick()
        return 'break'

    def _tick(self):
        if abs(self._vel) < 0.15:
            # 不清 _remain：触摸板单次 delta 很小，余量必须跨事件累积，
            # 否则小步长永远攒不满 1 行，触摸板会「滚不动」
            self._vel = 0.0
            self._job = None
            self._last_dir = 0
            return
        self._scroll_frac(self._vel)
        self._vel *= self._decay   # 摩擦衰减
        self._job = self.widget.after(16, self._tick)

