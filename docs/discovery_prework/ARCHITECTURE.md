# 镜仔目录发现模块 · 架构说明（前期任务）

> 复核更新：当前模块 0.2.0 / schema 2，详见 [REVIEW.md](REVIEW.md)。目录库增加去重事件键与 128 位身份编码。导入仍无 IO 副作用，显式构造 Catalog／DiscoveryService 会初始化或迁移指定的目录库；下文保留初版设计。

状态：前期原型，未接入正式界面。本文描述 `search_app/discovery/` 的结构、
接口、数据库、错误与恢复策略。正文/画面理解不在此任务范围。

## 1. 目标与边界

- 不依赖 Everything 本体/SDK/ES、不依赖 Windows Search 索引。
- 内置模块自行发现磁盘与目录；文件保留原位置，不复制入库。
- 本次只做「文件目录 + 增量同步」。正文/OCR/画面理解由后续分析队列消费，
  目录可搜 ≠ 内容可搜。
- 导入 `discovery` 包不启动扫描、监听或 GUI，不写任何数据库；首个写入动作
  只发生在显式调用 `DiscoveryService` 方法时。

## 2. 与现有工程的复用边界（P0 盘点结论）

| 现有组件 | 结论 |
|---|---|
| `qt_frontend/backend.py: Store.scan` | 先 `os.walk` 收集**整棵树的路径列表**再交 `index_files` 做正文索引；不能把根目录换成 C 盘就当全盘目录。 |
| `watcher.py: FileWatcher` | 单根监听后直接 `index_single_file`（写正文库 + 解析器）。目录发现需要独立事件层，不能让每次变更都触发 OCR/模型。 |
| `indexer.py`（`index_directory`/`refresh_index`） | 正文 + FTS + bigram + 章节/分类；全文件 sha256、解析器、OCR 都不适合纯目录元数据。其 `IGNORE_DIRS` 是**内容层**排除策略。 |
| `parsers.py` / `searcher.py` | 内容解析与全文检索保留；后续用适配层消费分析任务，本次不调用。 |
| `qt_frontend/jobs.py` | 继续用于界面报告任务状态；全盘目录与模型分析不在 GUI 线程执行。 |
| 旧 `index.db` / 收藏 / 分类 / 配置 | 不动。目录库独立为 `catalog.db`。 |

## 3. 两条发现通道

### 3.1 通用目录通道（已实现，可用）
- 显式栈 DFS + `os.scandir` 流式枚举，**不先收集整盘路径列表**；分批写 SQLite
  （默认 1000 条/事务）。
- 元数据发现不打开文件内容、不做全文件哈希、不调解析器/OCR。
- 取消、进度（每 100 条上报一次）、错误隔离（单目录/单条目失败记 failures 表，
  不中断整体）、普通用户权限、幂等重扫（中断后重跑补全，不推进「游标」）。
- 重解析点（符号链接 / 目录联接点）按 `link` 记录、**不递归**；目录循环用
  `(st_dev, st_ino)` 已访问集合兜底。
- 同一对象的多条硬链接路径：`objects` 记录身份 `(source_id, volume_serial,
  file_id)`，`entries` 每条路径各占一行，不会因 file_id 相同被吞掉。

### 3.2 原生 NTFS USN 通道（只读封装 + 有界实验）
- `usn.py`：ctypes 封装 `FSCTL_QUERY_USN_JOURNAL` / `FSCTL_ENUM_USN_DATA` /
  `FSCTL_READ_USN_JOURNAL`。显式声明 `CreateFileW` / `DeviceIoControl` /
  `GetLastError` 的参数与返回类型；句柄由 `close_handle` 释放。
- 只读：不创建/删除日志、不安装服务/驱动、不自动提权、不改 USN 日志。
- 权限不足 → 返回 `unsupported` / `permission_required`，回退通用通道，
  绝不假报 `native_ready`。
- 记录解析：V2（8 字节 FRN）与 V3（16 字节 FRN）按 MS 文档解析；校验
  RecordLength 边界与 UTF-16 字节对齐；未知 major version（如 V4
  range-tracking 记录）跳过并计入 `unsupported`，不硬解析。
- 设备输出缓冲区前 8 字节是「下一个 USN」，记录从 offset 8 开始（见
  MS「Walking a Buffer of Change Journal Records」），解析器用 `start_offset=8`。
- CTL_CODE 推导：`(FILE_DEVICE_FILE_SYSTEM=9)<<16 | (FILE_ANY_ACCESS=0)<<14 |
  (fn)<<2 | method`：
  `QUERY_USN_JOURNAL = fn61, BUFFERED → 0x000900F4`；
  `ENUM_USN_DATA = fn44, NEITHER → 0x000900B3`；
  `READ_USN_JOURNAL = fn46, NEITHER → 0x000900BB`。

## 4. 模块结构

```
search_app/discovery/
  __init__.py   公开 API，无副作用
  errors.py     DiscoveryError / PermissionRequired / UnsupportedError /
                JournalChanged / JournalExpired / Cancelled
  model.py      数据契约 dataclass（与 NATIVE_DISCOVERY_PLAN 最小接口对齐）
  filters.py    目录层/内容层排除规则、LIKE 字面转义、根目录边界
  schema.py     catalog.db DDL 与 user_version 迁移
  catalog.py    Catalog：写入/查询/checkpoint/事件重放（单事务原子）
  scan.py       流式 scandir 通用扫描
  sync.py       FRN→路径重建、USN 记录→ChangeEvent（纯函数）
  usn.py        ctypes 只读 USN 封装与记录解析
  probe.py      卷列表与能力探测
  provider.py   DiscoveryService：list/probe/scan/sync/search/coverage
  watch.py      watchdog 监听适配（只更新元数据，不触正文写库流程）
```

