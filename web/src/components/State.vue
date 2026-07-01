<script setup lang="ts">
defineProps<{
  type: "loading" | "empty" | "error";
  title?: string;
  cta?: string;
}>();

defineEmits<{
  action: [];
}>();
</script>

<template>
  <div
    class="state"
    role="status"
    :aria-live="type === 'error' ? 'assertive' : 'polite'"
  >
    <template v-if="type === 'loading'">
      <div class="spinner" aria-label="加载中" />
    </template>
    <template v-else-if="type === 'empty'">
      <p class="state-title">{{ title || "暂无数据" }}</p>
      <button
        v-if="cta"
        type="button"
        class="state-cta"
        @click="$emit('action')"
      >
        {{ cta }}
      </button>
    </template>
    <template v-else>
      <p class="state-title">{{ title || "加载失败" }}</p>
      <button type="button" class="state-cta" @click="$emit('action')">
        重试
      </button>
    </template>
  </div>
</template>

<style scoped>
.state {
  text-align: center;
  padding: 30px;
  color: var(--muted);
}

.spinner {
  width: 28px;
  height: 28px;
  margin: 0 auto;
  border: 3px solid var(--border);
  border-top-color: var(--accent);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

.state-title {
  font-size: var(--text-md);
  margin: 12px 0;
}

.state-cta {
  padding: 8px 16px;
  background: var(--accent);
  color: #fff;
  border: none;
  border-radius: var(--radius);
  cursor: pointer;
  min-height: 44px;
  font-size: var(--text-base);
}

.state-cta:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}
</style>
