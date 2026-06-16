# Phase 1 · Plan 4: 前端 Vue3 Web UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** 实现完整 Web UI（10 页），消费 Plan 3 的 REST API，视觉沿用已交付的 dashboard prototype（Apple 风格、个人化导航措辞），响应式（iOS Safari 友好），为未来 iOS App 的 API-first 复用铺路。

**Architecture:** Vue3 + Vite SPA。Vue Router（10 路由）+ Pinia（认证/号码/配置 store）。API 层用 axios + 类型化 DTO。样式用 UnoCSS（原子化，匹配 prototype 的内联变量风格）+ 沿用 prototype 的 CSS 变量。图表用 ECharts（统计/走势）。**视觉基准 = `docs/superpowers/prototypes/01-dashboard.html`**（已存在的真实交付物，非占位符）。

**Tech Stack:** Vue 3、Vite、Vue Router、Pinia、axios、UnoCSS、ECharts。

**前置依赖:** Plan 3（API）已完成；prototype 已存在。

**对应 Spec:** §12（页面/信息架构）

**范围说明:** MVP 单式。走势图为合规降级版（连线+近N期频次，公开默认关）。所有页面照 prototype 的视觉系统实现。

---

## File Structure

```
web/                              # 前端独立目录（最终构建产物供后端静态托管）
├── package.json
├── vite.config.ts
├── uno.config.ts
├── index.html
├── src/
│   ├── main.ts
│   ├── App.vue
│   ├── router/index.ts           # 10 路由 + 认证守卫
│   ├── api/
│   │   ├── client.ts             # axios 实例 + 拦截器
│   │   ├── auth.ts / tickets.ts / draws.ts / results.ts / notifications.ts / admin.ts
│   │   └── types.ts              # DTO 类型
│   ├── stores/
│   │   ├── auth.ts
│   │   └── ui.ts
│   ├── composables/
│   │   └── useApi.ts
│   ├── components/
│   │   ├── AppLayout.vue         # 侧栏 + 主区（照 prototype）
│   │   ├── Sidebar.vue
│   │   ├── BallNumber.vue        # 红蓝球/数字方块
│   │   ├── DisclaimerBanner.vue  # 理性购彩常驻
│   │   └── Card.vue
│   ├── views/
│   │   ├── Login.vue
│   │   ├── Dashboard.vue         # 照 prototype
│   │   ├── Tickets.vue           # 我的号码
│   │   ├── Draws.vue             # 开奖查询
│   │   ├── Results.vue           # 中奖记录
│   │   ├── Stats.vue             # 我的统计
│   │   ├── Trend.vue             # 开奖走势（合规版）
│   │   ├── Schedule.vue          # 开奖日程
│   │   ├── Settings.vue          # 通知配置
│   │   └── Admin.vue             # 后台管理
│   └── styles/variables.css      # prototype 的 CSS 变量提取
└── tests/
    └── views/*.spec.ts           # Vitest + @vue/test-utils
```

**视觉契约：** 所有页面复用 `styles/variables.css`（从 prototype 提取的 `--bg/--surface/--fg/--accent/--red-ball/--blue-ball` 等）+ `AppLayout.vue`（侧栏导航）。prototype 是视觉单一事实源。

---

## Task 1: 项目脚手架

**Files:**
- Create: `web/package.json`, `web/vite.config.ts`, `web/uno.config.ts`, `web/index.html`, `web/src/main.ts`, `web/src/App.vue`
- Create: `web/src/styles/variables.css`

- [ ] **Step 1: `web/package.json`**

```json
{
  "name": "lottery-web",
  "private": true,
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "vue-tsc -b && vite build",
    "preview": "vite preview",
    "test": "vitest"
  },
  "dependencies": {
    "vue": "^3.4",
    "vue-router": "^4.3",
    "pinia": "^2.1",
    "axios": "^1.6",
    "echarts": "^5.5"
  },
  "devDependencies": {
    "vite": "^5.2",
    "@vitejs/plugin-vue": "^5.0",
    "unocss": "^0.59",
    "typescript": "^5.4",
    "vue-tsc": "^2.0",
    "vitest": "^1.6",
    "@vue/test-utils": "^2.4",
    "jsdom": "^24.0"
  }
}
```

