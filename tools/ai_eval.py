# -*- coding: utf-8 -*-
"""AI 问答检索质量评测（golden 集，可重复运行）。

语料：20 篇合成制度文档，每篇含一条「唯一主题事实」（数值型），
其余为通用条款。每题 2 个问法，共 40 题：
- 语义转述题：不含主题关键词的改写（测向量语义检索）
- 关键词题：含主题关键词的定向查询（测关键词加权检索）

判分：标准答案字符串（为主题句的子串）出现在 top-K 命中块文本即判对。
指标：Recall@1/@3/@5/@8、MRR、平均首条得分、平均检索耗时。

用法:
    python tools/ai_eval.py [--docs 20] [--base http://127.0.0.1:11434] [--embed bge-m3]
"""
import os
import sys
import time
import shutil
import tempfile
import argparse

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'search_app'))

# (主题句, 标准答案[主题句子串], 语义转述问法, 关键词问法)
TOPICS = [
    ('女职工生育享受产假 98 天', '98 天', '生完孩子能休息多久？', '产假是多少天？'),
    ('工作满一年的员工每年享受带薪年休假 5 天', '5 天', '入职满一年每年能休几天带薪假？', '年休假有几天？'),
    ('高温作业岗位发放高温津贴每月 300 元', '300 元', '夏天高温岗位每月能领多少补贴？', '高温津贴每月多少钱？'),
    ('公司年度培训经费预算为 2 万元', '2 万元', '每年用于培训的钱有多少预算？', '培训经费预算是多少？'),
    ('差旅住宿报销标准为每晚 400 元', '400 元', '出差住酒店一晚最多报销多少？', '住宿报销标准是多少？'),
    ('加班餐费补贴为每次 30 元', '30 元', '加班一次给多少饭补？', '餐费补贴每次多少？'),
    ('员工通讯补贴为每月 100 元', '100 元', '每个月的话费补贴是多少？', '通讯补贴每月多少？'),
    ('公务车辆保养周期为 5000 公里', '5000 公里', '公务车开多少里程要保养？', '车辆保养周期多少公里？'),
    ('消防应急演练每半年组织 1 次', '每半年组织 1 次', '消防演练多长时间搞一回？', '应急演练多久组织一次？'),
    ('员工健康体检每年组织 1 次', '每年组织 1 次', '多久安排一次体检？', '体检多久一次？'),
    ('安全帽使用满 3 年必须强制更换', '满 3 年', '安全帽戴满几年就得换掉？', '安全帽使用年限是多久？'),
    ('灭火器年检周期为 12 个月', '12 个月', '灭火器隔多久要送检一次？', '灭火器年检周期多长？'),
    ('配电房巡检频率为每 4 小时一次', '每 4 小时', '配电房隔几个小时要巡查？', '配电房巡检频率是多少？'),
    ('特种作业操作证每 3 年复审一次', '每 3 年复审', '特种作业证多长时间复审一回？', '操作证复审周期多长？'),
    ('危险废物贮存期限不得超过 90 天', '90 天', '危废最多能在库里放多少天？', '危废贮存期限是多久？'),
    ('应急物资每季度盘点 1 次', '每季度盘点 1 次', '应急物资多久清点一次？', '应急物资盘点频率是多少？'),
    ('超过 30 米的高处作业需要专项审批', '30 米', '多高的工作算高空作业要审批？', '高处作业多高要审批？'),
    ('动火作业许可证有效期为 8 小时', '8 小时', '动火证批下来能用几个钟头？', '动火作业证有效期多久？'),
    ('受限空间作业每 2 小时检测一次气体', '每 2 小时', '进受限空间多久测一次气体？', '受限空间气体检测频率是多少？'),
    ('作业场所噪声不得超过 85 分贝', '85 分贝', '工作现场噪音上限是多少分贝？', '作业场所噪声限值多少？'),
]


