# 镜仔目录发现模块 · 交接清单

> 复核更新：下文为 dsh 初交付记录。当前修订为模块 0.2.0 / schema 2，修复与验证结论见 [REVIEW.md](REVIEW.md)，当前清单见 review-manifest.json；未接入正式前端。

状态：前期原型完成，停在交接关口。未修改正式界面、启动入口、打包脚本、
正式 EXE、快捷方式、配置、旧 index.db 与用户资料；未 git add / commit / 发布。

## 1. 交付文件

模块（`search_app/discovery/`）：

```
__init__.py   公开 API
errors.py     DiscoveryError / PermissionRequired / UnsupportedError /
              JournalChanged / JournalExpired / Cancelled
model.py      SourceInfo/SourceCapabilities/SearchRequest/SearchPage/
              FileHit/ChangeEvent/ScanReport/SyncReport/CoverageReport
filters.py    目录层排除规则、LIKE 字面转义、根目录边界
schema.py     catalog.db DDL（user_version=1）
catalog.py    Catalog：写入/查询/checkpoint/事件重放（单事务原子）
scan.py       流式 scandir 通用扫描
sync.py       FRN→路径重建、USN 记录→ChangeEvent（纯函数）
usn.py        ctypes 只读 USN 封装与记录解析（V2/V3）
probe.py      卷列表与能力探测
provider.py   DiscoveryService（最小接口实现）
watch.py      watchdog 监听适配（只更新元数据）
```

测试（`search_app/tests/test_discovery_*.py`）：filters/catalog/scan/sync/usn
共 40 个用例，全部通过。

工具（`tools/`）：

```
discovery_probe.py     卷与通道探测（不启动全盘扫描）
discovery_demo.py      样本目录建库 + 中文查询 + 更名增量
discovery_benchmark.py 合成 ≥10 万记录基准
```

文档：`docs/discovery_prework/ARCHITECTURE.md`、`VALIDATION.md`、
`HANDOFF.md`、`handoff.json`。运行输出在 `.tmp/discovery-prework/`（catalog、
demo/probe/benchmark 产物与 txt 记录），不属于交付源码。

## 2. 各通道完成程度

| 通道 | 完成度 | 说明 |
|---|---|---|
| 通用目录扫描 | 可运行 | 流式 scandir、分批写库、取消/进度/错误隔离/重解析点/硬链接/幂等重扫 |
| 字面查询 | 可运行 | 名称/路径/全部、扩展名/来源/根目录边界、排序分页、% _ \ 字面转义 |
| 事件重放 | 可运行 | created/deleted/renamed/moved/modified，更名与内容变化分开，幂等 |
| checkpoint | 可运行 | 与事件应用同事务原子提交；崩溃不推进游标 |
| USN 只读封装 | 已封装 | QUERY/ENUM/READ 三个 FSCTL，ctypes 参数与句柄释放显式声明 |
| USN 记录解析 | V2/V3 可解析 | 截断/未知版本校验；V4 跳过不消费 |
| USN 能力探测 | 真实验证 | C/D/E 无权限回退；F 可读 |
| USN READ 增量 | 真实验证 | F 读回 557 条 V3 记录，游标推进 |
| USN ENUM 全量建目录 | 缺口 | F 返回 ERROR_HANDLE_EOF(38)，待核实；未用于全量建库 |
| watchdog 监听 | 已封装 | 只更新元数据，溢出→needs_rescan；真实 FS 行为未入 CI |
| 全盘内容解析/多模态 | 未开始 | 不属本任务 |

## 3. 后续接入方式（建议）

1. 后台进程持有 `DiscoveryService`，用 `jobs.py` 向界面报告
   `scan_source/sync_source` 进度；扫描在非 GUI 线程执行。
2. 界面「探索/智答」入口调用 `search_catalog` 展示目录结果；`get_coverage`
   显示覆盖状态与通道。
3. 先接通用通道跑通，再按 `probe_source` 结果对可读卷启用 USN READ 增量；
   不可读卷保持通用扫描 + 补扫。
4. 正文/OCR/画面分析：由分析队列消费 `entries` 中 `content_version` 变化，
   目录结果与内容结果分层展示，不互相冒充。
5. ENUM_USN_DATA(ERROR_HANDLE_EOF) 与 V4 extents 在接入阶段对照真实 Windows
   行为补齐。

## 4. 未解决问题

- 通用通道并发增删的零遗漏交接（无游标，靠监听 + 补扫）。
- ENUM_USN_DATA 在 F: 的 ERROR_HANDLE_EOF 原因。
- USN V4 range-tracking 多 extent 内容失效。
- 深页 OFFSET 分页性能（可换 keyset）。
- 超长路径（>260）与真实卷离线/日志过期的端到端实验未做。