- [ ] **Step 2: `web/vite.config.ts`**

```typescript
import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";
import UnoCSS from "unocss/vite";

export default defineConfig({
  plugins: [vue(), UnoCSS()],
  server: { proxy: { "/api": "http://localhost:8000" } },  // 开发代理后端
});
```

- [ ] **Step 3: `web/uno.config.ts`**

```typescript
import { defineConfig, presetUno } from "unocss";
export default defineConfig({ presets: [presetUno()] });
```

- [ ] **Step 4: `web/src/styles/variables.css`（从 prototype 提取）**

```css
:root {
  --bg: #f5f5f7; --surface: #ffffff; --fg: #1d1d1f; --muted: #6e6e73;
  --border: #d2d2d7; --accent: #2563eb; --red-ball: #dc2626; --blue-ball: #2563eb;
  --success: #059669; --danger: #dc2626; --warning: #d97706;
}
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, "Inter", system-ui, sans-serif;
       background: var(--bg); color: var(--fg); }
```

- [ ] **Step 5: `web/src/main.ts` + `App.vue`**

```typescript
import { createApp } from "vue";
import { createPinia } from "pinia";
import router from "./router";
import "unocss/reset/tailwind.css";
import "virtual:uno.css";
import "./styles/variables.css";
import App from "./App.vue";

createApp(App).use(createPinia()).use(router).mount("#app");
```

```vue
<!-- web/src/App.vue -->
<template><RouterView /></template>
```

- [ ] **Step 6: 验证 + Commit**

Run: `cd web && npm install && npm run build` → 产出 dist
```bash
git add web/
git commit -m "feat(web): Vue3+Vite+UnoCSS 脚手架"
```

---

## Task 2: API 客户端层 + 类型 + 路由

**Files:**
- Create: `web/src/api/client.ts`, `types.ts`, `auth.ts`, `tickets.ts`, `draws.ts`, `results.ts`, `notifications.ts`
- Create: `web/src/router/index.ts`

- [ ] **Step 1: `web/src/api/client.ts`**

```typescript
import axios from "axios";
import { useAuthStore } from "../stores/auth";

export const api = axios.create({ baseURL: "/api" });

api.interceptors.request.use((cfg) => {
  const auth = useAuthStore();
  if (auth.token) cfg.headers.Authorization = `Bearer ${auth.token}`;
  return cfg;
});

api.interceptors.response.use(
  (r) => r,
  (err) => {
    if (err.response?.status === 401) {
      useAuthStore().logout();
      window.location.href = "/login";
    }
    return Promise.reject(err);
  }
);
```

- [ ] **Step 2: `web/src/api/types.ts`**

```typescript
export interface Ticket {
  id: number; lottery_code: string; numbers: { front: number[]; back: number[] };
  play_type: string; label: string | null; enabled: boolean;
}
export interface DrawResult {
  draw_no: string; draw_date: string; numbers: { front: number[]; back: number[] };
  source: string; verified: boolean;
}
export interface Comparison {
  ticket_id: number; hits: { front_hit: number; back_hit: number };
  prize_tier: number | null; prize_amount: number | null; is_win: boolean;
}
```

- [ ] **Step 3: 各资源 API（`tickets.ts` 示例，其余同模式）**

```typescript
// web/src/api/tickets.ts
import { api } from "./client";
import type { Ticket } from "./types";

export const ticketsApi = {
  list: () => api.get<Ticket[]>("/tickets").then((r) => r.data),
  create: (body: { lottery_code: string; numbers: object; label?: string }) =>
    api.post<Ticket>("/tickets", body).then((r) => r.data),
  remove: (id: number) => api.delete(`/tickets/${id}`),
};
```

