# -*- coding: utf-8 -*-
"""
联网检索制度 / 法规 / 标准

内置官方来源，支持关键词搜索、打开网页、下载入库。
所有网络操作均在后台线程执行，UI 通过回调刷新。
"""
import html
import os
import re
import threading
import time
import requests
from urllib.parse import quote, urlparse, parse_qs

# ---------- 来源定义 ----------

SOURCES = [
    {
        'id': 'flk',
        'name': '国家法律法规数据库',
        'base': 'https://flk.npc.gov.cn',
        'desc': '法律 / 行政法规 / 地方性法规 / 司法解释',
        'has_api': True,
    },
    {
        'id': 'gov',
        'name': '中国政府网 政策文件库',
        'base': 'https://www.gov.cn',
        'desc': '国务院文件 / 部门规章 / 政策通知（可下载全文入库）',
        'has_api': True,
    },
    {
        'id': 'samr',
        'name': '国家标准全文公开',
        'base': 'https://openstd.samr.gov.cn',
        'desc': 'GB / GB/T 国家标准',
        'has_api': True,
    },
    {
        'id': 'hbba',
        'name': '行业标准信息服务平台',
        'base': 'https://hbba.sacinfo.org.cn',
        'desc': '行业标准：JB 机械 / TB 铁路 / JT 交通 / DL 电力 / AQ 应急等',
        'has_api': True,
    },
    {
        'id': 'dbba',
        'name': '地方标准信息服务平台',
        'base': 'https://dbba.sacinfo.org.cn',
        'desc': '各省地方标准 DB',
        'has_api': True,
    },
    {
        'id': 'mohurd',
        'name': '住建部标准公告',
        'base': 'https://www.mohurd.gov.cn',
        'desc': '工程建设标准 / 行业标准',
        'has_api': False,
    },
]

# 只能核对标题的来源：标准类库的正文是官方图片版或需登录，取不到文字
TITLE_VERIFY = ('samr', 'hbba', 'dbba')

# 软件内能抓到正文的来源：可双击预览定位关键词，可「下载入库」
TEXT_SOURCES = ('flk', 'gov')

# 行业 / 地方标准信息服务平台（sacinfo），同一个 POST 接口，换域名即可
_STD_BASES = {
    'hbba': 'https://hbba.sacinfo.org.cn',
    'dbba': 'https://dbba.sacinfo.org.cn',
}


def source_name(sid):
    """来源 id → 界面显示名。"""
    for s in SOURCES:
        if s['id'] == sid:
            return s['name']
    return sid

_UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 ' \
      '(KHTML, like Gecko) Chrome/124.0 Safari/537.36'
_TIMEOUT = 20

# FLK sxx 时效性代码（抓包实测）：1=已废止 2=已被修改 3=有效 4=尚未生效
FLK_STATUS = {'1': '已废止', '2': '已修改', '3': '有效', '4': '尚未生效'}
_STATUS_RANK = {'3': 0, '4': 1, '2': 2, '1': 3}

# 国家标准 openstd 状态 → 与法规同口径的时效性
SAMR_STATUS = {'现行': '有效', '即将实施': '尚未生效', '废止': '已废止'}

# 行业/地方标准平台只给中文状态，折成法规库那套数字码，有效版本才能置顶
_STD_CODE = {'有效': '3', '尚未生效': '4', '已废止': '1'}

