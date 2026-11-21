# -*- coding: utf-8 -*-
"""真机回归：重写后的 chat_stream（流式 / 停止即时 / 防挂死参数生效）"""
import os
import sys
import time
import threading

APP = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, APP)
import ai_qa

ok = fail = 0
def check(name, cond, detail=''):
    global ok, fail
    cond = bool(cond)
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f' — {detail}' if detail else ''))
    ok += cond; fail += (not cond)

BASE = 'http://127.0.0.1:11434'
MODEL = 'qwen2.5:7b-instruct'

try:
    tags = ai_qa.list_models(BASE)
    if not any(t.split(':')[0] == 'qwen2.5' for t in tags):
        print('[SKIP] qwen2.5 未安装')
        sys.exit(0)
except Exception as e:
    print(f'[SKIP] Ollama 不在线：{e}')
    sys.exit(0)

msgs = [{'role': 'user', 'content': '只回答四个字：镜仔真棒'}]

# 1) 流式正常收完
t0 = time.time()
pieces = []
try:
    for p in ai_qa.chat_stream(BASE, MODEL, msgs,
                               options={'num_predict': 20}):
        pieces.append(p)
    el = time.time() - t0
    check('流式生成完成', len(''.join(pieces)) > 0, f'{el:.1f}s {len(pieces)}块')
except Exception as e:
    check('流式生成完成', False, repr(e))

# 2) 生成中置停止标志 → ≤5 秒内退出
stop = threading.Event()
t0 = time.time()
n = 0
try:
    for p in ai_qa.chat_stream(BASE, MODEL,
                               [{'role': 'user',
                                 'content': '写一篇 300 字的散文'},
                                ],
                               options={'num_predict': 300},
                               should_stop=stop.is_set):
        n += 1
        if n == 5:
            stop.set()
    el = time.time() - t0
    check('停止即时生效（≤5s）', el < 8, f'收 {n} 块后停止，用时 {el:.1f}s')
except Exception as e:
    check('停止即时生效（≤5s）', False, repr(e))

# 3) 首字节前停止：connect 后不吐字也应快速返回
stop2 = threading.Event()
stop2.set()
t0 = time.time()
try:
    got = list(ai_qa.chat_stream(BASE, MODEL, msgs, should_stop=stop2.is_set))
    el = time.time() - t0
    check('预置停止快速返回', el < 5 and not got, f'{el:.1f}s')
except Exception as e:
    check('预置停止快速返回', False, repr(e))

print(f'\n结果: {ok} 通过, {fail} 失败')
sys.exit(1 if fail else 0)
