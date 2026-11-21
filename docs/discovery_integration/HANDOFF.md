# 交接清单（探索／智答候选版 · 给 Codex 独立复核）

> Codex 复核更新：本文保留 dsh 初交接历史。当前修订、实际验证及发布信息见 [REVIEW.md](REVIEW.md)、[使用说明](USER_GUIDE_0_3.md) 和 review-manifest.json。旧 handoff.json 不代表当前源码哈希。

日期：2026-10-28。状态：**可运行候选版，已停在复核点，未替换正式程序、未改快捷方式、
未开启真实全盘内容分析、未提交/发布。**

## 1. 修改清单（本轮新增/修改）

新增模块：
- `search_app/discovery/scheduler.py` — 应用级后台调度与范围管理（A）。
- `search_app/discovery/native_scan.py` — 完整原生 NTFS 建库 + 有界内存路径重建（B）。
- `search_app/qt_frontend/discovery_ui.py` — 探索/智答 Qt 接入、目录结果/预览、范围对话框（C/D/E）。
- `search_app/tests/test_discovery_scheduler.py`、`test_discovery_native.py`、`test_discovery_ui.py`。
- `tools/build_candidate.py`、`tools/discovery_integration_probe.py`、`tools/discovery_integration_e2e.py`、
  `tools/run_tests_logged.py`、`tools/seed_gui_data.py`。
- `docs/discovery_integration/*`（本清单 + 五件交付物）。

修改模块（均在任务书允许范围）：
- `search_app/discovery/catalog.py` — `search(..., dedupe_paths=False)`（重叠去重，默认关）、
  `remove_source`（只删索引）。原表结构/schema 版本不变。
- `search_app/qt_frontend/window.py` — 侧栏 3 项去图标、原生智答页、`start_search` 分支、
  `navigate('ai')`、退出时 `shutdown_discovery()`。
- `search_app/qt_frontend/minimal_ui.py` — 探索/智答切换 + 放大镜图标按钮、检索范围/范围管理
  入折叠、“筛选”文案、排序移入结果菜单。
- `search_app/qt_frontend/tools.py` — 结果菜单增加“排序”子菜单。
- `search_app/tests/test_qt_frontend.py` — 因入口变化调整 5 处断言（原因见 VALIDATION.md §1）。

未修改（保持与 review-manifest 一致）：`search_app/qt_main.py`、`search_app/main.py`、
`search_app/discovery/{provider,scan,sync,usn,watch,probe,filters,schema,model,errors}.py`、
其余 qt_frontend 文件、正式 EXE、index.db、config*.json、library、镜仔搜索新版/ 分发目录。

## 2. 开始 / 结束 git 状态

- 开始（`git status --short` 快照存 `.tmp/discovery-integration/git-status-start.json`）：
  5 个已存在的跟踪修改（.gitignore、README.md、search_app/_test_ui3.py、
  search_app/main.py、search_app/tests/test_winchrome_cover.py——**本轮未触碰**，
  为他人已有修改，保留不动）；其余大量 ??（上一轮 discovery/qt 文件等未跟踪）。
- 结束：`git status --short` 另存 `git-status-end.json`；本轮未执行 reset/clean/全量 add，未提交。
- 本轮每个额外改动都在第 1 节说明，源码哈希见 `handoff.json` 与候选 `build-manifest.json`。

## 3. 启动方式

候选 EXE（隔离，不碰正式资料库）：

```powershell
.\dist\discovery-candidate\镜仔搜索新版\镜仔搜索新版.exe --data-dir ".\.tmp\discovery-integration\gui-data"
```

源码运行（需 .venv-qt）：

```powershell
.venv-qt\Scripts\python.exe search_app\qt_main.py --data-dir ".\.tmp\discovery-integration\gui-data"
```

`gui-data` 已含 15000 文件样本 + `discovery.json(auto_scan=true)`：启动即自动继续建立目录，
适合复核“建目录时最小化/恢复”。

## 4. Codex 重点复核点

1. `catalog.search(dedupe_paths=True)` 的窗口函数去重是否正确（重叠根取更具体来源、
   硬链接多路径不合并、分页稳定）。
2. `native_scan.NativeIndexBuilder`：游标推进、未解析父路径→partial、V4 不消费、
   size/mtime 不伪造 0、取消/失败不清旧索引、checkpoint 提交边界。
3. `scheduler.DiscoveryController`：per-source 串行锁、跨来源并发上限、取消传播、
   移除范围只删索引、`auto_scan` 重启恢复。
4. `discovery_ui`：迟到的目录/正文结果不覆盖新输入（generation 校验）、目录命中不冒充
   语义命中、打开/所在位置/复制路径指向真实文件、加入资料库走原预检。
5. 候选 EXE 与构建来源：`build-manifest.json` 是否覆盖 discovery + qt_frontend + 入口
   与构建脚本哈希；EXE 哈希与 `handoff.json` 一致。
6. 保护哈希：正式 EXE×2、index.db、config.json、config.qt.json 与 review-manifest 一致；
   library 目录未变。

## 5. 剩余问题 / 缺口（诚实列出）

- 完整真实 F: 卷全量原生建库未运行（仅 1000 条有界能力验证）。
- 真实 Ollama 模型问答、联网检索、真实手机配对未验证（外部服务依赖，本轮不下载/上传/远程）。
- 多模态画面/OCR/视频/音频检索未实现（见 MULTIMODAL_NEXT.md）。
- 原生枚举“V4 range-tracking”与“日志重建/卷拔插恢复”未端到端验证（沿用上一轮缺口）。
- `start_scan_async` 的回调在工作线程直接调用（Qt 路径未使用；非 Qt 测试可用，
  生产 Qt 路径一律走 `jobs.submit` 的队列信号）。
- 目录结果“打开原文件/所在位置”在候选 EXE 实机仅验证按钮与路径正确，未逐一点击
  真实外部程序（避免打开用户文件）。
