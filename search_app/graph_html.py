# -*- coding: utf-8 -*-
"""引用关系图（单文件离线 HTML + 内联 SVG，零外部依赖）。

- 节点=库内文件，颜色按状态（现行绿/废止灰/被替代橙），有死链引用红描边
- 边=《文件》引用关系，带方向箭头与悬浮条款
- 两种布局可切换：
  · 分层（Sugiyama 简化版，默认）：依据在上、细则在下，前后引用一眼可读
  · 力导向（FR）：按分类分区，看同分类聚类
- 聚焦模式：双击节点只展开它上下游 N 层的引用链
- 选中高亮走事件委托 + 分组压暗，大库也不掉帧
"""
import json
import math
import os
import sqlite3
from pathlib import Path

from searcher import find_refs_in_text
from searcher import _doc_stems, _resolve_title

# 实际入库状态码：active / void / replaced
STATUS_COLOR = {'active': ('#1a7f37', '现行'),
                'void': ('#8a8a8a', '废止'),
                'replaced': ('#e08a00', '被替代')}
MAX_ALL_NODES = 200          # 超过则只画有引用关系的节点
ITERS = 220

# 分层布局参数
L_XGAP = 132.0
L_YGAP = 108.0
L_TOP = 84.0
L_MARGIN = 72.0
L_ISO_XGAP = 152.0
L_ISO_YGAP = 46.0
L_ISO_COLS = 10
LAYER_TARGET_W = 3400.0   # 最宽层的目标宽度（据此自适应压缩层内间距）
L_GAP_MIN = 66.0


def aggregate_graph(db_path):
    """汇总节点/边/死链（一次 JOIN 取全部条款，避免 N+1 查询）。
    nodes: {path:{name,status,doc_no,category}}
    edges: {(src,tgt): set(条款)}
    dead: {src: set(未解析标题)}
    """
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        docs = conn.execute(
            'SELECT path,name,status,doc_no,category FROM documents').fetchall()
        nodes = {r['path']: {'name': r['name'] or os.path.basename(r['path']),
                             'status': r['status'] or 'active',
                             'doc_no': r['doc_no'] or '',
                             'category': r['category'] or ''}
                 for r in docs}
        stems = _doc_stems(conn)
        edges, dead = {}, {}
        rows = conn.execute(
            'SELECT d.path AS path, l.line_text AS line_text '
            'FROM documents d JOIN lines l ON l.doc_id=d.id '
            'ORDER BY d.id, l.line_no').fetchall()
        cur_path = None
        for row in rows:
            p = row['path']
            if p != cur_path:
                cur_path = p
            for title, clause in find_refs_in_text(row['line_text'] or ''):
                tgt = _resolve_title(stems, title)
                if not tgt:
                    dead.setdefault(p, set()).add(title)
                elif tgt != p:
                    edges.setdefault((p, tgt), set()).add(clause or '')
        return nodes, edges, dead
    finally:
        conn.close()


# ---------- 力导向（按分类分区，保留原方案） ----------

def _layout_force(node_ids, edges_pairs, box=None):
    """确定性力导向坐标。孤立节点均匀摆外圈，连通节点居中力导向，
    最后做防重叠松弛。box=(x0,y0,x1,y1) 时只在该分类分区内布局。"""
    if box is None:
        x0, y0, x1, y1 = 0.0, 0.0, 1400.0, 900.0
    else:
        x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    n = len(node_ids)
    if n == 0:
        return {}
    idx = {p: i for i, p in enumerate(node_ids)}
    adj = [set() for _ in range(n)]
    for s, t in edges_pairs:
        if s in idx and t in idx and s != t:
            adj[idx[s]].add(idx[t])
            adj[idx[t]].add(idx[s])
    iso = [i for i in range(n) if not adj[i]]
    conn_idx = [i for i in range(n) if adj[i]]
    conn_set = set(conn_idx)
    cx, cy = x0 + w / 2.0, y0 + h / 2.0
    pos = [[0.0, 0.0] for _ in range(n)]
    r_out = 0.40 * min(w, h)
    r_in = max(r_out - 90.0, 120.0)
    for k_i, i in enumerate(iso):
        a = -math.pi / 2 + 2 * math.pi * k_i / max(len(iso), 1)
        pos[i] = [cx + r_out * math.cos(a), cy + r_out * math.sin(a)]
    for k_i, i in enumerate(conn_idx):
        a = 2 * math.pi * k_i / max(len(conn_idx), 1)
        pos[i] = [cx + 120 * math.cos(a), cy + 90 * math.sin(a)]

    def _clamp_conn(i):
        dx = pos[i][0] - cx
        dy = pos[i][1] - cy
        d = math.hypot(dx, dy)
        if d > r_in:
            pos[i][0] = cx + dx / d * r_in
            pos[i][1] = cy + dy / d * r_in

    k = 1.15 * math.sqrt((w * 0.8) * (h * 0.8) / max(n, 1))
    temp = w / 50.0
    for _ in range(ITERS):
        disp = [[0.0, 0.0] for _ in range(n)]
        for i in conn_idx:
            xi, yi = pos[i]
            for j in range(n):
                if j == i:
                    continue
                dx = xi - pos[j][0]
                dy = yi - pos[j][1]
                d2 = dx * dx + dy * dy or 0.01
                d = math.sqrt(d2)
                f = k * k / d
                ux, uy = dx / d, dy / d
                disp[i][0] += ux * f
                disp[i][1] += uy * f
        for i in conn_idx:
            for j in adj[i]:
                dx = pos[i][0] - pos[j][0]
                dy = pos[i][1] - pos[j][1]
                d = math.sqrt(dx * dx + dy * dy) or 0.01
                f = (d * d) / k
                ux, uy = dx / d, dy / d
                disp[i][0] -= ux * f
                disp[i][1] -= uy * f
        for i in conn_idx:
            disp[i][0] += (cx - pos[i][0]) * 0.02
            disp[i][1] += (cy - pos[i][1]) * 0.02
        for i in conn_idx:
            dx, dy = disp[i]
            d = math.sqrt(dx * dx + dy * dy) or 1
            lim = min(d, temp)
            pos[i][0] += dx / d * lim
            pos[i][1] += dy / d * lim
            _clamp_conn(i)
        temp *= 0.97
    min_sep = 88.0
    for _ in range(120):
        moved = False
        for i in range(n):
            for j in range(i + 1, n):
                dx = pos[j][0] - pos[i][0]
                dy = pos[j][1] - pos[i][1]
                d2 = dx * dx + dy * dy or 0.01
                if d2 >= min_sep * min_sep:
                    continue
                d = math.sqrt(d2)
                push = (min_sep - d) / 2 + 0.5
                ux, uy = dx / d, dy / d
                mi, mj = i in conn_set, j in conn_set
                if mi and mj:
                    pos[i][0] -= ux * push
                    pos[i][1] -= uy * push
                    pos[j][0] += ux * push
                    pos[j][1] += uy * push
                elif mi:
                    pos[i][0] -= ux * 2 * push
                    pos[i][1] -= uy * 2 * push
                elif mj:
                    pos[j][0] += ux * 2 * push
                    pos[j][1] += uy * 2 * push
                moved = True
        for i in conn_idx:
            _clamp_conn(i)
        if not moved:
            break
    return {p: (pos[i][0], pos[i][1]) for p, i in idx.items()}


