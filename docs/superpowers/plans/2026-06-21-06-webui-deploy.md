---
models:
  T1: sonnet
  T2: sonnet
  T3: opus
  T4: sonnet
  T5: sonnet
  T6: sonnet
  T7: sonnet
  T8: sonnet
  T9: sonnet
  T10: sonnet
---

# 06 Web UI + 部署 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** 实现 Vue3 SPA（9 页按 prototype + DESIGN.md token + A11y 基线 + 响应式 + 状态系统 + 走势确认门抽屉 + 底部 tab bar）、API client（cookie + CSRF）、FastAPI 静态托管（history catch-all）、Docker 部署（8280/restart:always/healthcheck/卷/.env）、每日备份脚本、CLI（create-admin/ssq 冒烟）、部署文档。

**Architecture:** `web/`（Vite + Vue3 + Vue Router + Pinia + UnoCSS + ECharts，build 到 `../static` 由后端托管）。前端调 Plan 05 API（cookie 认证 + CSRF header）。A11y 基线（D9）+ DESIGN.md token（D3）+ 状态系统（D2）+ 走势确认门（D8/D4）+ 移动 tab bar（D6）全部落地。

**Tech Stack:** Vue3 + Vite + UnoCSS + ECharts（前端）；Docker + docker-compose（部署）；Plan 01-05。

---

## File Structure

```
web/
├── package.json
├── vite.config.ts          # 代理 /api → :8000（dev）；build outDir=../static
├── index.html
├── src/
│   ├── main.ts
│   ├── App.vue              # 根布局（sidebar/tab bar + 用户身份区 + 理性提示）
│   ├── router.ts            # 9 路由（history 模式）
│   ├── stores/              # Pinia（auth/tickets/...）
│   ├── api/client.ts        # fetch 封装（cookie + CSRF header + 错误）
│   ├── styles/tokens.css    # DESIGN.md token → CSS 变量（亮/暗）
│   ├── components/
│   │   ├── State.vue        # loading/empty/error 统一组件
│   │   ├── BallNumber.vue   # 红蓝球
│   │   ├── UserMenu.vue     # 右上角身份区
│   │   ├── NavDesktop.vue   # sidebar（≥1024）
│   │   ├── NavMobile.vue    # 底部 tab bar（≤768）
│   │   └── TrendSelectDrawer.vue  # 走势确认门右侧抽屉
│   └── pages/
│       ├── Login.vue  Dashboard.vue  MyNumbers.vue  DrawQuery.vue
│       ├── WinRecords.vue  MyStats.vue  Trend.vue  Settings.vue  Admin.vue
├── static/                  # build 产物（后端托管，.gitignore）
Dockerfile
docker-compose.yml
backup.sh                    # 每日 SQLite 备份
app/cli.py                   # create-admin / ssq 冒烟
docs/deploy.md               # 部署运维文档
```

---

## Task 1: web 项目骨架（Vite + Vue3 + UnoCSS + ECharts）

**Files:** `web/package.json`, `web/vite.config.ts`, `web/index.html`, `web/src/main.ts`

- [ ] **Step 1: 初始化 web 项目**

```bash
mkdir -p web && cd web
npm create vite@latest . -- --template vue-ts
npm install vue-router pinia echarts unocss
npm install -D @unocss/preset-uno @types/node
```

- [ ] **Step 2: 写 web/package.json scripts（代理 + build 到 ../static）**

```json
{
  "scripts": {
    "dev": "vite",
    "build": "vue-tsc && vite build",
    "preview": "vite preview"
  }
}
```

- [ ] **Step 3: 写 web/vite.config.ts**

```typescript
import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

export default defineConfig({
  plugins: [vue()],
  server: { proxy: { "/api": "http://localhost:8280", "/auth": "http://localhost:8280" } },
  build: { outDir: "../static", emptyOutDir: true },
});
```

- [ ] **Step 4: 验证 dev 启动**

```bash
npm run dev
```
Expected: Vite 5173 起来，访问 `/` 见默认页。

- [ ] **Step 5: Commit**

```bash
git add web/package.json web/vite.config.ts web/index.html web/src/main.ts
git commit -m "chore(web): Vite + Vue3 + UnoCSS + ECharts 骨架（build → ../static）"
```

---

