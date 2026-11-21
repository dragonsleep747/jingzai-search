# -*- coding: utf-8 -*-
"""临时验证：分类自动归类/自查 + 分类: 查询前缀 + ThinkFilter"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import indexer
import searcher
import ai_qa

ok = fail = 0


def check(name, cond):
    global ok, fail
    print(('PASS ' if cond else 'FAIL ') + name)
    ok, fail = ok + (1 if cond else 0), fail + (0 if cond else 1)


db = os.path.join(tempfile.gettempdir(), '_jz_cat_test.db')
if os.path.exists(db):
    os.remove(db)

# 1. 规则读写
check('默认规则读取', indexer.get_category_rules(db) ==
      indexer.DEFAULT_CATEGORY_RULES)
rules = [['国家标准文件', 'GB/T', 'GB50'],
         ['公司制度', '制度', '办法']]   # 扁平格式：[名, 词1, 词2...]
check('保存规则', indexer.save_category_rules(db, rules) is True)
check('回读规则', indexer.get_category_rules(db) == rules)

# 2. infer_category
check('文件名命中国标',
      indexer.infer_category('GB/T 3608-2008 高处作业分级.pdf',
                             '', '', rules) == '国家标准文件')
check('带空格编号归一化',
      indexer.infer_category('GB 50160-2008 石油化工防火标准.pdf',
                             '', '', rules) == '国家标准文件')
check('文号命中国标',
      indexer.infer_category('x.pdf', 'GB/T 3608-2008', '', rules)
      == '国家标准文件')
check('正文命中公司制度',
      indexer.infer_category('abc.docx', '', '第一章 总则\n本办法适用于',
                             rules) == '公司制度')
check('未命中返回空', indexer.infer_category('照片.png', '', '风景', rules) == '')

# 2b. 默认规则：JGJ 行标 / GBT 国标 / 正则 DB\d 地标
dr = indexer.DEFAULT_CATEGORY_RULES
check('JGJ→行标',
      indexer.infer_category('JGJ 80-2016 高处作业.pdf', '', '', dr)
      == '行业标准文件')
check('JGJT→行标',
      indexer.infer_category('JGJT 250-2011 职业标准.pdf', '', '', dr)
      == '行业标准文件')
check('GBT→国标',
      indexer.infer_category('GBT 50502-2009 施工组织设计.pdf', '', '', dr)
      == '国家标准文件')
check('re:GB\\d 国标',
      indexer.infer_category('GB 50300-2013 质量验收.pdf', '', '', dr)
      == '国家标准文件')
check('re:DB\\d 地标',
      indexer.infer_category('DBJ08-903-2010 现场施工安全.pdf', '', '', dr)
      == '地方标准文件')
check('DGTJ→地标',
      indexer.infer_category('DGTJ08-2035-2014 监理安全监督规程.pdf', '', '',
                             dr) == '地方标准文件')
check('文件名优先于正文（引用国标不误判）',
      indexer.infer_category('安全生产管理办法.docx', '',
                             '依据 GB 50300-2013 制定', dr) == '公司制度')

# 3. 入库自动归类
lib = os.path.join(tempfile.gettempdir(), '_jz_cat_lib')
os.makedirs(lib, exist_ok=True)
p1 = os.path.join(lib, 'GB 50160-2008 防火标准.txt')
with open(p1, 'w', encoding='utf-8') as f:
    f.write('石油化工企业设计防火标准\n第一条 目的')
p2 = os.path.join(lib, '安全生产管理办法.txt')
with open(p2, 'w', encoding='utf-8') as f:
    f.write('第一章 总则\n本办法适用于公司各部门')
p3 = os.path.join(lib, '会议纪要.txt')
with open(p3, 'w', encoding='utf-8') as f:
    f.write('今天开了个会')
conn, _ = indexer.get_db(db)
indexer._upsert(conn, p1, '.txt', 'text', 'utf-8', 1.0, 100,
                [(1, '石油化工企业设计防火标准'), (2, '第一条 目的')], False)
indexer._upsert(conn, p2, '.txt', 'text', 'utf-8', 1.0, 100,
                [(1, '第一章 总则'), (2, '本办法适用于公司各部门')], False)
indexer._upsert(conn, p3, '.txt', 'text', 'utf-8', 1.0, 100,
                [(1, '今天开了个会')], False)
conn.commit()
conn.close()
m = indexer.get_metadata(db, p1)
check('入库自动归类-国标', m['category'] == '国家标准文件')
check('入库自动归类-制度', indexer.get_metadata(db, p2)['category'] == '公司制度')
check('入库自动归类-未分类', indexer.get_metadata(db, p3)['category'] == '')

# 4. list_library 带分类
items = {it['path']: it for it in indexer.list_library(db)}
check('list_library 带 category',
      items[p1]['category'] == '国家标准文件' and items[p3]['category'] == '')

# 5. category_audit（手工改错一个 → mismatch；未命中 → uncategorized）
indexer.update_metadata(db, p1, category='公司制度')
audit = indexer.category_audit(db)
check('自查 mismatch',
      any(x['path'] == p1 and x['suggested'] == '国家标准文件'
          for x in audit['mismatch']))
check('自查 uncategorized',
      any(x['path'] == p3 for x in audit['uncategorized']))
check('自查 counts', audit['total'] == 3)

# 6. apply_categories 一键修正
n = indexer.apply_categories(db, {p1: '国家标准文件', p3: ''})
check('apply_categories', n == 2 and
      indexer.get_metadata(db, p1)['category'] == '国家标准文件')

# 7. 分类: 查询前缀（FTS 不可用的临时库走 LIKE / 元数据浏览）
free, flt = searcher.parse_advanced_query('分类:国家标准文件 防火')
check('parse 分类前缀', flt['category'] == ['国家标准文件'] and free == '防火')
free, flt = searcher.parse_advanced_query('分类:未分类')
check('parse 未分类', flt['category'] == ['未分类'])
free, flt = searcher.parse_advanced_query('分类:国家标准文件|公司制度')
check('parse 多分类', flt['category'] == ['国家标准文件', '公司制度'])
rows = searcher.search(db, '分类:国家标准文件')
check('search 纯分类浏览', {r['path'] for r in rows} == {p1})
rows = searcher.search(db, '分类:公司制度 防火')
check('search 分类+正文词', {r['path'] for r in rows} == set())
rows = searcher.search(db, '分类:国家标准文件|公司制度 办法')
check('search 多分类+正文', {r['path'] for r in rows} == {p2} or
      {r['path'] for r in rows} <= {p1, p2})
rows = searcher.search(db, '分类:未分类')
check('search 未分类浏览', {r['path'] for r in rows} == {p3})

# 8. ThinkFilter
tf = ai_qa.ThinkFilter()
out = tf.feed('<think>思')
out += tf.feed('考中</think>答')
out += tf.feed('案 A')
out += tf.flush()
check('ThinkFilter 跨块过滤', out == '答案 A')
tf2 = ai_qa.ThinkFilter()
out2 = tf2.feed('普通回答')
out2 += tf2.flush()
check('ThinkFilter 无思考段', out2 == '普通回答')
tf3 = ai_qa.ThinkFilter()
out3 = tf3.feed('a<think>')
out3 += tf3.feed('x</think>')
out3 += tf3.flush()
check('ThinkFilter 结束即放行', out3 == 'a')

# 9. update_metadata 兼容（不传 category 不覆盖）
indexer.update_metadata(db, p2, doc_no='Q/TEST 1-2026')
check('update 不覆盖分类', indexer.get_metadata(db, p2)['category'] == '公司制度')

os.remove(db)
print(f'\n{ok} passed, {fail} failed')
sys.exit(1 if fail else 0)