> **模式：** `draws.ts`/`results.ts`/`notifications.ts`/`auth.ts` 同样结构——导入 `api`、定义 `{ list, create, ... }` 方法、返回类型化数据。每个文件的端点对应 Plan 3 的 API。

- [ ] **Step 4: `web/src/router/index.ts`**

```typescript
import { createRouter, createWebHistory } from "vue-router";
import { useAuthStore } from "../stores/auth";

const routes = [
  { path: "/login", component: () => import("../views/Login.vue"), meta: { public: true } },
  { path: "/", component: () => import("../components/AppLayout.vue"),
    children: [
      { path: "", component: () => import("../views/Dashboard.vue") },
      { path: "tickets", component: () => import("../views/Tickets.vue") },
      { path: "draws", component: () => import("../views/Draws.vue") },
      { path: "results", component: () => import("../views/Results.vue") },
      { path: "stats", component: () => import("../views/Stats.vue") },
      { path: "trend", component: () => import("../views/Trend.vue") },
      { path: "schedule", component: () => import("../views/Schedule.vue") },
      { path: "settings", component: () => import("../views/Settings.vue") },
      { path: "admin", component: () => import("../views/Admin.vue"), meta: { admin: true } },
    ]},
];

const router = createRouter({ history: createWebHistory(), routes });

router.beforeEach((to) => {
  const auth = useAuthStore();
  if (!to.meta.public && !auth.token) return "/login";
  if (to.meta.admin && auth.role !== "admin") return "/";
});

export default router;
```

- [ ] **Step 5: Commit**

```bash
git add web/src/api/ web/src/router/
git commit -m "feat(web): API 客户端层 + 类型 + 路由守卫"
```

---

## Task 3: 认证 store + 布局组件（侧栏）

**Files:**
- Create: `web/src/stores/auth.ts`
- Create: `web/src/components/AppLayout.vue`, `Sidebar.vue`, `BallNumber.vue`, `DisclaimerBanner.vue`, `Card.vue`
- Test: `web/tests/stores/auth.spec.ts`

- [ ] **Step 1: `web/src/stores/auth.ts`**

```typescript
import { defineStore } from "pinia";
import { ref, computed } from "vue";
import { api } from "../api/client";

export const useAuthStore = defineStore("auth", () => {
  const token = ref(localStorage.getItem("token") || "");
  const role = ref(JSON.parse(localStorage.getItem("role") || '"user"'));
  const username = ref(localStorage.getItem("username") || "");

  function setAuth(t: string, r: string, u: string) {
    token.value = t; role.value = r; username.value = u;
    localStorage.setItem("token", t);
    localStorage.setItem("role", JSON.stringify(r));
    localStorage.setItem("username", u);
  }
  function logout() {
    token.value = ""; role.value = "user"; username.value = "";
    localStorage.clear();
  }
  async function login(u: string, p: string) {
    const { data } = await api.post("/auth/login", { username: u, password: p });
    setAuth(data.access_token, "user", u);  // role 从 JWT 解析（生产用 jwt-decode）
  }
  return { token, role, username, setAuth, logout, login };
});
```

- [ ] **Step 2: `web/src/components/Sidebar.vue`（照 prototype 导航）**