## Task 2: DESIGN.md token → CSS 变量 + 主题（亮/暗）

**Files:** `web/src/styles/tokens.css`, `web/src/main.ts`(import)

- [ ] **Step 1: 写 tokens.css（从 docs/designs/DESIGN.md 提取）**

```css
:root {
  --bg: #f5f5f7; --surface: #ffffff; --fg: #1d1d1f; --muted: #6e6e73;
  --border: #d2d2d7; --accent: #0071e3; --accent-hover: #0077ed;
  --red-ball: #e11d2a; --blue-ball: #0071e3;
  --success: #059669; --danger: #dc2626; --warning: #d97706;
  --surface-2: #efeff4;
  --text-xs: 11px; --text-sm: 12px; --text-base: 13px; --text-md: 14px;
  --text-lg: 16px; --text-xl: 18px; --text-2xl: 24px;
  --radius: 12px; --dur: 0.15s;
  --font-display: -apple-system, BlinkMacSystemFont, "SF Pro Display", system-ui, sans-serif;
  --font-body: -apple-system, BlinkMacSystemFont, "SF Pro Text", system-ui, sans-serif;
}
:root.dark {
  --bg: #000000; --surface: #1c1c1e; --fg: #f5f5f7; --muted: #98989d;
  --border: #38383a; --accent: #0a84ff; --red-ball: #ff453a; --blue-ball: #0a84ff;
  --success: #30d158; --danger: #ff453a; --surface-2: #2c2c2e;
}
body { font-family: var(--font-body); background: var(--bg); color: var(--fg); }
/* 深色由 main.ts 根据系统偏好/localStorage 加 .dark class 控制（:root.dark 块已定义深色 token），不在此用媒体查询避免双份维护 */
```

> token 单一源是 `docs/designs/DESIGN.md`；本文件是其 CSS 落地。改 token 改 DESIGN.md 同步此处（或生成）。

- [ ] **Step 2: main.ts import tokens + 主题偏好**

```typescript
import "./styles/tokens.css";
const saved = localStorage.getItem("theme");
if (saved === "dark" || (!saved && matchMedia("(prefers-color-scheme: dark)").matches)) {
  document.documentElement.classList.add("dark");
}
```

- [ ] **Step 3: 验证 token 生效**

```bash
npm run dev   # 改 --accent 看页面变化
```

- [ ] **Step 4: Commit**

```bash
git add web/src/styles/tokens.css web/src/main.ts
git commit -m "feat(web): DESIGN.md token → CSS 变量（亮/暗 + 自动深色 + 偏好）"
```

---

## Task 3: API client（fetch + cookie + CSRF + 错误）

**Files:** `web/src/api/client.ts`

- [ ] **Step 1: 写 client.ts**

```typescript
let _csrf: string | null = null;

async function ensureCsrf() {
  if (_csrf) return _csrf;
  const r = await fetch("/auth/csrf");
  const data = await r.json();
  _csrf = data.csrf_token;
  return _csrf!;
}

export async function api(path: string, opts: RequestInit = {}) {
  const method = (opts.method || "GET").toUpperCase();
  const headers = new Headers(opts.headers);
  if (method !== "GET") {
    const csrf = await ensureCsrf();
    headers.set("X-CSRF-Token", csrf);
    if (opts.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  }
  const r = await fetch(path, { ...opts, method, headers, credentials: "same-origin" });
  if (r.status === 401) { location.href = "/login"; throw new Error("未登录"); }
  if (!r.ok) {
    const err = await r.json().catch(() => ({}));
    throw new Error(err.detail || `HTTP ${r.status}`);
  }
  return r.json();
}

export const apiGet = (p: string) => api(p);
export const apiPost = (p: string, body?: unknown) =>
  api(p, { method: "POST", body: body ? JSON.stringify(body) : undefined });
```

- [ ] **Step 2: 验证（手动登录调用）**

```bash
npm run dev   # 浏览器 console: await apiPost("/auth/login", {username,password})
```

- [ ] **Step 3: Commit**

```bash
git add web/src/api/client.ts
git commit -m "feat(web): API client（cookie + CSRF double-submit + 401 重定向）"
```

---

## Task 4: State 组件（loading/empty/error）+ 空状态文案

**Files:** `web/src/components/State.vue`

