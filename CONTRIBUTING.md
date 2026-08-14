# 贡献指南

感谢关注！贡献前请先读两条硬约束：

## 合规红线（不可协商）

本项目定位「事后核对 + 个人号码管理」。**以下方向的 Issue / PR 一律关闭**：号码预测、AI 选号推荐、冷热号排序推荐、必中宣传、购彩代购 / 支付接入、高频彩 / 私彩相关。走势图保持「综合分布 + 频次」形态，不排序、不标冷热、不推荐。

## 正确性纪律

本项目处理金钱相关信息，最高纪律是「**中奖永不静默漏通知**」：

- DB 写单事务一次 commit，禁止 split-commit
- 批量循环逐行故障用 SAVEPOINT 隔离（`session.begin_nested()`），不中断整批
- 金额一律用「分」（int）存储
- 领域层（`app/domain/`）零 IO——`uv run lint-imports` 强制

## 开发流程

1. Fork + 分支（`feat/...` / `fix/...`）
2. **TDD**：先写失败测试，再最小实现（80%+ 覆盖率是底线；新逻辑必须有测试）
3. 提交信息：`<type>: <描述>`（type ∈ feat/fix/refactor/docs/test/chore/perf/ci）
4. PR 前本地过门禁：
   ```bash
   uv run ruff check . && uv run lint-imports && uv run pytest -q
   cd web && npm test && npm run build
   bash scripts/publish-check.sh --grep-only
   ```
5. PR 描述写清：动机、方案、测试证据（输出贴图/文本）

## 彩种规则改动

奖级表 / 比对规则改动的权威依据是 `docs/reference/lottery-rules.md`（其来源为官方规则页）。**改代码前先改文档并附官方来源链接**；双色球（唯一生产验证彩种）的回归基线 `tests/domain/test_ssq_regression_baseline.py` 必须保持绿。

## 许可证

提交即表示你同意贡献内容按本项目的 **AGPL-3.0-only** 许可发布。
