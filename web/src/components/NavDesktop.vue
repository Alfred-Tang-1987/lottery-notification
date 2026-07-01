<script setup lang="ts">
import { computed } from 'vue';
import { useRoute } from 'vue-router';
import { useAuthStore } from '../stores/auth';

const route = useRoute();
const auth = useAuthStore();

const items = [
  { to: '/', label: '仪表盘', icon: '📊' },
  { to: '/numbers', label: '我的号码', icon: '🎫' },
  { to: '/query', label: '开奖查询', icon: '🔍' },
  { to: '/wins', label: '中奖记录', icon: '🏆' },
  { to: '/stats', label: '我的统计', icon: '📈' },
  { to: '/trend', label: '开奖走势', icon: '📉' },
  { to: '/settings', label: '设置', icon: '⚙️' },
  { to: '/admin', label: '后台管理', icon: '🛠', admin: true },
];

const visibleItems = computed(() =>
  items.filter((item) => !item.admin || auth.isAdmin)
);
</script>

<template>
  <aside class="sidebar" role="navigation" aria-label="主导航">
    <div class="brand">兑奖了吗？</div>
    <nav class="nav">
      <router-link
        v-for="item in visibleItems"
        :key="item.to"
        :to="item.to"
        class="nav-link"
        :aria-current="route.path === item.to ? 'page' : undefined"
      >
        <span class="nav-icon" aria-hidden="true">{{ item.icon }}</span>
        <span class="nav-label">{{ item.label }}</span>
      </router-link>
    </nav>
  </aside>
</template>

<style scoped>
.sidebar {
  width: 240px;
  min-height: 100vh;
  background: var(--surface);
  border-right: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  position: fixed;
  left: 0;
  top: 0;
  bottom: 0;
  z-index: 40;
}

.brand {
  height: 56px;
  display: flex;
  align-items: center;
  padding: 0 20px;
  font-size: var(--text-xl);
  font-weight: 600;
  border-bottom: 1px solid var(--border);
}

.nav {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 12px;
  flex: 1;
}

.nav-link {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 12px;
  border-radius: var(--radius);
  color: var(--fg);
  text-decoration: none;
  font-size: var(--text-md);
  min-height: 44px;
  transition: background-color var(--dur-base);
}

.nav-link:hover {
  background: var(--surface-2);
}

.nav-link[aria-current='page'] {
  background: var(--accent);
  color: #fff;
}

.nav-icon {
  font-size: var(--text-lg);
  line-height: 1;
}

.nav-label {
  line-height: 1;
}
</style>