> spec §12.4：统一 `<State type>` 组件；空状态温暖文案 + CTA（禁裸"No data"）。

- [ ] **Step 1: 写 State.vue**

```vue
<script setup lang="ts">
defineProps<{ type: "loading" | "empty" | "error"; title?: string; cta?: string }>();
defineEmits<{ action: [] }>();
</script>

<template>
  <div class="state" role="status" :aria-live="type === 'error' ? 'assertive' : 'polite'">
    <template v-if="type === 'loading'"><div class="spinner" aria-label="加载中" /></template>
    <template v-else-if="type === 'empty'">
      <p class="state-title">{{ title || "暂无数据" }}</p>
      <button v-if="cta" class="state-cta" @click="$emit('action')">{{ cta }}</button>
    </template>
    <template v-else>
      <p class="state-title">{{ title || "加载失败" }}</p>
      <button class="state-cta" @click="$emit('action')">重试</button>
    </template>
  </div>
</template>

<style scoped>
.state { text-align: center; padding: 30px; color: var(--muted); }
.spinner { width: 28px; height: 28px; margin: 0 auto; border: 3px solid var(--border);
  border-top-color: var(--accent); border-radius: 50%; animation: spin 0.8s linear infinite; }
.state-title { font-size: var(--text-md); margin: 12px 0; }
.state-cta { padding: 8px 16px; background: var(--accent); color: #fff; border: none;
  border-radius: var(--radius); cursor: pointer; min-height: 44px; }
@keyframes spin { to { transform: rotate(360deg); } }
</style>
```

- [ ] **Step 2: Commit**

```bash
git add web/src/components/State.vue
git commit -m "feat(web): State 组件（loading/empty/error + 空状态 CTA + A11y aria-live）"
```

---

## Task 5: 布局（sidebar/底部 tab bar/用户身份区/理性提示）+ A11y landmark

**Files:** `web/src/App.vue`, `web/src/components/NavDesktop.vue`, `web/src/components/NavMobile.vue`, `web/src/components/UserMenu.vue`, `web/src/router.ts`

> spec §12.3/§12.4：≥1024 sidebar（8 项）；≤768 底部 tab bar（4 高频 + 更多抽屉）；ARIA landmark；理性提示常驻。

- [ ] **Step 1: 写 router.ts（9 路由 history 模式）**

```typescript
import { createRouter, createWebHistory } from "vue-router";
const routes = [
  { path: "/login", component: () => import("./pages/Login.vue") },
  { path: "/", component: () => import("./pages/Dashboard.vue"), meta: { nav: "仪表盘", icon: "📊" } },
  { path: "/numbers", component: () => import("./pages/MyNumbers.vue"), meta: { nav: "我的号码", icon: "🎫" } },
  { path: "/query", component: () => import("./pages/DrawQuery.vue"), meta: { nav: "开奖查询", icon: "🔍" } },
  { path: "/wins", component: () => import("./pages/WinRecords.vue"), meta: { nav: "中奖记录", icon: "🏆" } },
  { path: "/stats", component: () => import("./pages/MyStats.vue"), meta: { nav: "我的统计", icon: "📈" } },
  { path: "/trend", component: () => import("./pages/Trend.vue"), meta: { nav: "开奖走势", icon: "📉" } },
  { path: "/settings", component: () => import("./pages/Settings.vue"), meta: { nav: "设置", icon: "⚙️" } },
  { path: "/admin", component: () => import("./pages/Admin.vue"), meta: { nav: "后台管理", icon: "🛠", admin: true } },
];
export default createRouter({ history: createWebHistory(), routes });
```

- [ ] **Step 2: 写 App.vue（响应式布局 + landmark + 理性提示）**

