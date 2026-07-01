<script setup lang="ts">
import { ref, onMounted } from 'vue';
import { useAuthStore } from '../stores/auth';

const auth = useAuthStore();
const open = ref(false);

onMounted(() => {
  if (!auth.initialized) {
    auth.fetchMe();
  }
});

async function handleLogout() {
  open.value = false;
  await auth.logout();
}
</script>

<template>
  <div class="user-menu">
    <button
      type="button"
      class="user-trigger"
      aria-haspopup="menu"
      :aria-expanded="open"
      @click="open = !open"
    >
      <span class="user-avatar" aria-hidden="true">{{ auth.user?.username?.[0] || '👤' }}</span>
      <span class="user-name">{{ auth.user?.username || '未登录' }}</span>
      <span class="user-caret" aria-hidden="true">▾</span>
    </button>

    <div v-if="open" class="dropdown" role="menu" aria-label="用户菜单">
      <div class="dropdown-item user-info" role="none">
        <span class="user-role" v-if="auth.isAdmin">管理员</span>
        <span class="user-role" v-else>普通用户</span>
      </div>
      <router-link to="/settings" class="dropdown-item" role="menuitem" @click="open = false">
        账号设置
      </router-link>
      <button
        type="button"
        class="dropdown-item logout"
        role="menuitem"
        @click="handleLogout"
      >
        登出
      </button>
    </div>
  </div>
</template>

<style scoped>
.user-menu {
  position: relative;
}

.user-trigger {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 10px;
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  cursor: pointer;
  min-height: 44px;
  color: var(--fg);
  font-size: var(--text-md);
}

.user-avatar {
  width: 28px;
  height: 28px;
  border-radius: 50%;
  background: var(--accent);
  color: #fff;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: var(--text-sm);
  font-weight: 600;
}

.user-name {
  max-width: 120px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.user-caret {
  color: var(--muted);
  font-size: var(--text-xs);
}

.dropdown {
  position: absolute;
  top: calc(100% + 8px);
  right: 0;
  min-width: 160px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  box-shadow: var(--shadow);
  padding: 8px;
  display: flex;
  flex-direction: column;
  gap: 4px;
  z-index: 100;
}

.dropdown-item {
  padding: 10px 12px;
  border-radius: var(--radius);
  font-size: var(--text-md);
  text-decoration: none;
  color: var(--fg);
  min-height: 44px;
  display: flex;
  align-items: center;
  background: none;
  border: none;
  cursor: pointer;
  width: 100%;
  text-align: left;
}

.dropdown-item:hover {
  background: var(--surface-2);
}

.user-info {
  color: var(--muted);
  font-size: var(--text-sm);
  pointer-events: none;
}

.logout {
  color: var(--danger);
}
</style>