# 工地行话 → 法规/标准里的正式词（实测：搜「塔吊」两个库都是 0 条）
_ALIASES = {
    '塔吊': ['塔式起重机', '起重机械'],
    # 注意：别给「起重机」加 起重机械 别名。法规库按词切分模糊匹配，
    # 一起查会拉进一屏《XX省农业机械管理条例》（含「机械」），而国标库
    # 自己就能把「起重机械」标题的标准匹配出来，加了只有噪音没有收益。
    '塔式起重机': ['塔吊', '起重机械'],
    '起重机械': ['塔式起重机', '塔吊'],
    '人货梯': ['施工升降机'],
    '龙门架': ['物料提升机'],
    '脚手架': ['扣件式钢管脚手架', '建筑施工脚手架'],
    '模板支架': ['模板工程', '高大模板'],
    '基坑': ['基坑工程', '建筑深基坑'],
    '临边防护': ['洞口作业', '安全防护'],
    '临时用电': ['施工现场临时用电', '施工用电'],
    '配电箱': ['施工现场临时用电', '电气安全'],
    '动火作业': ['焊接与切割', '消防安全'],
    '有限空间': ['受限空间'],
    '危大工程': ['危险性较大的分部分项工程'],
    '安全员': ['专职安全生产管理人员'],
    '专职安全员': ['专职安全生产管理人员'],
    '专职安全生产管理人员': ['专职安全员'],
    '高处作业': ['坠落防护', '高处作业安全'],
    '扬尘': ['施工扬尘', '大气污染防治'],
    '消防': ['建筑防火', '消防安全'],
    '混凝土': ['混凝土结构', '预拌混凝土'],
    '钢筋': ['钢筋工程', '钢筋混凝土'],
}

# 单次查询最多展开成几个词，避免请求过多
_MAX_VARIANTS = 3

# 一个来源最多发几次请求（原词 + 别名 + 同义词合并后的上限）
MAX_QUERY_TERMS = 6


def merge_terms(*groups):
    """合并查询词表：去重、限量，靠前的组优先（原词 > 别名 > 同义词）。"""
    out = []
    for g in groups:
        for t in (g or []):
            t = (t or '').strip()
            if t and t not in out:
                out.append(t)
    return out[:MAX_QUERY_TERMS]


def expand_keyword(keyword, limit=_MAX_VARIANTS):
    """行话 → 正式词：返回 [原词, 别名…]（去重、保序、限量）。

    实测「塔吊」在法规库与国标库都是 0 条，正式叫法「塔式起重机」有结果，
    所以一律拿原词 + 别名一起查，再合并去重。
    """
    kw = (keyword or '').strip()
    if not kw:
        return []
    out = [kw]
    for a in _ALIASES.get(kw, []):
        if a and a not in out:
            out.append(a)
        if len(out) >= limit:
            break
    return out


def _safe_filename(title):
    """清洗为 Windows 合法文件名。"""
    name = re.sub(r'[\\/:*?"<>|]', '_', (title or '未命名').strip())
    return name or '未命名'


def target_path_for(item, save_dir):
    """该结果下载后对应的本地 txt 路径（供查重）。"""
    return os.path.join(save_dir, _safe_filename(item.get('title', '')) + '.txt')


def body_start_line(text):
    """download_flk 写入的元数据头部占几行 → 正文起始行下标（0 基）。

    头部固定为「标题 / 发布机关 / 公布日期 / 施行日期 / 时效性 / 类型 / 来源」
    再空一行，末尾以「来源：」结尾，据此定位比写死行数稳。
    """
    for i, ln in enumerate((text or '').split('\n')[:12]):
        if ln.startswith('来源：'):
            return i + 2
    return 0


def body_of(text):
    """正文部分（去掉 download_flk 写的元数据头部）。"""
    start = body_start_line(text)
    if start <= 0:
        return (text or '').strip()
    return '\n'.join((text or '').split('\n')[start:]).strip()


# 本次运行中已确认「官方取不到正文」的 txt，避免每次双击都白抓一遍
_NO_TEXT_CACHE = set()


