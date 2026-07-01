<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue';
import NavDesktop from './components/NavDesktop.vue';
import NavMobile from './components/NavMobile.vue';
import UserMenu from './components/UserMenu.vue';

const MOBILE_BREAKPOINT = 768;
const isMobile = ref(window.innerWidth <= MOBILE_BREAKPOINT);

function updateViewport() {
  isMobile.value = window.innerWidth <= MOBILE_BREAKPOINT;
}

onMounted(() => {
  window.addEventListener('resize', updateViewport);
});

onUnmounted(() => {
  window.removeEventListener('resize', updateViewport);
});
</script>

<template>
  <div class="app">
    <header class="topbar" role="banner">
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

    <footer class="disclaimer" role="contentinfo">
      理性购彩 量力而行 · 彩票为独立随机事件，历史不代表未来
    </footer>
  </div>
</template>

<style scoped>
.app {
  display: flex;
  flex-direction: column;
  min-height: 100vh;
}

.topbar {
  height: 56px;
  background: var(--surface);
  border-bottom: 1px solid var(--border);
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 16px;
  position: sticky;
  top: 0;
  z-index: 30;
}

.brand {
  font-size: var(--text-xl);
  font-weight: 600;
}

.body {
  display: flex;
  flex: 1;
}

.main {
  flex: 1;
  padding: 16px;
  margin-left: 0;
  margin-bottom: 0;
}

@media (min-width: 769px) {
  .main {
    margin-left: 240px;
    padding: 20px;
  }
}

.disclaimer {
  text-align: center;
  font-size: var(--text-xs);
  color: var(--muted);
  padding: 10px;
  background: var(--bg);
  border-top: 1px solid var(--border);
}

@media (max-width: 768px) {
  .disclaimer {
    margin-bottom: 60px;
  }
}
</style>
