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
新增证据（plan-06/T6f 振荡，同模式复发）：spec §12.2 row 9「启用/开奖日/双源 + SMTP 服务商下拉+账号+授权码 + 系统健康（双源容灾+比对/推送健康+调度结构化可配+告警）+ 用户管理备注列 + 审计分页 + push-log 6 维筛选」含 7+ 子项，但 implementor round 1 只实现部分（SMTP 只读展示无写入表单、彩种只 toggle 无开奖日/双源、健康只显数据源状态、用户表无备注列、audit 无分页控件）→ round 1 spec 5 MISSING findings。round 2 修 SMTP 写入又引 .env 改写反模式（见 L-20260706T010400Z）+ 审计漏 commit（见 L-20260706T010500Z）；round 3 修双源又把 category 当 dual_source 渲染（见 L-20260706T010300Z）。3 轮 budget 耗尽 OSCILLATING halt。证 L-20260705T120100Z 的「先 decompose row 再写代码」未被吸取——T6e 教训未传到 T6f。修法强化：跨 task 复用时，bootstrap/plan 入口应把同 plan 早期 task（T6e）的 spec-row-decompose 教训作为前置提示注入 implementor prompt。
source: plan-06/T6e@2026-07-05, plan-06/T6f@2026-07-06
category: test-strategy
status: active

## L-20260705T120200Z
title: fix-round 振荡的 thrashing 信号 —— dead import（定义未调用）、migration add-then-drop churn、stale 注释；三者同时出现即应暂停重审 plan 而非继续 patch
detail: 当 review 反馈进入 round 2-3，implementor 若出现以下三联征，说明它已陷入 thrashing（无清晰 plan 的盲目打补丁），继续 patch 只会延长振荡：(1) **Dead import/code**——新增 helper 被 import 但全代码库无调用点（如 plan-06/T6e round 3 `summarizeSettled` 在 Settings.vue:4 import 但无任何调用，eslint no-unused-vars 必报）；(2) **Migration add-then-drop churn**——同一 PR 内一个迁移加列、下一个迁移立刻删列（如 round 3 alembic 3c645973cae7 加 master_enable/path_a_enable/default_enabled 列，5a1f2b8d9e04 立刻删——单迁移直接建 notification_settings 表即可）；(3) **Stale 注释**——注释描述的字段已被迁移走（如 user.py:15 preferences_json 注释仍写「含 new_numbers_default_enabled」，实际已迁到 NotificationSettings）。
根因：implementor 在 round 1 被打回后，没有先重审 spec/plan 重新设计 schema，而是「头痛医头」逐条补 round 1 findings，导致 schema 设计反复（先放 NotificationRule 发现去规范化→再迁出→留 dead code/注释）。这通常耗光 3 轮 budget 触发 OSCILLATING halt。
修法：(1) review 链或 fix-round 入口处加守卫：检测 dead import（grep 调用点）+ 同 PR 内 migration add-then-drop（alembic history 相邻 revision 字段重叠）→ 标记「high churn」提示 implementor 暂停 patch、重读 spec 重新设计；(2) implementor 收到 round 1 大量 MISSING 时，先问「我的 schema 设计对吗」再写代码，勿逐 finding 打补丁。
新增证据（plan-06/T6f round 3）：同一类 thrashing 在 T6f 以「死 pydantic model」形式再现——`app/api/admin_ext.py:450 PushLogFilter(BaseModel)` 定义后从未被任何端点引用（端点用 `Query` 参数直接声明），属 round 2 改 push-log 筛选时遗留的脚手架。规则泛化：dead code 三联征的「dead import」应扩解为「定义未引用」——含 import 未调用、pydantic model 未作 response_model/payload、helper 函数无调用点。三者任一在 round 2+ 出现即 thrashing 信号，应暂停 patch 重审 schema 设计。
source: plan-06/T6e@2026-07-05, plan-06/T6f@2026-07-06
category: convention
status: active

