# -*- coding: utf-8 -*-
"""联网检索回归：关键词同义扩展 / 国标网页解析 / 联网窗口端到端。

离线可跑（解析用内置 HTML 片段，联网调用全部被 monkeypatch）。
"""
import os
import re
import sys
import tempfile
import time
import tkinter as tk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import indexer
import main
import websearch

# ---------- 临时库，不碰真实索引 ----------
main.App._start_hotkeys = lambda self: None
db = os.path.join(tempfile.mkdtemp(prefix='jzl_'), 't.db')
lib = tempfile.mkdtemp(prefix='jzl_lib_')
_old_lib = indexer.LIBRARY_DIR
try:
    indexer.LIBRARY_DIR = lib
    f = os.path.join(lib, 'x.txt')
    with open(f, 'w', encoding='utf-8') as fh:
        fh.write('第一条 总则\n本制度依据《安全生产法》制定\n')
    indexer.index_single_file(f, db)
finally:
    indexer.LIBRARY_DIR = _old_lib
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


# ---------- 1. 关键词同义扩展 ----------
check('塔吊 → 补出正式词 塔式起重机', '塔式起重机' in websearch.expand_keyword('塔吊'))
check('原词始终排第一',
      websearch.expand_keyword('塔吊')[0] == '塔吊')
check('别名去重且限量 3',
      len(websearch.expand_keyword('塔吊')) == 3
      and len(set(websearch.expand_keyword('塔吊'))) == 3)
check('生词只返回原词',
      websearch.expand_keyword('安全生产') == ['安全生产'])
check('空关键词返回空表', websearch.expand_keyword('   ') == [])
check('merge_terms 合并去重且原词组优先',
      websearch.merge_terms(['塔吊', '塔式起重机'],
                            ['塔式起重机', '起重机械']) ==
      ['塔吊', '塔式起重机', '起重机械'])
check('merge_terms 限量 6 个',
      len(websearch.merge_terms([f'w{i}' for i in range(10)])) ==
      websearch.MAX_QUERY_TERMS)
check('「起重机」不做别名扩展（避免法规库拉进一屏农业机械噪音）',
      websearch.expand_keyword('起重机') == ['起重机'])
check('title_hits 认下发词而不是只认原词',
      websearch.title_hits(['塔吊', '塔式起重机'],
                           'GB 5144-2026 塔式起重机安全规程') == 1
      and websearch.title_hits(['起重机'], 'GB 5144-2026 塔式起重机安全规程') == 1)
check('国标跳转指向官方在线预览页（不再只是信息页）',
      'showGb?type=online' in websearch.open_url_of(
          {'source': 'samr', 'hcno': 'X'})
      and websearch.open_url_of(
          {'source': 'flk', 'detail_url': 'u'}) == 'u')

# ---------- 2. 国标结果页解析（内置 HTML 片段，不联网） ----------
_FIXTURE = """
<tbody><tr><th width="50">序号</th><th>标准号</th></tr></tbody>
<tbody>
<tr>
  <td>1</td>
  <td><a href="javascript:void(0)" onclick="showInfo('A1B2C3D4E5F601');">GB/T 22437.3-2025</a></td>
  <td></td><td><span class="label label-warning">采</span></td>
  <td class="mytxt"><a href="javascript:void(0)" onclick="showInfo('A1B2C3D4E5F601');">起重机 载荷与载荷组合的设计原则 第3部分：塔式起重机</a></td>
  <td>推标</td><td><span class="text-success">现行</span></td>
  <td>2025-10-31 00:00:00.0</td><td>2026-05-01 00:00:00.0</td>
  <td><button onclick="showInfo('A1B2C3D4E5F601');">查看详细</button></td>
</tr>
<tr>
  <td>2</td>
  <td><a href="javascript:void(0)" onclick="showInfo('A1B2C3D4E5F602');">GB 5144-2026</a></td>
  <td>中文</td><td></td>
  <td class="mytxt">塔式起重机安全规程</td>
  <td>强标</td><td><span>即将实施</span></td>
  <td>2026-01-28 00:00:00.0</td><td></td>
  <td><button onclick="showInfo('A1B2C3D4E5F602');">查看详细</button></td>
</tr>
<tr>
  <td>3</td>
  <td><a href="javascript:void(0)" onclick="showInfo('A1B2C3D4E5F603');">GB/T 9999-1990</a></td>
  <td></td><td></td>
  <td>已废止的老标准&nbsp;测试</td>
  <td>推标</td><td><span>废止</span></td>
  <td>1990-01-01 00:00:00.0</td><td>1991-01-01 00:00:00.0</td>
  <td><button onclick="showInfo('A1B2C3D4E5F603');">查看详细</button></td>
</tr>
</tbody>
"""


