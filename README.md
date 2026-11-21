# 镜仔搜索 / Jingzai Search

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

**English summary.** Jingzai Search is a Windows desktop full-text search engine for local
document libraries. It indexes the *body* of Office / PDF / text / code / image (OCR) files
into a local SQLite FTS5 index, then shows hit positions, surrounding context and chapter
chains. All indexing and search happens offline on the user's own machine. This repository
contains the **complete source code** (Python + Qt Widgets frontend), the test suite, the
architecture/security documentation and the build scripts, released under the
[MIT License](LICENSE). The pre-built Windows ZIP on the [Releases page](../../releases)
is distributed separately under the commercial EULA bundled with that package; the same
copyright holder licenses the source code here under MIT. No user documents, index
databases, credentials, binaries or personal records are included in this repository.

Windows 本地文件内容全文检索引擎：把散落在硬盘里的 Office / PDF / 代码 / 图片（OCR）
文档建成 SQLite 全文索引，离线检索正文，并显示命中位置、上下文与章节链。

## 功能

- **全文检索**：SQLite FTS5 trigram 中文子串 + bigram 短词倒排 + 写法归一化（标准号 `GB50016` ↔ `GB 50016`、文号括号互通、全半角互通）
- **查询语法**：空格 = AND，`a|b` / `a OR b` = OR，`-词` = NOT；字段 `文号:` `名称:` `状态:` `日期:` `分类:`
- **模糊容错**：容忍任意位置 1 个字的错打 / 多打 / 少打，精确命中权重更高
- **上下文定位**：命中行前后文、整段预览、章节链（`第三章 > 第三条 > 2.`）
- **格式解析**：txt / md / csv / json、docx / xlsx / pptx、pdf（含扫描版 OCR 兜底）、doc / xls / ppt、zip / 7z / rar 逐成员带前缀
- **桌面端**：Qt Widgets 主界面（`search_app/qt_main.py`）与旧版 Tkinter 完整界面（`search_app/main.py`）
- **可选 AI 问答**：本机 Ollama 语义索引与文档问答，不安装也能使用全部检索功能
- **资料发现**：`search_app/discovery/` 提供目录扫描、原生 USN / 文件监听、增量同步与调度

## 仓库状态

| 项目 | 说明 |
|---|---|
| 源码 | 本仓库，自 2026-11 起公开，MIT |
| Windows 体验版 | [Release `windows-2026-11-03`](../../releases/tag/windows-2026-11-03)，`JingzaiSearch-Windows-2026-11-03.zip`（202,110,839 字节） |
| SHA-256 | `8c407340979052123f66b4fc9e0ae595c207adca4670fc319af2157f4569855d` |
| 平台 | Windows 10/11 x64；Python 3.10+（开发环境 3.12） |
| 维护者 | SleepDragon（GitHub [`dragonsleep747`](https://github.com/dragonsleep747)），独立维护者 |

Release 页面及其资产、标签均未因本次源码公开而改变。体验版以空资料库启动，
随附的《用户许可协议》只约束该二进制包；本仓库源码另行按 MIT 授权。

## 快速开始（源码运行）

```powershell
git clone https://github.com/dragonsleep747/jingzai-search.git
cd jingzai-search
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r search_app\requirements.txt
.\.venv\Scripts\python.exe -m pip install PySide6
.\.venv\Scripts\python.exe search_app\qt_main.py       # Qt 桌面端
# 或：.\.venv\Scripts\python.exe search_app\main.py      # 旧版 Tkinter 完整界面
```

首次启动选择要索引的目录，等待建库完成即可搜索。`index.db`、`config.json`、
`catalog.db` 等运行时文件会在本机生成，已被 `.gitignore` 排除，**不会**进入版本库。

可选外部程序：Tesseract-OCR（中文识别）、poppler（PDF 转图）、SumatraPDF（PDF 页码定位）、
Inno Setup 6（生成安装包）。`search_app/requirements.txt` 列出全部 pip 依赖。

## 测试与打包

```powershell
python search_app\tests\run_tests.py                 # stdlib unittest 套件
python tools\exception_audit.py --out tools\audit.txt
.\tools\build_qt.ps1                                 # 需 .venv-qt + PyInstaller + PySide6
```

打包脚本（`search_app/*.spec`）在存在 `SumatraPDF.exe` 时才会把它打进产物；
该可执行文件为 GPLv3 第三方组件，**不随本源码仓库分发**，请自行从上游获取。
更多构建说明见 [docs/QT_DESKTOP.md](docs/QT_DESKTOP.md)。

## 目录结构

| 路径 | 说明 |
|---|---|
| `search_app/qt_main.py`、`search_app/qt_frontend/` | Qt Widgets 桌面端 |
| `search_app/main.py`、`search_app/ui_dialogs.py` | 旧版 Tkinter 完整界面 |
| `search_app/indexer.py` | 目录扫描、SQLite FTS5 索引、增量更新、错误表 |
| `search_app/searcher.py` | 布尔 / 高级 / 模糊查询、聚合、章节与上下文 |
| `search_app/parsers.py`、`detector.py` | 各格式解析与类型 / 编码识别 |
| `search_app/discovery/` | 资料发现、原生 USN、监听与同步 |
| `search_app/ai_qa.py`、`qa_win.py` | 可选 Ollama 语义索引与问答 |
| `search_app/tests/` | 单元测试与回归测试 |
| `tools/` | 构建、验证、异常治理与验收脚本 |
| `docs/` | 架构、安全、Qt 桌面端、体验与验收文档 |

## 许可与第三方组件

- 本仓库源码与文档：[MIT License](LICENSE)，Copyright (c) 2026 SleepDragon。
- Release 中的 Windows 体验版 ZIP：按包内随附的《用户许可协议》提供，与源码许可分开。
- 第三方组件（Qt 6.12 / PySide6 / SumatraPDF 3.6.1 / Tesseract / poppler / 各 pip 依赖）
  各自适用其上游许可，详见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) 与 Release 说明。

## 隐私说明

这是从本地工作区整理的公开源码快照：已移除 19 GB 本地索引库、打包 EXE、DLL / PYD、
虚拟环境、著作权 / 实名 / 合规 / 宣传材料、运行配置与测试截图；所有本机绝对路径已替换为
相对路径，历史内部文档中的旧令牌值已脱敏。贡献时请勿提交任何真实资料库或凭据，
见 [CONTRIBUTING.md](CONTRIBUTING.md) 与 [SECURITY.md](SECURITY.md)。
