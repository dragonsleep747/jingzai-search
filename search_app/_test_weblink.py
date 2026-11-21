# -*- coding: utf-8 -*-
"""预览区正文 URL 可点击回归（无头可跑）"""
import os
import sys
import tempfile
import tkinter as tk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import indexer
import main

main.App._start_hotkeys = lambda self: None
db = os.path.join(tempfile.mkdtemp(prefix='jzl_'), 't.db')
lib = tempfile.mkdtemp(prefix='jzl_lib_')
URL = ('https://flk.npc.gov.cn/detail?id=ff8081817a66b816017a7956b7db0ad4'
       '&fileId=&type=&title=%E4%B8%AD%E5%8D%8E')
f = os.path.join(lib, 'x.txt')
with open(f, 'w', encoding='utf-8') as fh:
    fh.write('第一条 总则\n'
             f'来源：国家法律法规数据库 {URL}\n'
             '本制度依据《中华人民共和国安全生产法》第十条制定\n')
indexer.index_single_file(f, db)
main.DB_PATH = db

try:
    from tkinterdnd2 import TkinterDnD
    root = TkinterDnD.Tk()
except Exception:
    root = tk.Tk()
root.withdraw()

_passed = 0
def check(name, cond):
    global _passed
    assert cond, f'FAIL: {name}'
    _passed += 1
    print(f'  ok {_passed:02d}  {name}')

app = main.App(root)
root.update_idletasks()
app.dark.set(False)
app._apply_ui_theme()
root.update_idletasks()
app._show_preview(f, 2)
root.update_idletasks()

txt = app.preview.get('1.0', 'end')
check('正文含来源链接原文', URL in txt)
check('个 URL 被标成 weblink 标签', len(app.preview.tag_ranges('weblink')) == 2)
rng = app.preview.tag_ranges('weblink')
check('weblink 文本 = 完整 URL', app.preview.get(rng[0], rng[1]) == URL)
check('weblink 下划线', str(app.preview.tag_cget('weblink', 'underline'))
      in ('1', 'True', 'true'))
check('weblink 用品牌棕金色',
      app.preview.tag_cget('weblink', 'foreground').lower() == '#8b6f47')

# 引用链接仍可用（同一次渲染里两种链接共存）
app._show_preview(f, 3)
root.update_idletasks()
_r = app.preview.tag_ranges('refmiss')   # 目标未入库 → refmiss
check('第 3 行《引用》仍渲染为可点击链接',
      len(_r) == 2 and app.preview.get(_r[0], _r[1])
      == '《中华人民共和国安全生产法》第十条')
_w = app.preview.tag_ranges('weblink')   # 整段渲染会带上第 2 行的链接
check('URL 未被《引用》吞掉', all(
    app.preview.get(_w[i], _w[i + 1]) == URL for i in range(0, len(_w), 2)))

# ---------- 只读保护：正文（含链接）不允许被用户删改 ----------
check('预览为只读状态', app.preview.cget('state') == 'disabled')
_before = app.preview.get('1.0', 'end')
try:
    app.preview.tk.call(app.preview._w, 'insert', '1.0', 'XXXX')
except Exception:
    pass
check('外部插入被 Tk 挡下', app.preview.get('1.0', 'end') == _before)
try:
    app.preview.tk.call(app.preview._w, 'delete', '1.0', '1.4')
except Exception:
    pass
check('外部删除被 Tk 挡下', app.preview.get('1.0', 'end') == _before)
app.preview.tag_add('sel', '1.0', '1.5')
for ev in ('<BackSpace>', '<Delete>', '<KeyPress-z>', '<<Cut>>'):
    app.preview.event_generate(ev)
root.update()
check('按键/删除/剪切都改不动正文',
      app.preview.get('1.0', 'end') == _before)
_r2 = app.preview.tag_ranges('weblink')
check('链接标签范围完好未错位',
      len(_r2) == 2 and app.preview.get(_r2[0], _r2[1]) == URL)
app._show_preview(f, 1)      # 程序内部写入必须仍然可用
root.update_idletasks()
check('内部刷新仍能正常写入正文', '本制度依据' in app.preview.get('1.0', 'end'))
_orig_status = app.status.cget('text')
check('已挂上只读提示的按键绑定',
      bool(app.preview.bind('<KeyPress>')))