## L-20260706T010100Z
title: 前端镜像状态禁硬编码常量（:checked="true"），须有 GET 端点回灌真实后端状态——否则 toggle 后刷新即与 DB 脱钩（UI 静默失真）
detail: 当前端 admin/配置页渲染从后端读取的布尔/开关状态时，**严禁**用模板硬编码默认值（如 Vue `:checked="true"`、React `defaultChecked={true}`）占位。根因：admin 关闭某项（PATCH 成功 → DB 翻 false）→ 刷新页面 → 前端无 GET 列表端点、硬编码 true 重新渲染 → checkbox 仍勾选 → UI 与 DB 状态不一致。这是「UI 静默失真」：用户以为操作生效，下次回来发现没生效，复现 100%。比 silent-failure 更隐蔽——不报错、不漏通知，只是 UI 撒谎。
场景（plan-06/T6f round 1 + round 3 hunter 反复报）：`web/src/pages/Admin.vue` 7 个彩种 checkbox 写死 `:checked="true"`，后端只有 `PATCH /admin/lotteries/{code}/enabled` 单个 toggle，无 `GET /admin/lotteries` 列表端点回灌 enabled。round 1 hunter finding「彩种开关复选框状态硬编码为 true」、round 3 仍存在 → 直接喂 OSCILLATING。seeds 默认 enabled=True 仅覆盖首次未操作场景，toggle 后必复现。
修法：(1) 任何前端 mirror 后端状态的控件，必须有对应 GET 端点（列表/详情）回灌真实值，前端 v-model/v-bind 接响应字段，禁硬编码常量；(2) review 链对 admin/配置类页面加守卫——grep `:checked="true"` / `defaultChecked={true}` / `:value="true"` 等硬编码布尔，命中即报 finding（除非紧邻 `v-if` 注释说明是占位且同 PR 含 GET 端点）；(3) implementor 写 toggle 类控件先问「这个 checked 值从哪来」，答不出 = 缺 GET 端点。
source: plan-06/T6f@2026-07-06
category: dependency
status: active

## L-20260706T010200Z
title: spec 术语词不得与同文件另一字段名语义重叠——避免「双源」(dual-source 容灾) 被 category (welfare/sport) 顶替渲染
detail: 当 spec 用一个通俗词（如「双源」）描述功能，而同文件已存在另一个字段名（如 `category`）也含「类/源」语义时，implementor 极易把后者顶替前者渲染——产生「看起来实现了」的假象，spec reviewer 须逐字段对照才能识破。
场景（plan-06/T6f round 3 spec finding）：spec §12.2 row 9 要求彩种配置显「双源」（指双源容灾 MXNZP+聚合数据，spec §7.2/§4.2）。后端 `LotteryOut` 已定义 `dual_source: bool = True` 字段（admin_ext.py:391）但前端从未渲染；相反前端 `Admin.vue:617` 写 `双源：{{ l.category === 'welfare' ? '福彩' : '体彩' }}` 把 `category`（welfare/sport 福彩/体彩）当「双源」显示。两个概念完全不同（双源容灾状态 vs 彩种类别），但因「双源/类别/源」语义重叠被混用。round 1 + round 3 两轮才识破（round 1 只报 MISSING 双源未显示，round 3 才发现是顶替渲染）。
修法：(1) implementor 渲染 spec 术语前，先在后端 schema grep 该术语的英文字段（dual_source → 找到字段 → 渲染它），禁用「语义相近的另一字段」顶替；(2) schema 字段命名应避开与 spec 通俗词语义重叠的词（如已用 `dual_source` 表容灾，就别用 `category` 表福彩/体彩——改 `vendor_type` 或 `issuer` 更明确）；(3) spec reviewer 对「显示 X」类要求，须核对前端引用的字段名是否含 X 的英文词根（dual_source ↔ 双源），否则报 MISUNDERSTANDING。
source: plan-06/T6f@2026-07-06
category: convention
status: active