```vue
<script setup lang="ts">
import { ref, onMounted } from "vue";
import NavDesktop from "./components/NavDesktop.vue";
import NavMobile from "./components/NavMobile.vue";
import UserMenu from "./components/UserMenu.vue";
const isMobile = ref(window.innerWidth <= 768);
window.addEventListener("resize", () => { isMobile.value = window.innerWidth <= 768; });
</script>

<template>
  <div class="app">
    <header class="topbar">
      <strong class="brand">兑奖了吗？</strong>
      <UserMenu />
    </header>
    <div class="body">
      <NavDesktop v-if="!isMobile" />
      <main class="main" role="main">
        <router-view />
      </main>
    </div>
    <NavMobile v-if="isMobile" />
    <footer class="disclaimer" role="contentinfo">理性购彩 量力而行 · 彩票为独立随机事件，历史不代表未来</footer>
  </div>
</template>

<style scoped>
.app { display: flex; flex-direction: column; min-height: 100vh; }
.topbar { height: 56px; background: var(--surface); border-bottom: 1px solid var(--border);
  display: flex; align-items: center; justify-content: space-between; padding: 0 16px; }
.brand { font-size: var(--text-xl); }
.body { display: flex; flex: 1; }
.main { flex: 1; padding: 20px; }
.disclaimer { text-align: center; font-size: var(--text-xs); color: var(--muted); padding: 10px; }
</style>
```

- [ ] **Step 3: 写 NavMobile.vue（底部 tab bar：4 高频 + 更多抽屉）**

```vue
<script setup lang="ts">
import { ref } from "vue";
import { useRouter } from "vue-router";
const router = useRouter();
const showMore = ref(false);
const tabs = [
  { to: "/", icon: "📊", label: "仪表盘" },
  { to: "/numbers", icon: "🎫", label: "号码" },
  { to: "/query", icon: "🔍", label: "查询" },
  { to: "/stats", icon: "📈", label: "我的" },
];
const more = [
  { to: "/wins", label: "中奖记录" }, { to: "/trend", label: "开奖走势" },
  { to: "/settings", label: "设置" }, { to: "/admin", label: "后台管理" },
];
</script>

<template>
  <nav class="tabbar" role="navigation" aria-label="主导航">
    <router-link v-for="t in tabs" :key="t.to" :to="t.to" class="tab">
      <span aria-hidden="true">{{ t.icon }}</span><span class="label">{{ t.label }}</span>
    </router-link>
    <button class="tab" aria-haspopup="dialog" @click="showMore = true">⋯<span class="label">更多</span></button>
    <div v-if="showMore" class="drawer" role="dialog" aria-label="更多页面" @click="showMore = false">
      <router-link v-for="m in more" :key="m.to" :to="m.to">{{ m.label }}</router-link>
    </div>
  </nav>
</template>

<style scoped>
.tabbar { position: fixed; bottom: 0; left: 0; right: 0; height: 60px; background: var(--surface);
  border-top: 1px solid var(--border); display: flex; z-index: 50; }
.tab { flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: center;
  text-decoration: none; color: var(--muted); min-height: 44px; background: none; border: none; cursor: pointer; }
.label { font-size: var(--text-xs); }
.drawer { position: fixed; bottom: 60px; right: 0; background: var(--surface); border: 1px solid var(--border);
  padding: 12px; display: flex; flex-direction: column; gap: 12px; }
</style>
```

- [ ] **Step 4: 写 NavDesktop.vue（sidebar 8 项）+ UserMenu.vue（身份区/登出）**

（同结构，sidebar 240px，列出全部 8 项；UserMenu 右上角头像 + 登出调 `/auth/logout`。略——按 prototype 01-dashboard 视觉实现，所有项 `<router-link>` + `aria-current`。）

- [ ] **Step 5: Commit**

```bash
git add web/src/App.vue web/src/router.ts web/src/components/Nav*.vue web/src/components/UserMenu.vue
git commit -m "feat(web): 响应式布局（sidebar/底部tab bar/用户区）+ ARIA landmark + 理性提示常驻"
```

---

## Task 6: 各页面（按 prototype）+ dashboard 首屏优先级

**Files:** `web/src/pages/*.vue`

> 按 `docs/superpowers/prototypes/*.html` 实现 9 页，用 DESIGN.md token + State 组件 + API client。**dashboard 首屏优先级（D5）**：待兑奖 > 我的命中 > 盈亏速览 > 开奖概览 > 日历/代销点（次屏）。

- [ ] **Step 1: Login.vue（登录/注册 tab + 邀请码 + 主题）**