def fetch_item_text(item, save_dir, progress=None):
    """取该结果的纯文本，供软件内预览并定位关键词。

    下载目录里已有同名 txt 就直接复用（预览过/下载过的秒开），
    否则抓全文后落盘，下次直接读缓存。
    返回 (text, path, body_start)。仅 TEXT_SOURCES 支持，其余抛 RuntimeError。
    """
    src = item.get('source')
    if src not in TEXT_SOURCES:
        raise RuntimeError('该来源取不到正文，请在浏览器中查看详情页')
    path = target_path_for(item, save_dir)
    text = ''
    if os.path.exists(path) and path not in _NO_TEXT_CACHE:
        try:
            with open(path, encoding='utf-8') as f:
                text = f.read()
        except OSError:
            text = ''
        # 旧版本对扫描件只写下了元数据头部，正文一个字没有。这种缓存必须
        # 丢弃重抓（新版会先试带文字层的 Word 版 OFD），否则永远看不到正文。
        if not body_of(text):
            text = ''
    if not text.strip():
        if src == 'gov':
            path = download_gov(item, save_dir, progress=progress)
        else:
            path = download_flk(item, save_dir, progress=progress)
        with open(path, encoding='utf-8') as f:
            text = f.read()
        if not body_of(text):
            _NO_TEXT_CACHE.add(path)
    return text, path, body_start_line(text)


def _rank_results(items, keyword, terms=None):
    """标题相关性优先（完整含关键词 > 大部分字符命中 > 其余），
    同档内有效版本置顶。稳定排序，其余保持 API 原顺序。

    terms 是实际下发过的词表（原词 + 别名 + 同义词）：任一词完整出现在
    标题里都算第一档。否则扩展查出来的结果会被原词的模糊噪音压在下面
    （搜「人货梯」原词命中的全是「残疾人XX条例」）。
    """
    kw = re.sub(r'\s+', '', keyword or '')
    fulls = [re.sub(r'\s+', '', t) for t in (terms or [keyword]) if t]
    chars = set(kw)
    n = max(len(chars), 1)

    def _key(it):
        t = re.sub(r'\s+', '', it.get('title', ''))
        if any(f and f in t for f in fulls):
            tr = 0
        elif kw and sum(1 for c in chars if c in t) / n >= 0.6:
            tr = 1
        else:
            tr = 2
        return (tr, _STATUS_RANK.get(str(it.get('status_code', '')), 4))

    items.sort(key=_key)


def _make_session():
    s = requests.Session()
    s.headers.update({
        'User-Agent': _UA,
        'Accept': 'application/json, text/plain, */*',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
        'Origin': 'https://flk.npc.gov.cn',
        'Referer': 'https://flk.npc.gov.cn/',
    })
    return s


# ---------- 国家法律法规数据库 ----------

def search_flk(keyword, page=1, size=20, terms=None):
    """返回 (results, total)。按原词 + 别名 + 同义词分别查，合并去重。

    terms 给定时用它当查询词表（软件本地检索的「近义词」开关决定要不要
    把同义词并进来，联网这层只管照着发请求）。
    """
    merged, seen, total = [], set(), 0
    for kw in (terms or expand_keyword(keyword)):
        rows, t = _flk_query(kw, page, size)
        total += t
        for it in rows:
            key = it['bbbs'] or it['title']
            if key in seen:
                continue
            seen.add(key)
            merged.append(it)
    _rank_results(merged, keyword, terms)
    return merged, total


def _flk_query(keyword, page=1, size=20):
    """单次国家法律法规数据库查询，返回 (results, total)。"""
    url = 'https://flk.npc.gov.cn/law-search/search/list'
    payload = {
        "searchRange": 1,
        "sxrq": [],
        "gbrq": [],
        "searchType": 2,
        "sxx": [],
        "gbrqYear": [],
        "flfgCodeId": [],
        "zdjgCodeId": [],
        "searchContent": keyword,
        "xgzlSearch": False,
        "orderByParam": {"order": "-1", "sort": ""},
        "pageNum": page,
        "pageSize": size
    }
    sess = _make_session()
    r = sess.post(url, json=payload, timeout=_TIMEOUT)
    r.raise_for_status()
    d = r.json()
    # 响应有两种格式：扁平 {"total":N,"rows":[...]} 或包装 {"code":200,"data":{...}}
    if 'rows' in d or 'total' in d:
        data = d
    else:
        if d.get('code') not in (200, None):
            raise RuntimeError(d.get('msg', '搜索失败'))
        data = d.get('data') or {}
    total = data.get('total', 0)
    out = []
    for row in data.get('rows', []):
        title = re.sub(r'<[^>]+>', '', row.get('title', ''))
        bbbs = row.get('bbbs', '')
        detail_url = f'https://flk.npc.gov.cn/detail?id={bbbs}&fileId=&type=&title={quote(title)}' if bbbs else ''
        sc = str(row.get('sxx', ''))
        out.append({
            'source': 'flk',
            'source_name': '国家法律法规数据库',
            'title': title,
            'bbbs': bbbs,
            'office': row.get('zdjgName', ''),
            'publish': row.get('gbrq', ''),
            'expiry': row.get('sxrq', ''),
            'type': row.get('flxz', ''),
            'status_code': sc,
            'status': FLK_STATUS.get(sc, ''),
            'detail_url': detail_url,
            'pdf_url': '',
            'doc_url': '',
            'html_url': '',
        })
    return out, total


