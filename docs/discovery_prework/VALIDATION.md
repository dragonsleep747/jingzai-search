# 镜仔目录发现模块 · 验证报告

> 复核更新：下文保留初交付数据。当前修订、289 项测试及重新实测结果见 [REVIEW.md](REVIEW.md) 与 review-manifest.json，不以旧哈希作为当前版本校验依据。

运行环境：Windows 11 (10.0.26200)，Python 3.12.7，SQLite 3.45.3（FTS5 已编译，
本目录库不使用 FTS）。工作目录 `.`。

## 1. 测试命令与结果

```powershell
cd .
python search_app\tests\run_tests.py test_discovery_filters   # 9  通过
python search_app\tests\run_tests.py test_discovery_catalog   # 13 通过
python search_app\tests\run_tests.py test_discovery_scan      # 5  通过
python search_app\tests\run_tests.py test_discovery_sync      # 8  通过
python search_app\tests\run_tests.py test_discovery_usn       # 5  通过
```

合计 40 个 discovery 测试全部通过（stdlib unittest，无 pytest）。

现有回归（未覆盖 Qt 界面的旧模块）：

```powershell
python search_app\tests\run_tests.py test_core
python search_app\tests\run_tests.py test_searcher
python search_app\tests\run_tests.py test_indexer
python search_app\tests\run_tests.py test_parsers
python search_app\tests\run_tests.py test_export
python search_app\tests\run_tests.py test_service
python search_app\tests\run_tests.py test_import_surface
python search_app\tests\run_tests.py test_ipc
python search_app\tests\run_tests.py test_winchrome_cover
```

以上 9 个旧回归全部通过。

**未运行**：`test_qt_backend / test_qt_display / test_qt_experience /
test_qt_frontend` —— 开发解释器未安装 PySide6（打包环境才有），与本次前期
任务无关，未运行。

## 2. 验证覆盖对照（任务书 P3）

| 场景 | 方式 | 结果 |
|---|---|---|
| 中文/空格/特殊字符名称 | `test_discovery_scan.test_chinese_space_special`（安全生产法.txt、带 空格.pdf、带_下划线.docx、100%.xls） | 通过 |
| 目录范围边界（D:\资料 ≠ D:\资料备份） | `test_discovery_catalog.test_root_boundary` + demo | 通过 |
| 分页不遗漏、不重复 | `test_discovery_catalog.test_pagination_no_dup` | 通过 |
| 扫描中取消 | `test_discovery_scan.test_cancel` + benchmark 实测 106.72 ms 停止 | 通过 |
| 文件更名 | `test_discovery_catalog.test_rename_file_keeps_content_version` | 通过 |
| 目录更名/子树移动 | `test_discovery_catalog.test_directory_rename_moves_subtree` | 通过 |
| 内容修改（与更名区分） | `test_discovery_catalog.test_modified_bumps_content_version` | 通过 |
| 重复事件 | `test_discovery_catalog.test_event_replay_idempotent` | 通过 |
| 崩溃/重启恢复 + 游标不推进 | `apply_usn_batch` 单事务设计；`test_event_replay_idempotent` | 设计保证 + 单测通过 |
| 无权限目录 | 单目录 scandir 失败记 failures 不中断（scan 错误隔离） | 通过（代码路径） |
| 日志过期/Journal ID 变化 | `sync_source` 比对逻辑；`test_discovery_sync` 合成验证 | 设计实现，真实触发未模拟 |
| 通知溢出 | `watch.mark_overflow` → `needs_rescan` | 实现（合成） |
| 索引自身 db/WAL/临时输出不写监听循环 | 监听只更新 catalog.db 元数据，不写旧 index.db；目录层忽略系统卷 | 设计保证 |
| 链接/重解析点循环 | `test_discovery_scan.test_junction_loop_no_hang`（真实 junction） | 通过 |
| 硬链接多路径 | `test_discovery_scan.test_hardlink_two_paths` + catalog 单测 | 通过 |

## 3. USN 原生通道实测（真实只读实验，非模拟）

`python tools\discovery_probe.py` 输出：

- 4 个 NTFS 卷：C/D/E/F。
- C、D、E：`cannot open volume handle (access denied?)` → `provider=generic`，
  `native_usn_readable=False`（普通用户无卷句柄权限，诚实回退）。
- F：`provider=usn`，`native_usn_readable=True`，
  `journal_id=134328034074976684`，`next_usn=29740480`。

有界 `FSCTL_READ_USN_JOURNAL` 实验（`start_usn=first_usn`，`timeout=0`，
64 KiB 缓冲）：

- 读回 65488 字节，**557 条 V3 记录**全部解析成功（0 截断 / 0 未知版本），
  样例文件名 `WIN_20260716_15_09_43_Pro.mp4`，reason 含 FILE_CREATE(0x100)、
  DATA_EXTEND/OVERWRITE 等，游标正确推进。

有界 `FSCTL_ENUM_USN_DATA` 实验：

- F: 返回 `ERROR_HANDLE_EOF(38)`。**缺口**：该卷 MFT 枚举从
  StartFileReferenceNumber=0 即报 EOF，需接入阶段进一步核实（不影响 READ
  增量通道）。C/D/E 无权限未测。

## 4. 性能基准（合成记录，独立数据库）

`python tools\discovery_benchmark.py --records 100000`（结果已存
`.tmp\bench-output.txt`）：

| 指标 | 数值 |
|---|---|
| 硬件平台 | Windows-11-10.0.26200-SP0 |
| Python / SQLite | 3.12.7 / 3.45.3 |
| 写入条目 | 100,000 |
| 数据库大小 | 59.73 MiB |
| 批量写入（5000 条/批） | 7.53 s，13,276 条/秒 |
| Python 内存峰值（tracemalloc） | 31.6 MiB |
| 名称查询 冷（首次连接，命中） | 15.86 ms |
| 名称查询 冷（未命中） | 14.25 ms |
| 名称查询 热（5 次） | min 15.34 ms / avg 15.74 ms |
| 分页 首页 limit=20 | 18.04 ms |
| 分页 深页 offset=90000 | 93.63 ms |
| 扩展名筛选 txt 首页 | 20 条 |
| 真实扫描取消响应 | 106.72 ms（stopped=True） |

说明：以上为**合成记录**结果，不代表真实全盘扫描速度；不预设「百万文件秒搜」。
深页 offset 越大越慢属预期（无 keyset 分页），已列为优化项。

## 5. demo 结果（真实样本目录）

`python tools\discovery_demo.py`（结果已存 `.tmp\demo-output.txt`）：

- 对 `.tmp\discovery-prework\sample` 建 catalog.db，中文查询「安全」「脚手架」
  「JGJ」各命中 1 条；根目录边界正确排除「资料备份」；更名后新名命中、旧名
  0 残留。

## 6. 限制与已知问题

1. 通用通道无可靠游标：目录通知与枚举交接只能靠监听 + 补扫；无法保证扫描
   期间并发增删零遗漏（USN 通道才有真实游标）。
2. 深页 OFFSET 分页随 offset 增长变慢；后续可换 keyset 分页。
3. USN V4 range-tracking 记录仅跳过不消费 extents；多 extent 大文件的精确
   内容失效需要后续接入阶段实现。
4. ENUM_USN_DATA 在 F: 返回 ERROR_HANDLE_EOF，待接入阶段核实。
5. 未做超长路径（>260）专项复测；未做真实卷离线/日志过期端到端实验（只有
   代码路径与合成验证）。