class _Resp:
    text = _FIXTURE
    encoding = 'utf-8'

    def raise_for_status(self):
        pass


_orig_get = websearch.requests.get
websearch.requests.get = lambda *a, **k: _Resp()
try:
    rows = websearch._openstd_query('塔式起重机')
finally:
    websearch.requests.get = _orig_get
check('解析出 3 行（表头行被跳过）', len(rows) == 3)
check('标准号+名称合成标题',
      rows[0]['title'] == 'GB/T 22437.3-2025 起重机 载荷与载荷组合的设计原则 '
                          '第3部分：塔式起重机')
check('来源标识为 samr / 国家标准全文公开',
      rows[0]['source'] == 'samr'
      and rows[0]['source_name'] == '国家标准全文公开')
check('现行 → 有效', rows[0]['status'] == '有效')
check('即将实施 → 尚未生效', rows[1]['status'] == '尚未生效')
check('废止 → 已废止', rows[2]['status'] == '已废止')
check('发布日期取到日期部分', rows[0]['publish'] == '2025-10-31')
check('实施日期为空时不报错', rows[1]['expiry'] == '')
check('类别进类型列（强标）', rows[1]['type'] == '强标')
check('实体 &nbsp; 被还原为空格',
      rows[2]['title'] == 'GB/T 9999-1990 已废止的老标准 测试')
check('详情页链接指向 newGbInfo',
      rows[0]['detail_url'] ==
      'https://openstd.samr.gov.cn/bzgk/gb/newGbInfo?hcno=A1B2C3D4E5F601')

# ---------- 3. 多词合并去重 + 相关性排序 ----------
_orig_query = websearch._openstd_query


def _fake_query(kw):
    if kw == '塔吊':
        return [{'source': 'samr', 'source_name': '国家标准全文公开',
                 'title': '无关标准 X', 'std_code': 'X1', 'bbbs': '',
                 'office': '', 'publish': '', 'expiry': '', 'type': '',
                 'status_code': '', 'status': '', 'detail_url': ''}]
    return [{'source': 'samr', 'source_name': '国家标准全文公开',
             'title': f'塔吊 塔式起重机安全规程（来自 {kw}）', 'std_code': 'GB 5144',
             'bbbs': '', 'office': '', 'publish': '', 'expiry': '', 'type': '',
             'status_code': '', 'status': '', 'detail_url': ''}]


websearch._openstd_query = _fake_query
try:
    merged, total = websearch.search_openstd('塔吊')
    check('多词结果合并去重（同标准号只留一条）', len(merged) == 2 and total == 2)
    check('标题完整含关键词的排前面', merged[0]['title'].startswith('塔吊'))
finally:
    websearch._openstd_query = _orig_query

_rows = [{'title': '残疾人教育条例', 'status_code': '3'},
         {'title': '施工升降机安全规程', 'status_code': '1'}]
websearch._rank_results(_rows, '人货梯', ['人货梯', '施工升降机'])
check('标题命中扩展词的结果排到最前（扩展词不只是发请求）',
      _rows[0]['title'] == '施工升降机安全规程')

# ---------- 4. search_all 路由到国标来源 ----------
_orig_openstd = websearch.search_openstd
_called = []
websearch.search_openstd = lambda kw, **k: (_called.append(kw), ([], 0))[1]
try:
    websearch.search_all('塔吊', ['samr'])
    check('search_all 已接上国标来源', _called == ['塔吊'])
finally:
    websearch.search_openstd = _orig_openstd

# ---------- 4b. 行业 / 地方标准信息服务平台（sacinfo JSON 接口） ----------
_STD_FIXTURE = {
    'current': 1, 'pages': 2, 'records': [
        {'chName': '起重机械维护保养规范', 'code': 'DB61/T 2130-2025',
         'chargeDept': '陕西省市场监督管理局', 'industry': '陕西省',
         'issueDate': 1757952000000, 'actDate': 1760544000000,
         'pk': 'PK1', 'status': '现行'},
        {'chName': '已废止的老标准', 'code': 'DB61/T 0001-1990',
         'chargeDept': '', 'industry': '陕西省', 'issueDate': 0,
         'actDate': None, 'pk': 'PK2', 'status': '废止'},
        {'chName': '', 'code': 'X', 'pk': 'PK3', 'status': '现行'},
    ],
}