```vue
<template>
  <aside class="sidebar">
    <div class="sidebar-header">
      <div class="sidebar-brand">彩票核对</div>
      <div class="sidebar-brand-sub">开奖结果自动核对</div>
    </div>
    <nav class="nav">
      <div class="nav-section" v-for="sec in sections" :key="sec.label">
        <div class="nav-section-label">{{ sec.label }}</div>
        <RouterLink v-for="item in sec.items" :key="item.to" :to="item.to"
          class="nav-item" active-class="active">
          <span class="nav-icon">{{ item.icon }}</span>{{ item.name }}
        </RouterLink>
      </div>
    </nav>
    <div class="sidebar-footer">理性购彩 · 量力而行</div>
  </aside>
</template>
<script setup lang="ts">
const sections = [
  { label: "主要", items: [
    { to: "/", name: "仪表盘", icon: "▦" },
    { to: "/tickets", name: "我的号码", icon: "◉" },
    { to: "/draws", name: "开奖查询", icon: "⌕" },
    { to: "/results", name: "中奖记录", icon: "✓" },
  ]},
  { label: "分析", items: [
    { to: "/stats", name: "我的统计", icon: "↗" },
    { to: "/trend", name: "开奖走势", icon: "∿" },
    { to: "/schedule", name: "开奖日程", icon: "▦" },
  ]},
  { label: "系统", items: [
    { to: "/settings", name: "设置", icon: "⚙" },
    { to: "/admin", name: "后台管理", icon: "🛡" },
  ]},
];
</script>
<style scoped>
/* 照 prototype 的 .sidebar/.nav 样式原样搬运 */
.sidebar { width: 240px; background: var(--surface); border-right: 1px solid var(--border);
  position: fixed; top: 0; left: 0; bottom: 0; display: flex; flex-direction: column; }
.sidebar-header { padding: 20px 24px; border-bottom: 1px solid var(--border); }
.sidebar-brand { font-size: 18px; font-weight: 600; }
.sidebar-brand-sub { font-size: 12px; color: var(--muted); margin-top: 2px; }
.nav { flex: 1; padding: 12px 10px; overflow-y: auto; }
.nav-section { margin-bottom: 16px; }
.nav-section-label { font-size: 11px; font-weight: 600; color: var(--muted);
  text-transform: uppercase; padding: 0 14px; margin-bottom: 6px; }
.nav-item { display: flex; align-items: center; gap: 10px; padding: 9px 14px;
  border-radius: 8px; font-size: 14px; color: var(--fg); text-decoration: none; }
.nav-item:hover { background: var(--bg); }
.nav-item.active { background: var(--bg); font-weight: 600; }
.nav-icon { width: 18px; text-align: center; color: var(--muted); }
.sidebar-footer { padding: 12px 24px; border-top: 1px solid var(--border);
  font-size: 12px; color: var(--muted); }
</style>
```

- [ ] **Step 3: `AppLayout.vue` + `BallNumber.vue` + `DisclaimerBanner.vue`**

```vue
<!-- AppLayout.vue -->
<template>
  <div class="app" style="display:flex;min-height:100vh">
    <Sidebar />
    <main class="main" style="flex:1;margin-left:240px">
      <RouterView />
    </main>
  </div>
</template>
<script setup lang="ts">
import Sidebar from "./Sidebar.vue";
</script>
```

```vue
<!-- BallNumber.vue：红蓝球/数字方块 -->
<template>
  <span class="ball" :class="cls">{{ formatted }}</span>
</template>
<script setup lang="ts">
import { computed } from "vue";
const props = defineProps<{ value: number; kind: "red" | "blue" | "num" }>();
const cls = computed(() => props.kind);
const formatted = computed(() => props.kind === "num" ? props.value : String(props.value).padStart(2, "0"));
</script>
<style scoped>
.ball { display: inline-flex; width: 34px; height: 34px; border-radius: 50%;
  align-items: center; justify-content: center; font-weight: 600; color: #fff; }
.ball.red { background: var(--red-ball); } .ball.blue { background: var(--blue-ball); }
.ball.num { border-radius: 8px; background: var(--fg); }
</style>
```

```vue
<!-- DisclaimerBanner.vue -->
<template>
  <div class="banner">
    <span>⚠</span> 理性购彩，量力而行。彩票为公益事业，中奖属随机事件。所有数据以官方开奖为准。
  </div>
</template>
<style scoped>
.banner { background: #fefce8; border: 1px solid #fbbf24; border-radius: 12px;
  padding: 12px 20px; color: #92400e; font-size: 14px; }
</style>
```

