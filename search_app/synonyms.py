# -*- coding: utf-8 -*-
"""
镜仔搜索 · 同义词语义扩展
Copyright (c) 2026 SleepDragon
SPDX-License-Identifier: MIT
"""

"""搜索同义词扩展

- 内置企业制度场景高频同义词组（安全员/专职安全管理员 等）
- 用户自定义别名（存 config.json 的 custom_synonyms，每行「主词 = 别名1, 别名2」）
- expand_map(): 把用户正向关键词映射为 {原词: [同义词,...]}，交由
  searcher.search()/search_fuzzy() 做组内任一命中（OR）扩展
"""

import re

# 内置同义词组：组内任一词命中即视为该词命中（双向生效）
# 覆盖企业制度/安全管理常见的不同叫法
BUILTIN_GROUPS = [
    ('安全员', '专职安全管理员', '安全管理人员', '安全管理员'),
    ('负责人', '主要负责人', '分管负责人', '第一责任人'),
    ('领导', '管理层', '领导班子'),
    ('制度', '管理办法', '管理规定', '实施细则'),
    ('培训', '教育培训', '学习教育'),
    ('隐患', '事故隐患', '风险隐患'),
    ('检查', '排查', '督查', '巡查'),
    ('应急', '应急处置', '应急救援'),
    ('考核', '考评', '考核评价'),
    ('员工', '职工', '从业人员'),
    ('公司', '本单位', '我司'),
    ('事故', '安全事件', '生产安全事故'),
    ('演练', '应急演练', '演习'),
    ('特种作业', '特殊工种'),
    ('交底', '安全交底', '技术交底'),
    ('奖惩', '奖罚', '奖励与处罚'),
]

# 自定义行格式：主词 = 别名1, 别名2（= 或 ：，别名按 , ， 、 分隔）
_LINE_RE = re.compile(r'^\s*(.+?)\s*[=：:]\s*(.+?)\s*$')


def parse_custom(text):
    """解析自定义同义词文本 → [[主词, 别名...], ...]，忽略空行/#注释/无=行"""
    groups = []
    for line in (text or '').splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        m = _LINE_RE.match(line)
        if not m:
            continue
        main = m.group(1).strip()
        syns = [s.strip() for s in re.split(r'[,，、]', m.group(2)) if s.strip()]
        if main and syns:
            groups.append([main] + syns)
    return groups


def _related(a, b):
    """两词是否同义关联：仅承认精确相等。
    不再做子串包含——包含判定会跨组污染：搜「事故」会被「事故隐患」带入
    隐患组、搜「应急」会被「应急演练」带入演练组。同组词本就一起扩展，
    无需包含判定；自定义别名显式声明在组内，精确相等即可命中。"""
    return a == b


def expand_map(tokens, custom_text=''):
    """正向关键词 → {原词: [同义词,...]}（原词不在返回值中=无扩展）

    tokens: 已解析的正向词列表（见 main.kw_terms）
    内置组与自定义组同时参与；仅精确相等匹配（同组词一起扩展，不做子串包含）
    """
    groups = [list(g) for g in BUILTIN_GROUPS] + parse_custom(custom_text)
    syn = {}
    for tok in tokens:
        if not tok or len(tok) < 2:
            continue
        s = set()
        for g in groups:
            if any(_related(tok, w) for w in g):
                s.update(w for w in g if w != tok)
        s.discard(tok)
        if s:
            syn[tok] = sorted(s, key=lambda w: (len(w), w))
    return syn


def describe(syn_map, limit=3):
    """状态栏展示用：'安全员→专职安全管理员、安全管理人员'（最多 limit 组）"""
    parts = []
    for tok, syns in syn_map.items():
        if len(parts) >= limit:
            parts.append('…')
            break
        parts.append(f'{tok}→{"、".join(syns[:3])}'
                     + ('等' if len(syns) > 3 else ''))
    return ' · 已扩展: ' + '；'.join(parts) if parts else ''
