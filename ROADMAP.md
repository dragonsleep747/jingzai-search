# Roadmap

> 状态说明：以下是**计划**，不是已完成功能。项目很年轻，star / fork / issue 都很少，
> 这里如实记录优先级，不夸大进度。已完成动作见 [docs/MAINTENANCE_LOG.md](docs/MAINTENANCE_LOG.md)，
> 已发布内容见 [CHANGELOG.md](CHANGELOG.md)。

## 正在做：维护优先

- 复现并分类中文 Word / PDF 的索引失败与编码问题（Issue 模板已就绪）
- 为索引器、命中位置 / 上下文定位、打包脚本补回归用例
- 每次 Windows Release 生成并核对 SHA-256 清单
- 跟踪上游依赖安全公告；发布前运行 `tools/check_public_repo.py` 扫描令牌 / 密钥泄漏
- 保持 `docs/MAINTENANCE_LOG.md` 的真实、可验证留痕

## 下一步：来自现有设计文档

- 原生资料发现与增量同步：[docs/NATIVE_DISCOVERY_PLAN.md](docs/NATIVE_DISCOVERY_PLAN.md)、[docs/discovery_prework/ARCHITECTURE.md](docs/discovery_prework/ARCHITECTURE.md)
- 检索与问答后续改进：[docs/SEARCH_AND_QA_NEXT_PLAN.md](docs/SEARCH_AND_QA_NEXT_PLAN.md)
- 多模态与阅读体验：[docs/discovery_integration/MULTIMODAL_NEXT.md](docs/discovery_integration/MULTIMODAL_NEXT.md)
- 旧版完整功能向 Qt 迁移：[docs/QT_PARITY_REVIEW.md](docs/QT_PARITY_REVIEW.md)

## 明确不做

- 不把用户资料库、索引数据库或打包二进制产物提交到本仓库
- 不为指标刷 star / fork / 下载量，不虚构使用量
- 不在缺少回归覆盖的前提下重写检索引擎

## 参与方式

见 [CONTRIBUTING.md](CONTRIBUTING.md) 与 Issue / PR 模板。维护者为独立开发者，响应尽力而为。