class _StdResp:
    def raise_for_status(self):
        pass

    def json(self):
        return _STD_FIXTURE


_orig_post = websearch.requests.post
websearch.requests.post = lambda *a, **k: _StdResp()
try:
    rows = websearch._std_query('dbba', '起重机')
finally:
    websearch.requests.post = _orig_post
check('地方标准接口解析出 2 行（无名称的跳过）', len(rows) == 2)
check('标准号+名称合成标题',
      rows[0]['title'] == 'DB61/T 2130-2025 起重机械维护保养规范')
check('来源标识为 dbba / 地方标准信息服务平台',
      rows[0]['source'] == 'dbba'
      and rows[0]['source_name'] == '地方标准信息服务平台')
check('现行 → 有效（带 status_code 3，有效版本才能置顶）',
      rows[0]['status'] == '有效' and rows[0]['status_code'] == '3')
check('废止 → 已废止（status_code 1）',
      rows[1]['status'] == '已废止' and rows[1]['status_code'] == '1')
check('毫秒时间戳转成日期',
      bool(re.match(r'\d{4}-\d{2}-\d{2}$', rows[0]['publish']))
      and bool(re.match(r'\d{4}-\d{2}-\d{2}$', rows[0]['expiry'])))
check('时间戳缺失或为 0 时日期留空',
      rows[1]['publish'] == '' and rows[1]['expiry'] == '')
check('归口单位进发布机关列', rows[0]['office'] == '陕西省市场监督管理局')
check('行业/省份进类型列', rows[0]['type'] == '陕西省')
check('详情页链接指向 stdDetail',
      rows[0]['detail_url'] == 'https://dbba.sacinfo.org.cn/stdDetail/PK1')
check('_ms_date 非法输入返回空串',
      websearch._ms_date('') == '' and websearch._ms_date(None) == ''
      and websearch._ms_date('abc') == '' and websearch._ms_date(0) == '')

_orig_stdq = websearch._std_query


def _fake_stdq(sid, kw, size=10):
    return [{'source': sid, 'source_name': websearch.source_name(sid),
             'title': f'JGJ 59-2011 {kw}', 'std_code': 'JGJ 59-2011',
             'bbbs': '', 'office': '', 'publish': '', 'expiry': '', 'type': '',
             'status_code': '', 'status': '', 'detail_url': ''}]


websearch._std_query = _fake_stdq
try:
    _m, _t = websearch.search_std('hbba', '塔吊')
finally:
    websearch._std_query = _orig_stdq
check('行业标准多词结果按标准号去重（3 个词只留 1 条）',
      len(_m) == 1 and _t == 1)

_orig_std = websearch.search_std
_std_called = []
websearch.search_std = lambda sid, kw, **k: (
    _std_called.append((sid, kw)), ([], 0))[1]
try:
    websearch.search_all('塔吊', ['hbba', 'dbba'])
    check('search_all 已接上行业/地方标准来源',
          sorted(_std_called) == [('dbba', '塔吊'), ('hbba', '塔吊')])
finally:
    websearch.search_std = _orig_std

try:
    websearch.download_item({'source': 'hbba'}, tempfile.mkdtemp())
    check('行业标准下载给出详情页指引', False)
except RuntimeError as e:
    check('行业标准下载给出详情页指引', '详情页' in str(e))

# ---------- 4c. 中国政府网政策文件库（JSON 在 searchVO.listVO） ----------
_GOV_FIXTURE = {
    'code': 200, 'msg': '操作成功', 'data': None,
    'searchVO': {'totalCount': 15, 'listVO': [
        {'title': '国务院办公厅关于进一步加强<em>安全</em>生产工作的通知',
         'pcode': '国办发〔2026〕26号', 'puborg': '国务院办公厅',
         'pubtimeStr': '2026.09.17', 'pubtime': 1789574400000,
         'childtype': '市场监管、安全生产监管\\安全生产监管',
         'url': 'https://www.gov.cn/zhengce/zhengceku/202609/content_1.htm'},
        {'title': '', 'url': 'https://www.gov.cn/x.htm'},
    ]},
}