## 5. catalog.db 结构

`PRAGMA user_version = 1`，独立于旧 `index.db`；WAL 模式。

- `sources`：来源（卷/目录）与状态。`state ∈ new|scanning|partial|complete|
  needs_rescan|offline`；`needs_rescan`/`rescan_reason` 记录补扫原因。
- `objects`：对象身份 `(source_id, volume_serial, file_id)`。
- `entries`：每条路径一行。显示路径 `path/name` 与匹配键 `match_path/
  match_name`（Windows 大小写不敏感）分开；`ext/kind/size/mtime_ns/status/
  scan_generation/content_version`。
- `events`：可重放变更事件，`(source_id, seq)` 幂等，`applied` 标记完成。
- `checkpoints`：`(source_id, provider, cursor, journal_id, generation,
  committed_at)`——批次与游标同事务原子提交。
- `failures`：拒绝访问/失败项追踪。
- `settings`：来源递增序号、扫描代次等。

索引：`entries(source_id, path)`、`(source_id, match_path)`、
`(source_id, match_name)`、`(source_id, parent_path)`、`(source_id, ext)`；
`events(source_id, seq)`；`failures(ts)`。不使用 FTS。

## 6. 接口（对齐 NATIVE_DISCOVERY_PLAN 最小契约）

```python
svc = DiscoveryService(db_path)
svc.list_sources() -> list[SourceInfo]
svc.probe_source(source_id) -> SourceCapabilities
svc.scan_source(source_id, progress, cancel) -> ScanReport
svc.sync_source(source_id, cursor, progress, cancel) -> SyncReport
svc.search_catalog(SearchRequest) -> SearchPage
svc.get_coverage() -> CoverageReport
```

`SearchRequest` 默认**字面**检索；`% _ \` 转义为字面；`scope ∈ name|path|all`；
`ext` 过滤；`root` 根目录边界（`D:\资料` 不会误匹配 `D:\资料备份`）；排序
`name|path|size|mtime`；分页 `limit/offset`，候选按 SQL 按需读取，不整库载入。
`SearchPage.count` 未完成时 `total_hint=None`，不阻塞返回。

## 7. 错误与恢复策略

1. **扫描中断**：`scan_source` 用 `scan_generation` 标记本代；只在**完整扫描**
   结束时 `prune_stale`（只删 `status='present'` 且不属于本代的旧条目）。
   取消/异常 → `state=partial`、`needs_rescan=1`，**不清理**，避免卷离线/权限
   不足被误判为批量删除。
2. **数据库事务失败不推进游标**：`apply_usn_batch` 把「事件落表 + 应用 +
   checkpoint 推进」放在**同一事务**；崩溃回滚则游标不前进，重启后按旧游标
   幂等重放。
3. **Journal ID 变化 / 游标过期**：`sync_source` 先比对 `journal_id`、再比对
   `start >= lowest_valid_usn`，任一不符 → `needs_rescan`，不盲目重放。
4. **通知溢出 / 监听失效**：`watch.py` 只更新元数据；溢出或不确定时置
   `needs_rescan`，由调用方安排 `scan_source` 补扫。
5. **更名与内容变化分开**：`renamed/moved` 只改路径列；`modified` 更新
   size/mtime 并 `content_version+1`；更名不升内容版本（不触发重算画面向量）。

## 8. 排除规则（目录层 ≠ 内容层）

- **目录层**（`filters.DIR_LAYER_*`）：只排除会循环/系统内部/写库循环的对象——
  `$Recycle.Bin`、`System Volume Information`、`$Windows.~BT/WS`、`WinREAgent`、
  `Config.Msi`、`Dumps`；固定文件 `pagefile.sys/hiberfil.sys/swapfile.sys`；
  卷根一级的 `Windows`/`Windows.old` 等。**不复用 `indexer.IGNORE_DIRS`**，
  因此 `dist/build/venv` 等目录里的文件名仍会被目录层发现（用户可能要找）。
- **内容层**（后续分析队列）：沿用 `indexer.IGNORE_DIRS`，排除 dev 构建缓存、
  虚拟环境等，避免解析器浪费在这些目录。
- 覆盖缺口：目录名为 `Windows` 的用户目录在**卷根一级**会被跳过（kind=volume
  且 depth=0 才生效）；显式添加 `D:\Windows` 作为目录来源时不受影响。极端
  深路径（>260）依赖 Python 3.6+ 的 `os.scandir/stat` 自动 `\\?\` 处理，未单独
  复测超长前缀场景，列为待补测项。

## 9. 已实现 / 仅模拟 / 未完成（诚实标注）

- **已实现 + 真实验证**：通用目录扫描、字面查询、根目录边界、硬链接多路径、
  重解析点不递归（junction 实测）、事件重放幂等、checkpoint 原子、取消响应、
  F: 卷 USN `QUERY` + `READ` 真实读回 557 条 V3 记录并正确解析。
- **仅模拟验证**：USN 记录 V2/V3 解析的边界用例（截断/未知版本）用合成字节；
  FRN→路径重建与事件转换用合成记录；watchdog 事件注入为合成调用（真实 FS
  监听行为依赖运行环境，未纳入 CI 断言）。
- **未完成 / 缺口**：F: 卷 `FSCTL_ENUM_USN_DATA` 返回 `ERROR_HANDLE_EOF(38)`
  （需接入阶段进一步核实，暂不影响 READ 增量）；USN V4 range-tracking 记录
  解析（跳过但不消费 extents）；全盘内容解析与多模态检索。
