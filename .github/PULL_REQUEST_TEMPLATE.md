## 改动说明

<!-- 改了什么、为什么；用简短中文或英文 -->

## 相关问题

<!-- 例如 Closes #123；没有就写 None -->

## 验证

```powershell
python -m compileall -q search_app tools
python tools/check_public_repo.py
python search_app\tests\run_tests.py
```

- [ ] 上述命令已运行，结果贴在下方或 PR 评论
- [ ] 未提交 `index.db` / `catalog.db` / `config.json` / `*.exe` / `*.dll` / `*.pyd` / 虚拟环境
- [ ] 未提交真实资料、截图中的真实内容、实名材料、令牌或 API key
- [ ] 新增依赖已同步 `search_app/requirements.txt` 与 `THIRD_PARTY_NOTICES.md`
- [ ] 文档涉及本机路径时已改为相对路径
- [ ] 用户可见变更已写入 `CHANGELOG.md`（如适用）

## 实际命令输出摘要

<!-- 只贴脱敏后的 PASS / FAIL 摘要，不要贴真实资料 -->