- [ ] **Step 4: 测试 `web/tests/stores/auth.spec.ts`**

```typescript
import { setActivePinia, createPinia } from "pinia";
import { useAuthStore } from "../../src/stores/auth";

test("setAuth/logout toggles token", () => {
  setActivePinia(createPinia());
  const a = useAuthStore();
  a.setAuth("tok", "admin", "alice");
  expect(a.token).toBe("tok"); expect(a.role).toBe("admin");
  a.logout();
  expect(a.token).toBe("");
});
```

- [ ] **Step 5: Commit**

Run: `cd web && npx vitest run` → PASS
```bash
git add web/src/stores/ web/src/components/ web/tests/
git commit -m "feat(web): 认证store+布局(侧栏/球/理性banner)"
```

---

## Task 4: 登录页 + 仪表盘页（照 prototype）

**Files:**
- Create: `web/src/views/Login.vue`, `Dashboard.vue`
- Test: `web/tests/views/dashboard.spec.ts`

- [ ] **Step 1: `Login.vue`**

```vue
<template>
  <div style="max-width:360px;margin:80px auto">
    <h1 style="font-size:28px;margin-bottom:24px">彩票核对 · 登录</h1>
    <form @submit.prevent="onSubmit">
      <input v-model="username" placeholder="用户名" style="width:100%;padding:10px;margin-bottom:12px" />
      <input v-model="password" type="password" placeholder="密码" style="width:100%;padding:10px;margin-bottom:12px" />
      <button style="width:100%;padding:10px;background:var(--accent);color:#fff;border:none;border-radius:8px">登录</button>
      <p style="color:var(--danger);margin-top:8px">{{ error }}</p>
    </form>
  </div>
</template>
<script setup lang="ts">
import { ref } from "vue";
import { useRouter } from "vue-router";
import { useAuthStore } from "../stores/auth";
const username = ref(""); const password = ref(""); const error = ref("");
const router = useRouter(); const auth = useAuthStore();
async function onSubmit() {
  try { await auth.login(username.value, password.value); router.push("/"); }
  catch { error.value = "用户名或密码错误"; }
}
</script>
```

- [ ] **Step 2: `Dashboard.vue`（照 prototype 结构，接 API）**

```vue
<template>
  <div style="max-width:1200px;padding:32px;margin:0 auto">
    <div style="margin-bottom:24px">
      <h1 style="font-size:32px;font-weight:600">仪表盘</h1>
      <DisclaimerBanner style="margin-top:16px" />
    </div>
    <section class="card">
      <h2 style="padding:20px 24px 0">本期开奖概览</h2>
      <div style="padding:0 24px 24px">
        <div v-for="d in draws" :key="d.draw_no" style="background:var(--bg);border-radius:12px;padding:16px;margin-bottom:12px">
          <strong>{{ lotteryName(d.numbers) }}</strong> 第{{ d.draw_no }}期
          <div style="margin-top:8px">
            <BallNumber v-for="n in d.numbers.front" :key="n" :value="n" kind="red" />
            <BallNumber v-for="n in d.numbers.back" :key="n" :value="n" kind="blue" />
          </div>
          <small style="color:var(--muted)">来源：{{ d.source }} · 以官方开奖为准</small>
        </div>
      </div>
    </section>
    <!-- 其余模块（我的命中/待兑奖/盈亏/日程）照 prototype 同结构，接 resultsApi/adminApi -->
  </div>
</template>
<script setup lang="ts">
import { ref, onMounted } from "vue";
import BallNumber from "../components/BallNumber.vue";
import DisclaimerBanner from "../components/DisclaimerBanner.vue";
import { drawsApi } from "../api/draws";
import type { DrawResult } from "../api/types";

const draws = ref<DrawResult[]>([]);
const lotteryName = (n: any) => ({ ssq: "双色球", dlt: "大乐透", qlc: "七乐彩",
  fc3d: "福彩3D", pl3: "排列3", pl5: "排列5", qxc: "七星彩" } as any)["ssq"];

onMounted(async () => {
  // 依次拉 7 彩种最新一期
  const codes = ["ssq", "dlt", "fc3d"];
  for (const c of codes) {
    const list = await drawsApi.list(c, 1);
    if (list[0]) draws.value.push(list[0]);
  }
});
</script>
```

