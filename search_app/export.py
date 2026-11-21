# -*- coding: utf-8 -*-
"""镜仔搜索 · 导出与 AI 上下文构建（无界面）

从 main.py 抽出的内容生成逻辑：Word 制度摘编构建器、搜索命中转
AI 问答上下文。纯数据/文件操作，不依赖 tkinter；异常以 (ok, err)
元组返回，由界面层决定如何提示。
"""
import os
import re as _re

from indexer import get_metadata, record_error
from searcher import get_line_range, get_paragraph


def write_digest_docx(db_path, fn, title, subtitle, sections,
                      highlight_terms=(), context_n=0):
    """汇编 docx 通用构建器（书签汇编/搜索结果汇编共用）。
    sections: [(path, [(位置标题, 命中行号), ...])]
    highlight_terms: 需黄色高亮的关键词。
    context_n>0（制度汇编模式）：不整篇摘抄，只导出命中行上下各 N 行
        重点片段，相邻片段自动合并，命中行加粗。成功返回 (True, None)。"""
    try:
        from docx import Document
        from docx.shared import Pt, RGBColor
        from docx.oxml.ns import qn
        from docx.enum.text import WD_COLOR_INDEX
        doc = Document()
        normal = doc.styles['Normal']
        normal.font.name = '微软雅黑'
        normal.font.size = Pt(10.5)
        normal.element.rPr.rFonts.set(qn('w:eastAsia'), '微软雅黑')

        def _cn(run, size=None, bold=None, color=None, hl=False):
            run.font.name = '微软雅黑'
            run._element.rPr.rFonts.set(qn('w:eastAsia'), '微软雅黑')
            if size:
                run.font.size = Pt(size)
            if bold is not None:
                run.font.bold = bold
            if color:
                run.font.color.rgb = RGBColor(*color)
            if hl:
                run.font.highlight_color = WD_COLOR_INDEX.YELLOW

        tp = doc.add_paragraph()
        _cn(tp.add_run(title), size=18, bold=True,
            color=(0x1A, 0x56, 0xB0))
        sub = doc.add_paragraph()
        _cn(sub.add_run(subtitle), size=9, color=(0x88, 0x88, 0x88))
        status_map = {'active': '现行有效', 'void': '已废止',
                      'replaced': '已被替代'}
        terms = sorted({t for t in highlight_terms if t},
                       key=len, reverse=True)
        hl_pat = _re.compile('(' + '|'.join(_re.escape(t) for t in terms)
                             + ')') if terms else None

        def _para_with_hl(p, text):
            if not hl_pat:
                _cn(p.add_run(text), size=10.5)
                return
            for i, seg in enumerate(hl_pat.split(text)):
                if not seg:
                    continue
                _cn(p.add_run(seg), size=10.5, hl=(i % 2 == 1))

        for path, locs in sections:
            meta = get_metadata(db_path, path)
            st = status_map.get(meta['status'], meta['status'])
            h = doc.add_paragraph()
            _cn(h.add_run(f'《{os.path.basename(path)}》'),
                size=13, bold=True,
                color=(0x33, 0x33, 0x33) if meta['status'] == 'active'
                else (0xCC, 0x00, 0x00))
            parts = [f'状态：{st}']
            if meta['doc_no']:
                parts.append(f'文号：{meta["doc_no"]}')
            if meta['effective_date']:
                parts.append(f'施行日期：{meta["effective_date"]}')
            ml = doc.add_paragraph()
            _cn(ml.add_run('  ·  '.join(parts)), size=9,
                color=(0x66, 0x66, 0x66))
            if context_n:
                # 制度汇编：只取命中行上下 N 行重点片段，相邻区间自动合并
                hit_lines = sorted({int(ln) for _, ln in locs})
                ranges = []
                for hl in hit_lines:
                    s, e = hl - context_n, hl + context_n
                    if ranges and s <= ranges[-1][1] + 1:
                        ranges[-1][1] = max(ranges[-1][1], e)
                        ranges[-1][2].add(hl)
                    else:
                        ranges.append([s, e, {hl}])
                for idx, (s, e, hitset) in enumerate(ranges):
                    rows = get_line_range(db_path, path, s, e)
                    if not rows:
                        continue
                    if idx > 0:
                        gap = doc.add_paragraph()
                        _cn(gap.add_run('…………（略）…………'),
                            size=9, color=(0x99, 0x99, 0x99))
                    loc = doc.add_paragraph()
                    _cn(loc.add_run(f'◆ 第 {s}–{e} 行（关键词附近重点）'),
                        size=10.5, bold=True,
                        color=(0x0A, 0x7A, 0x2F))
                    for ln, text in rows:
                        if not text.strip():
                            continue
                        p = doc.add_paragraph()
                        run = p.add_run(text)
                        if ln in hitset:
                            # 命中行：加粗 + 关键词黄底，一眼定位重点
                            _cn(run, size=10.5, bold=True, hl=bool(terms))
                        else:
                            _cn(run, size=10, color=(0x55, 0x55, 0x55))
                    doc.add_paragraph()
                continue
            seen_para = set()   # 同一条款多簇命中只导出一次
            for loc_txt, line_no in locs:
                para = get_paragraph(db_path, path, line_no)
                if not para:
                    continue
                key = para[0][0]
                if key in seen_para:
                    continue
                seen_para.add(key)
                loc = doc.add_paragraph()
                _cn(loc.add_run(f'◆ {loc_txt}'), size=10.5, bold=True,
                    color=(0x0A, 0x7A, 0x2F))
                for _ln, text in para:
                    if text.strip():
                        p = doc.add_paragraph()
                        _para_with_hl(p, text)
                doc.add_paragraph()
        doc.save(fn)
        return True, None
    except Exception as e:
        record_error(db_path, 'export_docx', e)
        return False, str(e)


def build_search_hits(groups, ctx_map, max_files=8, max_chars=6000):
    """把当前搜索结果转成 ai_qa.build_messages 可吃的 hits 列表。
    每文件取前 4 条命中行（±1 行上下文，复用搜索时已取的 ctx_map），
    按命中数排序截断；score 按文件序递减，模拟相关度。"""
    hits = []
    total_chars = 0
    for gi, g in enumerate(groups[:max_files]):
        score = max(0.6, 0.95 - gi * 0.05)
        lines, seen = [], set()
        for m in g['matches'][:4]:
            ln = m['line_no']
            if ln in seen:
                continue
            seen.add(ln)
            c = ctx_map.get((g['path'], ln)) or {}
            if c.get('prev'):
                lines.append(c['prev'])
            lines.append(m['line_text'])
            if c.get('next'):
                lines.append(c['next'])
        text = '\n'.join(x.strip() for x in lines if x.strip())
        if not text:
            continue
        hits.append({
            'name': g['name'], 'path': g['path'],
            'line_no': g.get('first_line') or g['matches'][0]['line_no'],
            'status': g.get('status') or 'active',
            'score': score, 'chapter': '',
            'doc_no': '', 'effective_date': '',
            'text': text,
        })
        total_chars += len(text)
        if total_chars >= max_chars:
            break
    return hits