_Ev = type('Ev', (), {})
def _key(keysym, char):
    e = _Ev()
    e.keysym, e.char = keysym, char
    app._on_preview_key(e)
    return app.status.cget('text')
app.status.configure(text='')
_msg = _key('z', 'z')
check('敲字时提示「只读」', '只读' in _msg)
app.status.configure(text='')
check('BackSpace 也提示「只读」', '只读' in _key('BackSpace', '\x08'))
app.status.configure(text='')
check('Ctrl+C 不打扰用户',
      _key('c', '\x03') == '' and _key('Left', '') == '')
app.status.configure(text=_orig_status)

# 单击 → 调 webbrowser.open（拦截，不真开浏览器）
opened = []
_orig_open = main.webbrowser.open
main.webbrowser.open = lambda u, *a, **k: opened.append(u)
try:
    app._show_preview(f, 2)
    root.update_idletasks()
    rng = app.preview.tag_ranges('weblink')
    _idx = rng[0]
    _orig_index = app.preview.index       # 用完必须还原：
    app.preview.index = lambda spec, _i=_idx: _i   # 绕过坐标换算
    app._on_weblink_click(type('E', (), {'x': 0, 'y': 0})())
finally:
    main.webbrowser.open = _orig_open
    app.preview.index = _orig_index       # 否则后续渲染拿到 Tcl_Obj 会崩
check('单击 weblink 调起浏览器且 URL 正确', opened == [URL])
check('状态栏提示已打开链接', '已在浏览器打开' in app.status.cget('text'))

# 主题切换后仍保留链接色（不丢样式）
app.dark.set(True)
app._apply_ui_theme()
root.update_idletasks()
check('暗色下 weblink 换色为 #5CB3FF',
      app.preview.tag_cget('weblink', 'foreground').lower() == '#5cb3ff')
app.dark.set(False)
app._apply_ui_theme()
check('浅色下 weblink 回到 #2F5E8C',
      app.preview.tag_cget('weblink', 'foreground').lower() == '#8b6f47')

# ---------- 关键词特殊标注：预览黄底红字 + 列表【】框选 ----------
app.keyword.set('安全生产法')
app._syn_map = {}
app._show_preview(f, 2)
root.update_idletasks()
_hl = app.preview.tag_ranges('hl')
check('预览里关键词被标了 hl 标签', len(_hl) >= 1)
check('hl 覆盖的正是关键词',
      any(app.preview.get(_hl[i], _hl[i + 1]) == '安全生产法'
          for i in range(0, len(_hl), 2)))
check('hl 是黄底红字',
      app.preview.tag_cget('hl', 'background').lower() == '#fff35f'
      and app.preview.tag_cget('hl', 'foreground').lower() in
      ('#d00000', '#dd0000', '#d00'))

# Tk 里后创建的标签优先级更高：hit（命中行整行底色）必须低于 hl，
# 否则整行底色会把关键词黄底盖住（用户实测反馈的那一幕）
_names = list(app.preview.tk.call(app.preview._w, 'tag', 'names'))
check('hl 优先级高于 hit（命中行里仍能看见黄底关键词）',
      _names.index('hl') > _names.index('hit'))
check('hl 仍低于 fhl / syn（过滤词、同义词优先级不降级）',
      _names.index('hl') < _names.index('fhl')
      and _names.index('hl') < _names.index('syn'))

# 列表片段：Treeview 没法给单元格局部上色，用【】框出关键词
_sn = main.App._snippet('职工教育经费按公司职工工资总额的2.5%计提', '教育经费')
check('列表片段把关键词框成【教育经费】', '【教育经费】' in _sn)
check('框选后关键词仍完整保留', '教育经费' in _sn.replace('【', '').replace('】', ''))
_sn2 = main.App._snippet('前文很长很长很长很长很长很长很长' * 3 + '安全生产',
                         '安全生产')
check('片段被截断时也带省略号且框选正常',
      _sn2.startswith('…') and '【安全生产】' in _sn2)
_sn3 = main.App._snippet('无关文本', '')
check('关键词为空时片段不加标记', '【' not in _sn3)
app.keyword.set('')

print(f'\n全部通过：{_passed} 项')
sys.stdout.flush()
os._exit(0)