# ---------- 国家标准全文公开（服务端直出 HTML，可解析） ----------

_OS_ROW_RE = re.compile(r'<tr[^>]*>(?:(?!</tr>).)*?showInfo\(\'([0-9A-Fa-f]+)\'\)'
                        r'(?:(?!</tr>).)*?</tr>', re.S)
_OS_TD_RE = re.compile(r'<td[^>]*>(.*?)</td>', re.S)
_OS_TAG_RE = re.compile(r'<[^>]+>')
_OS_DATE_RE = re.compile(r'(\d{4}-\d{2}-\d{2})')


def _os_text(cell):
    """去标签 + 解实体 + 压空白。"""
    t = html.unescape(_OS_TAG_RE.sub('', cell or ''))
    return re.sub(r'\s+', ' ', t).strip()


def search_openstd(keyword, size=10, terms=None):
    """国家标准全文公开：解析搜索结果页，返回 (results, total)。"""
    merged, seen = [], set()
    for kw in (terms or expand_keyword(keyword)):
        for it in _openstd_query(kw):
            key = it['std_code'] or it['title']
            if key in seen:
                continue
            seen.add(key)
            merged.append(it)
    _rank_results(merged, keyword, terms)
    return merged[:size * 2], len(merged)


def _openstd_query(keyword):
    """单次国家标准查询，返回 result dict 列表。"""
    url = 'https://openstd.samr.gov.cn/bzgk/gb/std_list'
    params = {'p.p1': 0, 'p.p90': 'circulation_date', 'p.p91': 'desc',
              'p.p2': keyword}
    headers = {'User-Agent': _UA,
               'Referer': 'https://openstd.samr.gov.cn/bzgk/gb/'}
    r = requests.get(url, params=params, headers=headers, timeout=_TIMEOUT)
    r.raise_for_status()
    r.encoding = 'utf-8'
    out = []
    for m in _OS_ROW_RE.finditer(r.text):
        hcno = m.group(1)
        cells = [_os_text(c) for c in _OS_TD_RE.findall(m.group(0))]
        if len(cells) < 9:
            continue
        code, name = cells[1], cells[4]
        if not name:
            continue
        dm = _OS_DATE_RE.search(cells[7])
        em = _OS_DATE_RE.search(cells[8])
        out.append({
            'source': 'samr',
            'source_name': '国家标准全文公开',
            'title': f'{code} {name}'.strip(),
            'std_code': code,
            'bbbs': '',
            'office': '国家标准化管理委员会',
            'publish': dm.group(1) if dm else '',
            'expiry': em.group(1) if em else '',
            'type': cells[5],
            'status_code': '',
            'status': SAMR_STATUS.get(cells[6], ''),
            'detail_url': 'https://openstd.samr.gov.cn/bzgk/gb/newGbInfo'
                          f'?hcno={hcno}',
            'hcno': hcno,
            'pdf_url': '',
            'doc_url': '',
            'html_url': '',
        })
    return out