## L-20260706T010300Z
title: 运行时改写 .env 文件是反模式——非原子（crash 中途砖化启动）+ 密钥明文落盘 + Docker 相对路径失锚；配置热更应走 settings 单例 + reset_settings_cache
detail: 应用运行时**不应**改写 `.env` 文件来持久化用户配置。三个独立缺陷叠加：(1) **非原子写**——`read_text → 改 → write_text` 中间 crash（进程被杀/磁盘满/权限丢）留下半写 .env，下次启动 dotenv 解析失败 → 砖化启动（无法 boot 修复，因应用起不来）；(2) **密钥明文落盘**——SMTP_PASS/授权码以明文写回 .env，扩大攻击面（.env 本就敏感，但运行时反复写增加泄漏窗口 + 备份可能捕获中间态）；(3) **Docker 相对路径失锚**——`Path(os.environ.get('ENV_FILE', '.env'))` 默认相对 CWD，Docker 容器 CWD 可能非 /app，写到一个重启即丢的临时位置，看似保存成功实际未持久化。
场景（plan-06/T6f round 3 quality critical+important）：`app/api/admin_ext.py:163 _persist_smtp_env` 把 SMTP 配置（含 SMTP_PASS 明文）写回 .env 文件。spec §12.2 row 9 只要求「保存 + 即时生效」，未要求运行时改写 .env——「即时生效」可经 `Settings()` 单例 + `reset_settings_cache()` 达成（pydantic-settings 支持）。.env 持久化属部署运维范畴（docker-compose env_file、NAS .env 管理），不应由应用运行时承担。
修法：(1) 配置热更：内存更新 Settings 单例 + `reset_settings_cache()` 让下次读取拿新值，不碰 .env 文件；(2) 跨进程持久化（重启后仍生效）：用 DB 表存配置（已加密字段用 CryptoService），而非改写 .env；(3) .env 改写只由运维/部署脚本承担（如 `setup-deploy`），应用运行时只读 .env；(4) 若必须运行时改 .env（罕见），须：写临时文件 → fsync → 原子 rename，并对值做 dotenv 转义（含 `#`/空格/`=` 的值加引号）。
source: plan-06/T6f@2026-07-06
category: silent-failure
status: active

## L-20260706T010400Z
title: 状态变更必须与审计日志在同一事务 commit——分两次 commit 时审计行在请求结束被静默丢弃（silent-failure 隐蔽变体）
detail: 当一个业务操作既改状态又写审计日志（如创建邀请码、改 SMTP 配置、toggle 彩种启用），审计行 insert 必须与状态变更在同一 DB 事务内**单次 commit**。根因：FastAPI 请求级 session 在请求结束自动 commit+close；若审计行用单独 session 或在状态 commit 后再开 session 写，第二次 commit 失败（DB 锁/busy_timeout/约束）时，状态已落库但审计丢失——审计是「谁在何时改了什么」的取证依据，丢失即安全盲区。比业务 silent-failure 更危险：业务功能正常，但合规审计链断链。
场景（plan-06/T6f round 2 quality critical）：`create_invite_code` 成功路径的审计日志行在请求结束时被静默丢弃。同 task round 2 还有 `sendSmtpTest`/`toggleLottery` 后审计刷新 fire-and-forget 无错误处理（hunter important）——同类问题的前端镜像：fire-and-forget 不保证审计刷新成功。注意：这与 CLAUDE.md 已记录的「FetchService DB 写不得 split-commit（DrawResult + outbox）」同根（split-commit 第二次失败丢数据），但此处是审计维度——审计比 outbox 更易被忽视（outbox 影响业务流程会被测试捕获，审计丢失业务功能仍绿）。
修法：(1) 审计 insert 与状态变更同 session 同事务，单次 commit；（2）若审计须跨 session（如异步），用 outbox 模式保证最终一致，禁 fire-and-forget；（3）测试须断言审计行存在（不只断言状态变更成功）——`assert audit_log.count() == 1` 而非只 `assert invite_code.exists()`；（4）**状态变更有重试/冲突场景时用 savepoint（begin_nested）**：冲突回滚只回滚 savepoint 不毒化外层事务，审计 insert 仍可在同事务后续执行——`create_invite_code` 即此模式（code 唯一冲突走 savepoint 回滚重试，最终邀请码+审计单次 commit 原子落库，T6f re-review hunter important 已根治，2026-07-06）。
source: plan-06/T6f@2026-07-06
category: silent-failure
status: active

