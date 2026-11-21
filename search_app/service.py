# -*- coding: utf-8 -*-
"""镜仔搜索 · 无界面工具函数（service 层）

从 main.py 抽出的纯函数：文本/字节/时间格式化、日期与生效日校验、
拖拽路径解析、命中行标签解析、表格行识别、结果过滤缓存预热、
行预算估算、Excel 定位解析、索引库快照。全部不依赖 tkinter。
"""
import glob
import os
import re
import sys
import time
import sqlite3
import datetime
from urllib.parse import unquote, urlparse

from searcher import extract_page


def fmt_size(n):
    """字节 → 人类可读"""
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if n < 1024 or unit == 'TB':
            return f'{n:.1f} {unit}' if unit != 'B' else f'{int(n)} B'
        n /= 1024
    return f'{n:.1f} TB'


def fmt_eta(seconds):
    """剩余时间格式化：123s → '02:03'；>1h → '1h05m'"""
    try:
        seconds = int(seconds)
    except (TypeError, ValueError):
        return '—'
    if seconds < 0:
        return '—'
    if seconds < 60:
        return f'~{seconds}s'
    m, s = divmod(seconds, 60)
    if m < 60:
        return f'~{m:02d}:{s:02d}'
    h, m = divmod(m, 60)
    return f'~{h}h{m:02d}m'


def fmt_mtime(ts):
    dt = datetime.datetime.fromtimestamp(ts)
    now = datetime.datetime.now()
    if dt.date() == now.date():
        return f'今天 {dt.strftime("%H:%M")}'
    yesterday = now - datetime.timedelta(days=1)
    if dt.date() == yesterday.date():
        return f'昨天 {dt.strftime("%H:%M")}'
    return dt.strftime('%m-%d %H:%M')


def valid_date(s):
    """校验 YYYY-MM-DD 且为真实日期（2021-02-30 判为不合法）"""
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', s or ''):
        return False
    try:
        time.strptime(s, '%Y-%m-%d')
        return True
    except ValueError:
        return False


def eff_sort_key(eff_date):
    """effective_date → 可比较起始日键；空串排最前（视为最旧）"""
    s = (eff_date or '').split('~')[0].strip()
    return s if len(s) >= 8 else '0000-00-00'


def find_fuzzy_pos(text, kw):
    """模糊行：定位一字之差的实际匹配位置（找不到返回 -1）。
    先试错字/多打（前缀+任意1字+后缀），再试少打（剩余实心≥3字）。"""
    n = len(kw)
    for i in range(n):
        m = re.search(re.escape(kw[:i]) + '.' + re.escape(kw[i + 1:]),
                      text)
        if m:
            return m.start()
    for i in range(n):
        frag = kw[:i] + kw[i + 1:]
        if len(frag) >= 3:
            j = text.find(frag)
            if j >= 0:
                return j
    return -1


def is_table_row(txt):
    """判断一行是否是表格行（docx/xlsx 解析出的 ` | ` 分隔格式）。
    规则：有 ≥2 个 ' | ' 且非空单元格 ≥2 个。"""
    if not txt:
        return False
    parts = [c.strip() for c in txt.split(' | ')]
    return len(parts) >= 3 and sum(1 for c in parts if c) >= 2


def parse_dropped_paths(data):
    """
    tkinterdnd2 的 evt.data 是字符串，多文件用空格分隔，
    路径含空格时被 {} 包围。解析成 list[Path]。
    """
    paths = []
    if not data:
        return paths
    items = []
    cur = ''
    in_brace = False
    i = 0
    while i < len(data):
        c = data[i]
        if c == '{':
            in_brace = True
            i += 1
            continue
        if c == '}':
            in_brace = False
            items.append(cur)
            cur = ''
            i += 1
            continue
        if c == ' ' and not in_brace and cur:
            items.append(cur)
            cur = ''
            i += 1
            continue
        cur += c
        i += 1
    if cur:
        items.append(cur)
    for it in items:
        it = it.strip()
        if not it:
            continue
        # 处理 file:// 前缀
        if it.startswith('file://'):
            p = urlparse(it)
            it = unquote(p.path)
            if sys.platform.startswith('win') and it.startswith('/'):
                it = it[1:]
        if os.path.exists(it):
            paths.append(it)
    return paths


def line_path_from_tags(tags):
    """从命中行 tags 解析文件路径（兼容模糊行/精确行两种标签顺序）"""
    if not tags or 'line' not in tags:
        return None
    i = list(tags).index('line')
    return tags[i + 1] if len(tags) > i + 1 else None


def prepare_filter_cache(groups):
    """把每个文件名/命中行的小写文本预计算好（可在后台线程调用）。

    结果内过滤的判定要遍历全部命中行（数万条），每次敲键都现算
    .lower() 纯属重复劳动；预热一次后过滤只剩字符串包含判断。"""
    for g in groups or ():
        if '_low_name' in g:
            continue
        g['_low_name'] = (g.get('name') or '').lower()
        for cl in g.get('clusters', ()):
            if '_low' not in cl:
                cl['_low'] = [((m.get('line_text') or '').lower(),
                               (m.get('cross_text') or '').lower())
                              for m in cl.get('lines', ())]


def plan_row_cost(p):
    """估算该组会插入多少行（用于按行预算切帧）"""
    shown = min(3, len(p['visible']))
    return 1 + shown + (1 if len(p['visible']) > 3 else 0)


