# 镜仔搜索 · 架构说明

> 版本：v1.1.0 · 本文档描述模块职责、数据流与关键设计取舍。

## 总览

```
                 ┌──────────────────────────────────────────────┐
 文件系统 ──────▶ │  indexer.py     目录扫描 / 增量更新 / 错误表    │
 (Office/PDF/代码) │     │  调用 parsers.py + detector.py          │
                 └──────┼───────────────────────────────────────┘
                        ▼  (documents / lines / lines_fts / bigram / chapters)
                 ┌──────────────────────────────────────────────┐
                 │          index.db（SQLite, FTS5 trigram）     │
                 └──────┬───────────────────────────────────────┘
        ┌───────────────┼────────────────────────────┐
        ▼               ▼                            ▼
  searcher.py     mobilesvc.py                 ai_qa.py
  桌面检索引擎    局域网手机网页(HTTP)          Ollama 语义索引
        │               │                            │
        ▼               ▼                            ▼
  main.py(桌面UI)   手机浏览器               qa_win.py(AI问答窗)
  quickwin.py(热键)  (扫码配对登录)
  tray.py(托盘)
```

## 数据存储

`index.db`（SQLite）核心表：

| 表 | 作用 |
|----|------|
| `documents` | 文件元数据：path/name/ext/ftype/status/doc_no/category/effective_date |
| `lines` | 全文按行：`(doc_id, line_no, line_text)`，主查询载体 |
| `lines_fts` | FTS5 内容表（trigram 分词），支撑中文任意子串匹配 |
| `bigram` | 2 字 token 倒排（`(tok, doc_id, line_no)`），短词查询加速 |
| `chapters` | 章节定位（标题/起止行/层级），支撑「目录跳转」与章节链 |
| `errors` | 解析/搜索失败记录（含完整堆栈），不再静默丢错 |
| `settings` | 库级开关（如 `bigram_ready`） |

## 模块职责

| 模块 | 职责 | 关键函数 |
|------|------|----------|
| `detector.py` | magic bytes 判型、扩展名回落、编码自识别 | `detect_type` / `detect_encoding` |
| `parsers.py` | 各格式 → `(line_no, line_text)` 生成器 | `parse_text/docx/xlsx/pptx/pdf/archive` |
| `indexer.py` | 扫描建库、增量更新、bigram 重建、错误表、文库管理（归类/去重/打包恢复） | `index_directory` / `refresh_index` / `parse_file` |
| `searcher.py` | 查询解析、FTS/LIKE/bigram 三层执行、取消钩子、聚类聚合、章节/上下文/引用 | `_build_fts_query` / `parse_advanced_query` / `_search_fuzzy_impl` / `aggregate_results` |
| `main.py` | 桌面端 UI 骨架 + 业务编排（继承 ui_dialogs） | `class App`（122 方法） |
| `ui_dialogs.py` | 对话框/结果树/封面/导入线程等 212 个 GUI 方法（混入 `App`） | `DialogsMixin` |
| `runtime.py` | 运行时定位：数据根/DB 路径/备份目录，并同步 indexer/parsers 目录常量 | `DATA_ROOT` / `DB_PATH` / `LIBRARY_DIR` / `_DB_RELOCATED` |
| `thirdparty.py` | 可选第三方依赖统一加载降级（热键/动效/镀铬/托盘/问答/拖拽/主题） | `_HOTKEY_OK` / `_QA_OK` / `_DND_AVAILABLE` / `_SVTTK` |
| `core.py` | 应用路径、配置读写、数据根定位、口令生成、关键词归一、引用串 | `load_config` / `save_config` / `_gen_token` / `kw_terms` / `build_citation` |
| `service.py` | 无界面工具：格式/日期/生效日、拖拽路径、命中行标签、片段/页码、圆角几何、PDF 阅读器定位、索引快照 | `fmt_size` / `valid_date` / `snippet` / `bar_pts` / `snapshot_db` |
| `export.py` | Word 制度摘编汇编、搜索命中转 AI 问答上下文 | `write_digest_docx` / `build_search_hits` |
| `quickwin.py` | 全局热键快速搜索浮窗 | — |
| `tray.py` | 系统托盘 | — |
| `mobilesvc.py` | 局域网手机网页：登录/搜索/文档/上传/AI 问答 | `start_server` / `_Handler` |
| `ai_qa.py` | Ollama 连接、embedding、语义索引、冲突检测 | `build_semantic_index` / `semantic_search` / `detect_conflicts` |
| `qa_win.py` | AI 问答窗口 | — |
| `websearch.py` | 联网检索（制度/法规/标准下载） | — |
| `winchrome.py` | 内嵌 Chromium/Edge 浏览封装 | — |
| `ipc.py` | 单实例互斥、`jingzai://` 协议、命令行传参 | `read_command` / `register_protocol` |
| `anim.py` | tkinter 动画/缓动/骨架屏 | `Tweener` / `SkeletonShimmer` |
| `graph_html.py` | 文档引用关系图渲染 | `aggregate_graph` / `generate_graph` |
| `synonyms.py` | 同义词扩展 | `expand_map` |

## 检索执行链路（一次关键词查询）

1. `parse_advanced_query` 拆出「自由词表达式 + 元数据过滤」
2. `_build_fts_query` 生成 FTS5 MATCH（写法归一 + 同义词 OR 组）
3. 有 `<3` 字 token → 走 bigram / LIKE 兜底；否则 FTS5 短语
4. 模糊层：`_fuzzy_patterns` 生成通配模式，LIKE 全表扫（score 0.8）
5. `aggregate_results` 按文件聚合、按「精确命中权重 + 密度」排序、聚片段簇
6. UI 展示时按需 `fetch_context_lines` / `get_paragraph` / `get_chapter_chain_str`

## 关键设计取舍

- **取消优先**：长 SQL 通过 SQLite progress handler 挂取消钩子（`_install_cancel`），旧搜索可被新搜索毫秒级中断，避免 IO 争抢
- **只读连接线程级复用**：搜索/上下文/章节等高频小查询共享线程内连接（`_conn`），autocommit 无事务
- **写法归一在查询侧做**：不重建索引，为含字母/括号的 token 生成变体组内 OR，解决 `GB50016` / `ＧＢ５００１６` / `建标[2003]206号` 等漏检
- **模糊容错分层计分**：精确命中 2 分权重，一字之差 1 分，密度加成，保证「对的最前」
- **错误不静默**：索引/搜索失败写入 `errors` 表（`record_error`），可事后审计

## 已知债务（演进路线）

- `main.py` 剩余 9115 行（App 类 122 方法 + 混入 212 方法）；已抽出 `core`/`service`/`export`/`ui_dialogs`/`runtime`/`thirdparty` 六层（见上方模块表）。下一步可把 App 内引用 `Tooltip`/`ReadonlyText`/`RoundButton`/`_grab_screen_region` 等模块级 GUI 构件的方法继续外迁（先把这些构件提为 `ui_widgets`），以及把检索编排/文库导入等业务方法从 App 抽出为无界面 `controller`
- 全项目 608 处宽泛 `except Exception`（GUI 容错为主，338 处 `pass`），已用 `tools/exception_audit.py` 建账，逐步收敛
- 移动端服务为局域网明文 HTTP（见 `docs/SECURITY.md` 残留风险）