# ---------- 中国政府网 政策文件库 ----------
#
# 实测：检索接口只认 t=zhengcelibrary_gw（t=zhengcelibrary 恒 0 条），
# 结果在 searchVO.listVO[]；搜索结果页本身是 JS 渲染的空壳，抓 HTML 没用。

_GOV_API = 'https://sousuo.www.gov.cn/search-gov/data'

_GOV_HEADERS = {
    'User-Agent': _UA,
    'Referer': 'https://sousuo.www.gov.cn/',
    'X-Requested-With': 'XMLHttpRequest',
    'Accept': 'application/json, text/plain, */*',
}


def search_gov(keyword, size=10, terms=None):
    """中国政府网政策文件库：按词表分别查，合并去重（同链接只留一条）。"""
    merged, seen = [], set()
    for kw in (terms or expand_keyword(keyword)):
        for it in _gov_query(kw, size):
            key = it['detail_url'] or it['title']
            if key in seen:
                continue
            seen.add(key)
            merged.append(it)
    _rank_results(merged, keyword, terms)
    return merged[:size * 2], len(merged)


def _gov_query(keyword, size=10, page=1):
    """单次政策库查询，返回 result dict 列表。"""
    params = {'t': 'zhengcelibrary_gw', 'q': keyword, 'timetype': 'timeqb',
              'sort': 'score', 'sortType': 1,
              'searchfield': 'title:content', 'p': page, 'n': size}
    r = requests.get(_GOV_API, params=params, headers=_GOV_HEADERS,
                     timeout=_TIMEOUT)
    r.raise_for_status()
    d = r.json()
    if d.get('code') not in (200, None):
        raise RuntimeError(d.get('msg') or '搜索失败')
    out = []
    for rec in ((d.get('searchVO') or {}).get('listVO') or []):
        # 标题里带 <em> 高亮标签，得清掉
        title = _os_text(str(rec.get('title') or ''))
        if not title:
            continue
        out.append({
            'source': 'gov',
            'source_name': source_name('gov'),
            'title': title,
            'std_code': str(rec.get('pcode') or '').strip(),
            'bbbs': '',
            'office': str(rec.get('puborg') or rec.get('fwdw')
                          or '').strip(),
            'publish': str(rec.get('pubtimeStr') or '').replace('.', '-'),
            'expiry': '',
            # 政策库只给「市场监管、安全生产监管」这种大类
            'type': str(rec.get('childtype') or '').split('\\')[0].strip(),
            'status_code': '',
            'status': '',
            'detail_url': str(rec.get('url') or '').strip(),
            'pdf_url': '',
            'doc_url': '',
            'html_url': '',
        })
    return out


def _div_text(src, div_id):
    """取 id=div_id 的 div 内部文字。正则收不了嵌套 div，自己数深度。"""
    m = re.search(r'<div[^>]*id="' + re.escape(div_id) + r'"[^>]*>', src, re.I)
    if not m:
        return ''
    rest = src[m.end():]
    depth, end = 1, len(rest)
    for t in re.finditer(r'<(/?)div\b[^>]*>', rest, re.I):
        depth += -1 if t.group(1) else 1
        if depth == 0:
            end = t.start()
            break
    frag = rest[:end]
    frag = re.sub(r'<(script|style)\b.*?</\1>', '', frag, flags=re.S | re.I)
    frag = re.sub(r'<br\s*/?>', '\n', frag, flags=re.I)
    frag = re.sub(r'</(p|div|tr|h[1-6]|li)>', '\n', frag, flags=re.I)
    txt = html.unescape(re.sub(r'<[^>]+>', '', frag))
    txt = txt.replace('\u3000', ' ').replace('\xa0', ' ')
    lines = [re.sub(r'[ \t]+', ' ', ln).strip() for ln in txt.split('\n')]
    return '\n'.join(ln for ln in lines if ln)