# ---------- 分层有向布局（Sugiyama 简化版） ----------

def _layout_layered(node_ids, pairs):
    """分层布局：无引用边的孤立节点分离到底部网格；
    其余按 s→t（s 引用 t）方向，t（依据）在第 0 层（最上）。
    1) DFS 找反馈边破环  2) 最长路径分层
    3) 中位数 barycenter 多趟扫描减交叉。
    返回 {id:(x,y)}、{id:level}、画布 (LW,LH)。
    """
    ids = list(node_ids)
    sset = set(ids)
    incident = {p: False for p in ids}
    succ = {p: set() for p in ids}
    for s, t in pairs:
        if s in sset and t in sset and s != t:
            succ[s].add(t)
            incident[s] = True
            incident[t] = True
    iso = [p for p in ids if not incident[p]]
    linked = [p for p in ids if incident[p]]

    # --- 破环：DFS 三色标记，回边（指向灰色祖先）记为反馈边 ---
    color = {p: 0 for p in linked}
    back = set()

    def _dfs(u):
        color[u] = 1
        for v in sorted(succ[u]):
            if color.get(v, 2) == 0:
                _dfs(v)
            elif color.get(v) == 1:
                back.add((u, v))
        color[u] = 2

    for p in sorted(linked):
        if color[p] == 0:
            _dfs(p)
    dag = {p: sorted(t for t in succ[p] if (p, t) not in back)
           for p in linked}

    # --- 最长路径分层（无出边的纯依据=第 0 层） ---
    depth = {}

    def _depth(p):
        if p in depth:
            return depth[p]
        depth[p] = 0 if not dag[p] else 1 + max(_depth(t) for t in dag[p])
        return depth[p]

    for p in linked:
        _depth(p)
    layer_of = {p: depth[p] for p in linked}
    n_layers = max(layer_of.values(), default=0) + 1
    layers = [[] for _ in range(n_layers)]
    for p in sorted(linked, key=lambda q: (layer_of[q], q)):
        layers[layer_of[p]].append(p)

    # --- 层内排序减交叉：barycenter 中位数，上下交替扫描 ---
    def _median(vals):
        vals = sorted(vals)
        m = len(vals)
        if m % 2:
            return vals[m // 2]
        return (vals[m // 2 - 1] + vals[m // 2]) / 2

    pos_in_layer = {}
    for L, ls in enumerate(layers):
        for i, p in enumerate(ls):
            pos_in_layer[p] = i
    for sweep in range(8):
        if sweep % 2 == 0:
            # 自上而下：按所引用依据（上一层）位置排
            for L in range(1, n_layers):
                keys = {}
                for p in layers[L]:
                    vals = [pos_in_layer[t] for t in dag[p]
                            if layer_of.get(t) == L - 1]
                    keys[p] = _median(vals) if vals else pos_in_layer[p]
                layers[L].sort(key=lambda p: keys[p])
        else:
            # 自下而上：按引用者（下一层）位置排
            pred = {p: [] for p in linked}
            for u in linked:
                for t in dag[u]:
                    pred[t].append(u)
            for L in range(n_layers - 2, -1, -1):
                keys = {}
                for p in layers[L]:
                    vals = [pos_in_layer[u] for u in pred[p]
                            if layer_of.get(u) == L + 1]
                    keys[p] = _median(vals) if vals else pos_in_layer[p]
                layers[L].sort(key=lambda p: keys[p])
        for L, ls in enumerate(layers):
            for i, p in enumerate(ls):
                pos_in_layer[p] = i

    # --- 坐标：层内间距自适应（宽层压缩，避免整图被缩成斑点），每层居中 ---
    max_count = max((len(ls) for ls in layers), default=1)
    gap = min(L_XGAP, max(L_GAP_MIN,
                          LAYER_TARGET_W / max(1, max_count - 1)))
    layer_w = [(len(ls) - 1) * gap for ls in layers]
    max_w = max(layer_w, default=0.0)
    center_x = L_MARGIN + max_w / 2
    coords = {}
    guides = []
    for L, ls in enumerate(layers):
        x0 = center_x - layer_w[L] / 2
        y = L_TOP + L * L_YGAP
        for i, p in enumerate(ls):
            coords[p] = (x0 + i * gap, y)
        guides.append({'x0': x0 - 20, 'x1': x0 + layer_w[L] + 20,
                       'y': y, 'L': L})

    # --- 孤立节点：底部网格带 ---
    iso_y0 = L_TOP + n_layers * L_YGAP + 78
    guides.append({'x0': L_MARGIN,
                   'x1': L_MARGIN + min(len(iso), L_ISO_COLS) * L_ISO_XGAP,
                   'y': iso_y0 - 30, 'L': -1})
    for i, p in enumerate(iso):
        r, c = divmod(i, L_ISO_COLS)
        coords[p] = (L_MARGIN + c * L_ISO_XGAP,
                     iso_y0 + r * L_ISO_YGAP)
    iso_rows = math.ceil(max(len(iso), 1) / L_ISO_COLS)
    LW = max(L_MARGIN * 2 + max_w,
             L_MARGIN * 1.5 + min(len(iso), L_ISO_COLS) * L_ISO_XGAP)
    LH = iso_y0 + max(iso_rows - 1, 0) * L_ISO_YGAP + 56
    return coords, layer_of, round(LW), round(LH), iso_y0, guides


# 分类分区配色（淡底色, 边框/标题色），按分类索引循环
CAT_PALETTE = [('#e8f1fe', '#1f6feb'), ('#e9f7ef', '#1a7f37'),
               ('#fdf0e2', '#bf7000'), ('#f3eafd', '#8250df'),
               ('#fdeaea', '#cf222e'), ('#e6f9f8', '#0e8c8c'),
               ('#fff4d6', '#9a6700'), ('#eef0f3', '#57606a')]

BRAND = '#0F6CBD'


def generate_graph(db_path):
    """生成双布局 HTML，返回 (html, stats)。"""
    nodes_all, edges, dead = aggregate_graph(db_path)
    total = len(nodes_all)
    if total > MAX_ALL_NODES:
        related = set()
        for s, t in edges:
            related.add(s)
            related.add(t)
        for p in dead:
            related.add(p)
        nodes = {p: v for p, v in nodes_all.items() if p in related}
    else:
        nodes = dict(nodes_all)
    pairs = [(s, t) for (s, t) in edges if s in nodes and t in nodes]

    # ---- 力导向：按分类网格分区 ----
    groups = {}
    for p, info in nodes.items():
        groups.setdefault(info['category'] or '未分类', []).append(p)
    cats = sorted(groups, key=lambda c: (-len(groups[c]), c))
    k = len(cats)
    cols = max(1, math.ceil(math.sqrt(k)))
    rows = max(1, math.ceil(k / cols))
    max_n = max((len(v) for v in groups.values()), default=1)
    cell_w = max(640.0, min(1500.0, 140.0 * math.sqrt(max_n) + 320.0))
    cell_h = max(480.0, min(1050.0, 140.0 * math.sqrt(max_n) + 250.0))
    FW, FH = cell_w * cols, cell_h * rows
    PAD = 52.0
    f_coords, boxes, cat_colors = {}, {}, {}
    for gi, cat in enumerate(cats):
        gx, gy = gi % cols, gi // cols
        box = (gx * cell_w + PAD, gy * cell_h + PAD,
               (gx + 1) * cell_w - PAD, (gy + 1) * cell_h - PAD)
        boxes[cat] = (gx * cell_w, gy * cell_h,
                      (gx + 1) * cell_w, (gy + 1) * cell_h)
        cat_colors[cat] = CAT_PALETTE[gi % len(CAT_PALETTE)]
        f_coords.update(_layout_force(groups[cat], pairs, box=box))

    # ---- 分层布局 ----
    l_coords, layer_of, LW, LH, iso_y0, guides = _layout_layered(
        list(nodes.keys()), pairs)

    up, down = {}, {}
    for s, t in pairs:
        down.setdefault(s, []).append(t)
        up.setdefault(t, []).append(s)

    data = []
    for p, info in nodes.items():
        x, y = l_coords[p]
        fx, fy = f_coords[p]
        color, _ = STATUS_COLOR.get(info['status'],
                                    ('#57606a', info['status']))
        data.append({'id': p, 'uri':Path(p).absolute().as_uri(), 'name': info['name'],
                     'status': info['status'],
                     'cat': info['category'] or '未分类',
                     'doc_no': info['doc_no'],
                     'x': round(x, 1), 'y': round(y, 1),
                     'fx': round(fx, 1), 'fy': round(fy, 1),
                     'lv': layer_of.get(p, -1),
                     'color': color, 'dead': p in dead,
                     'up': up.get(p, []), 'down': down.get(p, [])})
    edge_data = []
    name_by_path = {p: v['name'] for p, v in nodes.items()}
    cat_by_path = {p: (v['category'] or '未分类') for p, v in nodes.items()}
    for (s, t), clauses in edges.items():
        if s not in nodes or t not in nodes:
            continue
        edge_data.append({
            's': s, 't': t, 'sc': cat_by_path[s], 'tc': cat_by_path[t],
            'cross': cat_by_path[s] != cat_by_path[t],
            'label': f'{name_by_path[s]} → {name_by_path[t]}'
                     + ('（' + '、'.join(sorted(c for c in clauses if c))[:40]
                        + '）' if any(clauses) else '')})
    dead_data = {name_by_path.get(p, os.path.basename(p)): sorted(t)
                 for p, t in dead.items() if p in nodes}
    cat_meta = [{'name': c, 'n': len(groups[c]),
                 'fill': cat_colors[c][0], 'stroke': cat_colors[c][1]}
                for c in cats]
    stats = {'nodes': len(nodes), 'nodes_total': total,
             'edges': len(edge_data),
             'dead_docs': sum(1 for p in dead if p in nodes),
             'cats': len(cats)}
    W, H = round(max(FW, LW)), round(max(FH, LH))
    html = _render(data, edge_data, dead_data, stats, boxes, cat_meta,
                   W, H, round(iso_y0), guides)
    return html, stats


def _esc(s):
    import html as _h
    return _h.escape(str(s or ''), quote=True)


def _render(data, edges, dead_data, stats, boxes, cat_meta, W, H, iso_y0,
            guides):
    payload = json.dumps(
        {'nodes': data, 'edges': edges, 'dead': dead_data,
         'view': {'w': W, 'h': H}, 'isoY': iso_y0},
        ensure_ascii=False)
    # 力导向模式的分类分区背景
    zone_svg = []
    for c in cat_meta:
        x0, y0, x1, y1 = boxes[c['name']]
        zone_svg.append(
            f'<g class="zone" data-cat="{_esc(c["name"])}">'
            f'<rect x="{x0 + 8}" y="{y0 + 8}" width="{x1 - x0 - 16}" '
            f'height="{y1 - y0 - 16}" rx="14" fill="{c["fill"]}" '
            f'stroke="{c["stroke"]}" stroke-width="1.6" '
            f'stroke-dasharray="6 4"/>'
            f'<text x="{x0 + 26}" y="{y0 + 36}" font-size="17" '
            f'font-weight="700" fill="{c["stroke"]}">{_esc(c["name"])}'
            f' <tspan font-size="12" font-weight="400">({c["n"]} 个文件)'
            f'</tspan></text></g>')
    # 分层模式的层引导线（仅分层可见，JS 切换 opacity）
    guide_svg = []
    for g in guides:
        label = ('无引用关系的文件' if g['L'] < 0
                 else f"第 {g['L']} 层"
                      + (' · 原始依据' if g['L'] == 0 else ''))
        guide_svg.append(
            f'<line x1="{round(g["x0"], 1)}" y1="{g["y"]}" '
            f'x2="{round(g["x1"], 1)}" y2="{g["y"]}"/>'
            f'<text x="{round(g["x0"] - 6, 1)}" y="{g["y"] + 4}" '
            f'text-anchor="end">{_esc(label)}</text>')
    edge_svg = []
    pos = {d['id']: (d['x'], d['y']) for d in data}
    for e in edges:
        x1, y1 = pos[e['s']]
        x2, y2 = pos[e['t']]
        cls = 'edge cross' if e.get('cross') else 'edge'
        marker = 'arwc' if e.get('cross') else 'arw'
        edge_svg.append(
            f'<line class="{cls}" data-s="{_esc(e["s"])}" '
            f'data-t="{_esc(e["t"])}" data-sc="{_esc(e["sc"])}" '
            f'data-tc="{_esc(e["tc"])}" x1="{x1}" y1="{y1}" x2="{x2}" '
            f'y2="{y2}" marker-end="url(#{marker})">'
            f'<title>{_esc(e["label"])}</title></line>')
    node_svg = []
    for d in data:
        stroke = '#d1242f' if d['dead'] else '#ffffff'
        sw = 3 if d['dead'] else 1.5
        t = (f"{d['name']}｜分类：{d['cat']}｜"
             f"{STATUS_COLOR.get(d['status'], ('', d['status']))[1]}")
        if d['doc_no']:
            t += f"｜{d['doc_no']}"
        if d['dead']:
            t += '｜存在无法解析的引用（死链）'
        node_svg.append(
            f'<g class="node" data-id="{_esc(d["id"])}" '
            f'data-cat="{_esc(d["cat"])}">'
            f'<circle cx="{d["x"]}" cy="{d["y"]}" r="9" fill="{d["color"]}" '
            f'stroke="{stroke}" stroke-width="{sw}"/>'
            f'<text x="{d["x"] + 12}" y="{d["y"] + 4}">{_esc(d["name"])}</text>'
            f'<title>{_esc(t)}</title></g>')
    legend = ('<span><i style="background:#1a7f37"></i>现行</span>'
              '<span><i style="background:#8a8a8a"></i>废止</span>'
              '<span><i style="background:#e08a00"></i>被替代</span>'
              '<span><i style="background:#fff;border:2px solid #d1242f"></i>'
              '红描边=有死链</span>'
              '<span><i style="background:transparent;border-top:2px dashed '
              '#c07a00;width:16px;border-radius:0"></i>虚线=跨分类引用</span>')
    chips = ['<button class="catchip on" data-cat="" '
             'onclick="toggleCat(this)">全部分类</button>']
    for c in cat_meta:
        chips.append(
            f'<button class="catchip" data-cat="{_esc(c["name"])}" '
            f'style="border-color:{c["stroke"]};color:{c["stroke"]}" '
            f'onclick="toggleCat(this)">{_esc(c["name"])} '
            f'({c["n"]})</button>')
    note = (f'共 {stats["cats"]} 个分类 / {stats["nodes_total"]} 个文件，'
            f'当前显示 {stats["nodes"]} 个'
            + ('（仅显示有引用关系的节点）'
               if stats['nodes_total'] > stats['nodes'] else '')
            + f'；引用边 {stats["edges"]} 条（含跨分类），'
              f'死链文件 {stats["dead_docs"]} 个。')
    return f'''<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>引用关系图</title>
<style>
 body{{margin:0;font-family:"Microsoft YaHei UI",sans-serif;background:#f5f6f8}}
 header{{padding:10px 16px;background:{BRAND};color:#fff}}
 header b{{font-size:16px}} .sub{{font-size:12px;opacity:.92;margin-top:2px}}
 .cats{{padding:7px 16px;background:#fff;border-bottom:1px solid #ddd;
   display:flex;flex-wrap:wrap;gap:6px}}
 .catchip{{font-size:12px;padding:3px 11px;border:1px solid #9aa7b5;
   border-radius:13px;background:#fff;cursor:pointer;color:#444;
   transition:background .18s,color .18s,border-color .18s,
     transform .18s,box-shadow .18s}}
 .catchip:hover{{transform:translateY(-1px);box-shadow:0 2px 6px rgba(0,0,0,.12)}}
 .catchip:active{{transform:scale(.96)}}
 .catchip.on{{background:{BRAND};border-color:{BRAND};color:#fff!important}}
 .legend{{padding:6px 16px;background:#fff;border-bottom:1px solid #ddd;
   font-size:13px}}
 .legend span{{margin-right:18px}}
 .legend i{{display:inline-block;width:10px;height:10px;border-radius:50%;
   margin-right:4px;vertical-align:middle}}
 .toolbar{{display:flex;flex-wrap:wrap;align-items:center;gap:6px;margin-top:5px}}
 .seg{{display:inline-flex;border:1px solid #c9d2dc;border-radius:5px;
   overflow:hidden}}
 .seg button{{border:0;border-right:1px solid #c9d2dc;border-radius:0}}
 .seg button:last-child{{border-right:0}}
 .toolbar button{{padding:3px 10px;border:1px solid #c9d2dc;
   background:#fff;border-radius:5px;cursor:pointer;font-size:12px;color:#333;
   transition:background .18s,color .18s,border-color .18s,
     transform .15s,box-shadow .18s}}
 .toolbar button:hover{{background:#eef3f8;transform:translateY(-1px);
   box-shadow:0 2px 6px rgba(0,0,0,.10)}}
 .toolbar button:active{{transform:scale(.95)}}
 .toolbar button.on{{background:{BRAND};border-color:{BRAND};color:#fff}}
 .toolbar button:disabled{{opacity:.45;cursor:default;background:#fff;color:#333}}
 .toolbar select{{font-size:12px;padding:2px 4px;border:1px solid #c9d2dc;
   border-radius:4px}}
 .toolbar .hint{{color:#66707c;font-size:12px;margin-left:6px}}
 .main{{display:flex;height:calc(100vh - 172px)}}
 .canvas{{flex:1;overflow:hidden;background:#fff;cursor:grab}}
 .canvas.panning{{cursor:grabbing}}
 svg{{display:block;width:100%;height:100%}}
 #zones{{opacity:0;transition:opacity .25s;pointer-events:none}}
 #guides{{transition:opacity .25s}}
 #guides line{{stroke:#e2e7ee;stroke-width:1;stroke-dasharray:2 5}}
 #guides text{{font-size:11px;fill:#98a2b0}}
 .node{{cursor:pointer}} .node text{{font-size:12px;fill:#222;
   paint-order:stroke;stroke:#fff;stroke-width:3px;stroke-linejoin:round;
   transition:opacity .18s}}
 /* 语义缩放：低倍总览只看圆点分层（Gephi/Obsidian 做法），放大才显名字 */
 #vp.z-far .node text{{opacity:0}}
 /* 默认边降噪；选中时分组整体压暗，只重绘关联边（事件委托，无全量遍历） */
 .edge{{stroke:#aeb8c4;stroke-width:1.4;opacity:.75;
   transition:stroke .18s,opacity .18s,stroke-width .18s}}
 .edge.cross{{stroke:#c07a00;stroke-width:1.6;stroke-dasharray:7 4;
   opacity:.9}}
 #nodes.sel .node{{opacity:.26;transition:opacity .18s}}
 #nodes.sel .node.hl{{opacity:1}}
 #edges.sel .edge{{opacity:.08}}
 .node circle{{transition:stroke-width .15s,stroke .18s}}
 .node.sel circle{{stroke:{BRAND};stroke-width:4}}
 .edge.hl{{stroke:{BRAND};stroke-width:3;opacity:1}}
 .edge.hl.cross{{stroke:#a35f00;stroke-width:3;opacity:1}}
 .node.hl text{{font-weight:700;fill:{BRAND}}}
 /* 入场与面板动效（uiverse 风格） */
 @keyframes jzFadeUp{{from{{opacity:0;transform:translateY(10px)}}
   to{{opacity:1;transform:none}}}}
 @keyframes jzFadeIn{{from{{opacity:0}}to{{opacity:1}}}}
 #vp{{animation:jzFadeIn .3s ease-out}}
 .cats,.legend{{animation:jzFadeUp .28s ease-out both}}
 .legend{{animation-delay:.06s}}
 aside{{animation:jzFadeUp .32s ease-out .1s both}}
 #panel>*{{animation:jzFadeUp .22s ease-out}}
 @media (prefers-reduced-motion:reduce){{
  #vp,.cats,.legend,aside,#panel>*{{animation:none}}
  *{{transition:none!important}}}}
 aside{{width:304px;background:#fff;border-left:1px solid #ddd;padding:12px;
   overflow:auto;font-size:13px}}
 aside h3{{margin:0 0 8px;font-size:14px}}
 aside .grp{{margin-bottom:10px}}
 aside .item{{padding:4px 8px;border-radius:4px;background:#f5f7fa;
   margin:3px 0;cursor:pointer}}
 aside .item:hover{{background:#e3edf8;color:{BRAND}}}
 aside .dir{{font-weight:700;margin:10px 0 4px}}
 .dead{{color:#d1242f}}
</style></head><body>
<header><b>引用关系图</b>
 <div class="sub">读法：从上到下 = 依据 → 制度 → 细则；
  A → B 表示 A 引用了 B，顺箭头往顶部可追溯依据；
  单击节点看上下游，双击节点只看它的引用链</div>
</header>
<div class="cats">{''.join(chips)}</div>
<div class="legend">{legend}　{_esc(note)}
 <div class="toolbar">
  <span class="seg">
   <button id="btnLayer" class="on">⇉ 分层看前后</button>
   <button id="btnForce">⇶ 力导向看聚类</button></span>
  <select id="depth" title="聚焦层数">
   <option value="1">1 层</option>
   <option value="2" selected>2 层</option>
   <option value="3">3 层</option></select>
  <button id="btnFocus" disabled>◎ 聚焦此文件</button>
  <button id="btnExitFocus" disabled>✕ 退出聚焦</button>
  <button id="btnFit">⤢ 适应窗口</button>
  <button id="btnReset">⟲ 重置</button>
  <span class="hint">滚轮缩放 · 拖拽平移</span>
 </div>
</div>
<div class="main">
 <div class="canvas" id="cv">
  <svg viewBox="0 0 {W} {H}" preserveAspectRatio="xMidYMid meet">
   <defs><marker id="arw" markerWidth="9" markerHeight="9" refX="8" refY="3"
    orient="auto" markerUnits="strokeWidth">
    <path d="M0,0 L8,3 L0,6 z" fill="#9aa6b2"/></marker>
    <marker id="arwc" markerWidth="9" markerHeight="9" refX="8" refY="3"
    orient="auto" markerUnits="strokeWidth">
    <path d="M0,0 L8,3 L0,6 z" fill="#c07a00"/></marker></defs>
   <g id="vp">
    <g id="zones">{''.join(zone_svg)}</g>
    <g id="guides">{''.join(guide_svg)}</g>
    <g id="edges">{''.join(edge_svg)}</g>
    <g id="nodes">{''.join(node_svg)}</g>
   </g>
  </svg>
 </div>
 <aside><h3>节点详情</h3><div id="panel">
   点击任意节点，查看它引用的依据与引用它的文件；
   侧栏名称可继续点击，沿引用链一路追溯。</div></aside>
</div>
<script>
const G = {payload};
const byId = {{}};
G.nodes.forEach(n => byId[n.id] = n);
const nodesG = document.getElementById('nodes'),
      edgesG = document.getElementById('edges'),
      zonesG = document.getElementById('zones'),
      guidesG = document.getElementById('guides'),
      cv = document.getElementById('cv'),
      svg = document.querySelector('svg'),
      vp = document.getElementById('vp'),
      panel = document.getElementById('panel');
const nodeEls = new Map();
[...nodesG.children].forEach(g => nodeEls.set(g.dataset.id, g));
const lineEls = [...edgesG.children];
/* 邻接与边索引（只建一次） */
const nInc = new Map(), nOut = new Map(), linesOf = new Map();
G.nodes.forEach(n => {{ nInc.set(n.id, []); nOut.set(n.id, []);
 linesOf.set(n.id, []); }});
G.edges.forEach((e, i) => {{ nOut.get(e.s).push(e.t); nInc.get(e.t).push(e.s);
 linesOf.get(e.s).push(i); linesOf.get(e.t).push(i); }});

/* ---- 坐标 / 边端点（按当前布局，裁到圆点边缘） ---- */
let mode = 'layer';
function xy(n, m) {{ return m === 'layer' ? [n.x, n.y] : [n.fx, n.fy]; }}
function edgePts(e, m) {{
 const s = byId[e.s], t = byId[e.t];
 let [sx, sy] = xy(s, m), [tx, ty] = xy(t, m);
 const dx = tx - sx, dy = ty - sy, d = Math.hypot(dx, dy) || 1;
 return {{ x1: sx + dx / d * 10, y1: sy + dy / d * 10,
          x2: tx - dx / d * 10, y2: ty - dy / d * 10 }};
}}

/* ---- 布局切换：420ms 缓动，节点与边同步滑动 ---- */
let animId = null;
function morph(to) {{
 if (to === mode) return;
 mode = to;
 document.getElementById('btnLayer').classList.toggle('on', to === 'layer');
 document.getElementById('btnForce').classList.toggle('on', to === 'force');
 zonesG.style.opacity = to === 'force' ? '1' : '0';
 guidesG.style.opacity = to === 'force' ? '0' : '1';
 const cStart = [], tStart = [], lStart = [];
 nodeEls.forEach((g, id) => {{
  const c = g.querySelector('circle'), tx = g.querySelector('text'),
        n = byId[id], [px, py] = xy(n, to);
  cStart.push([c, c.cx.baseVal.value, c.cy.baseVal.value, px, py]);
  tStart.push([tx, +tx.getAttribute('x'), +tx.getAttribute('y'),
               px + 12, py + 4]);
 }});
 lineEls.forEach((l, i) => {{
  const p = edgePts(G.edges[i], to);
  lStart.push([l, +l.getAttribute('x1'), +l.getAttribute('y1'),
               +l.getAttribute('x2'), +l.getAttribute('y2'), p]);
 }});
 if (animId) cancelAnimationFrame(animId);
 const t0 = performance.now(), DUR = 420;
 function frame(now) {{
  let t = Math.min(1, (now - t0) / DUR);
  t = t < .5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2;
  cStart.forEach(a => {{ a[0].setAttribute('cx', a[1] + (a[3] - a[1]) * t);
   a[0].setAttribute('cy', a[2] + (a[4] - a[2]) * t); }});
  tStart.forEach(a => {{ a[0].setAttribute('x', a[1] + (a[3] - a[1]) * t);
   a[0].setAttribute('y', a[2] + (a[4] - a[2]) * t); }});
  lStart.forEach(a => {{
   a[0].setAttribute('x1', a[1] + (a[5].x1 - a[1]) * t);
   a[0].setAttribute('y1', a[2] + (a[5].y1 - a[2]) * t);
   a[0].setAttribute('x2', a[3] + (a[5].x2 - a[3]) * t);
   a[0].setAttribute('y2', a[4] + (a[5].y2 - a[4]) * t); }});
  if (t < 1) animId = requestAnimationFrame(frame);
  else {{ animId = null; afterMove(); }}
 }}
 animId = requestAnimationFrame(frame);
}}
document.getElementById('btnLayer').onclick = () => morph('layer');
document.getElementById('btnForce').onclick = () => morph('force');

/* ---- 缩放 / 平移（滚轮 rAF 合帧，避免快速滚动抖动） ---- */
let vz = {{ x: 0, y: 0, k: 1 }}, drag = null, moved = false;
function apply() {{ vp.setAttribute('transform',
 'translate(' + vz.x + ',' + vz.y + ') scale(' + vz.k + ')');
 vp.classList.toggle('z-far', vz.k < 0.6); }}
function svgPt(e) {{ const pt = svg.createSVGPoint();
 pt.x = e.clientX; pt.y = e.clientY;
 return pt.matrixTransform(svg.getScreenCTM().inverse()); }}
let wEvt = null, wPending = false;
function doZoom() {{
 wPending = false; const e = wEvt, p = svgPt(e);
 const f = e.deltaY < 0 ? 1.12 : 1 / 1.12;
 const nk = Math.max(.1, Math.min(6, vz.k * f)), r = nk / vz.k;
 vz.x = p.x - (p.x - vz.x) * r;
 vz.y = p.y - (p.y - vz.y) * r;
 vz.k = nk; apply();
}}
cv.addEventListener('wheel', e => {{ e.preventDefault(); wEvt = e;
 if (!wPending) {{ wPending = true; requestAnimationFrame(doZoom); }} }},
 {{ passive: false }});
cv.addEventListener('mousedown', e => {{ if (e.button !== 0) return;
 drag = {{ x: e.clientX, y: e.clientY }}; moved = false;
 cv.classList.add('panning'); }});
window.addEventListener('mousemove', e => {{ if (!drag) return;
 const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
 if (Math.abs(dx) + Math.abs(dy) > 3) moved = true;
 const sc = svg.getScreenCTM().a;
 vz.x += dx / (sc * vz.k); vz.y += dy / (sc * vz.k);
 drag = {{ x: e.clientX, y: e.clientY }}; apply(); }});
window.addEventListener('mouseup', () => {{ drag = null;
 cv.classList.remove('panning'); }});
function fitBox(x0, x1, y0, y1) {{
 const cw = cv.clientWidth, chh = cv.clientHeight;
 const kk = Math.min(cw / (x1 - x0), chh / (y1 - y0), 2.4);
 vz.k = kk;
 vz.x = (cw - (x0 + x1) * kk) / 2;
 vz.y = (chh - (y0 + y1) * kk) / 2;
 apply();
}}
function bbox(ids) {{
 let x0 = 1e9, x1 = -1e9, y0 = 1e9, y1 = -1e9;
 ids.forEach(id => {{ const n = byId[id], [x, y] = xy(n, mode);
  x0 = Math.min(x0, x); x1 = Math.max(x1, x + 70);
  y0 = Math.min(y0, y); y1 = Math.max(y1, y + 8); }});
 return [x0 - 50, x1 + 50, y0 - 46, y1 + 46];
}}
function fitIds(ids, silent) {{
 if (!ids.size) return;
 const b = bbox(ids);
 if (silent) {{ const cw = cv.clientWidth, chh = cv.clientHeight;
  const kk = Math.min(cw / (b[1] - b[0]), chh / (b[3] - b[2]), 2.4);
  vz.k = kk; vz.x = (cw - (b[0] + b[1]) * kk) / 2;
  vz.y = (chh - (b[2] + b[3]) * kk) / 2; apply(); }}
 else fitBox(b[0], b[1], b[2], b[3]);
}}
function fitView() {{ fitIds(new Set(G.nodes.map(n => n.id))); }}
function resetView() {{ vz = {{ x: 0, y: 0, k: 1 }}; apply(); }}
document.getElementById('btnFit').onclick = fitView;
document.getElementById('btnReset').onclick = resetView;
function afterMove() {{
 if (focusSet) fitIds(focusSet, true);
 else if (activeCat) fitCat(activeCat, true);
 else fitView();
}}
/* 双击：节点=聚焦；空白=退出聚焦/适应窗口 */
nodesG.addEventListener('dblclick', e => {{
 const g = e.target.closest('.node');
 if (g) focusNode(g.dataset.id, +document.getElementById('depth').value);
}});
cv.addEventListener('dblclick', e => {{
 if (e.target.closest('.node')) return;
 if (focusSet) exitFocus(); else fitView();
}});

/* ---- 分类筛选 ---- */
let activeCat = '';
function catPass(id) {{ const n = byId[id];
 return !activeCat || n.cat === activeCat; }}
function applyVisibility() {{
 nodeEls.forEach((g, id) => {{
  g.style.display = catPass(id) ? '' : 'none'; }});
 lineEls.forEach((l, i) => {{ const e = G.edges[i];
  l.style.display = (catPass(e.s) && catPass(e.t)) ? '' : 'none'; }});
 if (mode === 'force')
  [...zonesG.children].forEach(z => {{
   z.style.display = (!activeCat || z.dataset.cat === activeCat) ? '' : 'none';
  }});
}}
function toggleCat(btn) {{
 const c = btn.dataset.cat;
 activeCat = activeCat === c ? '' : c;
 if (focusSet) exitFocus(true);
 document.querySelectorAll('.catchip').forEach(b =>
  b.classList.toggle('on', b.dataset.cat === activeCat
   || (!activeCat && b.dataset.cat === '')));
 applyVisibility();
 if (activeCat) fitCat(activeCat); else fitView();
}}
function fitCat(c, silent) {{
 fitIds(new Set(G.nodes.filter(n => n.cat === c).map(n => n.id)), silent);
}}

/* ---- 聚焦模式：只展开以某节点为中心、N 层内的引用链 ---- */
let focusSet = null;
function focusNode(id, d) {{
 if (!byId[id]) return;
 let frontier = new Set([id]), seen = new Set([id]);
 for (let step = 0; step < d; step++) {{
  const nxt = new Set();
  frontier.forEach(p => {{
   nOut.get(p).forEach(t => {{ if (!seen.has(t)) nxt.add(t); }});
   nInc.get(p).forEach(s => {{ if (!seen.has(s)) nxt.add(s); }});
  }});
  if (!nxt.size) break;
  nxt.forEach(t => seen.add(t));
  frontier = nxt;
 }}
 focusSet = seen;
 activeCat = '';
 document.querySelectorAll('.catchip').forEach(b =>
  b.classList.toggle('on', b.dataset.cat === ''));
 nodeEls.forEach((g, pid) => g.style.display = seen.has(pid) ? '' : 'none');
 lineEls.forEach((l, i) => {{ const e = G.edges[i];
  l.style.display = (seen.has(e.s) && seen.has(e.t)) ? '' : 'none'; }});
 document.getElementById('btnExitFocus').disabled = false;
 select(id);
 fitIds(seen);
}}
function exitFocus(keepView) {{
 focusSet = null;
 document.getElementById('btnExitFocus').disabled = true;
 applyVisibility();
 if (!keepView) {{ if (activeCat) fitCat(activeCat); else fitView(); }}
}}
document.getElementById('btnFocus').onclick = () => {{
 if (curSel) focusNode(curSel, +document.getElementById('depth').value);
}};
document.getElementById('btnExitFocus').onclick = () => exitFocus();

/* ---- 选中 / 高亮（事件委托；只动关联元素，其余靠分组 CSS 压暗） ---- */
let curSel = null;
const hlN = new Set(), hlL = new Set();
function _resetHl() {{
 nodesG.classList.remove('sel'); edgesG.classList.remove('sel');
 hlN.forEach(id => nodeEls.get(id).classList.remove('hl', 'sel'));
 hlL.forEach(i => lineEls[i].classList.remove('hl'));
 hlN.clear(); hlL.clear();
}}
function clearSelect() {{
 _resetHl(); curSel = null;
 document.getElementById('btnFocus').disabled = true;
 panel.textContent = '点击任意节点，查看它引用的依据与引用它的文件；'
  + '侧栏名称可继续点击，沿引用链一路追溯。';
}}
nodesG.addEventListener('click', e => {{
 if (moved) return;
 const g = e.target.closest('.node');
 if (!g) {{ clearSelect(); return; }}
 select(g.dataset.id);
}});
function select(id) {{
 if (curSel === id) {{ clearSelect(); return; }}
 _resetHl(); curSel = id;
 nodesG.classList.add('sel'); edgesG.classList.add('sel');
 const n = byId[id];
 new Set([id, ...n.up, ...n.down]).forEach(p => {{
  const gg = nodeEls.get(p);
  if (gg && gg.style.display !== 'none') gg.classList.add('hl');
  hlN.add(p);
 }});
 nodeEls.get(id).classList.add('sel');
 linesOf.get(id).forEach(i => {{
  if (lineEls[i].style.display !== 'none') lineEls[i].classList.add('hl');
  hlL.add(i);
 }});
 document.getElementById('btnFocus').disabled = false;
 renderPanel(n);
}}
panel.addEventListener('click', e => {{
 const it = e.target.closest('.jumpto');
 if (it && byId[it.dataset.id]) selectJumpto(it.dataset.id);
}});
function selectJumpto(id) {{
 if (focusSet && !focusSet.has(id)) exitFocus(true);
 nodeEls.get(id).dispatchEvent(new MouseEvent('click', {{ bubbles: true }}));
}}

/* ---- 侧栏内容（DOM 构建，天然防注入） ---- */
function el(tag, cls, text) {{
 const d = document.createElement(tag);
 if (cls) d.className = cls;
 if (text != null) d.textContent = text;
 return d;
}}
function renderPanel(n) {{
 panel.innerHTML = '';
 let g = el('div', 'grp');
 g.appendChild(el('b', null, n.name));
 g.appendChild(el('div', null, '状态：' + ({{active:'有效',void:'已废止',replaced:'已替代'}}[n.status] || n.status)));
 g.appendChild(el('div', null, (n.doc_no || '无文号') + ' · 分类：' + n.cat));
 g.appendChild(el('div', null, n.lv >= 0
  ? '第 ' + n.lv + ' 层（最上层第 0 层为原始依据）'
  : '无引用关系的文件'));
 panel.appendChild(g);
 const actions = el('div','grp');
 const open = el('a',null,'打开原文件');
 open.href = n.uri;
 open.target = '_blank';
 actions.appendChild(open);
 actions.appendChild(document.createTextNode('　'));
 const back = el('a',null,'返回软件检索');
 back.href = 'jingzai://search?kw=' + encodeURIComponent('名称:' + n.name);
 back.title = '需已在软件的“设置 → 启动与唤起”中启用软件唤起';
 actions.appendChild(back);
 const copy = el('button',null,'复制文件路径');
 copy.onclick = async () => {{
  try {{ await navigator.clipboard.writeText(n.id); }}
  catch (_) {{
   const field = el('textarea'); field.value = n.id;
   field.style.position='fixed'; field.style.opacity='0';
   document.body.appendChild(field); field.select();
   if (!document.execCommand('copy')) {{ field.remove(); copy.textContent='请复制下方路径'; return; }}
   field.remove();
  }}
  copy.textContent='已复制';
  setTimeout(() => copy.textContent='复制文件路径',1300);
 }};
 actions.appendChild(copy);
 actions.appendChild(el('div','path',n.id));
 panel.appendChild(actions);
 panel.appendChild(el('div', 'dir', '它引用的依据 →（' + n.down.length + '）'));
 if (n.down.length) n.down.forEach(p => {{
  const it = el('div', 'item jumpto');
  it.dataset.id = p;
  it.textContent = '[' + byId[p].cat + '] ' + byId[p].name;
  panel.appendChild(it);
 }});
 else panel.appendChild(el('div', 'item', '无（它本身就是最上层依据，或未引用其他文件）'));
 panel.appendChild(el('div', 'dir', '← 引用它的文件（' + n.up.length + '）'));
 if (n.up.length) n.up.forEach(p => {{
  const it = el('div', 'item jumpto');
  it.dataset.id = p;
  it.textContent = '[' + byId[p].cat + '] ' + byId[p].name;
  panel.appendChild(it);
 }});
 else panel.appendChild(el('div', 'item', '无（暂无文件引用它）'));
 const dl = G.dead[n.name];
 if (dl && dl.length) {{
  panel.appendChild(el('div', 'dir dead',
   '无法解析的《》引用（可能文件未入库或名称不一致）：'));
  dl.forEach(t => panel.appendChild(el('div', 'item', '《' + t + '》')));
 }}
 panel.scrollTop = 0;
}}

/* Esc：取消选中，再按一次退出聚焦 */
window.addEventListener('keydown', e => {{
 if (e.key !== 'Escape') return;
 if (curSel) clearSelect();
 else if (focusSet) exitFocus();
}});
let rzT = null;
window.addEventListener('resize', () => {{ clearTimeout(rzT);
 rzT = setTimeout(() => {{
  if (focusSet) fitIds(focusSet, true);
  else if (activeCat) fitCat(activeCat, true);
  else fitView();
 }}, 180); }});
window.addEventListener('load', () => requestAnimationFrame(fitView));
</script></body></html>'''
