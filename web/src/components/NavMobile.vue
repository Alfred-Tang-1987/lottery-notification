<script setup lang="ts">
import { ref, computed } from 'vue';
import { useRoute } from 'vue-router';
import { useAuthStore } from '../stores/auth';

const route = useRoute();
const auth = useAuthStore();
const showMore = ref(false);

const tabs = [
  { to: '/', label: '仪表盘', icon: '📊' },
  { to: '/numbers', label: '号码', icon: '🎫' },
  { to: '/query', label: '查询', icon: '🔍' },
  { to: '/stats', label: '我的', icon: '📈' },
];

const more = computed(() => {
  const base = [
    { to: '/wins', label: '中奖记录' },
    { to: '/trend', label: '开奖走势' },
    { to: '/settings', label: '设置' },
  ];
  if (auth.isAdmin) {
    base.push({ to: '/admin', label: '后台管理' });
  }
  return base;
});

function closeDrawer() {
  showMore.value = false;
}
</script>

<template>
  <nav class="tabbar" role="navigation" aria-label="主导航">
    <router-link
      v-for="t in tabs"
      :key="t.to"
      :to="t.to"
      class="tab"
      :aria-current="route.path === t.to ? 'page' : undefined"
    >
      <span class="tab-icon" aria-hidden="true">{{ t.icon }}</span>
      <span class="tab-label">{{ t.label }}</span>
    </router-link>
    <button
      type="button"
      class="tab"
      aria-haspopup="dialog"
      :aria-expanded="showMore"
      @click="showMore = true"
    >
      <span class="tab-icon" aria-hidden="true">⋯</span>
      <span class="tab-label">更多</span>
    </button>

    <!-- 更多抽屉 -->
    <div
      v-if="showMore"
      class="drawer-backdrop"
      role="presentation"
      @click="closeDrawer"
    >
      <aside
        class="drawer"
        role="dialog"
        aria-modal="true"
        aria-label="更多页面"
        @click.stop
      >
        <div class="drawer-header">
          <strong>更多页面</strong>
          <button
            type="button"
            class="drawer-close"
            aria-label="关闭"
            @click="closeDrawer"
          >
            ✕
          </button>
        </div>
        <router-link
          v-for="m in more"
          :key="m.to"
          :to="m.to"
          class="drawer-link"
          :aria-current="route.path === m.to ? 'page' : undefined"
          @click="closeDrawer"
        >
          {{ m.label }}
        </router-link>
      </aside>
    </div>
  </nav>
</template>

<style scoped>
.tabbar {
  position: fixed;
  bottom: 0;
  left: 0;
  right: 0;
  height: 60px;
  background: var(--surface);
  border-top: 1px solid var(--border);
  display: flex;
  z-index: 50;
}

.tab {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  text-decoration: none;
  color: var(--muted);
  min-height: 44px;
  background: none;
  border: none;
  cursor: pointer;
  padding: 4px 0;
  transition: color var(--dur-base);
}

.tab[aria-current='page'] {
  color: var(--accent);
}

.tab-icon {
  font-size: var(--text-lg);
  line-height: 1;
}

.tab-label {
  font-size: var(--text-xs);
  margin-top: 2px;
  line-height: 1;
}

.drawer-backdrop {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.35);
  z-index: 60;
}

.drawer {
  position: fixed;
  top: 0;
  right: 0;
  bottom: 0;
  width: min(280px, 80vw);
  background: var(--surface);
  border-left: 1px solid var(--border);
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  box-shadow: -4px 0 16px rgba(0, 0, 0, 0.12);
}

.drawer-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
  font-size: var(--text-lg);
}

.drawer-close {
  background: none;
  border: none;
  color: var(--muted);
  font-size: var(--text-lg);
  cursor: pointer;
  min-height: 44px;
  min-width: 44px;
}

.drawer-link {
  padding: 12px;
  border-radius: var(--radius);
  color: var(--fg);
  text-decoration: none;
  font-size: var(--text-md);
  min-height: 44px;
  display: flex;
  align-items: center;
}

.drawer-link:hover {
  background: var(--surface-2);
}

.drawer-link[aria-current='page'] {
  background: var(--accent);
  color: #fff;
}
</style>