def download_gov(item, save_dir, progress=None):
    """抓政策原文（详情页正文容器 id=UCAP-CONTENT）存成 txt 后入库。"""
    if progress:
        try:
            progress(0, 0)
        except Exception:
            pass
    url = item.get('detail_url', '')
    if not url:
        raise RuntimeError('缺少详情页链接，无法下载')
    r = requests.get(url, headers={'User-Agent': _UA,
                                   'Referer': 'https://www.gov.cn/',
                                   'Accept-Language': 'zh-CN,zh;q=0.9'},
                     timeout=_TIMEOUT)
    r.raise_for_status()
    r.encoding = 'utf-8'
    body = _div_text(r.text, 'UCAP-CONTENT')
    if not body:
        raise RuntimeError('该文件的正文取不到（页面结构变了或正文在附件里）')
    meta = [
        item.get('title', ''),
        f"发布机关：{item.get('office', '')}",
        f"公布日期：{item.get('publish', '')}",
        f"文号：{item.get('std_code', '')}",
        f"类型：{item.get('type', '')}",
        f"来源：{item.get('source_name', '')} {url}",
        '',
    ]
    path = target_path_for(item, save_dir)
    with open(path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(meta) + body)
    return path


# ---------- 行业 / 地方标准信息服务平台（JSON 接口，需 POST 表单） ----------

def _ms_date(v):
    """毫秒时间戳 → YYYY-MM-DD（取不到就空串）。"""
    try:
        n = int(v)
    except (TypeError, ValueError):
        return ''
    if n <= 0:
        return ''
    try:
        return time.strftime('%Y-%m-%d', time.localtime(n / 1000.0))
    except (ValueError, OSError):
        return ''


def search_std(sid, keyword, size=10, terms=None):
    """行业 / 地方标准平台：按词表分别查，合并去重（同标准号只留一条）。"""
    merged, seen = [], set()
    for kw in (terms or expand_keyword(keyword)):
        for it in _std_query(sid, kw, size):
            key = it['std_code'] or it['title']
            if key in seen:
                continue
            seen.add(key)
            merged.append(it)
    _rank_results(merged, keyword, terms)
    return merged[:size * 2], len(merged)


def _std_query(sid, keyword, size=10):
    """单次行业/地方标准查询。GET 会返回前端页面的 HTML，必须 POST 表单。"""
    base = _STD_BASES[sid]
    headers = {
        'User-Agent': _UA,
        'X-Requested-With': 'XMLHttpRequest',
        'Accept': 'application/json, text/javascript, */*; q=0.01',
        'Referer': f'{base}/stdList?key={quote(keyword)}',
    }
    r = requests.post(f'{base}/stdQueryList',
                      data={'key': keyword, 'pageSize': size, 'current': 1},
                      headers=headers, timeout=_TIMEOUT)
    r.raise_for_status()
    d = r.json()
    out = []
    for rec in (d.get('records') or []):
        name = re.sub(r'\s+', ' ', str(rec.get('chName') or '')).strip()
        if not name:
            continue
        code = str(rec.get('code') or '').strip()
        status = SAMR_STATUS.get(str(rec.get('status') or '').strip(), '')
        pk = str(rec.get('pk') or '').strip()
        out.append({
            'source': sid,
            'source_name': source_name(sid),
            'title': f'{code} {name}'.strip(),
            'std_code': code,
            'bbbs': '',
            'office': str(rec.get('chargeDept') or '').strip(),
            'publish': _ms_date(rec.get('issueDate')),
            'expiry': _ms_date(rec.get('actDate')),
            # 行业库给行业（铁路/建筑工业…），地方库给省份，比「行业标准」有用
            'type': str(rec.get('industry') or '').strip(),
            'status_code': _STD_CODE.get(status, ''),
            'status': status,
            'detail_url': f'{base}/stdDetail/{pk}' if pk else '',
            'pdf_url': '',
            'doc_url': '',
            'html_url': '',
        })
    return out


def open_url_of(item):
    """结果该在浏览器里打开哪个页面。

    国标的信息页（newGbInfo）还要再点一次「在线预览」，直接给在线预览页
    （pdf.js 查看器，自带页内搜索），少一次点击。
    """
    if (item or {}).get('source') == 'samr' and item.get('hcno'):
        return ('https://openstd.samr.gov.cn/bzgk/std/showGb?type=online'
                f'&hcno={item["hcno"]}&request_locale=zh')
    return (item or {}).get('detail_url') or (item or {}).get('pdf_url') or ''