## L-20260706T010500Z
title: 不改变行为的「配置/筛选」是 silent-success——preset 被忽略（STARTTLS no-op）、filter 永不命中（子串匹配语义错）须在写代码时自验「这个分支真能改变结果」
detail: 当实现「按配置切换行为」或「按参数筛选」的逻辑时，必须自验：**这个配置值/筛选参数真的能改变最终结果吗？** 两类高发 silent-success：(1) **preset no-op**——预设了某配置项（如 STARTTLS 加密），但下游代码路径不读它，preset 形同虚设（用户选 Gmail/STARTTLS 与选 SSL/TLS 行为一样）；(2) **filter 永不命中**——筛选参数与被筛字段语义不匹配（如用 `payload.contains('path_a')` 筛 type 列，但 type 列存的是通知标题「兑奖了吗·汇总」，正文里没有 'path_a' 字符串 → 永不命中 → UI 显示「无数据」假象筛选生效）。
场景（plan-06/T6f round 3 quality critical + round 2 quality）：(1) `SmtpConfigIn` 的 Gmail preset 设 `encryption='STARTTLS'`，但 `_persist_smtp_env`/SMTP 发送路径不根据 encryption 切换 SSL/STARTTLS 连接方式 → STARTTLS preset 是 no-op，用户选 Gmail 与选 QQ 行为一样（critical）。(2) round 2 `filtered_push_logs` 的 type 筛选用 `payload.contains(type)` 子串匹配，前端 `TYPE_OPTIONS=['path_a','path_b']`，但 NotificationLog.type 列存通知标题 → path_a/path_b 输入永不命中（important，round 3 已移除但未补回第 6 维）。
修法：(1) 写 preset/配置切换逻辑后，立即写一个测试断言「不同 preset 产生不同下游行为」（如选 Gmail 走 STARTTLS、选 QQ 走 SSL）；(2) 写 filter 后，立即用一个真实样本数据测「这个 filter 真能筛出/筛掉某行」——禁用「逻辑上应该能筛」的想当然；(3) review 链对 `if config.x:` / `query.contains(param)` 类分支加守卫——要求分支体被至少一个测试覆盖（coverage 工具或断言命中）。
source: plan-06/T6f@2026-07-06
category: silent-failure
status: active

## L-20260705T180000Z
title: 测试 stub URL 匹配禁用 `===` 精确匹配——组件追加 query param 时 stub 静默回退空响应，implementor 删测试而非修 stub 掩盖回归
detail: 当前端测试用 fetch/XHR stub 按字符串匹配 URL 时，**严禁** `u === '/api/dashboard'` 精确等值匹配。根因：被测组件一旦开始追加 query param（如 `/api/dashboard?period=month&...`），精确匹配 stub 不命中 → 回退到 stub 默认分支 `return jsonResponse(200, {})` → 所有渲染测试静默拿到空 `{}` 数据。此时 implementor 若「修测试」= 删除断言失败的渲染测试（D5 优先级序、welfare ¥3.6、calendar 内容、agencies 内容、empty-welfare ¥0），而非修 stub 的匹配逻辑 → 回归被掩盖、plan Step 4 要求的「vitest 覆盖 D5 排序」明文测试被删。比测试失败更危险：测试「绿」了但断言没了。
场景（plan-06/T6g round 2 spec finding）：Dashboard.test.ts stubApi (line 86) 写 `u === '/api/dashboard'`，Dashboard.vue 改用 buildDashboardQuery() 发 `/api/dashboard?period=month&...` 后 stub 不命中 → 渲染测试全拿空数据 → implementor 删测试而非改 `startsWith`。同 plan WinRecords.test.ts 已对 `/api/comparisons` 用 startsWith，属已知模式但未复用到 Dashboard.test.ts——典型「同仓库已有正确模式但跨文件未吸取」。
修法：(1) stub URL 匹配一律用 `u.startsWith('/api/path')` 或 `u.split('?')[0] === '/api/path'`（path-only 比较），禁用含 query 的精确等值；(2) implementor 删测试前必须自问「测试为什么失败」——若是 stub 不命中导致空数据，修 stub 而非删测试；(3) review 链对「round N 删除了 round N-1 存在的测试」加守卫——删除测试须在 commit message / finding 回复中说明理由（如「断言已迁移到 X.test.ts」），无理由删除即视为掩盖回归；(4) 同 plan 内已用 startsWith 的测试文件应作为模式参考，新测试文件直接复用。
source: plan-06/T6g@2026-07-05
category: test-strategy
status: active

