# Changelog

本文件记录值得注意的变更，格式参考 [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)，
日期为 UTC+8。项目尚年轻，只记录真实发生的事件。

## [Unreleased]

### Added

- `MAINTAINERS.md`、`ROADMAP.md`、`CHANGELOG.md`、`docs/MAINTENANCE_LOG.md`：明确维护者、计划与真实维护留痕
- GitHub Issue / PR 模板，把索引失败、编码问题、命中位置错误、UI 缺陷变成结构化报告
- `tools/check_public_repo.py` 与本地维护检查：`python -m compileall -q search_app tools`、`python tools/check_public_repo.py`

## [2026-11-21] — 源码公开

### Added

- 发布清洗后的 Python / Qt 源码树、discovery 模块、测试套件、架构与安全文档、构建脚本，共 195 个文件约 5.3 MB
- `LICENSE`（MIT）、公开版 `README.md`、`CONTRIBUTING.md`、`SECURITY.md`、`THIRD_PARTY_NOTICES.md` 与严格的 `.gitignore`

### Changed

- 文档与构建脚本中的本机绝对路径改为相对路径
- PyInstaller spec 中的可选 SumatraPDF 二进制改为「存在时才打包」

### Removed

- 约 19 GB 本地索引库、EXE / DLL / PYD、虚拟环境、运行配置、实名与商业材料
- 历史内部文档中的一个旧令牌值（已脱敏）

## [2026-11-04] — Windows 体验版 `windows-2026-11-03`

### Added

- 首个公开预发布 `JingzaiSearch-Windows-2026-11-03.zip`（202,110,839 字节），附 SHA-256 与随附第三方许可文本
- Release 说明包含正文检索、命中位置 / 上下文、空资料库启动方式与校验值
