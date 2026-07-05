# Lessons Learned

## L-20260701T103320Z
title: plan-06 前端 task 测试策略（vitest 必要 scope，不进 gate；spec 行级覆盖须落到前端 form/持久化层）
detail: plan-06 是前端 plan（Vue3/TS），但 workflow test_command=uv run pytest 只覆盖 Python。前端 task（T3 API client、T4-T7 组件/页面）测试策略：(1) implementor 须加 vitest+jsdom 做自动化测试（cd web && npm test）——plan 原设计的「手动浏览器验证」在 TDD 纪律下不够、证明不了回归；(2) vitest 依赖 + web/vitest.config.ts（独立于 vite.config.ts）+ npm test script 是 plan 认可的必要 scope，review 不得以「超 scope」否定；(3) 已知盲区：plan-06 gate 只跑 pytest，前端测试不进 gate，手动验收须 npm test。参考 T3 落地：web/src/api/client.ts + client.test.ts（21 tests pass，覆盖 CSRF 缓存/校验、401 重定向、错误原文保留、falsy body 序列化、AbortSignal 超时）。
新增证据（plan-06/T6e 振荡）：Settings.vue 的 Settings.test.ts 只覆盖 notification rules + DND，遗漏 master_enable/path_a_enable/summary_time/new_numbers_default_enabled 四个新 toggle、summary_time 持久化、theme（preferences）持久化、template preview 渲染——spec reviewer 在 round 3 才发现（round 1-2 全在补后端字段/API），但 plan Step 4 「spec §12.2 row 8 全勾」明确要求前端 vitest 层。后端 tests/api/test_settings_t6e.py 覆盖 API roundtrip 但**不能替代前端 form 持久化测试**——gate 不跑 vitest，故此类缺失在 implementor 自报「全绿」时仍漏到 review 阶段。修法：implementor 写前端 toggle/表单类 task 时，先列「form 字段 × 持久化 × 渲染」三维矩阵再补测试，勿只测 happy path。
status: active

## L-20260705T120000Z
title: JSON-backed 偏好字段禁用 `bool(parsed.get(...))` —— 字符串 "false" 会被判 True（silent-success 陷阱）
detail: 当用户偏好/DND 等 dict 存进 JSON 列（如 preferences_json/dnd_json），读出后做布尔解析时，**严禁** `enabled = bool(parsed.get("enabled", False))`。原因：JSON 经 str→dict 往返或前端传值时，"false"（字符串）会被 `bool()` 判为 True（非空字符串皆真），导致「关闭」被静默当作「开启」持久化/读取——典型 silent-success，比对路径 silent-failure 更隐蔽（用户以为关了，系统照开）。
场景（plan-06/T6e round 2 quality finding）：`_parse_preferences` 用 `bool(parsed.get(...))` 解析 new_numbers_default_enabled 等开关，前端若传字符串 "false"（而非 JSON false），后端读为 True。同 task `_parse_dnd` 对损坏 dict 原样返回缺类型校验，二者严格度不一致。
修法：(1) 用显式布尔解析 `parsed.get(k, default) is True` 或 `str(parsed.get(k)).lower() in ("true", "1")`；(2) 在 API 入口用 pydantic model 校验（字段类型 `bool`），拒绝字符串；(3) 同一文件内所有 `_parse_*` helper 严格度须一致，避免「这个字段严、那个字段松」的认知不一致。
source: plan-06/T6e@2026-07-05
category: silent-failure
status: active

## L-20260705T120100Z
title: plan 含「spec §X row N 全勾」要求时，implementor 必须先逐条 decompose row 子项再写代码，否则 round 1 必爆 MISSING findings
detail: 当 plan Step 文字明确「spec §12.2 row 8 全勾」时，「全勾」= 该行**所有**子项都要实现+测试。implementor 常只读 Step 大纲（如「Step 3 主题切换 + DND 持久化」）就开始写，遗漏 row 内未在大纲逐条列出但「全勾」覆盖的子项。
场景（plan-06/T6e round 1 spec 5 findings 全 MISSING/EXTRA/MISUNDERSTANDING）：spec §12.2 row 8「推送时机（总开关+大奖即时简讯+次日汇总时间+免打扰时段）+ 偏好（外观联动·新号码默认启用）」含 5+ 子项，但 implementor 只实现了 DND + 每彩种 timing，漏 master_enable 总开关、path_a_enable 大奖即时开关、new_numbers_default_enabled 偏好开关——三者都是「全勾」明确范围。修复 round 1 → round 2 又引入新问题（去规范化、解析严格度），最终 round 3 才补齐但仍缺前端测试 → OSCILLATING。
修法：(1) implementor 接到含「全勾/全覆盖/row N」措辞的 task 时，先在 task 笔记里逐条列出 row 所有子项（标点「、」「，」「+」分隔的并列项）作为 checklist；(2) 每个子项映射到至少一个 UI 控件 + 一个后端字段 + 一个测试，三者缺一即视为未完成；(3) 不要被 Step 大纲的「主项」误导，大纲是摘要不是穷举。
source: plan-06/T6e@2026-07-05
category: test-strategy
status: active

## L-20260705T120200Z
title: fix-round 振荡的 thrashing 信号 —— dead import（定义未调用）、migration add-then-drop churn、stale 注释；三者同时出现即应暂停重审 plan 而非继续 patch
detail: 当 review 反馈进入 round 2-3，implementor 若出现以下三联征，说明它已陷入 thrashing（无清晰 plan 的盲目打补丁），继续 patch 只会延长振荡：(1) **Dead import/code**——新增 helper 被 import 但全代码库无调用点（如 plan-06/T6e round 3 `summarizeSettled` 在 Settings.vue:4 import 但无任何调用，eslint no-unused-vars 必报）；(2) **Migration add-then-drop churn**——同一 PR 内一个迁移加列、下一个迁移立刻删列（如 round 3 alembic 3c645973cae7 加 master_enable/path_a_enable/default_enabled 列，5a1f2b8d9e04 立刻删——单迁移直接建 notification_settings 表即可）；(3) **Stale 注释**——注释描述的字段已被迁移走（如 user.py:15 preferences_json 注释仍写「含 new_numbers_default_enabled」，实际已迁到 NotificationSettings）。
根因：implementor 在 round 1 被打回后，没有先重审 spec/plan 重新设计 schema，而是「头痛医头」逐条补 round 1 findings，导致 schema 设计反复（先放 NotificationRule 发现去规范化→再迁出→留 dead code/注释）。这通常耗光 3 轮 budget 触发 OSCILLATING halt。
修法：(1) review 链或 fix-round 入口处加守卫：检测 dead import（grep 调用点）+ 同 PR 内 migration add-then-drop（alembic history 相邻 revision 字段重叠）→ 标记「high churn」提示 implementor 暂停 patch、重读 spec 重新设计；(2) implementor 收到 round 1 大量 MISSING 时，先问「我的 schema 设计对吗」再写代码，勿逐 finding 打补丁。
source: plan-06/T6e@2026-07-05
category: convention
status: active