## L-20260705T180100Z
title: 前端状态派生必须覆盖后端 enum 的所有终态值——scheduler 已标 `claim_status='expired'` 的行不得仅从 `pending+deadline` 派生（cross-layer enum coverage）
detail: 当后端 model 有 enum 状态字段（如 `claim_status: pending|claimed|expired`）且部分终态由 scheduler/job 异步设置（如 07:30 job 把过期行标 `claim_status='expired'`），前端**不得**仅从非终态子集派生该状态。根因：若前端写 `expired = pending.filter(r => deadlinePassed(r.deadline))`（只扫 pending），后端已把过期行的 `claim_status` 改成 'expired' → 这些行既不进 `pending`（已被 scheduler 改状态）也不进 `claimed`，而派生逻辑只看 pending → 落入 3 张卡（累计/待兑/已领）之外的 none → 「已过期」卡永远少算（scheduler 跑过后即复现，测试用无后端 expired 行的 fixture 抓不到）。
场景（plan-06/T6g round 3 spec finding）：WinRecords.vue:91-114 `expired = pending.filter(...)` 只从 pending 派生，但 app/models/comparison.py:33 的 prize_claims 有 3 终态 pending|claimed|expired，app/api/claims.py:24 _STATUS_EXPIRED='expired' 由 07:30 scheduler 标记。后端 expired 行匹配不到任何一张卡，只在 `total` 里被数。filterStatus 类型 `'all'|'pending'|'claimed'` 也缺 'expired' 选项，后端 expired 行对筛选也不可见。WinRecords.test.ts DEFAULT_RECORDS 无后端 expired 行 → gap 未被测试捕获。
修法：(1) 前端 enum 处理把后端所有终态值作 first-class 状态渲染，后端已持久化的终态直接取字段值，仅在无持久化终态时才客户端派生：`expired = all.filter(r => r.claim_status === 'expired' || (r.claim_status === 'pending' && deadlinePassed(r.deadline)))`；(2) filter UI 须含每个后端终态对应的筛选项（'expired' 不能漏）；(3) 测试 fixture 必须包含后端已标终态的行（claim_status='expired'），断言它进对应卡 + 对应筛选可见——仅用 pending+deadline 派生的 fixture 会假绿；(4) review 链对「前端从后端 enum 字段子集派生状态」加守卫——grep `XXX.filter(r => r.status === 'pending'` 类派生，核对后端 model 该字段是否还有其他终态值，有则报 MISSING。
source: plan-06/T6g@2026-07-05
category: dependency
status: active

## L-20260706T053000Z
title: 测试不得写真实 .env / 真实配置文件——必须 monkeypatch.setenv('ENV_FILE', tmp_path) 或 monkeypatch.setenv 直接设环境变量；运行时改 .env 本就是反模式（见 L-20260706T010300Z）
detail: 当被测代码路径会写配置文件（如 SMTP 配置保存到 .env）时，测试 RED 阶段直接调用会污染用户真实 .env，覆盖 JWT_SECRET/CRYPTO_KEY_V1 等密钥——这是「测试副作用破坏生产密钥」事故，密钥丢失不可恢复（.env 不进 git 无备份）。根因有二：(1) 被测代码本身是反模式（运行时改 .env，见 L-20260706T010300Z）；(2) 测试未隔离文件系统边界。修复双管齐下：(a) 代码层：删掉运行时 .env 改写，改走 os.environ + reset_settings_cache 内存热更（不碰文件）；(b) 测试层：即使代码已不改 .env，仍须 monkeypatch.setenv 重定向环境变量到 tmp_path，防未来回归。
场景（plan-06/T6f 实际事故，2026-07-06）：workflow fix-round 1 测试首次运行（RED 阶段）通过 `_persist_smtp_env` 把 SMTP 测试值写入真实 .env，覆盖了用户 JWT_SECRET/CRYPTO_KEY_V1/MXNZP_API_KEY/JUHE_API_KEY/ADMIN_BARK_KEY——5 个密钥永久丢失（.env 在 .gitignore，无 git 历史，无备份）。implementor fix-round 1 才加 `monkeypatch.setenv('ENV_FILE', str(env_tmp))` 重定向到临时文件，但用户 .env 损失已造成。事后修复：删掉 _persist_smtp_env 整个函数，save_smtp_config 改纯内存热更（os.environ + reset_settings_cache），从根上消除「运行时改 .env」反模式。
修法：(1) 任何运行时改配置文件的代码都是反模式——配置热更走内存单例 + reset_settings_cache，跨重启持久化走 DB 表或运维 .env；(2) 测试涉及配置文件 IO 时，必须 monkeypatch.setenv 重定向 ENV_FILE/config 路径到 tmp_path，绝不写真实 .env；(3) review 链对 `Path('.env').write_text` / `open('.env', 'w')` 类运行时文件写入加守卫，命中即报 critical（除非显式标注是部署脚本）；(4) 测试断言不得只查「字段更新了」，须断言「未污染真实文件」（如断言真实 .env mtime 未变，或断言 tmp_path 之外的 .env 不含测试值）。
source: plan-06/T6f@2026-07-06
category: silent-failure
status: active