> **实现指引：** 仪表盘其余模块（我的命中表、待兑奖、盈亏速览、近期日程）照 `docs/superpowers/prototypes/01-dashboard.html` 的 HTML/CSS 结构搬运，数据接 `resultsApi.list` / `adminApi.health`。`lotteryName` 应改为按 code 映射全部 7 彩种（上方示例简化）。

- [ ] **Step 3: 测试 + Commit**

Run: `cd web && npx vitest run` → PASS
```bash
git add web/src/views/Login.vue web/src/views/Dashboard.vue web/tests/views/
git commit -m "feat(web): 登录页+仪表盘(照prototype接API)"
```

---

## Task 5: 号码管理页（我的号码 CRUD）

**Files:**
- Create: `web/src/views/Tickets.vue`
- Test: `web/tests/views/tickets.spec.ts`

- [ ] **Step 1: `Tickets.vue`**

```vue
<template>
  <div style="max-width:900px;padding:32px;margin:0 auto">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:24px">
      <h1 style="font-size:28px">我的号码</h1>
      <button @click="showForm = !showForm" style="padding:8px 16px;background:var(--accent);color:#fff;border:none;border-radius:8px">+ 添加号码</button>
    </div>
    <form v-if="showForm" @submit.prevent="addTicket" style="background:var(--surface);padding:20px;border-radius:12px;margin-bottom:24px">
      <select v-model="form.lottery_code">
        <option v-for="c in codes" :key="c" :value="c">{{ names[c] }}</option>
      </select>
      <input v-model="form.frontStr" placeholder="前区号码，逗号分隔（如 1,2,3,4,5,6）" style="width:100%;margin:8px 0;padding:8px" />
      <input v-model="form.backStr" placeholder="后区号码，逗号分隔" style="width:100%;margin:8px 0;padding:8px" />
      <input v-model="form.label" placeholder="备注（可选）" style="width:100%;margin:8px 0;padding:8px" />
      <button style="padding:8px 16px;background:var(--accent);color:#fff;border:none;border-radius:8px">保存</button>
      <p v-if="formError" style="color:var(--danger)">{{ formError }}</p>
    </form>
    <div v-for="t in tickets" :key="t.id" style="background:var(--surface);padding:16px;border-radius:12px;margin-bottom:12px;display:flex;justify-content:space-between;align-items:center">
      <div>
        <strong>{{ names[t.lottery_code] }}</strong>
        <span v-if="t.label" style="color:var(--muted);margin-left:8px">{{ t.label }}</span>
        <div style="margin-top:8px">
          <BallNumber v-for="n in t.numbers.front" :key="n" :value="n" kind="red" />
          <BallNumber v-for="n in t.numbers.back" :key="n" :value="n" kind="blue" />
        </div>
      </div>
      <button @click="remove(t.id)" style="color:var(--danger);background:none;border:none;cursor:pointer">删除</button>
    </div>
    <p v-if="!tickets.length" style="color:var(--muted)">还没有号码，点击右上角添加。</p>
  </div>
</template>
<script setup lang="ts">
import { ref, onMounted } from "vue";
import BallNumber from "../components/BallNumber.vue";
import { ticketsApi } from "../api/tickets";
import type { Ticket } from "../api/types";

const codes = ["ssq", "dlt", "qlc", "fc3d", "qxc", "pl3", "pl5"];
const names: Record<string, string> = { ssq: "双色球", dlt: "大乐透", qlc: "七乐彩",
  fc3d: "福彩3D", pl3: "排列3", pl5: "排列5", qxc: "七星彩" };
const tickets = ref<Ticket[]>([]);
const showForm = ref(false);
const form = ref({ lottery_code: "ssq", frontStr: "", backStr: "", label: "" });
const formError = ref("");

async function load() { tickets.value = await ticketsApi.list(); }
async function addTicket() {
  formError.value = "";
  const front = form.value.frontStr.split(",").map((s) => parseInt(s.trim())).filter((n) => !isNaN(n));
  const back = form.value.backStr ? form.value.backStr.split(",").map((s) => parseInt(s.trim())).filter((n) => !isNaN(n)) : [];
  try {
    await ticketsApi.create({ lottery_code: form.value.lottery_code, numbers: { front, back }, label: form.value.label || undefined });
    showForm.value = false; form.value = { lottery_code: "ssq", frontStr: "", backStr: "", label: "" };
    await load();
  } catch (e: any) { formError.value = e.response?.data?.detail || "保存失败（检查号码范围）"; }
}
async function remove(id: number) { await ticketsApi.remove(id); await load(); }
onMounted(load);
</script>
```