class _GovResp:
    def raise_for_status(self):
        pass

    def json(self):
        return _GOV_FIXTURE


_orig_get2 = websearch.requests.get
websearch.requests.get = lambda *a, **k: _GovResp()
try:
    grows = websearch._gov_query('安全生产')
finally:
    websearch.requests.get = _orig_get2
check('政策库解析出 1 行（空标题跳过）', len(grows) == 1)
check('标题里的 <em> 高亮标签被清掉',
      grows[0]['title'] == '国务院办公厅关于进一步加强安全生产工作的通知')
check('来源标识为 gov / 中国政府网 政策文件库',
      grows[0]['source'] == 'gov'
      and grows[0]['source_name'] == '中国政府网 政策文件库')
check('发文机关 / 文号 / 日期入列',
      grows[0]['office'] == '国务院办公厅'
      and grows[0]['std_code'] == '国办发〔2026〕26号'
      and grows[0]['publish'] == '2026-09-17')
check('分类只取大类（反斜杠前一段）',
      grows[0]['type'] == '市场监管、安全生产监管')
check('详情页链接入 detail_url',
      grows[0]['detail_url'].endswith('content_1.htm'))

_DIV_FIXTURE = """
<div class="pages_content" id="UCAP-CONTENT"><p>各省、自治区：</p>
<p>　一、落实<strong>安全</strong>生产责任</p><div>附件：<a href="x">表</a>
</div><p>二、加强监管。</p></div>
<div class="pages_print">打印</div>
"""
_gov_text = websearch._div_text(_DIV_FIXTURE, 'UCAP-CONTENT')
check('正文容器解析：只取容器内、嵌套 div 不截断',
      '落实安全生产责任' in _gov_text and '加强监管' in _gov_text
      and '打印' not in _gov_text and '<' not in _gov_text)
check('正文容器：全角空格被清、段落成行',
      '一、落实安全生产责任' in _gov_text.split('\n')
      and '各省、自治区：' in _gov_text.split('\n'))
check('政策库正文容器取不到时返回空串（好让上层报错）',
      websearch._div_text('<div>别的</div>', 'UCAP-CONTENT') == '')

_orig_gov = websearch.search_gov
_gov_called = []
websearch.search_gov = lambda kw, **k: (_gov_called.append(kw), ([], 0))[1]
try:
    websearch.search_all('安全生产', ['gov'])
    check('search_all 已接上政策文件库来源', _gov_called == ['安全生产'])
finally:
    websearch.search_gov = _orig_gov

check('政策库与法规库同属「能抓正文」来源',
      websearch.TEXT_SOURCES == ('flk', 'gov'))

# ---------- 5. 下载入口文案 ----------
try:
    websearch.download_item({'source': 'samr'}, tempfile.mkdtemp())
    check('国标下载给出详情页指引', False)
except RuntimeError as e:
    check('国标下载给出详情页指引', '详情页' in str(e))

# ---------- 6. 联网检索窗口端到端 ----------
check('来源表里只剩住建部是网页跳转',
      [s['id'] for s in websearch.SOURCES if not s['has_api']] == ['mohurd'])
check('国标来源已被标记为可出结果',
      any(s['id'] == 'samr' and s['has_api'] for s in websearch.SOURCES))
check('行业/地方标准来源已入表且可出结果',
      all(any(s['id'] == i and s['has_api'] for s in websearch.SOURCES)
          for i in ('hbba', 'dbba'))
      and websearch.TITLE_VERIFY == ('samr', 'hbba', 'dbba'))

app = main.App(root)
app.dark.set(False)
app._apply_ui_theme()
root.update_idletasks()

_fake_item = {'source': 'samr', 'source_name': '国家标准全文公开',
              'title': 'GB 5144-2026 塔式起重机安全规程', 'std_code': 'GB 5144',
              'bbbs': '', 'office': '国家标准化管理委员会', 'publish': '2026-01-28',
              'expiry': '', 'type': '强标', 'status_code': '', 'status': '尚未生效',
              'detail_url': 'https://openstd.samr.gov.cn/bzgk/gb/newGbInfo?hcno=X'}

_mode = ['hit']
_last_call = {}