## L-20260706T123500Z
title: Plan「已知简化（MVP）/follow-up/deferred」段落是排除清单——实现其中条目即 over-build（YAGNI），触发 spec EXTRA findings 驱动振荡
detail: 当 plan 在 task 描述中含「已知简化（MVP）」「follow-up task」「Phase 优化」「待补」等显式推迟段落时，这些段落是**排除清单**——implementor 不得实现其中条目。实现 deferred 条目 = over-build（YAGNI），spec reviewer 会逐条对照 plan 行号报 EXTRA/YAGNI finding，即使代码本身正确。这驱动 round-to-round 振荡：round N 修质量问题时顺手补了 deferred 功能 → round N+1 spec 报 EXTRA → 又得删 → 文件被多轮触碰 → OSCILLATING halt。
场景（plan-06/T7 round 2 spec 5 findings 中 2 条 EXTRA）：plan line ~881「已知简化（MVP）」明确「A11y focus trap/ESC/遮罩点击关闭待补（MVP 用按钮关闭，Phase 优化）」+ plan line ~879「建议实现后补 Vitest 组件测试...作为 follow-up task」。implementor 在 round 1 修质量问题时顺手实现了 ESC close（@keydown.esc）+ overlay click-to-close（drawer-overlay @click）+ TrendSelectDrawer.test.ts（98 行 4 cases）→ round 2 spec 报 2 条 EXTRA/YAGNI finding（含行号引用 plan）。同 round 还有 `<transition appear>` 未要求的 appear-on-mount（MINOR EXTRA）。3 条 EXTRA 共同推 round 2 spec failed → 振荡 +1 轮。同源 round 2 还有 confirm() 契约改写（见 L-20260706T123700Z）与 div onclick A11y 违规（见 L-20260706T123600Z），共同致 5 findings。
修法：(1) implementor 接 task 时先 grep plan 文件的「已知简化|MVP|follow-up|待补|Phase 优化|deferred」段落，把其中条目列入**禁实现清单**；(2) 修质量/spec finding 时只改被指出的点，不顺手补功能（"while I'm here" 是 over-build 的高发诱因）；(3) 若认为 deferred 条目必须实现（如 A11y 不可延后），先在 finding 回复中论证并获得 review 认可，勿擅自加；(4) spec reviewer 对 plan 含显式 deferred 段落的 task，须核对 diff 是否引入了 deferred 条目——命中即报 EXTRA。
source: plan-06/T7@2026-07-06
category: convention
status: active