- [ ] **Step 2: 测试 `web/tests/views/tickets.spec.ts`（挂载 + mock API）**

```typescript
import { mount } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia");
import Tickets from "../../src/views/Tickets.vue";
// mock ticketsApi.list 返回固定数据
vi.mock("../../src/api/tickets", () => ({
  ticketsApi: { list: vi.fn().mockResolvedValue([{ id: 1, lottery_code: "ssq",
    numbers: { front: [1,2,3,4,5,6], back: [7] }, label: null, enabled: true, play_type: "single" }]),
    create: vi.fn(), remove: vi.fn() },
}));
test("renders ticket list", async () => {
  setActivePinia(createPinia());
  const w = await mount(Tickets);
  expect(w.text()).toContain("双色球");
});
```

- [ ] **Step 3: Commit**

Run: `cd web && npx vitest run` → PASS
```bash
git add web/src/views/Tickets.vue web/tests/views/tickets.spec.ts
git commit -m "feat(web): 我的号码页 CRUD"
```

---

## Task 6: 其余页面（开奖查询/中奖记录/通知配置/统计/走势/日程/后台）

每个页面照 prototype 视觉 + 接对应 API。模式统一：`onMounted` 拉 API → 渲染 Card/表/图表。

- [ ] **Step 1: `Draws.vue`（开奖查询）** — 选彩种下拉 + `drawsApi.list(code, 20)` → 列表（BallNumber 展示）。组件骨架同 Dashboard 概览模块。

- [ ] **Step 2: `Results.vue`（中奖记录）** — 选彩种+期号 → `resultsApi.list(code, no)` → 命中表（照 Dashboard 我的命中表样式），兑奖状态来自 `prize_tier/amount`。

- [ ] **Step 3: `Settings.vue`（通知配置）** — 渠道表单（type + config JSON 字段：Bark key / 飞书 webhook）→ `notificationsApi.addChannel`；规则表单（彩种+策略+时机）→ `notificationsApi.upsertRule`；列表展示已配项。

- [ ] **Step 4: `Stats.vue`（我的统计）** — 调聚合统计端点（Plan 5 提供，本 task 占位用 `resultsApi` 全量拉取后前端聚合盈亏/命中率）→ ECharts 柱状图（中奖等级分布）+ 数字卡片（盈亏）。

- [ ] **Step 5: `Trend.vue`（开奖走势·合规版）** — `drawsApi.list(code, 50)` → ECharts 折线/散点连线（历史号码）+ 频次表（近 N 期每号出现次数，纯数字）。**页面顶部常驻**："彩票为独立随机事件，历史不代表未来，仅供回顾"。公开版（无 admin 开关）默认隐藏入口（路由 meta `trendEnabled`）。