def _fake_all(kw, srcs, callback=None, **k):
    """记下主界面实际下发的查询词，用来断言同义词确实带过去了。"""
    _last_call.clear()
    _last_call.update(kw=kw, terms=k.get('terms'))
    return ({'samr': [_fake_item], 'flk': []}, {}) if _mode[0] == 'hit' else ({}, {})


websearch.search_all = _fake_all
websearch.get_jump_url = lambda sid, kw: ''
_opened = []
main.webbrowser.open = lambda u, *a, **k: _opened.append(u)

import types


class _SyncThread:
    """把窗口里的后台搜索线程改成同步执行：
    无 mainloop 时子线程调 win.after 会抛 'main thread is not in main loop'。"""

    def __init__(self, target=None, daemon=None, args=(), kwargs=None):
        self._t, self._a = target, args or ()

    def start(self):
        self._t(*self._a)

    def join(self, timeout=None):
        pass


_orig_threading = main.threading
# 假的 threading 要跟真模块一样支持 Lock：窗口里核对正文用到了
main.threading = types.SimpleNamespace(Thread=_SyncThread,
                                      Lock=_orig_threading.Lock)

# ---------- 7. 主界面入口（原藏在 ⚙ 菜单里） ----------
check('主界面有联网检索入口按钮',
      hasattr(app, 'web_btn') and '联网检索' in str(app.web_btn.cget('text')))
check('入口在顶栏工具栏（与扫描入库/书签同排）',
      app.web_btn.master is app.scan_btn.master
      and app.web_btn.pack_info()['side'] == 'left')
check('入口排在「书签」之后（工具栏末位）',
      app.web_btn.master.winfo_children().index(app.web_btn)
      > app.web_btn.master.winfo_children().index(
          [w for w in app.web_btn.master.winfo_children()
           if w.winfo_class() == 'TButton'
           and '书签' in str(w.cget('text'))][0]))
check('入口不在搜索行（不再挤搜索框那一排）',
      app.web_btn.master is not app.search_btn.master)
_stt = __import__('tkinter.ttk', fromlist=['Style']).Style()
_brand = ('#2F5E8C', '#0F6CBD', '#2889DE')
check('入口用工具栏普通按钮，不抢「搜索」的主按钮样式',
      str(_stt.lookup(app.search_btn.cget('style'), 'background')).upper()
      in _brand
      and str(_stt.lookup(app.web_btn.cget('style'), 'background')).upper()
      not in _brand)
check('入口按钮样式与同排按钮一致（AppBar 风格）',
      str(_stt.lookup(app.web_btn.cget('style'), 'font'))
      == str(_stt.lookup(app.scan_btn.cget('style'), 'font')))
check('入口按钮的克隆样式保留 MDL2 图标字体（地球字形能显示）',
      'MDL2' in str(_stt.lookup(app.web_btn.cget('style'), 'font')))
_mm = app.root.nametowidget(app.more_btn.cget('menu'))
_menu_labels = []          # 分隔线没有 -label，跳过
for _i in range(_mm.index('end') + 1):
    try:
        _menu_labels.append(_mm.entrycget(_i, 'label'))
    except tk.TclError:
        pass
check('⚙菜单里已移除重复的联网检索项',
      not any('联网检索' in s for s in _menu_labels))

# 点主界面按钮应直接开窗（顺带验证命令已接线）
app.web_btn.invoke()
root.update_idletasks()
win = app._wins.get('websearch')      # _get_win 对已开窗口返回 None
check('点主界面入口即打开联网检索窗口', win is not None)
kids = win.winfo_children()


def _all(cls):
    out, stack = [], list(kids)
    while stack:
        w = stack.pop(0)
        if w.winfo_class() == cls:
            out.append(w)
        stack.extend(w.winfo_children())
    return out


def _find(cls, text=None):
    for w in _all(cls):
        if text is None or str(w.cget('text')) == text:
            return w
    return None


entry = _find('TEntry')
btn = _find('TButton', '搜索')
tree = _find('Treeview')
check('窗口里找到关键词框/搜索按钮/结果表',
      entry is not None and btn is not None and tree is not None)

_labels = [str(w.cget('text')) for w in _all('TCheckbutton')]
_src_rows = {w.master for w in _all('TCheckbutton')}
try:
    _scale = max(float(root.tk.call('tk', 'scaling')) / (96.0 / 72.0), 1.0)
