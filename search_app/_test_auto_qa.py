# -*- coding: utf-8 -*-
"""端到端验证：WAL 生效 + 入库→自动语义索引增量更新链路（临时库，不碰正式库）"""
import os
import sys
import json
import shutil
import sqlite3
import tempfile

APP = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, APP)

import indexer
import ai_qa

tmp = tempfile.mkdtemp(prefix='anzxun_test_')
db = os.path.join(tmp, 'index.db')
lib = os.path.join(tmp, 'library')
os.makedirs(lib, exist_ok=True)

ok = fail = 0
def check(name, cond, detail=''):
    global ok, fail
    cond = bool(cond)
    tag = 'PASS' if cond else 'FAIL'
    print(f'[{tag}] {name}' + (f' — {detail}' if detail else ''))
    ok += cond; fail += (not cond)

# 1) WAL 生效
conn, fts_ok = indexer.get_db(db)
mode = conn.execute('PRAGMA journal_mode').fetchone()[0]
busy = conn.execute('PRAGMA busy_timeout').fetchone()[0]
check('WAL 模式生效', mode == 'wal', f'journal_mode={mode}')
check('busy_timeout=30000', busy == 30000, f'busy_timeout={busy}')
conn.close()

# 2) 入库两个文本文件（走正式入库函数）
f1 = os.path.join(lib, '安全管理制度.txt')
with open(f1, 'w', encoding='utf-8') as f:
    f.write('第一章 总则\n' + '第一条 安全员负责日常安全检查与隐患排查。\n' * 8)
f2 = os.path.join(lib, '消防管理办法.txt')
with open(f2, 'w', encoding='utf-8') as f:
    f.write('第二章 消防职责\n' + '第二条 专职消防安全员每月组织演练。\n' * 8)
r1 = indexer.import_file_to_library(f1, db)
r2 = indexer.import_file_to_library(f2, db)
check('入库 2 个文件', r1 and r2)

# 3) 增量计划：两文件应都在 todo
conn = sqlite3.connect(db, timeout=30)
ai_qa.init_ai_tables(conn)
todo, gone = ai_qa.build_plan(conn)
check('build_plan 发现 2 个待建文件', len(todo) == 2, f'todo={len(todo)} gone={len(gone)}')
conn.close()

# 4) Ollama 实测：在线则真实构建语义索引
base = 'http://127.0.0.1:11434'
ollama_ok = True
try:
    tags = ai_qa.list_models(base)
    names = [t.split(':')[0] for t in tags]
    ollama_ok = 'bge-m3' in names
except Exception:
    ollama_ok = False
if ollama_ok:
    info = ai_qa.build_semantic_index(db, base, 'bge-m3')
    n = (info or {}).get('chunks') or 0
    check('语义索引构建（真实嵌入）', n > 0, f'chunks={n}')
    # 5) 再跑一轮：无变化应安静退出
    conn = sqlite3.connect(db, timeout=30)
    todo2, gone2 = ai_qa.build_plan(conn)
    conn.close()
    check('增量：第二轮无待建', len(todo2) == 0 and len(gone2) == 0,
          f'todo={len(todo2)} gone={len(gone2)}')
else:
    print('[SKIP] Ollama/bge-m3 未在线，跳过真实嵌入测试（增量计划逻辑已验证）')

# 6) WAL 下并发读写：写入线程 + 读线程同时跑
import threading
stop = threading.Event()
errs = []
def writer():
    c = sqlite3.connect(db, timeout=30)
    while not stop.is_set():
        try:
            c.execute('INSERT OR REPLACE INTO settings VALUES(?,?)', ('t', 'x'))
            c.commit()
        except Exception as e:
            errs.append(f'write:{e}')
    c.close()
def reader():
    c = sqlite3.connect(db, timeout=30)
    while not stop.is_set():
        try:
            c.execute('SELECT count(*) FROM documents').fetchone()
        except Exception as e:
            errs.append(f'read:{e}')
    c.close()
w1 = threading.Thread(target=writer, daemon=True)
r1t = threading.Thread(target=reader, daemon=True)
w1.start(); r1t.start()
import time; time.sleep(2)
stop.set(); w1.join(); r1t.join()
check('WAL 并发读写 2 秒无错误', not errs, '; '.join(errs[:3]))

shutil.rmtree(tmp, ignore_errors=True)
print(f'\n结果: {ok} 通过, {fail} 失败')
sys.exit(1 if fail else 0)