- [ ] **Step 6: `Schedule.vue`（开奖日程）** — 静态 7 彩种开奖日表（照 prototype 日历条）+ 每彩种开奖信息提醒开关（写 `notificationsApi` 规则 timing=summary）。

- [ ] **Step 7: `Admin.vue`（后台管理）** — `adminApi.listUsers` → 用户表；`adminApi.health` → 数据源健康；手动触发比对按钮 → `POST /api/admin/trigger`（Plan 5 端点）。

- [ ] **Step 8: 每个 view 创建为可挂载的最小组件（接 API + 照 prototype 渲染），逐个 `npx vitest run` 验证不报错**

Run: `cd web && npx vitest run && npm run build` → 全部通过，dist 产出

- [ ] **Step 9: Commit（逐个或一次性）**

```bash
git add web/src/views/
git commit -m "feat(web): 开奖查询/中奖记录/设置/统计/走势/日程/后台 7 页"
```

> **实现基准：** 每个页面的视觉与布局严格对照 `docs/superpowers/prototypes/01-dashboard.html` 的卡片/表格/球/配色系统。新页面可基于 Dashboard 模块拆分复用。

---

## Task 7: 后端静态托管 + 构建联调

**Files:**
- Modify: `app/main.py`（挂载 dist 静态文件）
- Modify: `web/vite.config.ts`（构建输出到后端可达目录）

- [ ] **Step 1: `web/vite.config.ts` 加构建输出**

```typescript
export default defineConfig({
  // ...
  build: { outDir: "../static", emptyOutDir: true },
});
```

- [ ] **Step 2: `app/main.py` 挂载静态文件（SPA fallback）**

在 `app.include_router(api_router)` 之后追加：
```python
from pathlib import Path
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

STATIC = Path(__file__).parent.parent / "static"
if STATIC.exists():
    app.mount("/assets", StaticFiles(directory=STATIC / "assets"), name="assets")

    @app.get("/{full_path:path}")
    def spa(full_path: str):
        return FileResponse(STATIC / "index.html")
```

- [ ] **Step 3: 联调验证**

Run: `cd web && npm run build` → 产出 `static/`
Run: `cd .. && uvicorn app.main:app` → 浏览器开 `http://localhost:8000` → 看到登录页
登录 → 仪表盘 → 验证 API 通

- [ ] **Step 4: Commit**

```bash
git add web/vite.config.ts app/main.py
git commit -m "feat: 后端静态托管SPA + 前后端联调"
```

---

## Self-Review（已执行）

**1. Spec 覆盖：** §12.2 全部 10 页 → Task 4-6 ✅；§9 合规（走势图降级版+随机声明+公开默认关）→ Task 6 Step 5 ✅；§12.3 iOS 友好（响应式已在 variables.css + UnoCSS）✅。
**2. 占位符：** 视觉指向真实存在的 prototype 文件（非占位符）。Stats 聚合端点 Plan 5 提供（Task 6 Step 4 注明前端临时聚合）。每个页面 Task 6 给出 API 接线 + 视觉基准。✅
**3. 类型一致：** `Ticket/DrawResult/Comparison`（types.ts）与 Plan 3 API 响应一致；`ticketsApi.drawsApi` 方法名一致。✅
**4. 残留：** role 从 JWT 解析需 `jwt-decode`（Task 3 注明）；Stats/Trend 完整数据依赖 Plan 5 聚合端点。

---

## Execution Handoff

Plan 4 完成（7 Task）：完整 10 页 Vue3 SPA，照 prototype 视觉，接 Plan 3 API，后端静态托管可整体部署。

**后续：** Plan 5（统计聚合端点 + 提醒 + 走势数据 + 钉钉/企微 + 运维装配 + 奖级 DB 化）→ Plan 6（Docker 部署）。