def title_hits(terms, title):
    """标题里命中几个下发词（国标只能核对标题：官方预览是图片版）。"""
    t = re.sub(r'\s+', '', title or '')
    return sum(1 for x in terms if x and re.sub(r'\s+', '', x) in t)


# ---------- 网页跳转型来源 ----------

def get_jump_url(source_id, keyword):
    """无 API 来源：返回浏览器打开的搜索 URL。"""
    kw = quote(keyword)
    if source_id == 'gov':
        return f'https://sousuo.www.gov.cn/sousuo/search.shtml?code=17da70961a7&searchWord={kw}&dataTypeId=107'
    if source_id == 'samr':
        return f'https://openstd.samr.gov.cn/bzgk/gb/std_list?r=0.123456&q={kw}'
    if source_id == 'mohurd':
        return f'https://www.mohurd.gov.cn/search/?q={kw}'
    return ''


# ---------- 统一搜索入口 ----------

def search_all(keyword, sources, callback=None, terms=None):
    """
    搜索多个来源（只处理 has_api=True 的，其余靠浏览器跳转）。
    callback(results, source_id, error) 每完成一个来源调用一次。
    terms：查询词表（见 search_flk），None = 只用内置行话别名。
    """
    results = {}
    errors = {}
    lock = threading.Lock()

    def _one(sid):
        try:
            if sid == 'flk':
                r, _ = search_flk(keyword, terms=terms)
            elif sid == 'gov':
                r, _ = search_gov(keyword, terms=terms)
            elif sid == 'samr':
                r, _ = search_openstd(keyword, terms=terms)
            elif sid in _STD_BASES:
                r, _ = search_std(sid, keyword, terms=terms)
            else:
                r = []
            with lock:
                results[sid] = r
        except Exception as e:
            with lock:
                errors[sid] = str(e)
        finally:
            if callback:
                callback(results, sid, errors.get(sid))

    threads = []
    for sid in sources:
        t = threading.Thread(target=_one, args=(sid,), daemon=True)
        threads.append(t)
        t.start()
    for t in threads:
        t.join(timeout=_TIMEOUT + 5)
    return results, errors


def _page_text(page_json):
    """从 reader/text 单页响应提取文本行。

    响应结构：areas[] → lines[] → text（部分文档）
    或 lines[] → chars[] → char（逐字符，需拼接）。
    按 boundary 坐标排序保证阅读顺序。
    """
    def _y(areas_or_lines):
        b = areas_or_lines.get('boundary', [0, 0])
        return (round(b[1], 1), b[0])

    out = []
    for area in sorted(page_json.get('areas', []), key=_y):
        for ln in sorted(area.get('lines', []), key=_y):
            t = ln.get('text')
            if t is None:
                chars = sorted(ln.get('chars', []),
                               key=lambda c: c.get('boundary', [0])[0])
                t = ''.join(c.get('char', '') for c in chars)
            t = (t or '').strip()
            if t:
                out.append(t)
    return out


_READER = 'https://flkofd.npc.gov.cn/reader'


def _flk_preview_file(sess, base, oss_path):
    """amazonFile/previewLink → 阅读器需要的带签名 file 参数。"""
    r = sess.get(f'{base}/amazonFile/previewLink',
                 params={'filePath': oss_path}, timeout=_TIMEOUT)
    r.raise_for_status()
    pj = r.json()
    pdata = pj.get('data') if isinstance(pj.get('data'), dict) else pj
    url = pdata.get('url', '')
    if not url and isinstance(pdata.get('file'), dict):
        url = pdata['file'].get('url', '')
    return parse_qs(urlparse(url).query).get('file', [''])[0]