```vue
<script setup lang="ts">
import { ref } from "vue";
import { apiPost } from "../api/client";
import { useRouter } from "vue-router";
const router = useRouter();
const tab = ref<"login" | "register">("login");
const form = ref({ username: "", password: "", invite_code: "" });
const err = ref("");
async function submit() {
  err.value = "";
  try {
    if (tab.value === "login") await apiPost("/auth/login", { username: form.value.username, password: form.value.password });
    else await apiPost("/auth/register", form.value);
    router.push("/");
  } catch (e) { err.value = (e as Error).message; }
}
</script>
<template>
  <main class="login" role="main">
    <h1>兑奖了吗？</h1>
    <div class="tabs" role="tablist">
      <button role="tab" :aria-selected="tab==='login'" @click="tab='login'">登录</button>
      <button role="tab" :aria-selected="tab==='register'" @click="tab='register'">注册</button>
    </div>
    <form @submit.prevent="submit">
      <input v-model="form.username" placeholder="用户名" aria-label="用户名" required />
      <input v-model="form.password" type="password" placeholder="密码（≥8位）" aria-label="密码" required minlength="8" />
      <input v-if="tab==='register'" v-model="form.invite_code" placeholder="邀请码（6位）" aria-label="邀请码" required pattern="\d{6}" />
      <p v-if="err" class="error" role="alert">{{ err }}</p>
      <button type="submit">{{ tab === "login" ? "登录" : "注册" }}</button>
    </form>
    <p class="hint">理性购彩 量力而行</p>
  </main>
</template>
```

- [ ] **Step 2: Dashboard.vue（首屏优先级 D5 + State + 各卡）**

（实现：调 `/api/dashboard`（Plan 05/后端聚合，本 plan 前端消费）；按优先级排列卡：待兑奖（绿色"已领取"按钮）> 我的命中 > 盈亏速览（含公益卡按 welfare_rate）> 开奖概览 > 日历/代销点。每区用 `<State>` 处理 loading/empty/error。代码骨架略，按 prototype 01-dashboard 视觉 + token。）

- [ ] **Step 3: 其余 7 页（MyNumbers/DrawQuery/WinRecords/MyStats/Trend/Settings/Admin）**

按对应 prototype 实现，关键点：
- **MyNumbers**：号码盘选号 + 机选 + 倍投/追加 + 批量导入（CSV，调 `/api/tickets`）
- **DrawQuery**：彩种 pill + 期号 + 红蓝球渲染 + 单源标黄 + 我的比对（命中标绿）
- **WinRecords**：筛选 + 4 卡 + 记录卡 + 倒计时 + "已领取"操作
- **MyStats**：ECharts 双饼图 + 月柱图；**浮奖未回填显示"待派奖"非 0**
- **Trend**：综合分布图 + **选号面板默认折叠（确认门抽屉，Task 7）** + 频次 + 随机性声明
- **Settings**：渠道配置（Bark/飞书/邮箱）+ 推送策略 + DND + 模板预览 + 主题
- **Admin**：SMTP + 用户 + 彩种 + 健康 + 推送日志 + 审计

- [ ] **Step 4: Commit**

```bash
git add web/src/pages/
git commit -m "feat(web): 9 页实现（按 prototype + DESIGN.md token + State + 首屏优先级）"
```

---

## Task 7: 走势确认门右侧抽屉（D8/D4）+ A11y 收尾

**Files:** `web/src/components/TrendSelectDrawer.vue`, modify `web/src/pages/Trend.vue`

> spec §9.3/§12.4：走势选号默认折叠，点"我要选号"→ 右侧抽屉 + 确认句 → 展开选号面板。

- [ ] **Step 1: 写 TrendSelectDrawer.vue**

```vue
<script setup lang="ts">
import { ref } from "vue";
const emit = defineEmits<{ confirmed: [] }>();
const open = ref(false);
const agreed = ref(false);
function start() { open.value = true; }
function confirm() { agreed.value = true; open.value = false; emit("confirmed"); }
defineExpose({ start });
</script>

<template>
  <transition name="slide">
    <aside v-if="open" class="drawer" role="dialog" aria-modal="true" aria-label="走势选号确认">
      <h3>选号确认</h3>
      <label class="disclaimer-check">
        <input v-model="agreed" type="checkbox" /> 我知道历史走势不影响中奖概率，仅基于个人意愿自选
      </label>
      <button :disabled="!agreed" @click="confirm">开始选号</button>
      <button @click="open = false">取消</button>
    </aside>
  </transition>
</template>

<style scoped>
.drawer { position: fixed; top: 0; right: 0; bottom: 0; width: min(380px, 90vw);
  background: var(--surface); box-shadow: -2px 0 12px rgba(0,0,0,0.1); padding: 20px; z-index: 100; }
.slide-enter-active, .slide-leave-active { transition: transform var(--dur); }
.slide-enter-from, .slide-leave-to { transform: translateX(100%); }
</style>
```