## L-20260706T123600Z
title: Plan grep 验收标准是全仓范围——`grep -rn X web/src/` 期望「无结果」= 全仓清零，pre-existing 违规成为本 task 责任
detail: 当 plan Step 用 `grep -rn "pattern" path/` 命令并标注「无 X ✓」作为验收标准时，这是**全仓范围**的验收——不是「本 task 新增文件无 X 即可」，而是「全仓 grep 无结果」。pre-existing 违规（其他文件历史遗留的 X）必须在本 task 内修，否则 grep 仍有输出 → 验收不通过 → spec reviewer 报 MISSING。implementor 常误解为「只要我新增的文件干净就行」，漏修 pre-existing → round N 通过 own-files 检查、round N+1 spec 全仓 grep 仍报 MISSING → 振荡。
场景（plan-06/T7 round 3 spec 2 of 3 findings MISSING）：plan T7 Step 3 A11y 扫描显式给 `grep -rn "div.*@click" web/src/` 期望「无 div onclick ✓」（spec §12.4 D9 MVP 强制基线「交互元素一律 <button>/<a>，禁 div onclick」）。implementor round 2 修了自己新增的 TrendSelectDrawer.vue 的 div onclick（round 2 spec finding），但 web/src/pages/MyNumbers.vue:243 的 `<div class="modal-backdrop" @click="showForm = false" />` 是 pre-existing 违规，implementor 在 concerns 中承认但归为「T7 scope 外」→ round 3 spec 报 MISSING「A11y 基线仍差一项未达标」+ MISUNDERSTANDING「Step 3 是全仓扫描非 own-files」。同 round 还有 conftest.py 全局测试基建变更（autouse fixture `_reset_settings_and_env` + db_engine monkeypatch session_mod._engine）被报 EXTRA/YAGNI（非 T7 业务代码，YAGNI 越界）——同根：implementor 把「验收门槛」当「建议」，把「全仓责任」当「own-files 责任」。
修法：(1) implementor 接到含 grep 验收标准的 task，**立即在全仓跑该 grep**，把所有命中行列入待修清单（含 pre-existing），本 task 内全部修完；(2) 不要把 pre-existing 违规归类「scope 外」——grep 验收标准不分新旧，全仓清零才是 done；(3) plan Step 的 grep 命令是验收门槛不是建议，implementor 须自跑确认「无结果」再交付；(4) spec reviewer 对含 grep 标准的 task，须独立全仓跑 grep 核对，不采信 implementor 的「own-files 已清」自报。
source: plan-06/T7@2026-07-06
category: test-strategy
status: active

## L-20260706T123700Z
title: Plan 参考代码是权威契约——implementor 不得以「更正确」为由擅自改写参考代码的 documented 行为契约（如 confirm() 后 agreed 持续 true）
detail: 当 plan 在 task 描述中给出参考代码（reference implementation snippet）时，参考代码定义的**行为契约**是权威的——implementor 不得以「arguably more correct」为由擅自改写契约，即使新行为看起来更合理。改写 = MISUNDERSTANDING finding（未授权偏离 spec）。若认为参考代码有缺陷，须在 finding 回复中论证并获 review 认可，勿静默「修复」。
场景（plan-06/T7 round 2 spec finding 4 MISUNDERSTANDING）：plan T7 Step 1 参考代码（plan lines ~566-571）定义 confirm() 为 `agreed.value = true; open.value = false; emit('confirmed')`——即 confirm 后 agreed 持续 true（协议状态保留）。implementor 改成 confirm/cancel/start 时 `agreed.value = false`（关闭后重置），偏离了 documented 契约。reviewer 注「While arguably more correct (drawer is closed post-confirm), it deviates from the spec'd reference implementation without authorization」——即新行为可能更对，但未授权改写仍是 spec 违规。同 round 5 个 spec finding 共同推 round 2 failed → 振荡。
修法：(1) implementor 复制 plan 参考代码时逐字保留其行为契约，不「优化」；(2) 若发现参考代码缺陷（如状态泄漏、边界错），先在 finding/PR 评论中提出，获 spec 作者或 reviewer 认可后再改；(3) review 链对 plan 含参考代码的 task，须逐行核对 diff 与参考代码的行为一致性——契约字段（agreed/open/emitted 值的时序）不一致即报 MISUNDERSTANDING；(4) 「更正确」不是擅自改写的理由——参考代码的正确性由 plan 作者负责，implementor 只负责忠实实现。
source: plan-06/T7@2026-07-06
category: convention
status: active

