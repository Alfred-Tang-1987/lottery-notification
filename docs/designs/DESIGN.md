# DESIGN.md — 兑奖了吗？设计系统

> 单一事实源。所有 Vue3 组件、prototype、深色模式都引用本文的 token。
> 从 9 页手写 prototype 反向提取（2026-06-21）。改动 token 改这里，9 页与实现同步。

## 1. 定位

**工具类 App UI（非营销落地页）**：密集信息、强可读性、最小 chrome、理性克制。契合 iOS-first（未来原生 App 复用）、家庭小圈子工具气质、合规红线（不张扬、不诱导）。

## 2. 色板（CSS 变量）

### 亮色 `:root`
| Token | 值 | 用途 |
|---|---|---|
| `--bg` | `#f5f5f7` | 页面底 |
| `--surface` | `#ffffff` | 卡片/面板 |
| `--fg` | `#1d1d1f` | 正文 |
| `--muted` | `#6e6e73` | 次要文字 |
| `--border` | `#d2d2d7` | 分隔线 |
| `--accent` | `#0071e3` | 主操作/链接 |
| `--accent-hover` | `#0077ed` | 悬停 |
| `--red-ball` | `#e11d2a` | 福彩红球/警告强调 |
| `--blue-ball` | `#0071e3` | 体彩蓝球/链接 |
| `--success` | `#059669` | 中奖/已领取/通过 |
| `--danger` | `#dc2626` | 过期/错误/临近过期 |
| `--warning` | `#d97706` | 待派奖/单源标黄 |

### 深色 `:root.dark`
| Token | 值 |
|---|---|
| `--bg` | `#000000` |
| `--surface` | `#1c1c1e` |
| `--fg` | `#f5f5f7` |
| `--muted` | `#98989d` |
| `--border` | `#38383a` |
| `--accent` | `#0a84ff` |
| `--red-ball` | `#ff453a` |
| `--blue-ball` | `#0a84ff` |
| `--success` | `#30d158` |
| `--danger` | `#ff453a` |

深色衍生：`--surface-2` `#2c2c2e`（次级面板/球底）、中奖行 `#1a3a1a`、今日日历 `#0a2540`。

### 语义规则
- **红蓝球语义化**：福彩红 `--red-ball`、体彩蓝 `--blue-ball`，不混用。红球勿用于"错误"（错误用 `--danger`），避免语义混淆。
- **公益金/金额强调用 `--red-ball`**（红色喜庆，中奖金额醒目）。
- 对比度：正文 `--fg`/`--bg` ≥ 4.5:1（AA），`--muted` 仅用于次要 meta 且 ≥ 4.5:1。

## 3. 字体

| Token | 栈 |
|---|---|
| `--font-display` | `-apple-system, BlinkMacSystemFont, 'SF Pro Display', 'Inter', system-ui, sans-serif` |
| `--font-body` | `-apple-system, BlinkMacSystemFont, 'SF Pro Text', 'Inter', system-ui, sans-serif` |
| `--font-mono` | `'SF Mono', 'JetBrains Mono', ui-monospace, monospace` |

**iOS-first 合理性**：SF Pro 栈与 Apple 系统一致、零网络加载、移动端 retina 清晰。工具类 App 用系统字是克制正确解，非"slop 放弃排版"。
**标题中文可选升级**：若需更"有设计感"，大标题（H1/dashboard 标题）可引入一款中文衬线/无衬线展示字（如思源黑体 Heavy），其余保持系统字。MVP 不强制。

## 4. 字号阶梯

| Token | 值 | 用途 |
|---|---|---|
| `--text-xs` | 11px | 徽章/标签/meta |
| `--text-sm` | 12px | 辅助说明 |
| `--text-base` | 13px | 正文（App UI 密集场景，移动 retina 可读） |
| `--text-md` | 14px | 重要正文/按钮 |
| `--text-lg` | 16px | 卡片标题 |
| `--text-xl` | 18px | 区块标题 |
| `--text-2xl` | 24px+ | 页面 H1 |

> A11y 注：正文 13px 是 App UI 密集场景的取舍（对标 iOS 设置 App）。**contrast 必须 AA**；老年用户路径（兑奖/统计）正文优先用 14-16px。

## 5. 间距 scale

`4 / 6 / 8 / 12 / 16 / 20 / 24 / 30 / 32` px。
- 卡片内边距：`12px 16px`（紧凑）/ `16px 20px`（标准）
- 区块间距：`20-24px`
- 球尺寸：28-32px，间距 6-8px

## 6. 圆角 / 阴影 / 动效

- **圆角**：统一 `12px`（卡片/球/按钮）。球为 50%。
- **阴影**：克制，仅悬浮/模态用 `0 2px 8px rgba(0,0,0,0.08)`。App UI 不堆装饰阴影（universal rule: 去掉装饰阴影仍应 premium）。
- **动效时长**：`--dur-fast` 0.1s、`--dur-base` 0.15s、`--dur-slow` 0.25s。缓动 `ease`。`transition: all 0.15s` 是默认。
- **动效纪律**：仅 `transform/opacity/background/border-color`，禁动 `width/height/top/left`（性能）。走势图遗漏/开奖球高亮用 `background`/`transform`。

## 7. 核心组件

- **号码球**：红/蓝圆（`--red-ball`/`--blue-ball`，白字），开出标实心圆，遗漏数字标 `--surface-2` 底。
- **卡片** `.card`：`--surface` 底、`--border` 1px、`12px` 圆角、`12px 16px` 内边距、`.card-title`（`--text-lg` 600）+ `.card-subtitle`（`--text-sm` `--muted`）。
- **导航**：桌面 240px sidebar（8 项）；**移动 375px 底部 tab bar（4 高频：仪表盘/号码/查询/我的）+ 其余入"更多"抽屉**。
- **状态组件** `<State>`：见 spec §12.4 交互状态系统。
- **走势确认门**：右侧抽屉（drawer），顶部确认句，展开选号面板——见 spec §9.3/§12.4。

## 8. 理性与合规视觉

- 全系统常驻"理性购彩 量力而行"，克制不张扬。
- 走势/统计强随机性声明常驻。
- emoji 仅功能性使用（🎉 中奖/📍 定位/🔔 提醒），不扩散为装饰。
- 不用紫渐变/3 列卡片墙/装饰 blob（AI slop 黑名单）。

## 9. A11y 基线（实现强制）

见 spec §12.4。要点：ARIA landmark（`<nav>/<main>/<aside>`）、交互元素一律 `<button>`（禁 `div onclick`）、`aria-label` 覆盖图标按钮、系统焦点环、触控靶 ≥44px、对比度 AA。