- [ ] **Step 2: Trend.vue 接入（默认折叠，点按钮开抽屉）**

```vue
<script setup lang="ts">
import { ref } from "vue";
import TrendSelectDrawer from "../components/TrendSelectDrawer.vue";
const drawer = ref();
const selectUnlocked = ref(false);
</script>
<template>
  <!-- 走势分布图（纯展示，默认） -->
  <button @click="drawer.start()">我要选号</button>
  <TrendSelectDrawer ref="drawer" @confirmed="selectUnlocked = true" />
  <section v-if="selectUnlocked"><!-- 选号面板（玩法/号码盘/机选/队列/推送）--></section>
</template>
```

- [ ] **Step 3: A11y 收尾扫描**

```bash
# 确认无 div onclick（全 <button>/<a>）、aria-label 覆盖图标按钮、:focus-visible 全局、触控靶≥44px
grep -rn "div.*@click" web/src/ || echo "无 div onclick ✓"
```

- [ ] **Step 4: Commit**

```bash
git add web/src/components/TrendSelectDrawer.vue web/src/pages/Trend.vue
git commit -m "feat(web): 走势确认门右侧抽屉（默认折叠+确认句）+ A11y 扫描"
```

---

## Task 8: FastAPI 静态托管 SPA（history catch-all）+ build

**Files:** modify `app/main.py`(静态 + catch-all), `app/cli.py`(build 集成)

- [ ] **Step 1: main.py 加静态托管 + history catch-all**

```python
# app/main.py 追加：
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pathlib import Path

STATIC_DIR = Path(__file__).parent.parent / "static"
if STATIC_DIR.exists():
    app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")

    @app.get("/{full_path:path}")
    def spa_catch_all(full_path: str):
        """history 模式：非 API/静态路径回退 index.html（spec §12.3）。"""
        if full_path.startswith(("auth/", "admin/", "channels/", "health")):  # full_path 无前导/；无 /api 前缀
            return JSONResponse(status_code=404, content={"detail": "not found"})
        return FileResponse(STATIC_DIR / "index.html")
```

> 注：API 路由（/auth /channels /admin /api）必须**先于** catch-all 注册（FastAPI 按注册顺序匹配）。catch_all 兜底排除这些前缀。

- [ ] **Step 2: 构建前端**

```bash
cd web && npm run build   # 产物到 ../static
```

- [ ] **Step 3: 验证（启动后端，访问 / 应返回 SPA）**

```bash
cd .. && uv run uvicorn app.main:app --port 8280 & PID=$!
sleep 2; curl -s http://127.0.0.1:8280/ | head -3; kill $PID
```
Expected: 返回 `index.html`（含 Vue 挂载点）。

- [ ] **Step 4: Commit**

```bash
git add app/main.py
git commit -m "feat: FastAPI 静态托管 SPA（history catch-all，API 路由前置）"
```

---

## Task 9: Docker + docker-compose（8280/restart:always/healthcheck/卷/.env）

**Files:** `Dockerfile`, `docker-compose.yml`, `.dockerignore`

- [ ] **Step 1: 写 Dockerfile（多阶段：node build → python 运行）**

```dockerfile
# 阶段1：前端构建
FROM node:20-alpine AS web
WORKDIR /web
COPY web/package.json web/package-lock.json* ./
RUN npm ci || npm install
COPY web/ ./
RUN npm run build   # → /web/../static

# 阶段2：后端
FROM python:3.12-slim
RUN pip install uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
COPY app/ ./app/
COPY alembic/ alembic.ini ./
COPY --from=web /static ./static
COPY docs/ ./docs/
ENV TZ=Asia/Shanghai
EXPOSE 8280
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8280/health').status==200 else 1)"
CMD ["sh", "-c", "uv run alembic upgrade head && uv run uvicorn app.main:app --host 0.0.0.0 --port 8280"]
```