def _flk_reader_page(sess, file_param, i):
    """reader/text 第 i 页 → 文本行列表。"""
    r = sess.get(f'{_READER}/text',
                 params={'file': file_param, '_i': i}, timeout=_TIMEOUT)
    r.raise_for_status()
    return _page_text(r.json())


def download_flk(item, save_dir, progress=None):
    """从 FLK API 提取纯文本，保存为 .txt 后入库。

    流程：flfgDetails 拿 oss 路径 → previewLink 拿带签名 file 参数
    → reader/info 拿页数 → reader/text 逐页提取文本。
    同一份法规官方存了 4 个副本（Word / Word 转 OFD / PDF / PDF 转 OFD），
    其中 PDF 版多为扫描件、reader 取不到文字层（实测《核安全法》PDF 版
    逐页返回空 areas），所以按「有文字层」的可能性排序逐个试。
    progress(cur_page, total_pages) 每开始抓一页回调一次（cur_page=0 表示准备中）。
    """
    def _emit(cur, total):
        if progress:
            try:
                progress(cur, total)
            except Exception:
                pass

    bbbs = item.get('bbbs', '')
    if not bbbs:
        raise RuntimeError('缺少 bbbs，无法下载')
    sess = _make_session()
    base = 'https://flk.npc.gov.cn/law-search'

    _emit(0, 0)
    r = sess.get(f'{base}/search/flfgDetails',
                 params={'bbbs': bbbs}, timeout=_TIMEOUT)
    r.raise_for_status()
    dj = r.json()
    data = dj.get('data') if isinstance(dj.get('data'), dict) else dj
    oss = data.get('ossFile', {})
    # Word 转出的 OFD 一定带文字层；PDF 及其 OFD 常是扫描件。docx 阅读器
    # 直接拒收（code:10005 不支持的格式），但留着兜底，试不通会自动跳过。
    cands = [oss.get('ossWordOfdPath'), oss.get('ossPdfOfdPath'),
             oss.get('ossPdfPath'), oss.get('ossWordPath')]
    cands = [c for c in dict.fromkeys(cands) if c]
    if not cands:
        raise RuntimeError('未找到文件路径')

    file_param, count, lines = '', 0, []
    for path in cands:
        try:
            fp = _flk_preview_file(sess, base, path)
            if not fp:
                continue
            r3 = sess.get(f'{_READER}/info',
                          params={'file': fp}, timeout=_TIMEOUT)
            n = int(r3.json().get('count', 0))
            if n <= 0:
                continue
            first = _flk_reader_page(sess, fp, 0)
            if not first:
                continue      # 首页就取不到文字 → 扫描件，别把几十页空响应拉完
            file_param, count, lines = fp, n, first
            break
        except Exception:
            continue

    for i in range(1, count):
        _emit(i + 1, count)
        lines.extend(_flk_reader_page(sess, file_param, i))

    # 头部加元数据，方便库内检索
    meta = [
        item.get('title', ''),
        f"发布机关：{item.get('office', '')}",
        f"公布日期：{item.get('publish', '')}",
        f"施行日期：{item.get('expiry', '')}",
        f"时效性：{item.get('status', '') or '未知'}",
        f"类型：{item.get('type', '')}",
        f"来源：{item.get('source_name', '')} {item.get('detail_url', '')}",
        '',
    ]
    text = '\n'.join(meta + lines)
    path = target_path_for(item, save_dir)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(text)
    return path


def download_item(item, save_dir, progress=None):
    """统一下载入口。法规库/政策库能抓全文，标准需在详情页自行预览/下载。"""
    if item.get('source') == 'flk':
        return download_flk(item, save_dir, progress=progress)
    if item.get('source') == 'gov':
        return download_gov(item, save_dir, progress=progress)
    if item.get('source') == 'samr':
        raise RuntimeError('国家标准请在浏览器打开的详情页中在线预览/下载')
    if item.get('source') in _STD_BASES:
        raise RuntimeError('行业/地方标准请在浏览器打开的详情页中查看/下载原文')
    raise RuntimeError('该来源仅支持网页跳转，请在浏览器中手动下载')