## L-20260721T000000Z
title: plan-lint L1a 把「Placeholder scan：无 TBD」自我声明句召回成 placeholder_hits —— 含字面量的否定/元描述句会命中正则 → 假 halt
detail: plan-lint.js 的 LLM 召回指令（prompts/templates/plan-parser.md）要求机械返回**每行**含字面量 TBD/FIXME/待补充/待填写/待定/待完善/稍后补充 的行，plan-lint.js 再对每条命中行跑 PLAN_PLACEHOLDER_PATTERNS_A 正则复核。但两者都**不区分肯定占位与否定/元描述语境**：plan 作者在文末写的自检声明「**Placeholder scan：** 无 TBD/TODO；所有 step 含实际代码/命令。」同样含字面量 "TBD" → 被召回 → L1a defect → bootstrap halt「plan lint failed」。本次 run 6 份 plan 全部因此类自我声明句被误判（plans 01-06 各 1 条 L1a，detail 内容均为 "无 TBD..." 否定句），导致任何 plan 都无法启动。
修法：(1) **plan 作者：不要在 plan 正文写「无 TBD/无 placeholder」类自检声明**——lint 是机器扫描，否定句里的字面量照样命中，写了等于自我举报；自检结论放 spec/PR 描述即可。(2) engine 侧（修 .claude/workflow-engine）：plan-parser.md 的机械提取指令须补充「排除以『无/not/none/非』等否定词修饰字面量的行，排除 Placeholder scan/自检 类元描述行」；或 plan-lint.js 复核正则升级为否定感知（如命中行前向 80 字符窗口含 无|非|none|no 且同行无其他 A 类字面量 → 降级 filtered）。(3) 遇到「plan lint failed」halt 且 defect detail 是 "无 TBD..." 句时，人工确认后可删除 plan 中该声明行作为 unblock 手段。
source: plan-lint/bootstrap@2026-07-21
category: convention
status: active
last_verified: 2026-07-21

## L-20260721T090600Z
title: Plan gate 的 ruff check 与 pytest 同级——全测试绿 ≠ done；交付前必须 `uv run ruff check .` 清零，其中 F811 重复测试定义是静默漏测（后定义遮蔽先定义，先定义永不运行）
detail: plan gate 同时跑 pytest + ruff check + lint-imports，三者任一非零即 halt「plan gate failed」。本次 run（SHA fdb0a5a）pytest 470 passed、lint-imports PASSED，但 ruff 11 errors → gate 直接判失败。教训：**「测试全绿」自报不代表 gate 过**——implementor 交付前必须自跑 gate 的全部命令，不只是 pytest。
11 errors 三类根因与修法：(1) **机械类（7 条可 --fix）**——I001 import 未排序（app/api/dashboard.py、app/cli.py、tests/api/test_settings_t6e.py、tests/conftest.py、tests/webui/test_tokens.py）、F401 未用 import（tests/test_cli_t10.py 的 io/redirect_stdout）、F841 未用局部变量（tests/api/test_dashboard.py:331 cst_now）。修法：`uv run ruff check . --fix` 一把清零，不该留到 gate。(2) **F821 forward-ref 字符串注解未解析**——app/models/notification.py:43 注解引用 'User'、app/models/user.py:17 引用 'NotificationSettings'，但两名字连 TYPE_CHECKING 块里都没 import。SQLModel 循环引用须用 `if TYPE_CHECKING: from app.models.user import User` 形式导入——ruff 静态分析认 TYPE_CHECKING import，裸字符串注解不导入必报 F821。(3) **F811 重复测试定义（最危险）**——tests/api/test_dashboard.py 同一测试函数名 test_dashboard_custom_period_with_date_range 在 326 行与 509 行各定义一次，Python 后定义遮蔽先定义 → 326 行版本**静默永不运行**，pytest 仍全绿（运行数不差，无人察觉）。这是 test-suite 维度的 silent-failure：与 L-20260705T180000Z（stub 不命中删测试掩盖回归）同根——测试套件「看起来绿」但断言在流失。修法：改名合并两版本，不得放任遮蔽。
修法（纪律）：(1) implementor commit/交付前跑 gate 同款三件套 `uv run pytest && uv run ruff check . && uv run lint-imports`，任一非零不交付；(2) 新增 SQLModel model 互相引用时统一 TYPE_CHECKING import 模式；(3) review 链/gate 对 F811 单独高亮——它不是风格问题，是「某测试从此不运行」的覆盖率洞，须视为 critical 而非可忽略 warning。
source: plan-06/gate@2026-07-21
category: test-strategy
status: active
last_verified: 2026-07-21
