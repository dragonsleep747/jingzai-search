# -*- coding: utf-8 -*-
"""
镜仔搜索 · 全文检索引擎
Copyright (c) 2026 SleepDragon
SPDX-License-Identifier: MIT
"""

"""关键词查询与上下文定位

P1-6  多关键词：空格=AND，a|b 或 a OR b=OR，-词=NOT
P1-13 类型过滤：file_type=all/text/office/pdf/code
P1-14 上下文行数：get_context 已支持 before/after 参数
"""
import re
import os
import sqlite3
import difflib
import threading

from indexer import bigram_ready, ro_conn

# 可走 bigram 索引的 2 字 token（与 indexer._bigram_toks 字符集一致）
_BIGRAM_TOKEN_RE = re.compile(r'^[0-9a-z\u4e00-\u9fff\u3400-\u4dbf]{2}$')

# 单次搜索结果硬上限（安全阀）：防止极端高频词把整库命中行物化进内存。
# 正常库规模下不会触及；触及即为异常查询，UI 会提示「已达上限」。
MAX_HITS = 50000

# 只读查询连接线程级复用：免每次 connect + 重建页缓存（搜索/上下文行/
# 章节定位/引用解析等高频小查询收益最大）。autocommit、查询间无事务，
# 每条 SELECT 都能看到最新已提交数据；线程结束连接随线程释放。
_conn_tls = threading.local()