def parse_excel_loc(line_text):
    """从行文本解析 Excel 定位 [Sheet!Cell]，返回 (sheet, cell) 或 None"""
    if not line_text:
        return None
    m = re.match(r'^\[([^\]]+?)!([A-Za-z]+\d+)\]', line_text)
    if m:
        return m.group(1), m.group(2)
    return None


def snapshot_db(db_path, backup_dir, tag):
    """危险操作前对 index.db 打即时快照（VACUUM INTO 一致快照）。
    命名 presnap_{tag}_时间戳.db：不参与常规 index_*.db 轮换，
    每个 tag 单独保留最近 3 份。返回快照路径，失败返回 None。"""
    if not os.path.exists(db_path):
        return None
    try:
        os.makedirs(backup_dir, exist_ok=True)
        dest = os.path.join(
            backup_dir,
            'presnap_%s_%s.db' % (tag, time.strftime('%Y%m%d_%H%M%S')))
        conn = sqlite3.connect(db_path, timeout=30)
        try:
            conn.execute('VACUUM INTO ?', (dest,))
        finally:
            conn.close()
        olds = sorted(glob.glob(
            os.path.join(backup_dir, 'presnap_%s_*.db' % tag)))
        for old in olds[:-3]:
            try:
                os.remove(old)
            except OSError:
                pass
        return dest
    except Exception:
        return None


def page_label(line_text, line_no=0, name=''):
    """结果树位置标签的页码：PDF/PPT 取真实页码，其余格式无页码概念 → 略"""
    pg = extract_page(line_text)
    return f'P{pg}' if pg else ''


def snippet(text, kw, fuzzy=False, width=200, lead=10):
    """列表片段：关键词尽量靠前（前留 lead 字上下文），保证在结果列
    可视宽度内直接看到命中词；模糊行定位一字之差的实际片段。"""
    if not text:
        return ''
    pos = text.lower().find(kw.lower()) if kw else -1
    if pos < 0 and fuzzy and kw:
        pos = find_fuzzy_pos(text, kw)
    if pos < 0:
        return text[:width]
    start = max(0, pos - lead)
    end = min(len(text), start + width)
    seg = text[start:end]
    # 列表是 ttk.Treeview，单元格内没法给局部文字上色，
    # 只能用【】把命中的词框出来（预览区是黄底红字高亮）
    m0, m1 = pos - start, min(pos - start + len(kw), len(seg))
    if 0 <= m0 < len(seg) and m1 > m0:
        seg = seg[:m0] + '【' + seg[m0:m1] + '】' + seg[m1:]
    return (('…' if start > 0 else '')
            + seg
            + ('…' if end < len(text) else ''))


def bar_pts(cx, half, y0, y1, r):
    """柱子的圆角矩形点集。Tk 没有圆角矩形，靠「每个角点写两遍」把样条
    拉成直角带圆（create_polygon(smooth=True)）；半径自动收缩，不超过
    半宽/半高，柱子很细时退化成直角矩形。"""
    x0, x1 = cx - half, cx + half
    r = max(0.0, min(r, float(half), (y1 - y0) / 2.0))
    if r < 0.5:
        return [x0, y0, x1, y0, x1, y1, x0, y1]
    return [x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r,
            x1, y1 - r, x1, y1, x1 - r, y1, x0 + r, y1,
            x0, y1, x0, y1 - r, x0, y0 + r, x0, y0]


def find_acrobat():
    """查找 Adobe Acrobat/Reader（打开 PDF 时可翻页+高亮搜索词）"""
    import winreg
    for key in (r'SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\Acrobat.exe',
                r'SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\AcroRd32.exe'):
        for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            try:
                with winreg.OpenKey(root, key) as k:
                    val, _ = winreg.QueryValueEx(k, None)
                    if val and os.path.exists(val):
                        return val
            except OSError:
                pass
    for c in (r'C:\Program Files\Adobe\Acrobat DC\Acrobat\Acrobat.exe',
              r'C:\Program Files (x86)\Adobe\Acrobat Reader DC\Reader\AcroRd32.exe'):
        if os.path.exists(c):
            return c
    return None


def find_sumatra(app_dir, library_dir):
    """查找 SumatraPDF（绿色免费 PDF 阅读器，支持 -page N -search 高亮）。
    优先级：打包内置版 → 程序目录内捆绑版 → 常见安装路径 → 注册表 App Paths。"""
    import winreg
    # 0. 打包内置版（PyInstaller _internal）与可执行文件同目录
    for d in (getattr(sys, '_MEIPASS', ''), os.path.dirname(sys.executable)):
        if d:
            p = os.path.join(d, 'SumatraPDF.exe')
            if os.path.exists(p):
                return p
    # 1. 程序目录/库目录内捆绑版（便携部署）
    for d in (app_dir, library_dir):
        p = os.path.join(d, 'SumatraPDF.exe')
        if os.path.exists(p):
            return p
    # 2. 常见安装路径
    for c in (r'C:\Program Files\SumatraPDF\SumatraPDF.exe',
              r'C:\Program Files (x86)\SumatraPDF\SumatraPDF.exe',
              os.path.expandvars(r'%LOCALAPPDATA%\SumatraPDF\SumatraPDF.exe')):
        if os.path.exists(c):
            return c
    # 3. 注册表 App Paths
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
            r'SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\SumatraPDF.exe'
            ) as k:
            val, _ = winreg.QueryValueEx(k, None)
            if val and os.path.exists(val):
                return val
    except OSError:
        pass
    return None
