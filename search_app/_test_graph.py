# -*- coding: utf-8 -*-
"""图谱按分类分区：构造3个分类+跨类引用，验证分区边界与渲染数据"""
import os, sys, tempfile, re, json, math
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import graph_html, indexer, sqlite3

ok = fail = 0
def check(name, cond, detail=''):
    global ok, fail
    cond = bool(cond)
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f' — {detail}' if detail else ''))
    ok += cond; fail += not cond

tmp = tempfile.mkdtemp(prefix='graph_test_')
db = os.path.join(tmp, 'index.db')
lib = os.path.join(tmp, 'library'); os.makedirs(lib, exist_ok=True)

specs = [
    ('安全管理制度A.txt', '安全管理', '第一章 总则\n安全员负责日常检查。\n依据《应急预案B》执行。\n' * 10),
    ('安全管理制度B.txt', '安全管理', '第二章 检查\n日常巡查要求。\n' * 10),
    ('应急预案B.txt', '应急管理', '第一条 总则\n应急响应流程。\n按《安全管理制度A》办。\n' * 10),
    ('消防规程C.txt', '消防管理', '消防设施维护。\n引用《应急预案B》。\n' * 10),
    ('消防规程D.txt', '消防管理', '动火管理规定。\n' * 10),
    ('未归类文件E.txt', '', '杂项内容。\n' * 10),
]
real_paths = []
for fn, cat, content in specs:
    fp = os.path.join(lib, fn)
    with open(fp, 'w', encoding='utf-8') as f:
        f.write(content)
    # 链接模式：不复制到全局 LIBRARY_DIR，避免污染真实库目录（返回状态字符串）
    indexer.link_file_to_library(fp, db)
    if cat:
        indexer.apply_categories(db, {fp: cat})
    real_paths.append(fp)

html, stats = graph_html.generate_graph(db)
check('4个分类（含未分类）', stats['cats'] == 4, str(stats['cats']))
check('6个节点', stats['nodes'] == 6, str(stats['nodes']))
check('有引用边', stats['edges'] >= 2, str(stats['edges']))

# 提取 payload，校验每个节点落在自己分类的 box 内
m = re.search(r'const G = (\{.*?\});\nconst byId', html, re.S)
payload = json.loads(m.group(1))
boxes = {}
for z in re.findall(r'<g class="zone" data-cat="([^"]+)">\s*<rect x="([\d.]+)" '
                    r'y="([\d.]+)" width="([\d.]+)" height="([\d.]+)"', html):
    cat, x, y, w, h = z[0], float(z[1]), float(z[2]), float(z[3]), float(z[4])
    boxes[cat] = (x, y, x + w, y + h)
check('渲染4个分区', len(boxes) == 4, str(list(boxes)))

bad = []
for n in payload['nodes']:
    x0, y0, x1, y1 = boxes.get(n['cat'], (0, 0, 0, 0))
    if not (x0 - 60 <= n['x'] <= x1 + 60 and y0 - 60 <= n['y'] <= y1 + 60):
        bad.append((n['name'], n['cat'], n['x'], n['y'], boxes[n['cat']]))
check('所有节点在各自分类分区内', not bad, str(bad[:3]))

cross = [e for e in payload['edges'] if e['cross']]
check('存在跨分类边（橙色虚线）', len(cross) >= 1, str([e['label'][:20] for e in cross]))
check('含分类筛选 chips', 'catchip' in html and 'toggleCat' in html)
check('含分类标题', '安全管理' in html and '应急管理' in html and '消防管理' in html)
check('viewBox 非1400x900(多分区网格)', 'viewBox="0 0 1400 1400"' in html
      or 'viewBox' in html)
vb = re.search(r'viewBox="0 0 (\d+) (\d+)"', html).groups()
check('画布为2x2网格(640x480/cell)', vb == ('1280', '960'), str(vb))

# JS 花括号配平粗检（f-string 双花括号已还原为单括号）
script = html[html.index('<script>'):]
check('JS 大括号配平', script.count('{') == script.count('}'),
      f"{script.count('{')} vs {script.count('}')}")
check('JS 小括号配平', script.count('(') == script.count(')'),
      f"{script.count('(')} vs {script.count(')')}")

out = os.path.join(tmp, 'graph.html')
with open(out, 'w', encoding='utf-8') as f:
    f.write(html)
print('HTML:', out)
print(f'结果: {ok} 通过, {fail} 失败')
sys.exit(1 if fail else 0)
