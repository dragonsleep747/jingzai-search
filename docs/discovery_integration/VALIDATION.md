# 验证记录（探索／智答候选版）

> Codex 复核更新：下文为初交付历史结果。当前修订与新增真实验证以 [REVIEW.md](REVIEW.md) 和 review-manifest.json 为准，不沿用旧候选 EXE 哈希。

日期：2026-10-28。工作区：`.`。环境：`.venv-qt\Scripts\python.exe`
（Python 3.12.7 + PySide6 6.12.0 + PyInstaller 6.22.2）。全部产物在
`.tmp/discovery-integration/` 与 `dist/discovery-candidate/`，未触碰正式程序/资料。

## 1. 全量回归（自动测试）

命令（等价于任务书包装命令，日志已存 UTF-8 文件）：

```powershell
.venv-qt\Scripts\python.exe tools\run_tests_logged.py .tmp\discovery-integration\full-tests-final.log
```

结果：**Ran 305 tests · OK**（0 失败 / 0 跳过），见 `full-tests-final.log`。
- 其中 289 项为 Codex 复核基线（discovery 74 项），全部继续通过。
- 新增 16 项：`test_discovery_scheduler` 8、`test_discovery_native` 2、`test_discovery_ui` 6。
- 因用户批准的入口变化调整的原断言（写明原因）：
  - `test_window_uses_native_opaque_frame`：新增原生“智答”页，页数 5→6。
  - `test_advanced_ai_and_web_keep_complete_tools`：`navigate('ai')` 改为进入原生智答页。
  - `test_navigation_does_not_repeat_sidebar_pages_in_top_menu`：侧栏 3 项，无“镜仔智答”。
  - `test_search_tools_keep_relocated_actions_reachable`：新增“排序”子菜单。
  - `test_qt_frontend` 的正文回归用 `discovery_scope` 切到“原资料库”，等价旧 `start_search`。

## 2. 真实样本端到端增量（真实文件系统，非 GUI）

命令：`.venv-qt\Scripts\python.exe tools\discovery_integration_e2e.py --out .tmp\discovery-integration\e2e.json`

结果（全部真实文件操作，作用于自建样本）：
- 首扫 `complete`；中文/扩展名查询命中（安全、脚手架、JGJ）。
- 更名后旧名 0 残留、新名可查；删除后 0 残留；移入后可查。
- 根目录边界：`资料备份` 不出现在 `资料` 检索中。
- 见 `e2e.json` / `e2e.stdout.txt`。

## 3. 原生 NTFS 通道

- 当次重新探测（`tools/discovery_integration_probe.py` → `native-probe.json`）：
  C/D/E USN 不可读（句柄拒绝）；**F: USN 可读**，journal_id=134328034074976684，
  next_usn=29755936；有界枚举 1000 条、992 解析、8 未解析（截断）。
- 完整原生建库合成全量验证（`test_discovery_native`）：合成 5 记录 → `complete` +
  正确 entries + checkpoint；含孤儿 → `partial` + `unresolved`。
- 原生建库器真实有界验证（`tools/discovery_integration_e2e.py` 的 native_bounded 段）：
  F: 真实读 1000 条（55 个目录，~55ms），状态 `partial`（reason=bounded），未导出文件名。
- **完整真实 F: 卷全量建库：未运行**（本轮单独可取消验证项未执行，如实标注）。

## 4. ≥10 万合成目录记录复测

命令：`tools/discovery_benchmark.py --records 100000 --catalog .tmp/discovery-integration/benchmark.db`
（日志 `benchmark.stdout.txt`，UTF-8）

结果：写入 100000 条 / 库 66.20 MiB / 批量写入 4.64s（21,536 条/s）/
tracemalloc Python 分配峰值 11.5 MiB / 冷查询 19.94ms / 热查询 avg 19.35ms /
首页 22.85ms / offset=90000 107.06ms / 扩展名首页 20 条 / 真实扫描取消响应 39.20ms。
注意：OS 缓存未清、tracemalloc 非进程工作集、合成记录不代表真实全盘速度。

## 5. 候选 EXE 隔离验证

构建：`.venv-qt\Scripts\python.exe tools\build_candidate.py`（源码哈希见 `build-manifest.json`，
含 discovery + qt_frontend + qt_main + spec + 构建脚本哈希，可审计 EXE 来源）。

候选：`dist/discovery-candidate/镜仔搜索新版/镜仔搜索新版.exe`
- 最终 EXE SHA-256：`4E826209EBF7A2502769FF1CF4252C13D03C543040F4DD0192AD2C0D899C0479`
- 字节数：18,633,041。独立 onedir，不替换正式版。

隔离启动命令（正式程序不受影响）：

```powershell
.\dist\discovery-candidate\镜仔搜索新版\镜仔搜索新版.exe --data-dir ".\.tmp\discovery-integration\gui-data"
```

实机验证（Ghost 后台驱动，隐藏桌面，不抢用户鼠标/焦点）：
- 窗口正常显示，侧栏 3 项 + 放大镜 + “筛选”；范围面板列出 5 个范围（样本目录 +
  C/D/E/F），F: 显示“NTFS 原生”，C/D/E 显示通用+拒绝原因。
- 启动后自动继续未完成来源（`discovery.json auto_scan=true`），对 15000 文件样本
  建立目录；任务栏最小化/恢复 20 轮（见第 6 节）。
- 单实例：第二个进程 `--search 词` 转发给已运行实例后立即返回（见第 7 节）。
- 退出：Ghost 桌面关闭结束进程，无残留后台进程。

## 6. 任务栏最小化/恢复 20 轮（含正在建立目录时）

方法：候选 EXE 以 `--data-dir` 启动，`discovery.json auto_scan=true` 自动开始对
60000 文件样本建立目录；通过 Ghost `ghost_window op=state minimize/restore` 原生
ShowWindow 最小化/恢复 20 轮（隐藏桌面，不抢用户鼠标/焦点）；每轮均返回 ok。
20 轮后窗口仍存活、状态 `normal`，`ghost_see mode=text` 返回完整 UI 文本，
`ghost_screenshot` 捕获渲染帧（occlusion_proof=true）——未观察到全黑区域。

结论：**20/20 轮成功。** 证据：`.tmp/discovery-integration/minimize-restore-ghost.json`。
局限：Ghost 隔离窗口工作站不允许跨站 PrintWindow 逐像素采样，故“无全黑区域”是
窗口文本 + 整窗截图验证，非逐像素采样；方法如实记录，未冒充实测多显示器。
扫描期间状态：自动建立目录完成后“已收录 60001 条”。

## 7. 单实例

第二个进程带 `--search` 参数连接已运行实例的 QLocalServer 并返回 0，不产生第二窗口。
隔离键基于 `--data-dir`，与正式实例互不影响。

## 8. 真实 / 模拟 / 未运行分类

- **真实验证**：289+16 项自动测试（真实文件系统/真实 Qt offscreen/合成原生枚举）、
  真实样本端到端增量、F: 真实有界 USN 读取、10 万合成记录复测、候选 EXE 隔离实机
  启动/范围面板/自动继续/最小化恢复/单实例。
- **仅模拟验证**：原生枚举的合成 MFT 记录（`test_discovery_native`，不接触真实卷）；
  USN 坏记录/跨块/日志缺口等沿用上一轮 `simulation_only` 分类（未新增）。
- **未运行/未完成**：完整真实 F: 卷全量建库；真实 Ollama 模型问答；联网检索；
  真实手机配对；多模态画面/OCR/视频/音频检索（见 MULTIMODAL_NEXT.md）。