# 干扰项：与真实条款高度相似但数值不同的文档，充当难负例
DECOYS = [
    '女职工生育可休产假 128 天。',
    '高温作业岗位每月发放高温补贴 500 元。',
    '加班餐费补助为每次 50 元。',
    '员工健康检查每两年组织 1 次。',
]


def build_corpus(root, n_docs):
    paths = []
    for i in range(1, n_docs + 1):
        topic, _, _, _ = TOPICS[i - 1]
        p = os.path.join(root, f'doc_{i:02d}.txt')
        paths.append(p)
        with open(p, 'w', encoding='utf-8') as f:
            f.write(f'《第{i}号管理制度》\n第一章 总则\n')
            f.write(f'第一条 本制度适用于全公司安全生产管理。\n')
            f.write(f'第二条 {topic}。\n')
            f.write(f'第三条 相关记录由主管部门归档保存。\n')
    for j, decoy in enumerate(DECOYS):
        p = os.path.join(root, f'decoy_{chr(97 + j)}.txt')
        paths.append(p)
        with open(p, 'w', encoding='utf-8') as f:
            f.write(f'《第{21 + j}号参考文件》\n第一章 总则\n')
            f.write(f'第一条 本制度适用于全公司安全生产管理。\n')
            f.write(f'第二条 {decoy}\n')
            f.write(f'第三条 相关记录由主管部门归档保存。\n')
    return paths


def golden_questions(n_docs):
    qs = []
    for i in range(1, n_docs + 1):
        _, answer, sem_q, kw_q = TOPICS[i - 1]
        qs.append((sem_q, answer, i))
        qs.append((kw_q, answer, i))
    return qs


def grade(top_hits, answer):
    """答案字符串出现在任一命中块文本即判对；返回首个命中排名(1-based)或0"""
    a = answer.replace(' ', '')
    for rank, h in enumerate(top_hits, 1):
        if a in (h.get('text') or '').replace(' ', ''):
            return rank
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--docs', type=int, default=20)
    ap.add_argument('--base', default='http://127.0.0.1:11434')
    ap.add_argument('--embed', default='bge-m3')
    ap.add_argument('--topk', type=int, default=8)
    a = ap.parse_args()

    root = tempfile.mkdtemp(prefix='ai_eval_')
    db = os.path.join(root, 'eval.db')

    from indexer import index_directory
    from ai_qa import build_semantic_index, semantic_search

    build_corpus(root, a.docs)
    t0 = time.perf_counter()
    index_directory(root, db)
    t_kw = time.perf_counter() - t0

    t0 = time.perf_counter()
    info = build_semantic_index(db, a.base, a.embed)
    t_sem = time.perf_counter() - t0

    qs = golden_questions(a.docs)
    ranks = []
    scores = []
    t_search = 0.0
    for q, ans, _ in qs:
        t0 = time.perf_counter()
        hits = semantic_search(db, a.base, a.embed, q, top_k=a.topk)
        t_search += time.perf_counter() - t0
        ranks.append(grade(hits or [], ans))
        scores.append((hits[0]['score'] if hits else 0.0))

    n = len(qs)
    def recall(k):
        return sum(1 for r in ranks if 0 < r <= k) / n
    mrr = sum(1 / r for r in ranks if r > 0) / n
    print(f'[build] keyword={t_kw:.2f}s  semantic={t_sem:.2f}s '
          f'(docs={info.get("docs")} chunks={info.get("chunks")} pulled={info.get("pulled")})')
    print(f'[eval] questions={n}  topk={a.topk}')
    for k in (1, 3, 5, a.topk):
        print(f'  Recall@{k:2d} = {recall(k):.1%}')
    print(f'  MRR       = {mrr:.3f}')
    print(f'  mean top-score = {sum(scores)/n:.3f}')
    print(f'  avg search latency = {t_search/n*1000:.1f} ms/题')
    print(f'  miss: {sum(1 for r in ranks if r == 0)}/{n}')
    shutil.rmtree(root, ignore_errors=True)


if __name__ == '__main__':
    main()