def _conn(db_path):
    """当前线程对 db_path 的可复用只读查询连接（不要 close）"""
    store = getattr(_conn_tls, 'conns', None)
    if store is None:
        store = _conn_tls.conns = {}
    key = os.path.abspath(db_path)
    conn = store.get(key)
    if conn is None:
        conn = sqlite3.connect(db_path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute('PRAGMA busy_timeout=30000')
            conn.execute('PRAGMA mmap_size=268435456')   # 256MB mmap 免拷贝读
            conn.execute('PRAGMA cache_size=-16384')     # 16MB 页缓存常驻
            conn.execute('PRAGMA temp_store=MEMORY')     # 中间结果走内存
        except sqlite3.DatabaseError:
            pass
        store[key] = conn
    # 清掉上一条查询可能残留的取消进度钩子，防止陈旧令牌误中断后续查询
    conn.set_progress_handler(None, 0)
    return conn


class SearchCancelled(Exception):
    """搜索被取消（已有更新的搜索请求）：调用方必须丢弃本次结果。"""


def _install_cancel(conn, cancel, every=20000):
    """把取消谓词接到 SQLite 进度钩子：长 SQL 也能在毫秒级被中断。

    没有这一步时，一条全表扫的 LIKE 要跑完才轮到 Python 侧检查取消，
    旧搜索会一直霸占磁盘与 CPU，与新搜索抢 IO。"""
    if cancel is None:
        try:
            conn.set_progress_handler(None, 0)
        except Exception:
            pass
        return
    def _hook():
        return 1 if cancel() else 0
    try:
        conn.set_progress_handler(_hook, every)
    except Exception:
        pass


def _ck(cancel):
    """Python 侧循环的取消检查点"""
    if cancel is not None and cancel():
        raise SearchCancelled()


def _cancellable(fn):
    """把 SQLite 因进度钩子中断而抛出的 OperationalError 归一成 SearchCancelled"""
    import functools

    @functools.wraps(fn)
    def _wrap(*args, **kw):
        cancel = kw.get('cancel')
        try:
            return fn(*args, **kw)
        except sqlite3.OperationalError:
            if cancel is not None and cancel():
                raise SearchCancelled()
            raise
    return _wrap


def _bigram_token(tok):
    """2 字且全部为可索引字符 → 返回小写 token；否则 None（回退 LIKE）"""
    t = tok.lower()
    return t if _BIGRAM_TOKEN_RE.match(t) else None


# ========== 标准号/文号写法归一化（全半角/空格/连字符/括号互通）==========
# 实测痛点：同一编号在不同文档里写法不一 —— GB50016 / GB 50016 / ＧＢ５００１６、
# GB50016-2014 / GB50016—2014、建标[2003]206号 / 建标〔2003〕206号，
# 只按字面搜必然漏检。查询侧为每个含字母数字/括号的 token 生成写法变体，
# 原词与变体组内 OR —— 无需重建索引。

_DASH_CHARS = '-－—–−―'      # 半角短横/全角短横/em/en/减号/破折
_BR_OPEN = '([【〔'
_BR_CLOSE = ')]】〕'
_BR_PAIRS = [('[', ']'), ('【', '】'), ('〔', '〕'),
             ('(', ')'), ('（', '）')]
_NORM_CAP = 12               # 单 token 变体上限，防笛卡尔爆炸


def _to_halfwidth(s):
    """全角字符 → 半角（含全角空格 0x3000 → 普通空格）"""
    out = []
    for ch in s:
        c = ord(ch)
        if c == 0x3000:
            c = 32
        elif 0xFF01 <= c <= 0xFF5E:
            c -= 0xFEE0
        out.append(chr(c))
    return ''.join(out)


def _needs_norm(term):
    """纯中文/普通中文标点的词无需归一化（零开销快速路径）"""
    for ch in term:
        c = ord(ch)
        if (ch in _DASH_CHARS or ch in _BR_OPEN or ch in _BR_CLOSE
                or ch == ' ' or 0x21 <= c <= 0x7E
                or 0xFF01 <= c <= 0xFF5E):
            return True
    return False


def _norm_variants(term):
    """返回该 token 的写法变体列表（原词排第一，去重保序，上限 _NORM_CAP）。
    纯中文等无需归一的词 → 仅 [term]。"""
    if not _needs_norm(term):
        return [term]
    base = _to_halfwidth(term)
    cands = [term]
    seen = {term}

    def add(s):
        if s and s not in seen and len(cands) < _NORM_CAP:
            seen.add(s)
            cands.append(s)

    if base != term:
        add(base)
    # 1) 大小写统一（FTS 路径本就 nocase；LIKE 路径入组前会 lower）
    up = base.upper()
    add(up)
    # 2) 空格有无 + 字母/数字边界补空格：GB50016 ↔ GB 50016
    compact = re.sub(r'\s+', '', up)
    add(compact)
    spaced = re.sub(r'([A-Z]+)(\d)', r'\1 \2', compact)
    add(spaced)
    # 3) 连字符互通（两轮迭代，覆盖标准号最多两个连字符的位置）
    frontier = list(cands)
    for _ in range(2):
        nxt = []
        for cand in frontier:
            for i, ch in enumerate(cand):
                if ch in _DASH_CHARS:
                    for d in _DASH_CHARS:
                        v = cand[:i] + d + cand[i + 1:]
                        if v not in seen and len(cands) < _NORM_CAP:
                            seen.add(v)
                            cands.append(v)
                            nxt.append(v)
        frontier = nxt
        if not frontier:
            break
    # 4) 括号互通：[【〔（( 与对应闭括号成对替换（如 [2003] → 【2003】）
    def _bracket_span(s, i):
        """开括号位置 i → 对应闭括号位置（取其后第一个闭括号字符），无则 None"""
        if i + 1 >= len(s):
            return None
        for j in range(i + 1, len(s)):
            if s[j] in _BR_CLOSE:
                return j
        return None

    frontier = list(cands)
    for _ in range(2):
        nxt = []
        for cand in frontier:
            for i, ch in enumerate(cand):
                if ch not in _BR_OPEN:
                    continue
                j = _bracket_span(cand, i)
                if j is None:
                    continue
                for op, cl in _BR_PAIRS:
                    if op == ch:
                        continue
                    v = cand[:i] + op + cand[i + 1:j] + cl + cand[j + 1:]
                    if v not in seen and len(cands) < _NORM_CAP:
                        seen.add(v)
                        cands.append(v)
                        nxt.append(v)
        frontier = nxt
        if not frontier:
            break
    return cands


def _term_group(tok, syn_map=None, syn_min_len=2):
    """一个正/负 token 的全部写法：归一变体 + 同义词（≥ syn_min_len 字）。
    组内任一命中即算该 token 命中（OR 语义）。"""
    grp = list(_norm_variants(tok))
    for s in (syn_map or {}).get(tok, ()):
        if len(s) >= syn_min_len and s not in grp:
            grp.append(s)
    return grp


# P1-13: 文件类型 → 扩展名集合
FILE_TYPE_EXTS = {
    'text':   ('.txt', '.md', '.log', '.csv', '.tsv', '.json', '.xml',
               '.html', '.htm', '.ini', '.yaml', '.yml', '.toml',
               '.conf', '.cfg', '.properties'),
    'office': ('.docx', '.xlsx', '.pptx'),
    'pdf':    ('.pdf',),
    'code':   ('.py', '.js', '.ts', '.jsx', '.tsx', '.java', '.c', '.cpp',
               '.h', '.hpp', '.cs', '.go', '.rs', '.rb', '.php', '.sh',
               '.bat', '.ps1', '.sql', '.vue'),
}


def _build_fts_query(keyword, syn_map=None):
    """
    把用户输入解析为 FTS5 MATCH 表达式。
    语法：空格=AND（隐式），| 或 OR=OR，前缀 -=NOT
    同义词扩展：syn_map={原词:[同义词,...]} 时该词输出括号 OR 组
    写法归一：标准号/文号自动补全角半角、空格、连字符、括号变体
    （同义词/变体 <3 字符跳过：trigram 分词对超短语无法建倒排）。
    示例：
        'a'         -> '"a"'
        'a b'       -> '"a" "b"'         (AND)
        'a | b'     -> '"a" OR "b"'      (OR)
        'a OR b'    -> '"a" OR "b"'
        'a -b'      -> '"a" NOT "b"'    (含 a 且不含 b)
        '安全员'    -> '("安全员" OR "专职安全管理员" ...)'
        'GB50016'   -> '("GB50016" OR "GB 50016" ...)'
    每个 token 用双引号包成短语，转义内部双引号。
    """
    syn_map = syn_map or {}
    expr = keyword.replace('|', ' OR ')
    tokens = expr.split()
    out = []

    def _phrase_group(tok, with_syns):
        """组装一个 token 的 OR 短语组（原词 + 写法变体 [+ 同义词]）"""
        parts = [tok]
        for v in _norm_variants(tok):
            if v != tok and v not in parts:
                parts.append(v)
        if with_syns:
            for s in syn_map.get(tok, ()):
                if s not in parts:
                    parts.append(s)
        parts = [p for p in parts if len(p) >= 3]
        parts = ['"' + p.replace('"', '""') + '"' for p in parts]
        if not parts:
            return ''                       # 全是 <3 字短语 → 调用方应已走 LIKE
        if len(parts) == 1:
            return parts[0]
        return '(' + ' OR '.join(parts) + ')'

    for tok in tokens:
        if tok == 'OR':
            out.append('OR')
        elif tok.startswith('-') and len(tok) > 1:
            out.append('NOT')
            grp = _phrase_group(tok[1:], with_syns=False)
            out.append(grp or '"%s"' % tok[1:].replace('"', '""'))
        elif tok.startswith('+') and len(tok) > 1:
            # 显式 AND token，去掉 +
            grp = _phrase_group(tok[1:], with_syns=True)
            out.append(grp or '"%s"' % tok[1:].replace('"', '""'))
        else:
            grp = _phrase_group(tok, with_syns=True)
            out.append(grp or '"%s"' % tok.replace('"', '""'))
    return ' '.join(out)


def _has_short_token(keyword):
    """是否有 <3 字符的非操作符 token（决定是否需要走 LIKE 兜底）"""
    expr = keyword.replace('|', ' OR ')
    for tok in expr.split():
        if tok in ('OR', 'NOT'):
            continue
        if tok.startswith(('-', '+')):
            tok = tok[1:]
        if len(tok) < 3:
            return True
    return False


def _type_filter_sql(file_type):
    """返回 (where_clause, params)，无过滤时 ('', [])"""
    if not file_type or file_type == 'all':
        return '', []
    exts = FILE_TYPE_EXTS.get(file_type, ())
    if not exts:
        return '', []
    placeholders = ','.join('?' for _ in exts)
    return f' AND d.ext IN ({placeholders})', list(exts)


def _status_clause(only_active):
    """有效状态过滤：only_active 时排除废止/被替代文件"""
    if not only_active:
        return ''
    return " AND (d.status IS NULL OR d.status='active')"


# ========== 高级查询语法：文号: / 状态: / 日期: / 名称: ==========

_FIELD_RE = re.compile(r'^(文号|状态|日期|名称|分类)[:：](\S+)$')

_STATUS_ALIASES = {
    '现行': 'active', '有效': 'active', '现行有效': 'active', 'active': 'active',
    '废止': 'void', '已废止': 'void', '失效': 'void', 'void': 'void',
    '替代': 'replaced', '已替代': 'replaced', '被替代': 'replaced',
    'replaced': 'replaced',
}


def parse_advanced_query(raw):
    """把搜索框输入拆成 (自由词表达式, 元数据过滤 dict)。
    支持 文号:xx 状态:废止 日期:2022..2024 名称:差旅 分类:公司制度；
    未知字段按普通词处理。
    filters: {'doc_no':[...], 'name':[...], 'status':[...], 'category':[...],
              'date': 'a..b'|None}"""
    filters = {'doc_no': [], 'name': [], 'status': [], 'category': [],
               'date': None}
    free = []
    # 注意：不能预替换 |，否则 状态:废止|替代 的多值会被拆散；
    # 自由词里的 | 由下游 _build_fts_query/_parse_boolean 自行处理
    for tok in (raw or '').split():
        m = _FIELD_RE.match(tok)
        if not m:
            free.append(tok)
            continue
        field, val = m.group(1), m.group(2)
        if field == '文号':
            filters['doc_no'].append(val)
        elif field == '名称':
            filters['name'].append(val)
        elif field == '分类':
            for v in re.split(r'[|,，/]', val):
                v = v.strip()
                if v and v not in filters['category']:
                    filters['category'].append(v)
        elif field == '状态':
            for v in re.split(r'[|,，/]', val):
                st = _STATUS_ALIASES.get(v.strip())
                if st and st not in filters['status']:
                    filters['status'].append(st)
        elif field == '日期':
            filters['date'] = val
    return ' '.join(free), filters


def _date_bound(s):
    """2022 → 2022-01-01/2022-12-31 自动补边；2022-03-01 原样。返回 (起,止) 或 None"""
    s = (s or '').strip()
    if not s:
        return None
    if re.fullmatch(r'\d{4}', s):
        return f'{s}-01-01', f'{s}-12-31'
    if re.fullmatch(r'\d{4}-\d{1,2}-\d{1,2}', s):
        y, mo, d = s.split('-')
        return f'{int(y):04d}-{int(mo):02d}-{int(d):02d}', None
    return None


def _meta_filter_sql(filters):
    """元数据过滤 → (SQL 片段, 参数)。日期按 effective_date 起始日比较。"""
    clauses, params = [], []
    for v in filters.get('doc_no', []):
        clauses.append("d.doc_no LIKE ? ESCAPE '\\'")
        params.append('%' + _escape_like(v) + '%')
    for v in filters.get('name', []):
        clauses.append("d.name LIKE ? ESCAPE '\\'")
        params.append('%' + _escape_like(v) + '%')
    cats = filters.get('category') or []
    if cats:
        parts, cp = [], []
        for c in cats:
            if c in ('未分类', 'none', '无'):
                parts.append("(d.category IS NULL OR d.category='')")
            else:
                parts.append("d.category LIKE ? ESCAPE '\\'")
                cp.append('%' + _escape_like(c) + '%')
        clauses.append('(' + ' OR '.join(parts) + ')')
        params.extend(cp)
    sts = filters.get('status') or []
    if sts:
        # active 兼容旧库 NULL（与「仅看有效」语义一致：NULL 视同现行）
        parts, sp = [], []
        for s in sts:
            if s == 'active':
                parts.append("(d.status IS NULL OR d.status='active')")
            else:
                parts.append('d.status=?')
                sp.append(s)
        clauses.append('(' + ' OR '.join(parts) + ')')
        params.extend(sp)
    dv = filters.get('date')
    if dv:
        if '..' in dv:
            lo_s, hi_s = dv.split('..', 1)
        else:
            lo_s = hi_s = dv
        lo, hi = _date_bound(lo_s), _date_bound(hi_s)
        if lo is not None:
            clauses.append("substr(d.effective_date,1,10) >= ?")
            params.append(lo[0])
        if hi is not None:
            clauses.append("substr(d.effective_date,1,10) <= ?")
            params.append(hi[1] if hi[1] else hi[0])
    sql = (' AND ' + ' AND '.join(clauses)) if clauses else ''
    return sql, params


def _browse_by_filters(conn, type_clause, type_params, sc, mc, mp):
    """无自由词、仅元数据过滤时：语义是「列出符合条件的文件」，
    每个文件只取首行作为代表（旧实现拉全部行封顶3万，大库纯浪费）。"""
    sql = f'''SELECT d.path, d.name, d.status,
                     COALESCE((SELECT MIN(line_no) FROM lines
                               WHERE doc_id=d.id AND line_text!=''), 0) AS line_no,
                     COALESCE((SELECT line_text FROM lines
                               WHERE doc_id=d.id AND line_text!=''
                               ORDER BY line_no LIMIT 1), '') AS line_text
              FROM documents d
              WHERE 1=1{type_clause}{sc}{mc}
              ORDER BY d.path'''
    # type_clause 引用 d.ext/status 等，documents 表自带这些列
    return conn.execute(sql, tuple(type_params) + tuple(mp)).fetchall()


def _search_impl(db_path, keyword, case_sensitive=False, file_type=None,
                 only_active=False, syn_map=None, cancel=None):
    """
    P1-6 多关键词 / P1-13 类型过滤 / 有效性过滤 / 同义词语义扩展

    keyword: 'a' / 'a b' / 'a | b' / 'a -b' 等表达式
    file_type: None|'all'|'text'|'office'|'pdf'|'code'
    only_active: True 时排除 status != active 的文件（废止/被替代）
    syn_map: {原词:[同义词,...]}，同义词与原词组内任一命中即算命中（OR）
    cancel: 无参可调用，返回 True 表示已有更新的搜索 → 抛 SearchCancelled
    优先走 FTS5（≥3 字符），任意 token <3 字符则降级到 LIKE 复合查询。
    LIKE 复合查询在 Python 端做 AND/OR/NOT 组合（保留全部条件）。
    结果额外带 status 字段（供 UI 标灰废止文件）。
    """
    conn = _conn(db_path)
    conn.row_factory = sqlite3.Row
    _install_cancel(conn, cancel)
    fts_ok = _check_fts(conn)
    type_clause, type_params = _type_filter_sql(file_type)
    sc = _status_clause(only_active)
    # 高级语法：文号:/状态:/日期:/名称: 前缀过滤，其余为自由词
    keyword, meta = parse_advanced_query(keyword)
    mc, mp = _meta_filter_sql(meta)

    if not keyword.strip():
        # 纯元数据浏览（至少要有一个过滤条件，避免空条件全表误拉）
        rows = (_browse_by_filters(conn, type_clause, type_params, sc, mc, mp)
                if (mc or type_clause or sc) else [])
    elif (fts_ok and not case_sensitive
            and not _has_short_token(keyword)):
        q = _build_fts_query(keyword, syn_map)
        if q.strip().startswith('NOT'):
            # FTS5 不允许表达式以 NOT 开头（纯 NOT 查询，如 -机密），改走 LIKE
            rows = None
        else:
            sql = f'''SELECT d.path, d.name, d.status, f.line_no, f.line_text
                      FROM lines_fts f JOIN documents d ON d.id = f.doc_id
                      WHERE f.line_text MATCH ?{type_clause}{sc}{mc}
                      ORDER BY d.path, f.line_no LIMIT {MAX_HITS}'''
            try:
                rows = conn.execute(sql, (q,) + tuple(type_params)
                                    + tuple(mp)).fetchall()
            except sqlite3.OperationalError:
                _ck(cancel)   # 被取消打断 → 直接放弃，别再回退 LIKE 重扫
                # FTS 执行异常（非法表达式/分词器限制）→ 回退 LIKE 兜底
                rows = None
        if rows is None:
            try:
                v = conn.execute("SELECT value FROM settings "
                                 "WHERE key='bigram_ready'").fetchone()
                use_bg = v is not None and v[0] == '1'
            except sqlite3.OperationalError:
                _ck(cancel)
                use_bg = False
            rows = _like_search(conn, keyword, case_sensitive, type_clause,
                                type_params, sc, syn_map, mc, mp,
                                use_bigram=use_bg, cancel=cancel)
    else:
        # LIKE 复合：取出候选集（取 OR 任一匹配 + 全部 AND 必须项），
        # 然后在 Python 端二次过滤保证 AND/NOT 严格成立
        try:
            v = conn.execute("SELECT value FROM settings "
                             "WHERE key='bigram_ready'").fetchone()
            use_bg = v is not None and v[0] == '1'
        except sqlite3.OperationalError:
            _ck(cancel)
            use_bg = False
        rows = _like_search(conn, keyword, case_sensitive, type_clause,
                            type_params, sc, syn_map, mc, mp,
                            use_bigram=use_bg, cancel=cancel)

    _ck(cancel)
    results = [{'path': r['path'], 'name': r['name'],
                'status': r['status'] or 'active',
                'line_no': r['line_no'], 'line_text': r['line_text']}
               for r in rows]
    # PDF/Word 硬换行跨缝兜底（仅 nocase；bigram 未就绪时内部自动跳过）
    if not case_sensitive and keyword.strip():
        results = _augment_cross_line(
            conn, results, keyword, syn_map,
            type_clause, type_params, sc, mc, mp, cancel=cancel)
    pass  # 只读连接复用，不关闭
    return results


search = _cancellable(_search_impl)


def _like_search(conn, keyword, case_sensitive, type_clause, type_params,
                 sc='', syn_map=None, mc='', mp=(), use_bigram=False,
                 cancel=None):
    """LIKE 兜底 + Python 端 AND/OR/NOT 组合。sc=状态过滤子句。
    mc/mp=高级语法元数据过滤子句与参数。
    syn_map: token → 同义词组，组内任一命中即算该 token 命中
    use_bigram: bigram 索引就绪时，2 字正 token 走倒排点查（其余仍 LIKE）"""
    syn_map = syn_map or {}
    parsed = _parse_boolean(keyword)

    def _grp(tok):
        """token → [token, 写法变体, 同义词...]（组内 OR）"""
        return _term_group(tok, syn_map, syn_min_len=2)

    # NOT 词同样展开写法变体（任一写法出现即排除）
    not_tokens = [v for t in parsed['not'] for v in _norm_variants(t)]

    and_groups = [_grp(t) for t in parsed['and']]
    or_groups = [[_grp(t) for t in grp] for grp in parsed['or']]
    # 候选集：OR 组任一匹配 + 所有 AND token 匹配
    # 先用 LIKE 取「任一 OR token 或 所有 AND token」候选
    candidates = set()  # (path, line_no)
    base_sql = f'''SELECT d.path, l.line_no, l.line_text, d.name, d.status
                  FROM lines l JOIN documents d ON d.id = l.doc_id
                  WHERE 1=1{type_clause}{sc}{mc}
                  ORDER BY d.path, l.line_no LIMIT {MAX_HITS}'''
    # 收集所有「正 token」（AND + OR 各分支，含同义词展开）
    positive_tokens = [t for grp in and_groups for t in grp]
    positive_tokens += [t for grp in or_groups for g2 in grp for t in g2]
    if not positive_tokens:
        # 只有 NOT，没法选候选 → 全表扫
        rows = conn.execute(base_sql, tuple(type_params)
                            + tuple(mp)).fetchall()
        for r in rows:
            candidates.add((r['path'], r['line_no'], r['line_text'],
                            r['name'], r['status'] or 'active'))
    else:
        # 每个 positive token 取 LIKE/bigram 匹配，候选并集
        for tok in positive_tokens:
            _ck(cancel)
            kw = tok if case_sensitive else tok.lower()
            bt = _bigram_token(tok) if (use_bigram and not case_sensitive) \
                else None
            if bt:
                # bigram 倒排点查：2 字词从全表扫变索引查
                # （结果排序由 Python 端统一做，SQL 不必 ORDER BY）
                sql = f'''SELECT d.path, l.line_no, l.line_text, d.name, d.status
                         FROM bigram b
                         JOIN lines l ON l.doc_id=b.doc_id AND l.line_no=b.line_no
                         JOIN documents d ON d.id=l.doc_id
                         WHERE b.tok = ?{type_clause}{sc}{mc}
                         LIMIT {MAX_HITS}'''
                params = (bt,) + tuple(type_params) + tuple(mp)
            else:
                like = '%' + _escape_like(kw) + '%'
                if case_sensitive:
                    sql = f'''SELECT d.path, l.line_no, l.line_text, d.name, d.status
                             FROM lines l JOIN documents d ON d.id = l.doc_id
                             WHERE l.line_text LIKE ? ESCAPE '\\'{type_clause}{sc}{mc}
                             ORDER BY d.path, l.line_no LIMIT {MAX_HITS}'''
                else:
                    sql = f'''SELECT d.path, l.line_no, l.line_text, d.name, d.status
                             FROM lines l JOIN documents d ON d.id = l.doc_id
                             WHERE LOWER(l.line_text) LIKE ? ESCAPE '\\'{type_clause}{sc}{mc}
                             ORDER BY d.path, l.line_no LIMIT {MAX_HITS}'''
                params = (like,) + tuple(type_params) + tuple(mp)
            rows = conn.execute(sql, params).fetchall()
            for r in rows:
                candidates.add((r['path'], r['line_no'], r['line_text'],
                                r['name'], r['status'] or 'active'))

    # Python 端过滤：必须满足所有 AND 组（组内词组任一命中）
    # + 至少一个 OR 组（若有，组内 token AND、token 内同义词 OR）+ 不含任何 NOT
    final = []
    n_ck = 0
    for path, line_no, text, name, status in candidates:
        n_ck += 1
        if not n_ck % 5000:
            _ck(cancel)   # 候选集可达数万条，周期性检查取消
        hay = text if case_sensitive else text.lower()
        if all(any(_contains(hay, t, case_sensitive) for t in grp)
               for grp in and_groups):
            if not or_groups or any(
                all(any(_contains(hay, t, case_sensitive) for t in g2)
                    for g2 in grp)
                for grp in or_groups
            ):
                if not any(_contains(hay, t, case_sensitive)
                           for t in not_tokens):
                    final.append({'path': path, 'name': name, 'status': status,
                                  'line_no': line_no, 'line_text': text})
    # 去重 + 排序
    seen = set()
    dedup = []
    for r in sorted(final, key=lambda x: (x['path'], x['line_no'])):
        key = (r['path'], r['line_no'])
        if key not in seen:
            seen.add(key)
            dedup.append(r)
    return dedup


def _contains(hay, tok, case_sensitive):
    """LIKE 风格的包含判断，hay 已按 case 预处理"""
    needle = tok if case_sensitive else tok.lower()
    return needle in hay


# ========== PDF/Word 硬换行跨缝兜底 ==========
# 现象：PDF 提取文本按视觉行切分（一行约 35-45 字），一个连续词恰好被
# 行边界切断时（如「…各级人民政府应｜急管理部门…」），行级 FTS/LIKE
# 都搜不到整词。预览区的「自适应换行」能合并显示，但索引仍是逐行的。
# 做法：对 ≥4 字中文词，用首字 bigram 倒排点查候选行，拼接相邻两行判断。
_CN_WORD_RE = re.compile(r'^[\u4e00-\u9fff]{4,}$')
_CROSS_INLINE_LIMIT = 2000  # 行内命中极多的超高频词才跳过（bigram 点查本身很快；
                            # 阈值过高时极端高频词的候选 Python 校验会有数十万次）


def _cross_line_hits(conn, word, type_clause, type_params, sc, mc, mp,
                     cancel=None):
    """找 word 被硬换行切断的位置：a 行尾 + 同文档下一行 b 行头拼成 word。
    bigram 候选必须双向点查（切断点 k 不确定）：
      k≥2（a 含词首两字）→ bigram=word[:2] 命中 a 行，b=a+1；
      k=1（a 只有首字）  → bigram=word[1:3] 命中 b 行，a=b-1。
    返回命中列表（line_no=a，带 cross_next/cross_text/cross_word）。"""
    out = []
    seen = set()
    # (bigram, 方向)
    probes = [
        (word[:2], 'plus'),    # bigram 在 a 行，b = a+1
        (word[1:3], 'minus'),  # bigram 在 b 行，a = b-1
    ]
    for bg, direction in probes:
        _ck(cancel)
        if direction == 'plus':
            join_a = 'la.line_no=bg.line_no'
            join_b = 'lb.line_no=bg.line_no + 1'
        else:
            join_a = 'la.line_no=bg.line_no - 1'
            join_b = 'lb.line_no=bg.line_no'
        sql = f'''SELECT la.line_no AS la_no, la.line_text AS ta,
                         lb.line_no AS lb_no, lb.line_text AS tb,
                         d.path, d.name, d.status
                  FROM bigram bg
                  JOIN lines la ON la.doc_id=bg.doc_id AND {join_a}
                  JOIN lines lb ON lb.doc_id=bg.doc_id AND {join_b}
                  JOIN documents d ON d.id=bg.doc_id
                  WHERE bg.tok=?{type_clause}{sc}{mc}
                  LIMIT 20000'''
        try:
            rows = conn.execute(
                sql, (bg,) + tuple(type_params) + tuple(mp)).fetchall()
        except sqlite3.OperationalError:
            _ck(cancel)
            continue
        for r in rows:
            key = (r['path'], r['la_no'])
            if key in seen:
                continue
            ta = _PAGE_RE.sub('', r['ta'] or '', count=1).rstrip()
            tb = _PAGE_RE.sub('', r['tb'] or '', count=1).lstrip()
            if not ta or not tb:
                continue
            if not any(ta.endswith(word[:k]) and tb.startswith(word[k:])
                       for k in range(1, len(word))):
                continue
            # 任一行已含整词 → 属正常行内命中，不重复补
            low_a, low_b = (r['ta'] or '').lower(), (r['tb'] or '').lower()
            if word in low_a or word in low_b:
                continue
            seen.add(key)
            out.append({'path': r['path'], 'name': r['name'],
                        'status': r['status'] or 'active',
                        'line_no': r['la_no'], 'line_text': r['ta'],
                        'cross_next': r['lb_no'], 'cross_text': r['tb'],
                        'cross_word': word})
    return out


def _augment_cross_line(conn, results, keyword, syn_map,
                        type_clause, type_params, sc, mc, mp, cancel=None):
    """对 ≥4 字中文自由词补跨行命中；补集仍须通过完整 AND/OR/NOT 校验，
    校验文本为相邻两行拼接（跨缝词所在文本）。bigram 未就绪则跳过。"""
    try:
        v = conn.execute("SELECT value FROM settings "
                         "WHERE key='bigram_ready'").fetchone()
        if not v or v[0] != '1':
            return results
    except sqlite3.OperationalError:
        _ck(cancel)
        return results
    parsed = _parse_boolean(keyword)
    raw_terms = list(parsed['and'])
    for grp in parsed['or']:
        raw_terms.extend(grp)
    words, seen_w = [], set()
    for t in raw_terms:
        if _CN_WORD_RE.match(t) and t not in seen_w:
            seen_w.add(t)
            words.append(t)
    if not words:
        return results
    existing = {(r['path'], r['line_no']) for r in results}
    inline_cnt = {w: 0 for w in words}
    n_ck = 0
    for r in results:
        n_ck += 1
        if not n_ck % 5000:
            _ck(cancel)
        low = (r['line_text'] or '').lower()
        for w in words:
            if inline_cnt[w] < _CROSS_INLINE_LIMIT and \
                    any(x.lower() in low for x in _norm_variants(w)):
                inline_cnt[w] += 1
    extras = []
    for w in words:
        _ck(cancel)
        if inline_cnt[w] >= _CROSS_INLINE_LIMIT:
            continue
        extras.extend(_cross_line_hits(
            conn, w, type_clause, type_params, sc, mc, mp, cancel=cancel))
    if not extras:
        return results

    def _grp(t):
        return [x.lower() for x in _term_group(t, syn_map, syn_min_len=2)]

    and_g = [_grp(t) for t in parsed['and']]
    or_g = [[_grp(t) for t in grp] for grp in parsed['or']]
    not_g = [x.lower() for t in parsed['not'] for x in _norm_variants(t)]
    accepted, acc_keys = [], set(existing)
    for r in extras:
        key = (r['path'], r['line_no'])
        if key in acc_keys:
            continue
        hay = (_PAGE_RE.sub('', r['line_text'] or '', count=1)
               + _PAGE_RE.sub('', r['cross_text'] or '', count=1)).lower()
        if not all(any(v in hay for v in g) for g in and_g):
            continue
        if or_g and not any(
                all(any(v in hay for v in g2) for g2 in grp)
                for grp in or_g):
            continue
        if any(v in hay for v in not_g):
            continue
        acc_keys.add(key)
        accepted.append(r)
        if len(results) + len(accepted) >= MAX_HITS:
            break
    if not accepted:
        return results
    merged = results + accepted
    merged.sort(key=lambda r: (r['path'], r['line_no']))
    return merged


def _parse_boolean(keyword):
    """
    解析用户输入为布尔结构。
    返回 {'and': [t1, t2], 'not': [t3], 'or': [[a, b], [c, d]]}
    - 空格 = AND
    - | 或 OR = OR 分隔
    - 前缀 - = NOT
    """
    expr = keyword.replace('|', ' OR ')
    tokens = expr.split()
    parsed = {'and': [], 'or': [], 'not': []}
    # 先按 OR 切分成若干段（连续 OR 链 = 多段单 token 备选）
    segments = [[]]
    for tok in tokens:
        if tok == 'OR':
            segments.append([])
            continue
        if tok.startswith('-') and len(tok) > 1:
            # NOT 是全局排除，不参与 OR 分段
            parsed['not'].append(tok[1:])
            continue
        if tok.startswith('+') and len(tok) > 1:
            tok = tok[1:]
        segments[-1].append(tok)
    # 去掉连续 OR / OR 首尾产生的空段
    segments = [s for s in segments if s]
    if len(segments) == 1:
        # 无 OR：整段为 AND
        parsed['and'].extend(segments[0])
    elif len(segments) > 1:
        # 单 token 段并入同一条 OR 链（互为备选）；
        # 多 token 段（如 a OR b c 里的 b c）按 AND 处理
        alts = []
        for seg in segments:
            if len(seg) == 1:
                alts.append(seg)
            else:
                parsed['and'].extend(seg)
        parsed['or'].extend(alts)
    return parsed


# ========== P0-2: 按文件聚合 ==========

def _build_clusters(matches, gap=2):
    """把相邻命中行聚成片段簇。
    gap: 两行行号差 ≤ gap 视为同一簇（允许命中行间夹 1-2 行未命中）。
    返回 [{'line_start','line_end','lines':[match,...]}]，按行号升序。
    """
    if not matches:
        return []
    ms = sorted(matches, key=lambda m: m['line_no'])
    clusters = []
    cur = {'line_start': ms[0]['line_no'], 'line_end': ms[0]['line_no'],
           'lines': [ms[0]]}
    for m in ms[1:]:
        if m['line_no'] - cur['line_end'] <= gap:
            cur['line_end'] = m['line_no']
            cur['lines'].append(m)
        else:
            clusters.append(cur)
            cur = {'line_start': m['line_no'], 'line_end': m['line_no'],
                   'lines': [m]}
    clusters.append(cur)
    return clusters


def aggregate_results(results):
    """
    把扁平的搜索结果按文件聚合成分组结构。

    返回 [{
        'path': str, 'name': str, 'hits': int,
        'first_line': int, 'last_line': int,
        'matches': [{'line_no': int, 'line_text': str}, ...],
        'clusters': [{'line_start','line_end','lines':[...]}]  # 临近行汇总
    }, ...] 按 hits 数量倒序。
    """
    groups = {}
    for r in results:
        p = r['path']
        if p not in groups:
            groups[p] = {
                'path': p,
                'name': r['name'],
                'status': r.get('status', 'active'),
                'hits': 0,
                'first_line': r['line_no'],
                'last_line': r['line_no'],
                'matches': [],
                'fuzzy_hits': 0,
            }
        g = groups[p]
        g['hits'] += 1
        score = r.get('score', 1.0)
        m = {'line_no': r['line_no'], 'line_text': r['line_text'],
             'score': score}
        # 跨行兜底命中：携带下行行号/文本/被切断的词，供 UI 拼接展示与跳转
        if r.get('cross_word'):
            m['cross_next'] = r['cross_next']
            m['cross_text'] = r.get('cross_text', '')
            m['cross_word'] = r['cross_word']
        g['matches'].append(m)
        if score < 1.0:
            g['fuzzy_hits'] += 1
        if r['line_no'] < g['first_line']:
            g['first_line'] = r['line_no']
        if r['line_no'] > g['last_line']:
            g['last_line'] = r['line_no']
    groups = sorted(
        groups.values(),
        key=lambda x: -(
            # 精确命中权重高；模糊命中（一字之差）折半
            (x['hits'] - x['fuzzy_hits']) * 2 + x['fuzzy_hits']
            # 密度加权：命中集中在小范围的文件更相关（span 越小密度越高）
            + x['hits'] / max(x['last_line'] - x['first_line'] + 1,
                              x['hits']) * 10))
    # 命中行排序 + 聚成临近片段簇
    for g in groups:
        g['matches'].sort(key=lambda m: m['line_no'])
        g['clusters'] = _build_clusters(g['matches'])
        span = max(g['last_line'] - g['first_line'] + 1, g['hits'])
        g['density'] = g['hits'] / span
        g['rank'] = ((g['hits'] - g['fuzzy_hits']) * 2 + g['fuzzy_hits']
                     + g['density'] * 10)
    return groups


# ========== P0-1: 章节定位 ==========

def fetch_context_lines(db_path, results, radius=1):
    """批量取每条命中行前后 radius 行的原文（每个文件只查一次）。
    返回 {(path, line_no): {'prev': str|None, 'next': str|None}}。"""
    # 文件 → 命中行集合
    by_file = {}
    for r in results:
        by_file.setdefault(r['path'], set()).add(r['line_no'])
    ctx = {}
    conn = _conn(db_path)
    conn.row_factory = sqlite3.Row
    try:
        for path, hit_nos in by_file.items():
            need = {}  # 候选相邻行号 → 对应的命中行
            # radius>1 时前后各档都要查，近档缺失时用更远档兜底
            for n in hit_nos:
                for d in range(1, radius + 1):
                    for adj in (n - d, n + d):
                        if adj not in hit_nos:
                            need.setdefault(adj, set()).add(n)
            if not need:
                continue
            doc = conn.execute(
                'SELECT id FROM documents WHERE path=?', (path,)).fetchone()
            if not doc:
                continue
            qmarks = ','.join('?' * len(need))
            rows = conn.execute(
                f'SELECT line_no, line_text FROM lines '
                f'WHERE doc_id=? AND line_no IN ({qmarks})',
                (doc['id'],) + tuple(need)).fetchall()
            adj_text = {r['line_no']: r['line_text'] for r in rows}
            # 按行号距离挑最近的一条非空：n-1 优先，缺失再取 n-2…，next 同理
            for n in hit_nos:
                slot = ctx.setdefault((path, n), {'prev': None,
                                                   'next': None})
                for d in range(1, radius + 1):
                    if slot['prev'] is None:
                        txt = adj_text.get(n - d)
                        if txt and txt.strip():
                            slot['prev'] = txt
                    if slot['next'] is None:
                        txt = adj_text.get(n + d)
                        if txt and txt.strip():
                            slot['next'] = txt
    finally:
        pass  # 只读连接复用，不关闭
    return ctx

def get_chapter_for_line(db_path, path, line_no):
    """
    返回匹配行所属的**所有祖先章节**（从根到叶，按 level 升序）。
    例：行属于"第三条 出差审批流程"下的"2. 部门审批"，
    返回 [{第三章 差旅报销}, {第三条 出差审批流程}, {2. 部门审批}]。

    办公场景下用户想知道完整定位链："第三章 → 第三条 → 2."，
    而不是只看到最末一级。

    返回 list[{title, start_line, end_line, level}] 或空列表。
    """
    conn = _conn(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        '''SELECT title, start_line, end_line, level FROM chapters
           WHERE doc_id = (SELECT id FROM documents WHERE path=?)
           AND ? BETWEEN start_line AND end_line
           ORDER BY level ASC''',
        (path, line_no)).fetchall()
    pass  # 只读连接复用，不关闭
    return [{'title': r['title'], 'start_line': r['start_line'],
             'end_line': r['end_line'], 'level': r['level']} for r in rows]


def get_chapter_chain_str(db_path, path, line_no):
    """把章节链渲染成 '第三章 差旅报销 > 第三条 出差审批流程 > 2. 部门审批'"""
    chain = get_chapter_for_line(db_path, path, line_no)
    if not chain:
        return ''
    return ' > '.join(c['title'] for c in chain)


def list_chapters(db_path, path):
    """列出某文件的所有章节，用于"目录跳转"功能（去重防重复索引残留）"""
    conn = _conn(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        '''SELECT DISTINCT title, start_line, end_line, level FROM chapters
           WHERE doc_id = (SELECT id FROM documents WHERE path=?)
           ORDER BY start_line''',
        (path,)).fetchall()
    pass  # 只读连接复用，不关闭
    return [{'title': r['title'], 'start_line': r['start_line'],
             'end_line': r['end_line'], 'level': r['level']} for r in rows]


def _check_fts(conn):
    try:
        conn.execute("SELECT 1 FROM lines_fts LIMIT 0")
        return True
    except sqlite3.OperationalError:
        return False


def get_context(db_path, path, line_no, before=5, after=5):
    """获取匹配行前后 N 行上下文（P1-14: before/after 可调）"""
    conn = _conn(db_path)
    rows = conn.execute('''SELECT line_no, line_text FROM lines
        WHERE doc_id = (SELECT id FROM documents WHERE path=?)
        AND line_no BETWEEN ? AND ? ORDER BY line_no''',
        (path, line_no - before, line_no + after)).fetchall()
    pass  # 只读连接复用，不关闭
    return rows


# ========== P0-3: 整段预览 ==========

# 段落边界：只把"空行"和"Markdown标题"作为段落切分点。
# 注意：编号行（1./1.1/第一条）不算段落边界——办公制度里
# "1. xxx 2. xxx 3. xxx" 通常是一个列表整体，应当被当作同一段呈现。
_PARAGRAPH_BOUNDARY = re.compile(
    r'^\s*$'                              # 空行
    r'|^\s*#{1,6}\s'                      # Markdown 标题
)


def get_line_range(db_path, path, start, end):
    """返回 [start, end] 行区间内容（章节目录跳转用）"""
    conn = ro_conn(db_path)
    return conn.execute(
        '''SELECT line_no, line_text FROM lines
           WHERE doc_id = (SELECT id FROM documents WHERE path=?)
             AND line_no BETWEEN ? AND ?
           ORDER BY line_no''',
        (path, start, end)).fetchall()


def get_line_text(db_path, path, line_no):
    """取单行完整文本（打开文件高亮定位用，空行/越界返回 ''）"""
    rows = get_line_range(db_path, path, line_no, line_no)
    return rows[0][1] if rows else ''


def get_paragraph(db_path, path, line_no, max_lines=80):
    """
    返回匹配行所在的整个段落（按空行 / Markdown 标题切段）。
    用于办公场景下"看完整条款"而不是被切断的5行。

    设计取舍：编号行（1./第一条/（一））不作为段落边界，
    因为办公制度中列表项应作为整体呈现。

    max_lines: 上限保护，避免整篇文档被当一段
    返回 [(line_no, line_text), ...]
    """
    # 只取目标行前后各 max_lines 的窗口（段落扩展不会超出此范围），
    # 走 idx_lines_doc 索引；旧实现 fetchall 整篇，大汇编点一次预览读几万行
    conn = ro_conn(db_path)
    rows = conn.execute(
        '''SELECT line_no, line_text FROM lines
           WHERE doc_id = (SELECT id FROM documents WHERE path=?)
             AND line_no BETWEEN ? AND ?
           ORDER BY line_no''',
        (path, max(1, line_no - max_lines), line_no + max_lines)).fetchall()
    if not rows:
        return []
    line_map = {r[0]: r[1] for r in rows}
    line_nos = sorted(line_map.keys())
    if line_no not in line_map:
        return [(line_no, '')]
    idx = line_nos.index(line_no)
    # 向前扩展：遇到空行或 Markdown 标题停止
    start = idx
    while start > 0:
        prev_text = line_map[line_nos[start - 1]]
        if not prev_text.strip():
            break
        if _PARAGRAPH_BOUNDARY.match(prev_text):
            break
        start -= 1
        if idx - start >= max_lines:
            break
    # 向后扩展
    end = idx
    while end < len(line_nos) - 1:
        next_text = line_map[line_nos[end + 1]]
        if not next_text.strip():
            break
        if _PARAGRAPH_BOUNDARY.match(next_text):
            break
        end += 1
        if end - idx >= max_lines:
            break
    return [(line_nos[i], line_map[line_nos[i]])
            for i in range(start, end + 1)]


# ---------- 页码提取 ----------
_PAGE_RE = re.compile(r'^\[(?:Page|Slide) (\d+)\]\s*')


def extract_page(line_text):
    """从行文本前缀提取页码（[Page N] / [Slide N]），无页码概念返回 None"""
    m = _PAGE_RE.match(line_text or '')
    return m.group(1) if m else None


# ---------- 模糊搜索（LIKE 通配，容忍任意位置 1 个字差异）----------
def _escape_like(s):
    """LIKE ESCAPE 转义 % _ \\"""
    return s.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')


def _fuzzy_patterns(term):
    """
    生成 LIKE 通配模式，容忍 1 个字的差异（降噪版）：
    - 原词（精确）
    - 错字/多打：「前缀_后缀」，全位置（含首尾）单字替换
    - 少打：删去一位后的纯实心模式，n≥3 即启用
    """
    pats = [f'%{_escape_like(term)}%']
    n = len(term)
    for i in range(n):
        pre, suf = term[:i], term[i + 1:]
        # 错字/多打：首/中/尾位均生成单字替换模式
        pats.append(f'%{_escape_like(pre)}_{_escape_like(suf)}%')
        # 少打：删去一位后的纯实心模式（n>=3 即启用）
        if n >= 3:
            pats.append(f'%{_escape_like(pre + suf)}%')
    seen, out = set(), []
    for p in pats:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def _fuzzy_one_term(conn, term, type_clause, type_params, sc='',
                    mc='', mp=(), fuzzy_layer=True, cancel=None):
    """
    单词模糊：精确层(score 1.0) + 一字之差层(score 0.8)。
    精确层走 FTS5 短语（快）；模糊层走 LIKE 通配（全表扫，秒级内）。
    fuzzy_layer=False 时只跑精确层（供写法归一化变体使用：变体是确定的
    标准号写法，不需要再容忍错字，也避免多个变体各做一次全表扫）。
    sc=状态过滤子句，mc/mp=高级语法元数据过滤。结果带 status 字段。
    """
    hits = {}
    n = len(term)
    # --- 精确层 ---
    if n >= 3 and _check_fts(conn):
        sql = f'''SELECT d.path, d.name, d.status, f.line_no, f.line_text
                  FROM lines_fts f JOIN documents d ON d.id = f.doc_id
                  WHERE f.line_text MATCH ?{type_clause}{sc}{mc}'''
        try:
            rows = conn.execute(sql, ('"%s"' % term.replace('"', '""'),)
                                + tuple(type_params) + tuple(mp)).fetchall()
            for r in rows:
                hits[(r['path'], r['line_no'])] = {
                    'path': r['path'], 'name': r['name'],
                    'status': r['status'] or 'active',
                    'line_no': r['line_no'], 'line_text': r['line_text'],
                    'score': 1.0}
        except sqlite3.OperationalError:
            _ck(cancel)
    if not hits:
        sql = f'''SELECT d.path, d.name, d.status, l.line_no, l.line_text
                  FROM lines l JOIN documents d ON d.id = l.doc_id
                  WHERE LOWER(l.line_text) LIKE ? ESCAPE '\\'{type_clause}{sc}{mc}'''
        try:
            rows = conn.execute(
                sql, ('%' + _escape_like(term.lower()) + '%',)
                + tuple(type_params) + tuple(mp)).fetchall()
            for r in rows:
                hits[(r['path'], r['line_no'])] = {
                    'path': r['path'], 'name': r['name'],
                    'status': r['status'] or 'active',
                    'line_no': r['line_no'], 'line_text': r['line_text'],
                    'score': 1.0}
        except sqlite3.OperationalError:
            _ck(cancel)
    # --- 一字之差层（写法变体不参与：变体本身是确定写法，且避免全表扫 ×N）---
    _ck(cancel)
    if fuzzy_layer and n >= 3:
        pats = _fuzzy_patterns(term)
        conds = ' OR '.join(["l.line_text LIKE ? ESCAPE '\\'"] * len(pats))
        sql = f'''SELECT d.path, d.name, d.status, l.line_no, l.line_text
                  FROM lines l JOIN documents d ON d.id = l.doc_id
                  WHERE ({conds}){type_clause}{sc}{mc}'''
        try:
            rows = conn.execute(sql, tuple(pats)
                                + tuple(type_params) + tuple(mp)).fetchall()
        except sqlite3.OperationalError:
            _ck(cancel)
            rows = []
        for r in rows:
            k = (r['path'], r['line_no'])
            if k not in hits:
                hits[k] = {'path': r['path'], 'name': r['name'],
                           'status': r['status'] or 'active',
                           'line_no': r['line_no'],
                           'line_text': r['line_text'], 'score': 0.8}
    return hits


def _search_fuzzy_impl(db_path, keyword, file_type=None, only_active=False,
                       syn_map=None, cancel=None):
    """
    模糊搜索 v2：容忍任意位置 1 个字的差异（打错/多打/少打）。
    - 精确命中 score 1.0 排最前，一字之差命中 score 0.8 随后
    - ≥3 字词启用容错；<3 字词只做精确（2 字词容错噪声过大）
    - 多词（空格分隔）词间 AND；不支持 | OR - 布尔语法
    - syn_map: {原词:[同义词,...]}，同义词与原词组内任一命中即算命中
    - cancel: 返回 True 表示已有更新的搜索 → 抛 SearchCancelled
    返回结果额外带 score 字段。结果带 status 字段。
    """
    conn = _conn(db_path)
    conn.row_factory = sqlite3.Row
    _install_cancel(conn, cancel)
    type_clause, type_params = _type_filter_sql(file_type)
    sc = _status_clause(only_active)
    syn_map = syn_map or {}
    # 高级语法：文号:/状态:/日期:/名称: 前缀过滤，其余为自由词
    keyword, meta = parse_advanced_query(keyword)
    mc, mp = _meta_filter_sql(meta)
    if not keyword.strip():
        # 纯元数据浏览（至少要有一个过滤条件，避免空条件全表误拉）
        if not (mc or type_clause or sc):
            pass  # 只读连接复用，不关闭
            return []
        rows = _browse_by_filters(conn, type_clause, type_params, sc, mc, mp)
        results = [{'path': r['path'], 'name': r['name'],
                    'status': r['status'] or 'active',
                    'line_no': r['line_no'], 'line_text': r['line_text'],
                    'score': 1.0} for r in rows]
        pass  # 只读连接复用，不关闭
        return results
    terms = [t for t in keyword.split() if t and t not in ('|', 'OR', '-')]
    if not terms:
        terms = [keyword.strip()]
    try:
        v = conn.execute("SELECT value FROM settings "
                         "WHERE key='bigram_ready'").fetchone()
        use_bg = v is not None and v[0] == '1'
    except sqlite3.OperationalError:
        _ck(cancel)
        use_bg = False
    per_term = []
    for t in terms:
        _ck(cancel)
        # 词组 = 原词 + 写法变体 + 同义词（≥2 字），组内任一命中即该词命中。
        # 原词/同义词跑精确+容错；归一化变体只跑精确层（确定写法无需容错）。
        syns = [s for s in syn_map.get(t, ()) if len(s) >= 2]
        normv = [v for v in _norm_variants(t) if v != t]
        grp = [t] + syns + normv
        merged = {}
        for wi, w in enumerate(grp):
            _ck(cancel)
            is_norm_variant = wi >= 1 + len(syns)
            if len(w) < 3:
                bt = _bigram_token(w) if use_bg else None
                if bt:
                    sql = f'''SELECT d.path, d.name, d.status, l.line_no, l.line_text
                              FROM bigram b
                              JOIN lines l ON l.doc_id=b.doc_id
                                 AND l.line_no=b.line_no
                              JOIN documents d ON d.id=l.doc_id
                              WHERE b.tok = ?{type_clause}{sc}{mc}
                              LIMIT 20000'''
                    params = (bt,) + tuple(type_params) + tuple(mp)
                else:
                    sql = f'''SELECT d.path, d.name, d.status, l.line_no, l.line_text
                              FROM lines l JOIN documents d ON d.id=l.doc_id
                              WHERE LOWER(l.line_text) LIKE ? ESCAPE '\\'{type_clause}{sc}{mc}
                              LIMIT 20000'''
                    params = ('%' + _escape_like(w.lower()) + '%',) \
                        + tuple(type_params) + tuple(mp)
                try:
                    rows = conn.execute(sql, params).fetchall()
                except sqlite3.OperationalError:
                    rows = []
                one = {(r['path'], r['line_no']):
                       {'path': r['path'], 'name': r['name'],
                        'status': r['status'] or 'active',
                        'line_no': r['line_no'],
                        'line_text': r['line_text'], 'score': 1.0}
                       for r in rows}
            else:
                one = _fuzzy_one_term(conn, w, type_clause, type_params,
                                      sc, mc, mp,
                                      fuzzy_layer=not is_norm_variant,
                                      cancel=cancel)
            for k, v in one.items():
                if k not in merged or v['score'] > merged[k]['score']:
                    merged[k] = v
        per_term.append(merged)
    if not per_term:
        pass  # 只读连接复用，不关闭
        return []
    keys = set(per_term[0])
    for s in per_term[1:]:
        keys &= set(s)
    results = [per_term[0][k] for k in keys]
    results.sort(key=lambda r: (-r['score'], r['path'], r['line_no']))
    pass  # 只读连接复用，不关闭
    return results


search_fuzzy = _cancellable(_search_fuzzy_impl)


# ========== 文档间引用关系解析（《文件》第X条）==========

# 《xxx》后面可选条款号；书名号内 2-50 字、不含书名号/换行
REF_RE = re.compile(
    r'《([^》\n]{2,50})》\s*'
    r'(第[一二三四五六七八九十百千零0-9０-９]+[条章节项部分篇编点])?'
)

_CN_NUM = {'零': 0, '一': 1, '二': 2, '三': 3, '四': 4, '五': 5,
           '六': 6, '七': 7, '八': 8, '九': 9}


def cn_to_int(s):
    """中文/阿拉伯数字串转 int（支持十/百/千），失败返回 None"""
    s = s.translate(str.maketrans('０１２３４５６７８９', '0123456789'))
    if s.isdigit():
        return int(s)
    units = {'十': 10, '百': 100, '千': 1000}
    section, num = 0, 0
    for ch in s:
        if ch in _CN_NUM:
            num = _CN_NUM[ch]
        elif ch in units:
            section += (num or 1) * units[ch]
            num = 0
        else:
            return None
    total = section + num
    return total or None


def find_refs_in_text(text):
    """提取一行文本中的引用：[(doc_title, clause), ...]
    支持《文件》第三条 与 《文件》第二章、第十条 枚举写法。"""
    out = []
    text = text or ''
    sep_re = re.compile(
        r'[、，,]\s*(第[一二三四五六七八九十百千零0-9０-９]+[条章节项部分篇编点])')
    for m in REF_RE.finditer(text):
        title = m.group(1).strip()
        if len(title) < 2:
            continue
        clauses = [m.group(2)] if m.group(2) else ['']
        # 继续吃后面的「、第X条 / ，第X章」枚举（30 字窗口内）
        if clauses[0]:
            tail = text[m.end():m.end() + 30]
            pos = 0
            while True:
                sm = sep_re.match(tail[pos:])
                if not sm:
                    break
                clauses.append(sm.group(1))
                pos += sm.end()
        for c in clauses:
            out.append((title, c))
    return out


def _doc_stems(conn):
    """{去扩展名文件名: path}"""
    rows = conn.execute('SELECT name, path FROM documents').fetchall()
    return {os.path.splitext(r[0])[0]: r[1] for r in rows if r[0]}


# 文件索引缓存：abspath -> (documents_count, stems, norm_map, code_map)
# 入库/删除文件后 count 变化即自动重建，无需手动失效；进程内复用，
# 让一次预览渲染里的几十次《引用》解析不必重复全表扫描+逐条标准化
_DOC_INDEX_CACHE = {}


def _norm_title(s):
    """标准化：去所有非中英文数字字符 → 小写"""
    return re.sub(r'[^\w\u4e00-\u9fff]', '', s or '').lower()


def _get_doc_index(conn, db_path):
    """返回 (stems, norm_map, code_map)，进程内缓存，按 documents 行数自动失效。
    norm_map / code_map 预算一次，避免每个引用对全部文件重算。"""
    key = os.path.abspath(db_path) if db_path else id(conn)
    try:
        cnt = conn.execute('SELECT COUNT(*) FROM documents').fetchone()[0]
    except sqlite3.OperationalError:
        cnt = None
    cached = _DOC_INDEX_CACHE.get(key)
    if cached is not None and (cnt is None or cached[0] == cnt):
        return cached[1], cached[2], cached[3]
    stems = _doc_stems(conn)
    norm_map = {stem: _norm_title(stem) for stem in stems}
    code_map = {stem: _extract_std_code(stem) for stem in stems}
    if cnt is not None:
        _DOC_INDEX_CACHE[key] = (cnt, stems, norm_map, code_map)
    return stems, norm_map, code_map


def invalidate_doc_index(db_path=None):
    """强制清缓存（入库/删除/换库后可调；一般靠 count 自动失效）"""
    if db_path:
        _DOC_INDEX_CACHE.pop(os.path.abspath(db_path), None)
    else:
        _DOC_INDEX_CACHE.clear()


# 标准号提取正则：GB/JGJ/DG/TJ/AQ/SY/HG/MT/DB/CECS 等 + 编号（含年份）
_STD_CODE_RE = re.compile(
    r'(?:gb|jg\s*j|dg|a\s*q|s\s*y|h\s*g|m\s*t|d\s*b|c\s*e\s*c\s*s|t\s*j)'
    r'\s*(?:/\s*[a-z]+\s*)?'     # 可选 /T /J 推荐性标记
    r'[\-/]?\s*\d[\d\.\-]*',      # 数字编号（含年份）
    re.I)


def _extract_std_code(s):
    """提取完整标准号 → 压缩成 'jgj3112013' 这样可比较的字符串；无则返回 None"""
    m = _STD_CODE_RE.search(s or '')
    if not m:
        return None
    return re.sub(r'[\s\-/()\[\]（）]+', '', m.group(0)).lower()


def _resolve_prepared(stems, norm_map, code_map, title):
    """同 _resolve_title，但用预算好的 norm_map/code_map（热路径）"""
    t = title.strip()

    # 1) 精确相等
    if t in stems:
        return stems[t]

    # 2) 标准号提取匹配（最可靠，完整编号含年份）
    code = _extract_std_code(t)
    if code:
        for stem, path in stems.items():
            if code_map.get(stem) == code:
                return path

    nt = _norm_title(t)

    # 3) 标准化后完全相等
    for stem, path in stems.items():
        if nt and nt == norm_map.get(stem):
            return path

    # 4) 标准化后互相包含（短 stem 优先，排除"关于印发《…》通知"这类壳）
    contains = []
    for stem, path in stems.items():
        ns = norm_map.get(stem)
        if not ns:
            continue
        if nt in ns or ns in nt:
            # 跳过太短的 stem（< 4 字符容易误匹配，比如"公司法"匹配所有含"公司"的）
            if len(stem) >= 4:
                contains.append((len(ns), path))
    if contains:
        contains.sort(key=lambda x: x[0])
        return contains[0][1]

    return None


def _resolve_title(stems, title):
    """《标题》→ 库内 path。优先级从高到低：
    1) 精确相等
    2) 标准号提取匹配（最可靠，完整编号含年份）
    3) 标准化后完全相等（去空格/标点/括号）
    4) 标准化后互相包含（短文件名优先，排除"关于印发《…》通知"这类壳）
    （旧签名兼容；热路径请用 _get_doc_index + _resolve_prepared）
    """
    norm_map = {stem: _norm_title(stem) for stem in stems}
    code_map = {stem: _extract_std_code(stem) for stem in stems}
    return _resolve_prepared(stems, norm_map, code_map, title)


def resolve_ref(db_path, title):
    """公共接口：《标题》→ 库内文件 path（无匹配返回 None）。
    文件索引进程内缓存（按 documents 行数自动失效）。"""
    conn = ro_conn(db_path)
    stems, norm_map, code_map = _get_doc_index(conn, db_path)
    return _resolve_prepared(stems, norm_map, code_map, title)


def find_clause_line(db_path, path, clause):
    """'第三条' → 目标文件对应起始行；chapters 标题优先，其次正文行"""
    if not clause:
        return None
    num_m = re.search(r'第([一二三四五六七八九十百千零0-9０-９]+)([条章节项部分篇编点])',
                      clause)
    conn = ro_conn(db_path)
    doc = conn.execute(
        'SELECT id FROM documents WHERE path=?', (path,)).fetchone()
    if not doc:
        return None
    doc_id = doc[0]
    kind = num_m.group(2)
    # 1) chapters 标题精确包含
    ch = conn.execute(
        '''SELECT start_line FROM chapters WHERE doc_id=?
           AND title LIKE ? ORDER BY start_line LIMIT 1''',
        (doc_id, f'%{clause}%')).fetchone()
    if ch:
        return ch[0]
    # 2) 正文行：条款号出现在行首附近（前 8 字内）
    row = conn.execute(
        '''SELECT line_no FROM lines WHERE doc_id=?
           AND substr(line_text,1,12) LIKE ?
           ORDER BY line_no LIMIT 1''',
        (doc_id, f'%{clause}%')).fetchone()
    if row:
        return row[0]
    # 3) 全文任意位置
    row = conn.execute(
        '''SELECT line_no FROM lines WHERE doc_id=? AND line_text LIKE ?
           ORDER BY line_no LIMIT 1''',
        (doc_id, f'%{clause}%')).fetchone()
    return row[0] if row else None


def outgoing_refs(db_path, path):
    """本文件引用了哪些库内文件。
    返回 [{title, clause, target_path, target_line, target_status, line_no}]"""
    conn = _conn(db_path)
    conn.row_factory = sqlite3.Row
    try:
        doc = conn.execute(
            'SELECT id FROM documents WHERE path=?', (path,)).fetchone()
        if not doc:
            return []
        stems = _doc_stems(conn)
        status_map = {r['path']: (r['status'] or 'active')
                      for r in conn.execute(
                          'SELECT path, status FROM documents')}
        rows = conn.execute(
            'SELECT line_no, line_text FROM lines WHERE doc_id=? ORDER BY line_no',
            (doc[0],)).fetchall()
        out, seen = [], set()
        for r in rows:
            for title, clause in find_refs_in_text(r['line_text']):
                target = _resolve_title(stems, title)
                if not target or target == path:
                    continue
                key = (target, clause)
                if key in seen:
                    continue
                seen.add(key)
                out.append({'title': title, 'clause': clause,
                            'target_path': target,
                            'target_status': status_map.get(target, 'active'),
                            'target_line': find_clause_line(
                                db_path, target, clause),
                            'line_no': r['line_no']})
        return out
    finally:
        pass  # 只读连接复用，不关闭


def incoming_refs(db_path, path):
    """哪些库内文件引用了本文件。
    返回 [{path, name, status, title, clause, line_no}]"""
    conn = _conn(db_path)
    conn.row_factory = sqlite3.Row
    try:
        self_row = conn.execute(
            'SELECT name FROM documents WHERE path=?', (path,)).fetchone()
        if not self_row:
            # 本文件未入库（fetchone 为 None）→ 无入引用可查
            return []
        self_name = os.path.splitext(self_row[0] or '')[0]
        self_norm = _norm_title(self_name)
        self_code = _extract_std_code(self_name)
        rows = conn.execute(
            '''SELECT l.doc_id, l.line_no, l.line_text, d.path, d.name,
                      d.status
               FROM lines l JOIN documents d ON d.id = l.doc_id
               WHERE d.path != ? AND l.line_text LIKE ?
               ORDER BY l.line_no''',
            (path, '%《%》%')).fetchall()
        out, seen = [], set()
        for r in rows:
            for title, clause in find_refs_in_text(r['line_text']):
                nt = _norm_title(title)
                tcode = _extract_std_code(title)
                # 精确相等 / 标准化后相等 / 文号匹配 始终认可
                matched = (title == self_name
                           or (bool(nt) and nt == self_norm)
                           or (bool(self_code) and tcode == self_code))
                # 子串判定仅限名称长度 ≥4：短名（<4）子串误报太多，不做
                if (not matched and len(self_name) >= 4
                        and self_norm and nt):
                    matched = nt in self_norm or self_norm in nt
                if not matched:
                    continue
                key = (r['path'], clause)
                if key in seen:
                    continue
                seen.add(key)
                out.append({'path': r['path'], 'name': r['name'],
                            'status': r['status'] or 'active',
                            'title': title, 'clause': clause,
                            'line_no': r['line_no']})
        return out
    finally:
        pass  # 只读连接复用，不关闭


def incoming_refs_multi(db_path, paths):
    """一批文件被标记废止时：只全表扫一次《》引用行，
    在 Python 端匹配所有目标文件名。返回 [(target_path, [ref...])]（仅非空目标）"""
    if not paths:
        return []
    conn = _conn(db_path)
    conn.row_factory = sqlite3.Row
    try:
        names = {}
        placeholders = ','.join('?' for _ in paths)
        for r in conn.execute(
                f'SELECT path, name FROM documents WHERE path IN ({placeholders})',
                tuple(paths)):
            names[r['path']] = os.path.splitext(r['name'] or '')[0]
        rows = conn.execute(
            '''SELECT l.doc_id, l.line_no, l.line_text, d.path, d.name, d.status
               FROM lines l JOIN documents d ON d.id = l.doc_id
               WHERE d.path NOT IN (%s) AND l.line_text LIKE '%%《%%》%%'
               ORDER BY l.line_no''' % placeholders,
            tuple(paths)).fetchall()
        result = {p: [] for p in names}
        seen = {p: set() for p in names}
        for r in rows:
            refs = list(find_refs_in_text(r['line_text']))
            if not refs:
                continue
            for tgt, self_name in names.items():
                for title, clause in refs:
                    if not (title == self_name or title in self_name
                            or self_name in title):
                        continue
                    key = (r['path'], clause)
                    if key in seen[tgt]:
                        continue
                    seen[tgt].add(key)
                    result[tgt].append({'path': r['path'], 'name': r['name'],
                                        'status': r['status'] or 'active',
                                        'title': title, 'clause': clause,
                                        'line_no': r['line_no']})
        return [(p, result[p]) for p in names if result[p]]
    finally:
        pass  # 只读连接复用，不关闭


def ref_network_warnings(db_path):
    """全库引用预警（一次扫描，两类风险）：
    - dead:     引用了「已废止/被替代」文件的引用（引用方需同步修订）
    - dangling: 引用了库内不存在文件的标题（被引用文件未入库或名称不符）
    返回 {'dead': [{src_path,src_name,line_no,line_text,title,clause,
                    tgt_path,tgt_status,tgt_name}],
          'dangling': [{src_path,src_name,line_no,line_text,title,clause}]}"""
    conn = _conn(db_path)
    conn.row_factory = sqlite3.Row
    try:
        docs = conn.execute(
            'SELECT path, name, status FROM documents').fetchall()
        stems = {os.path.splitext(r['name'])[0]: r['path']
                 for r in docs if r['name']}
        status_map = {r['path']: (r['status'] or 'active') for r in docs}
        rows = conn.execute(
            '''SELECT l.line_no, l.line_text, d.path, d.name
               FROM lines l JOIN documents d ON d.id = l.doc_id
               WHERE l.line_text LIKE '%《%》%'
               ORDER BY d.path, l.line_no''').fetchall()
        dead, dangling = [], []
        for r in rows:
            for title, clause in find_refs_in_text(r['line_text']):
                tgt = _resolve_title(stems, title)
                if tgt:
                    st = status_map.get(tgt, 'active')
                    if st in ('void', 'replaced'):
                        dead.append({
                            'src_path': r['path'], 'src_name': r['name'],
                            'line_no': r['line_no'],
                            'line_text': r['line_text'],
                            'title': title, 'clause': clause,
                            'tgt_path': tgt, 'tgt_status': st,
                            'tgt_name': os.path.basename(tgt)})
                else:
                    dangling.append({
                        'src_path': r['path'], 'src_name': r['name'],
                        'line_no': r['line_no'],
                        'line_text': r['line_text'],
                        'title': title, 'clause': clause})
        return {'dead': dead, 'dangling': dangling}
    finally:
        pass  # 只读连接复用，不关闭


def compliance_gap_scan(db_path, keyword):
    """合规 Gap 扫描（简化版）：
    1) 搜库内所有包含 keyword 的文件
    2) 收集这些文件中所有外部《引用》标题
    3) 对比：哪些已在库 / 哪些缺失
    返回 {
      'keyword': str,
      'n_files': int,     # 库内涉及的文件数
      'refs_found': [{title, clause, n_sources, sources:[path,...], in_lib:bool, tgt_status:str}],
      'n_unique': int,    # 唯一引用标题数
      'n_in_lib': int,    # 已入库数
      'n_missing': int    # 缺失数
    }"""
    import re as _re
    kw = (keyword or '').strip()
    if not kw:
        return None
    conn = _conn(db_path)
    try:
        # 1) 用 FTS5 搜所有包含 keyword 的文件
        files_hit = set()
        try:
            # lines_fts 必须显式 JOIN，并与 lines 按 doc_id/line_no 关联
            rows = conn.execute(
                "SELECT DISTINCT d.path, d.name "
                "FROM lines_fts f "
                "JOIN lines l ON l.doc_id=f.doc_id "
                "AND l.line_no=f.line_no "
                "JOIN documents d ON d.id=f.doc_id "
                "WHERE f.line_text MATCH ? LIMIT 500",
                # MATCH 参数按 build_fts_expr 风格加双引号短语
                ('"%s"' % kw.replace('"', '""'),)).fetchall()
            for path, name in rows:
                files_hit.add(path)
        except Exception:
            rows = []
        if not files_hit:
            # FTS 不可用，或 trigram 分词器对不足 3 字的词返回 0 行 → LIKE 回退
            rows = conn.execute(
                "SELECT DISTINCT d.path, d.name FROM lines l "
                "JOIN documents d ON d.id=l.doc_id "
                "WHERE l.line_text LIKE ? LIMIT 500",
                (f'%{kw}%',)).fetchall()
            for path, name in rows:
                files_hit.add(path)

        # 2) 收集这些文件里的所有外部引用
        refs = {}  # title -> {sources:[], clauses:set()}
        for path in files_hit:
            content = conn.execute(
                'SELECT line_text FROM lines WHERE doc_id='
                '(SELECT id FROM documents WHERE path=?)',
                (path,)).fetchall()
            for (line_text,) in content:
                if not line_text:
                    continue
                for m in REF_RE.finditer(line_text):
                    title = m.group(1).strip()
                    clause = (m.group(2) or '').strip()
                    r = refs.setdefault(title, {'sources': set(), 'clauses': set()})
                    r['sources'].add(path)
                    if clause:
                        r['clauses'].add(clause)

        # 3) 对比库内 vs 缺失
        stems = _doc_stems(conn)
        result_refs = []
        for title, info in refs.items():
            tgt = _resolve_title(stems, title)
            status = 'active'
            if tgt:
                meta = conn.execute(
                    'SELECT status FROM documents WHERE path=?',
                    (tgt,)).fetchone()
                if meta:
                    status = meta[0] or 'active'
            result_refs.append({
                'title': title,
                'clauses': sorted(info['clauses']),
                'n_sources': len(info['sources']),
                'sources': list(info['sources']),
                'in_lib': bool(tgt),
                'tgt_path': tgt,
                'tgt_status': status,
            })

        result_refs.sort(key=lambda x: -x['n_sources'])
        n_in = sum(1 for r in result_refs if r['in_lib'])
        return {
            'keyword': kw,
            'n_files': len(files_hit),
            'refs_found': result_refs,
            'n_unique': len(result_refs),
            'n_in_lib': n_in,
            'n_missing': len(result_refs) - n_in,
        }
    finally:
        pass  # 只读连接复用，不关闭


# ========== 版本对比（条款级 diff）==========

def diff_documents(db_path, path_a, path_b):
    """对比两文件行内容，返回结构化差异块：
    [{'type': equal|replace|delete|insert,
      'a': [(line_no, text)], 'b': [(line_no, text)]}]
    以 strip 后文本做比较键，连续相同/不同行合并成块。"""
    import difflib
    conn = _conn(db_path)
    try:
        def rows_of(p):
            doc = conn.execute(
                'SELECT id FROM documents WHERE path=?', (p,)).fetchone()
            if not doc:
                return []
            return conn.execute(
                'SELECT line_no, line_text FROM lines WHERE doc_id=? '
                'ORDER BY line_no', (doc[0],)).fetchall()
        ra, rb = rows_of(path_a), rows_of(path_b)
    finally:
        pass  # 只读连接复用，不关闭
    a = [(n, t) for n, t in ra if t.strip()]
    b = [(n, t) for n, t in rb if t.strip()]
    sm = difflib.SequenceMatcher(
        a=[t.strip() for _, t in a],
        b=[t.strip() for _, t in b],
        autojunk=False)
    blocks = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == 'equal' and i2 - i1 > 20:
            # 长相同段折叠：保留首尾各 2 行
            blocks.append({'type': 'equal',
                           'a': a[i1:i1 + 2], 'b': b[j1:j1 + 2]})
            blocks.append({'type': 'gap', 'a': [], 'b': [],
                           'skip': (i2 - i1 - 4, j2 - j1 - 4)})
            blocks.append({'type': 'equal',
                           'a': a[i2 - 2:i2], 'b': b[j2 - 2:j2]})
        else:
            blocks.append({'type': tag, 'a': a[i1:i2], 'b': b[j1:j2]})
    return blocks


# ========== 新旧版本自动配对 ==========

# 文件名主干归一化：去年份/版本/稿本标记，用于相似度配对
_VER_NOISE = [
    re.compile(r'(?:19|20)\d{2}\s*年?'),
    re.compile(r'[Vv]\d+(?:\.\d+)*'),
    re.compile(r'第?\d+版'),
    re.compile(r'(?:修订版?|新版|旧版|最新版?|试行版?|暂行|征求意见稿?|送审稿?|'
               r'报批稿?|草案|正式版?|定稿|修正版?)'),
    re.compile(r'[\s_\-（）()【】\[\]·]+'),
]


def _version_stem(name):
    """去扩展名 +去年份/版本/稿本词后的归一化主干（小写）"""
    stem = os.path.splitext(name or '')[0]
    for pat in _VER_NOISE:
        stem = pat.sub('', stem)
    return stem.strip().lower()


# 制度类通用尾缀：去掉后比对"主体词"，避免「差旅管理办法≈印章管理办法」误配
_GENERIC_TAIL = re.compile(
    r'(?:实施|管理)?(?:办法|制度|规定|细则|规程|规则|规范|条例|标准|'
    r'措施|须知|指南|流程|方案|意见|通知)$')


def _core_subject(stem):
    return _GENERIC_TAIL.sub('', stem or '')


def _is_version_like(name_a, name_b, threshold=0.8):
    """归一化后判定两文件名是否为同制度不同版本：
    整体相似度 ≥0.8，或去掉通用尾缀后的主体词完全相同。"""
    sa, sb = _version_stem(name_a), _version_stem(name_b)
    if not sa or not sb:
        return False, 0.0
    ratio = difflib.SequenceMatcher(None, sa, sb).ratio()
    ca, cb = _core_subject(sa), _core_subject(sb)
    same_core = bool(ca) and ca == cb
    return (ratio >= threshold or same_core), round(ratio, 3)


def find_version_siblings(db_path, path, threshold=0.8):
    """为 path 自动寻找同制度的其他版本文件。
    配对规则：
      1) doc_no 相同（且非空）优先；
      2) 无 doc_no 或同号无其他文件时，按文件名主干相似度（difflib）。
    返回 [{path,name,doc_no,effective_date,status,ratio,reason}]，
    同文号在前，其余按相似度、生效日期倒序。无候选返回 []。"""
    conn = _conn(db_path)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute(
            '''SELECT path, name, doc_no, effective_date, status, mtime
               FROM documents WHERE path=?''', (path,)).fetchone()
        if not cur:
            return []
        doc_no = cur['doc_no'] or ''
        my_stem = _version_stem(cur['name'])
        rows = conn.execute(
            '''SELECT path, name, doc_no, effective_date, status, mtime
               FROM documents WHERE path != ?''', (path,)).fetchall()

        def _mk(r, ratio, reason):
            return {'path': r['path'], 'name': r['name'],
                    'doc_no': r['doc_no'] or '',
                    'effective_date': r['effective_date'] or '',
                    'status': r['status'] or 'active',
                    'mtime': r['mtime'] or 0,
                    'ratio': ratio, 'reason': reason}

        out = []
        if doc_no:
            same = [_mk(r, 1.0, '同文号') for r in rows
                    if (r['doc_no'] or '') == doc_no]
            if same:
                out = same
        if not out and my_stem:
            for r in rows:
                like, ratio = _is_version_like(
                    cur['name'], r['name'], threshold)
                if like:
                    out.append(_mk(r, ratio, '文件名相似'))
        # 排序：相似度降序；同相似度按生效日期新→旧（空日期排末尾）
        out.sort(key=lambda x: (x['ratio'],
                                x['effective_date'] or '0000'),
                 reverse=True)
        return out
    finally:
        pass  # 只读连接复用，不关闭
