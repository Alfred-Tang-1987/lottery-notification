# Design Review Report: 彩票开奖自动核对与通知系统

**Review date**: 2026-06-21
**Plan reviewed**: spec + 9 HTML prototypes @ commit `7630556`
**Branch**: `main`

## Step 0

**Initial 6/10** → **9/10 after fixes**。视觉系统扎实（+6），状态覆盖/A11y 拖后腿（-4），本评审补齐规范。

- DESIGN.md：项目原无 → 已创建 `docs/designs/DESIGN.md`（token 单一源，从 prototype 反向提取）。
- 复用：9 页视觉系统（CSS 变量/红蓝球/8 项导航/用户身份区）全复用。
- Mockups：prototype 已是成熟视觉基准，方向已定（D8 走势确认门等），不为已定方向重新生成；聚焦规范缺口。

## 7 Passes

| Pass | 前 | 后 | 要点 |
|---|---|---|---|
| 1 信息架构 | 8 | 9 | dashboard 首屏优先级显式化（D5） |
| 2 交互状态 | 3 | 9 | loading/error 全缺→统一 `<State>` 组件 + 9 页状态表 |
| 3 用户旅程 | 6 | 8 | 空状态引导 + 中奖落地页补 |
| 4 AI slop | 8 | 8 | 手写非模板，emoji 仅功能性，无紫渐变/卡片墙 |
| 5 设计系统 | 5 | 9 | 无 DESIGN.md→已建 token 单一源（D3） |
| 6 响应式/A11y | 3 | 9 | A11y 基线（D1）+ 移动导航（D6）+ 断点 |
| 7 未决 | — | — | D3-D6 全解 |

## Decisions

| Decision | Choice | Rationale |
|---|---|---|
| D1 A11y 基线 | 规范（进任务） | landmark + button 化 + aria + 焦点环 + 44px + AA，强制 MVP |
| D2 状态系统 | 规范（进任务） | 统一 `<State>` 组件 + 9 页 loading/empty/error 文案 |
| D3 DESIGN.md | A | 落库 token 单一源（已建） |
| D4 走势确认门 | A | 右侧抽屉 + 确认句 |
| D5 dashboard 优先级 | A | 待兑奖>命中>盈亏>概览>日历/代销点 |
| D6 移动导航 | A | 底部 tab bar(4 高频) + 更多抽屉 |

## Implementation Tasks

- [ ] **T1 (P1)** — design — Vue 组件统一引用 DESIGN.md token（不 hardcode 色号/字号）
  - Surfaced by: Pass 5（无 DESIGN.md）
  - Files: web/ 组件 + docs/designs/DESIGN.md
  - Verify: grep 无散落 #色号/px 字号（全用 var()）
- [ ] **T2 (P1)** — ui — 交互状态 `<State>` 组件 + 9 页 loading/empty/error 覆盖（当前 loading 0/9、error 0/9）
  - Surfaced by: Pass 2
  - Files: web/ 全页
  - Verify: 每页 4 态视觉验证
- [ ] **T3 (P1)** — a11y — A11y 基线：ARIA landmark + `<button>` 化（消除 11 处 div onclick）+ aria-label + :focus-visible + 44px 触控靶 + AA 对比
  - Surfaced by: Pass 6（aria 0、role 2、div onclick 11）
  - Files: web/ 全页
  - Verify: axe/lighthouse a11y ≥90
- [ ] **T4 (P1)** — ui — 走势确认门右侧抽屉（drawer + 确认句 + 选号面板）
  - Surfaced by: D8/D4
  - Files: web/ trend 组件
  - Verify: 默认折叠；点"我要选号"→抽屉+确认句→展开
- [ ] **T5 (P2)** — ui — dashboard 首屏优先级重排（待兑奖>命中>盈亏>概览>日历/代销点）
  - Surfaced by: Pass 1/D5
  - Files: web/ dashboard
  - Verify: 首屏顶都是高优先级卡
- [ ] **T6 (P2)** — ui — 移动端底部 tab bar(仪表盘/号码/查询/我的) + 更多抽屉
  - Surfaced by: Pass 6/D6
  - Files: web/ 导航组件
  - Verify: 375px 底部 tab 可达 4 高频，其余在更多抽屉
- [ ] **T7 (P2)** — ui — 响应式断点 320/375/768/1024/1440 全 9 页验证（无溢出、触控友好）
  - Surfaced by: Pass 6
  - Files: web/ 全页
  - Verify: 5 断点视觉回归
- [ ] **T8 (P2)** — content — 空状态文案系统（温暖+主操作 CTA+上下文，禁裸"No data"）
  - Surfaced by: Pass 2/3
  - Files: web/ 空状态 + spec §12.4 表
  - Verify: 每空状态有 CTA

## NOT in scope
- 从零生成新视觉方向（prototype 已定稿，D8 确认门方向已定）
- 中文展示字引入（DESIGN.md 标为可选，MVP 不强制）
- `/design-review` 实现后视觉 QA（实现完成再跑）

## What already exists
9 页手写 prototype（统一视觉系统）+ CLAUDE.md 记录。全复用，token 已提取进 DESIGN.md。

## Completion Summary
- 7 passes 全跑，6→9/10
- 8 任务（P1×4、P2×4）
- 0 unresolved
- DESIGN.md 已创建并落库
- CEO + ENG + DESIGN 三 CLEAR
