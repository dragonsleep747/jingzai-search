# 维护日志 / Maintenance log

本文件只记录**已经发生、可验证**的维护动作，作为项目活跃维护的公开留痕。
不记录会变化的指标（star、下载量），也不记录任何用户数据。

## 2026-11-22 (UTC+8)

- 将本次两条自建提交的作者 / 提交者身份统一为仓库所有者 `dragonsleep747 <dragonsleep747@gmail.com>`，避免本机别名邮箱在 GitHub 上显示为另一个账号；仅重写这两条提交，初始提交与 Windows 体验版 Release 不变。
- 新增 `MAINTAINERS.md`、`ROADMAP.md`、`CHANGELOG.md` 与本文档，明确唯一维护者、后续计划与版本记录。
- 新增 GitHub Issue / PR 模板，把「索引失败、编码问题、命中位置错误、UI 缺陷」变成可复现的结构化报告；模板要求提交者确认不含真实资料与密钥。
- 新增 `tools/check_public_repo.py`：检查禁止产物（数据库、EXE/DLL/PYD、压缩包、超大文件）、本机绝对路径与密钥形态字符串。
- 本地维护检查：`python -m compileall -q search_app tools`、`python tools/check_public_repo.py`。
- 本地验证：上述两条命令均为 exit 0 / PASS。

## 2026-11-21 (UTC+8)

- 从约 19 GB 的本地工作区按白名单整理出 195 个公开文件（约 5.3 MB），推送到 `main`（提交 `23d1b9e`），保留原 `Initial commit` 与 Windows 体验版 Release。
- 为源码添加 MIT 许可证，编写公开 README、贡献指南、安全策略与第三方组件说明。
- 清理文档与构建脚本中的本机绝对路径，脱敏历史内部文档中的旧令牌值。
- 核验 GitHub 已识别主语言 Python、许可证 MIT；Release 资产、标签与 SHA-256 未改变。

## 复现方式

```powershell
python -m compileall -q search_app tools
python tools/check_public_repo.py
python search_app\tests\run_tests.py
```