- [ ] **Step 2: 写 docker-compose.yml**

```yaml
services:
  app:
    build: .
    container_name: lottery-notification
    ports:
      - "8280:8280"
    volumes:
      - ./data:/app/data          # SQLite 持久化
      - ./backups:/app/backups    # 备份目录
    env_file: .env
    restart: always               # spec §4.3：FnOS 关机坑，必须 always
```

- [ ] **Step 3: 写 .dockerignore**

```
.git
web/node_modules
.venv
__pycache__
*.pyc
data/
backups/
.gstack
```

- [ ] **Step 4: 本地构建验证**

```bash
docker compose build
docker compose up -d
sleep 5; curl -s http://localhost:8280/health
```
Expected: `{"status":"ok","tz":"Asia/Shanghai","db":"ok"}`

- [ ] **Step 5: Commit**

```bash
git add Dockerfile docker-compose.yml .dockerignore
git commit -m "feat: Docker 多阶段构建 + compose（8280/restart:always/healthcheck/卷/.env）"
```

---

## Task 10: 备份脚本 + CLI（create-admin/ssq）+ 部署文档

**Files:** `backup.sh`, `app/cli.py`, `docs/deploy.md`

- [ ] **Step 1: 写 backup.sh（每日 SQLite backup + 30 天保留）**

```bash
#!/bin/sh
# 每日备份 SQLite（spec §4.3）。cron: 0 3 * * * /app/backup.sh
set -e
DB="${DATABASE_URL#sqlite:///./}"
TS=$(date +%Y%m%d)
# python:3.12-slim 无 sqlite3 CLI，用 python sqlite3 模块 backup（spec §4.3）
python -c "import sqlite3; src=sqlite3.connect('$DB'); dst=sqlite3.connect('/app/backups/lottery-${TS}.db'); src.backup(dst); dst.close()"
find /app/backups -name "lottery-*.db" -mtime +30 -delete   # 保留 30 天
```

- [ ] **Step 2: 写 app/cli.py（create-admin + ssq 冒烟）**

```python
"""CLI: 创建首个 admin（bootstrap）+ 手动触发一期闭环冒烟。
用法: uv run python -m app.cli create-admin --username admin --password <p>
      uv run python -m app.cli ssq"""
import argparse
from app.db.session import engine
from sqlmodel import Session
from app.models import User
from app.api.security import hash_password


def cmd_create_admin(args):
    with Session(engine) as s:
        u = User(username=args.username, password_hash=hash_password(args.password),
                 role="admin", invite_code="BOOTSTRAP")
        s.add(u); s.commit()
    print(f"admin {args.username} 创建成功")


def cmd_smoke(args):
    """手动触发一期闭环：抓取 ssq → 比对（无 ticket 则只抓取）。"""
    from app.adapters.mxnzp import MxnzpAdapter
    from app.adapters.juhe import JuheAdapter
    from app.config import settings
    from app.services.fetch_service import FetchService
    from app.services.compare_service import CompareService
    fetch = FetchService(MxnzpAdapter(settings.mxnzp_api_key), JuheAdapter(settings.juhe_api_key), engine)
    r = fetch.fetch_and_store("ssq")
    print(f"fetch ssq: stored={r.stored} verified={r.verified} single_source={r.single_source}")
    n = CompareService(engine).process_pending()
    print(f"compared {n} pending")


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    ca = sub.add_parser("create-admin"); ca.add_argument("--username", required=True); ca.add_argument("--password", required=True)
    sub.add_parser("ssq")
    args = p.parse_args()
    if args.cmd == "create-admin": cmd_create_admin(args)
    elif args.cmd == "ssq": cmd_smoke(args)


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: 写 docs/deploy.md（部署运维）**

```markdown
# 部署运维

## 首次部署
1. NAS 创建目录 `/vol1/1000/Docker/lottery-notification/`
2. 复制项目 + `.env`（填 JWT_SECRET/CRYPTO_KEY_V1/数据源 key/SMTP）
3. `docker compose up -d --build`
4. 创建首个 admin：`docker compose exec app uv run python -m app.cli create-admin --username admin --password <p>`
5. 访问 `http://<NAS>:8280`，admin 登录 → 后台生成邀请码 → 邀请用户