except Exception:
    _scale = 1.0
check('国标勾选项不再标注「网页跳转」',
      '国家标准全文公开' in _labels
      and not any(t.startswith('国家标准全文公开（') for t in _labels))
check('政府网勾选项已去掉「网页跳转」（现在能出结果）',
      '中国政府网 政策文件库' in _labels
      and not any(t.startswith('中国政府网 政策文件库（') for t in _labels))
check('只剩住建部勾选项标注「网页跳转」',
      sum(1 for t in _labels if '网页跳转' in t) == 1
      and any(t.startswith('住建部标准公告') for t in _labels if '网页跳转' in t))
check('新增两个标准平台勾选项，且不标注「网页跳转」',
      all(any(t.startswith(n) for t in _labels)
          for n in ('行业标准信息服务平台', '地方标准信息服务平台'))
      and not any('地方标准信息服务平台（' in t for t in _labels))
check('来源勾选分两行后都排得进窗口宽度（新增来源后不挤爆）',
      len(_src_rows) == 2
      and all(r.winfo_reqwidth() <= 1056 * _scale for r in _src_rows))
check('底部「打开下载目录」按钮已建出（曾因前向引用整窗构建失败）',
      any('打开下载目录' in str(w.cget('text')) for w in _all('TButton')))
check('底部操作按钮齐全',
      all(any(k in str(w.cget('text')) for w in _all('TButton'))
          for k in ('查看网页', '下载入库', '全部下载入库', '关闭')))

# 状态栏：先按初始文案抓住它（搜索后文案会被改写）
_status = [w for w in _all('TLabel')
           if str(w.cget('text')).startswith('输入关键词后点')]
check('状态栏已建出', len(_status) == 1)

entry.insert(0, '塔吊')
btn.invoke()
for _ in range(80):
    root.update()
    if tree.get_children():
        break
    time.sleep(0.05)
check('搜索结果进表格', len(tree.get_children()) == 1)
check('表格显示来源+标题',
      list(tree.item(tree.get_children()[0], 'values'))[:2]
      == ['国家标准全文公开', 'GB 5144-2026 塔式起重机安全规程'])

check('状态栏报出条数并说明国标走官网在线预览',
      bool(_status) and '共 1 条' in str(_status[0].cget('text'))
      and '在线预览' in str(_status[0].cget('text')))
check('下发给来源库的查询词含行话别名',
      _last_call.get('terms', [])[:3] == ['塔吊', '塔式起重机', '起重机械'])
check('命中列列名已改为「命中」',
      str(tree.heading('verify', 'text')) == '命中')
check('国标行按标题核对，不再留空「—」',
      list(tree.item(tree.get_children()[0], 'values'))[2] == '✓ 标题')

# 同义词（本地检索那套，默认开）也要参与联网检索
check('联网默认带出本地同义词（安全员 → 专职安全管理员）',
      '专职安全管理员' in app._web_locate_terms('安全员'))
app.syn_var.set(False)
check('本地同义词开关关掉后联网也不扩展',
      '专职安全管理员' not in app._web_locate_terms('安全员'))
app.syn_var.set(True)
entry.delete(0, 'end')
entry.insert(0, '安全员')
btn.invoke()
for _ in range(80):
    root.update()
    if _status and '已扩展' in str(_status[0].cget('text')):
        break
    time.sleep(0.05)
check('同义词随关键词一起下发',
      '专职安全管理员' in (_last_call.get('terms') or []))
check('状态栏说明扩展了什么',
      '已扩展' in str(_status[0].cget('text'))
      and '专职安全管理员' in str(_status[0].cget('text')))

# 空结果：应提示正式叫法
entry.delete(0, 'end')
entry.insert(0, '塔吊')
_mode[0] = 'miss'
btn.invoke()
for _ in range(40):
    root.update()
    if _status and '俗称' in str(_status[0].cget('text')):
        break
    time.sleep(0.05)
_hint = str(_status[0].cget('text')) if _status else ''
check('零结果提示改用正式叫法', '塔式起重机' in _hint)
check('零结果提示不再只写「未找到相关结果」', '未找到相关结果' not in _hint)

main.threading = _orig_threading
win.destroy()
root.destroy()
print(f'\n全部通过：{_passed} 项')