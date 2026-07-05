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

## L-20260706T053000Z
title: 测试不得写真实 .env / 真实配置文件——必须 monkeypatch.setenv('ENV_FILE', tmp_path) 或 monkeypatch.setenv 直接设环境变量；运行时改 .env 本就是反模式（见 L-20260706T010300Z）
detail: 当被测代码路径会写配置文件（如 SMTP 配置保存到 .env）时，测试 RED 阶段直接调用会污染用户真实 .env，覆盖 JWT_SECRET/CRYPTO_KEY_V1 等密钥——这是「测试副作用破坏生产密钥」事故，密钥丢失不可恢复（.env 不进 git 无备份）。根因有二：(1) 被测代码本身是反模式（运行时改 .env，见 L-20260706T010300Z）；(2) 测试未隔离文件系统边界。修复双管齐下：(a) 代码层：删掉运行时 .env 改写，改走 os.environ + reset_settings_cache 内存热更（不碰文件）；(b) 测试层：即使代码已不改 .env，仍须 monkeypatch.setenv 重定向环境变量到 tmp_path，防未来回归。
场景（plan-06/T6f 实际事故，2026-07-06）：workflow fix-round 1 测试首次运行（RED 阶段）通过 `_persist_smtp_env` 把 SMTP 测试值写入真实 .env，覆盖了用户 JWT_SECRET/CRYPTO_KEY_V1/MXNZP_API_KEY/JUHE_API_KEY/ADMIN_BARK_KEY——5 个密钥永久丢失（.env 在 .gitignore，无 git 历史，无备份）。implementor fix-round 1 才加 `monkeypatch.setenv('ENV_FILE', str(env_tmp))` 重定向到临时文件，但用户 .env 损失已造成。事后修复：删掉 _persist_smtp_env 整个函数，save_smtp_config 改纯内存热更（os.environ + reset_settings_cache），从根上消除「运行时改 .env」反模式。
修法：(1) 任何运行时改配置文件的代码都是反模式——配置热更走内存单例 + reset_settings_cache，跨重启持久化走 DB 表或运维 .env；(2) 测试涉及配置文件 IO 时，必须 monkeypatch.setenv 重定向 ENV_FILE/config 路径到 tmp_path，绝不写真实 .env；(3) review 链对 `Path('.env').write_text` / `open('.env', 'w')` 类运行时文件写入加守卫，命中即报 critical（除非显式标注是部署脚本）；(4) 测试断言不得只查「字段更新了」，须断言「未污染真实文件」（如断言真实 .env mtime 未变，或断言 tmp_path 之外的 .env 不含测试值）。
source: plan-06/T6f@2026-07-06
category: silent-failure
status: active