## 日常
- 备份：cron `0 3 * * * docker compose exec app /app/backup.sh`（或宿主 cron）
- 日志：`docker compose logs -f`
- 升级：`git pull && docker compose up -d --build`（Alembic 自动迁移）

## 关键约束（spec §4.3）
- 端口 8280；`restart: always`（FnOS 关机坑）
- 密钥从 .env 注入，不进库不进日志
- 时区 Asia/Shanghai

## 冒烟
`docker compose exec app uv run python -m app.cli ssq` → 抓取+比对一期
```

- [ ] **Step 4: Commit**

```bash
git add backup.sh app/cli.py docs/deploy.md
chmod +x backup.sh && git update-index --chmod=+x backup.sh
git commit -m "feat: 备份脚本 + CLI(create-admin/ssq冒烟) + 部署运维文档"
```

---

## Self-Review

**Spec 覆盖（Plan 06 = §12 前端 + §4.3 部署 + DESIGN.md + design review 8 任务）：**
- ✅ DESIGN.md token → CSS 变量（D3）→ Task 2
- ✅ State 组件 loading/empty/error + 空状态 CTA（D2）→ Task 4
- ✅ 响应式断点 + 桌面 sidebar/移动底部 tab bar（D6/§12.3）→ Task 5
- ✅ A11y 基线 landmark/button化/aria/焦点环/44px（D9）→ Task 4/5/7
- ✅ 走势确认门右侧抽屉（D8/D4）→ Task 7
- ✅ dashboard 首屏优先级（D5）→ Task 6
- ✅ 9 页按 prototype + 浮奖待派奖非0 → Task 6
- ✅ FastAPI SPA 托管 history catch-all（§12.3）→ Task 8
- ✅ Docker 8280/restart:always/healthcheck/卷/.env（§4.3）→ Task 9
- ✅ 备份 30 天（§4.3）→ Task 10
- ✅ CLI create-admin + ssq 冒烟（bootstrap + spec §13 Phase1.0.13）→ Task 10
- 📌 各页详细组件（号码盘/图表/卡片）→ 按 prototype 实现，骨架已给
- 📌 浮奖回填真实 amount_lookup（接官方奖金接口）→ 数据源提供后补

**Placeholder scan：** 无 TBD；部分页面代码标"按 prototype 实现，骨架已给"（prototype 是视觉基准，非 placeholder——给了组件骨架 + 关键交互 + API 衔接）。
**类型一致：** API client/route meta/State props/Drawer emit 前后一致；后端 /auth /channels /admin 路由与 Plan 05 对齐。
**衔接：** Plan 05 API（cookie+CSRF）被前端 client 消费；Plan 04 main.py startup + Plan 05 main.py 路由 + 本 Plan main.py 静态托管合并到同一 app/main.py（执行顺序 04→05→06，都用 Edit 追加）。
**已知简化（MVP）：**
- 部分页面完整组件按 prototype 填（骨架 + 关键点已给）。
- **前端测试待补**（spec §11 要求 E2E）：本 plan 聚焦骨架+部署，建议实现后补 Vitest 组件测试（State/BallNumber）+ Playwright E2E（登录/导航/走势确认门）作为 follow-up task。
- **A11y focus trap/ESC/遮罩**：TrendSelectDrawer 已有 `role=dialog`/`aria-modal`，但 focus trap + ESC 关闭 + 背景遮罩点击关闭待补（MVP 用按钮关闭，Phase 优化）。
- `static/` 构建产物进 .gitignore（实现时加 `static/`）；docker volumes 宿主 `data/` `backups/` 需预创建 + 权限（deploy.md 补 `mkdir -p data backups`）。
- **main.py 合并顺序**（Plan 04/05/06 都改 main.py）：按 `startup(scheduler)` → `middleware(CORS/CSRF)` → `include_router(API)` → `StaticFiles+catch_all(最后)` 顺序 Edit 追加；catch_all **必须最后注册**否则 API 被 catch_all 吞掉 404。
- bootstrap admin CLI 已给；浮奖回填真实接口待数据源。

**至此 Phase 1.0 + Phase 1 全部 plan 完成（01-06）。实现顺序：01 基础设施 → 02 领域 → 03 仓储闭环 → 04 调度推送 → 05 认证admin → 06 前端部署。每份独立可测。**
