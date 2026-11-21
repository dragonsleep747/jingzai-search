# 贡献指南

欢迎通过 GitHub Issue 报告索引失败、编码问题、命中位置错误或 UI 缺陷；提交 PR 前请：

```powershell
python -m compileall -q search_app tools
python tools\check_public_repo.py
python search_app\tests\run_tests.py
python tools\exception_audit.py --out tools\audit.txt
```

- 复现问题时请给出文件类型、查询词、期望与实际结果，但**不要上传任何真实文档内容**。
- 不要提交 `index.db` / `catalog.db` / `config.json` / `*.exe` / `*.dll` / `*.pyd`、
  `__pycache__`、虚拟环境、截图中的真实资料、著作权 / 实名材料、任何密钥或令牌。
- 新增第三方依赖请同时更新 `search_app/requirements.txt` 与
  `THIRD_PARTY_NOTICES.md`，并注明其许可证。
- 提交信息用简短中文或英文说明「改了什么、为什么」。

维护者为独立开发者，回复可能不即时；本项目采用 MIT 许可证，贡献即视为同意按 MIT 授权。
