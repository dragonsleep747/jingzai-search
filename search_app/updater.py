# -*- coding: utf-8 -*-
"""检查标准更新 - 本地自查 + 一键跳转官网搜新版
零网络爬取、零合规风险、用户掌控。"""
import os, re, sqlite3
from urllib.parse import quote

_STD_CODE_RE = re.compile(
    r'(?:gb|jg\s*j|dg|a\s*q|s\s*y|h\s*g|m\s*t|d\s*b|c\s*e\s*c\s*s|t\s*j)'
    r'\s*(?:/\s*[a-z]+\s*)?'
    r'[\-/]?\s*\d[\d\.\-]*',
    re.I)


def scan_local(db_path):
    """本地自查：找出库内所有带标准号的文件 + 它们的状态。
    返回 {'files': [{path, name, std_code, status}], 'n_total': int}"""
    conn = sqlite3.connect(db_path, timeout=30)
    try:
        rows = conn.execute('SELECT path, name, status FROM documents').fetchall()
        docs = []
        for path, name, status in rows:
            std_code = None
            m = _STD_CODE_RE.search(name or '')
            if m:
                std_code = re.sub(r'[\s\-/()（）]+', '', m.group(0)).lower()
            status = status or 'active'
            docs.append({
                'path': path,
                'name': name,
                'std_code': std_code,
                'status': status,
            })
        # 只保留有标准号的
        std_docs = [d for d in docs if d['std_code']]
        std_docs.sort(key=lambda x: (x['status'] != 'active', x['name'].lower()))
        return {
            'files': std_docs,
            'n_total': len(docs),
            'n_with_std': len(std_docs),
            'n_void': sum(1 for d in std_docs if d['status'] == 'void'),
            'n_replaced': sum(1 for d in std_docs if d['status'] == 'replaced'),
        }
    finally:
        conn.close()


def build_search_url(std_code, extra_keywords='新版 修订 替代'):
    """生成百度搜索链接，搜这个标准号有没有新版"""
    query = f'{std_code} {extra_keywords}'
    return f'https://www.baidu.com/s?wd={quote(query)}'
