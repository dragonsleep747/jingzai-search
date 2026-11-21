# 发现接入实现说明（候选版）

> 当前说明：下文保留 dsh 初版设计。Codex 已修订调度、取消、持续监听、原生路径、混合检索与智答布局；当前模块 0.3.0 / schema 2，详见 [REVIEW.md](REVIEW.md) 和 review-manifest.json。

日期：2026-10-28。工作区：`.`。范围：任务书 `DSH_DISCOVERY_INTEGRATION.md` A–F。

本轮以 Codex 复核后的 discovery 0.2.0 / schema 2 / 289 项基线为起点，完成：
后台调度与范围管理、完整原生 NTFS 建库（合成全量验证 + F: 真实有界验证）、
探索接入（目录检索/预览/打开/增量）、极简界面与入口迁移、智答模式接入，
以及多模态后续设计。全部为可运行候选版，未替换正式程序。

## 1. 最终架构

```text
Qt 主窗口 (qt_frontend/window.py)
   └─ DiscoveryUiMixin (qt_frontend/discovery_ui.py)
        ├─ 探索/智答切换、检索范围（已选电脑位置/原资料库/两者）
        ├─ DiscoveryResults（目录结果） + DiscoveryPreview（文件预览）
        ├─ RangeDialog（搜索范围管理）
        └─ DiscoveryController (discovery/scheduler.py)   ← 应用级控制层
             └─ DiscoveryService (discovery/provider.py)   ← 0.2.0 服务接口
                  ├─ NativeIndexBuilder (discovery/native_scan.py)  ← 完整原生建库
                  ├─ scan.py（通用流式 scandir 通道）
                  ├─ sync.py / usn.py / watch.py / catalog.py / schema.py
                  └─ catalog.db（目录库，独立于 index.db 内容库）
```

- UI 主线程不探测卷、不扫盘、不做大查询/正文解析；所有重活经 `Jobs`（QThreadPool）
  或 `DiscoveryController` 的工作线程执行，通过队列信号回主线程。
- 每个来源扫描/同步串行（`DiscoveryController` 的 per-source `RLock`）；跨来源并发
  上限 2（`CONCURRENCY_LIMIT`）。重复点击、定时器与手动更新对同一来源互斥，不并行写入。
- 退出时 `Window.closeEvent → shutdown_discovery()` 取消运行中任务、停止 watcher、
  释放观察线程与句柄，不在 UI 线程长时间阻塞。

## 2. 后台调度与范围管理（A）

新模块 `search_app/discovery/scheduler.py`：

- `DiscoveryController(db_path, config_path)`：包装 `DiscoveryService`，持久化
  `discovery.json`（`selected` 来源列表 + `auto_sync_seconds`）。
- `ensure_volume_sources()`：幂等登记固定/可移动卷（稳定身份：卷 GUID 优先）。
- `add_directory(path)`：稳定身份 `dir:<sha256(normcase(path))[:24]>`，重复添加复用；
  可选加入已选列表。
- `select/remove_range`：勾选/取消；移除只删 `catalog.db` 里的索引与登记，
  绝不删除原文件。
- `list_ranges()`：来源 + 通道能力 + 状态 + 已发现数 + 失败数 + 原因 + 是否运行。
- `start_scan` / `sync`：按探测能力选择通道——卷且 USN 可读走原生完整建库，
  否则通用 scandir；`sync` 对通用通道按补扫处理。取消/失败/离线保留旧索引。
- `resume_watchers()`：启动后对已选通用通道来源恢复持久 `DiscoveryWatcher`。
- 覆盖信息（`get_coverage`）：状态、provider、online、discovered_count、
  failed_count、needs_rescan、rescan_reason、last_scan_at；未实现正文/图像进度，
  不写“完成”。

## 3. 完整原生 NTFS 初始建库（B）

新模块 `search_app/discovery/native_scan.py`：

- `NativeIndexBuilder.build(progress, cancel, limit_records)`：
  1. 只读打开卷句柄，`FSCTL_QUERY_USN_JOURNAL` 记录 Journal ID 与 next_usn。
  2. `FSCTL_ENUM_USN_DATA` 全量枚举；记录（file_ref/parent_ref/name/属性）落盘到
     临时 SQLite 暂存表（`_SpillDB`），内存与整盘记录数无关。
  3. 路径重建按批 SQL 推进（根：file_ref==parent_ref 或低 48 位 FRN=5）。
  4. 分批写入 catalog entries；`size/mtime` 为 **NULL（未知值）**，不伪造 0；
     重解析点记为 `link`；`file_id` 保留 V2 64 位 / V3 128 位（TEXT `f:` 十六进制）。
  5. 达到 `limit_records`/时间限制、枚举缺口、V4 能力、未解析父路径 → 标记
     `partial` + `needs_rescan`，绝不清旧记录、不冒充完整快照。
  6. 完整时：`prune_stale` 提交新快照 → `save_checkpoint(journal_id, next_usn)` →
     回放扫描期间变更（复用 `sync_source`）。
- 回退：卷句柄不可用/权限不足 → provider 标记 `generic` + 原因，UI 显示通用通道。

验证（见 VALIDATION.md）：
- 合成全量枚举：完整树 5 记录 → `complete` + 正确 entries + checkpoint；含孤儿 →
  `partial` + `unresolved` 原因。为 `tests/test_discovery_native.py`。
- F: 真实有界能力验证：`limit_records=1000` 真实读到 1000 条、55 个目录、约 55ms，
  状态 `partial`（原因 `bounded at 1000 records`），未导出整盘文件名。
- 完整真实 F: 卷建库**未运行**（单独可取消验证项，本轮未跑，诚实标注）。

## 4. 探索接入（C）

- 探索提供折叠内检索范围：已选电脑位置 / 原资料库 / 两者（`discovery_scope`）。
- 目录检索走 `Catalog.search(..., dedupe_paths=True)`：名称/路径字面匹配
  （`% _ \` 全转义）、扩展名、来源/根目录、大小写、排序、分页、可取消。
- 重叠根目录去重：SQL 窗口函数按 `match_path` 分区，优先根更具体的来源、再优先
  在线来源；**硬链接不同路径不合并**（去重键是路径，不是 object_id）。
- 目录结果与正文命中去重：同一路径正文命中优先，保留命中依据。
- 目录结果双击/打开原文件/打开所在位置/复制路径全部指向真实文件；目录索引路径
  不会被拼进 library。“加入资料库”复用原 `confirm_import` 预检/复制/冲突处理。
- 图片用现有 Qt 能力原图缩放预览；未知格式显示文件信息、原位置与打开动作；
  离线/已更名/已删除有明确提示。
- 空结果显示实际范围状态（“扫描进行中 / 尚未选择范围 / 已收录 N 条”），
  不把“目录尚未扫描/内容未分析”写成“电脑上没有这个文件”。

## 5. 极简界面与入口迁移（D）

- 侧栏只留“搜索、资料库、收藏”三个文字导航（去图标），底部“菜单”；去掉独立
  “镜仔智答”侧栏项。
- 第二列输入行左端“探索/智答”紧凑切换；探索执行按钮为 QPainter 绘制的真正放大镜
  图标（非字体符号），带 tooltip/无障碍名/回车与点击；不再占一整行的“搜索”大按钮。
- “选项”改“筛选”，默认折叠，显示实际生效条件数；检索范围、范围管理入口在折叠内。
- 常驻“相关程度”下拉移入“结果”菜单的“排序”子菜单；目录结果排序与其含义一致，
  正文结果保留原排序。
- 搜索历史/常用搜索/恢复阅读仍在输入框右侧历史图标。
- 正文顶部细工具栏与“阅读”菜单、PDF 操作保持不变（未缩减）。
- 底部菜单按“任务、工具、设置、帮助”组织，退出单独分隔（原逻辑保留）。
- 完整入口迁移表见 FUNCTION_MAP.md。

## 6. 智答接入（E）

- 模式切换到“智答”→ 原生智答页（复用 `ToolsMixin._ai_page`）：真实问题输入、
  “使用当前搜索结果作为依据”、流式回答、停止、失败提示、来源列表（点击回到右侧
  预览），以及“构建语义索引”。
- 问答走 `ai_qa.semantic_search` + `ai_qa.chat_stream`（本机 Ollama），
  `should_stop` 取消旧回答，迟到 token 不写新会话（generation 校验在 UI 层）。
- 无本机服务/模型时如实提示并保留“完整智答工作台”兼容入口
  （`legacy_tool('qa')`：模型管理、索引管理、多轮历史、停止/重答、导出、来源）。
- 未做内容分析的目录命中不作为可靠资料依据（仅名称/路径命中；不宣称语义命中）。

## 7. 多模态准备（F）

本轮只做盘点与设计，**不实现**画面/OCR/视频检索；设计见 `MULTIMODAL_NEXT.md`。

## 8. 版本与兼容

- `discovery.__version__` 保持 `0.2.0`；`schema.SCHEMA_VERSION` 保持 `2`（未升级，
  无迁移需求；新增列/表未引入）。新模块为增量：`scheduler.py`、`native_scan.py`。
- `Catalog.search` 新增 `dedupe_paths=False` 关键字参数（默认关闭，原调用不变）；
  `Catalog.remove_source` 新增（原表结构不变）。
- 不迁移 `index.db`；目录库仍是 `catalog.db`（隔离 data-dir 下）。
- 未修改调用约定让旧测试绕开真实实现：289 项基线全部继续通过。
