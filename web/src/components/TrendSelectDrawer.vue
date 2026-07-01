<script setup lang="ts">
import { ref } from 'vue';

const emit = defineEmits<{
  confirmed: [];
}>();

const open = ref(false);
const agreed = ref(false);

function start() {
  open.value = true;
}

function confirm() {
  agreed.value = true;
  open.value = false;
  emit('confirmed');
}

function cancel() {
  open.value = false;
}

defineExpose({ start });
</script>

<template>
  <transition name="slide">
    <aside
      v-if="open"
      class="drawer"
      role="dialog"
      aria-modal="true"
      aria-label="走势选号确认"
    >
      <div class="drawer-content">
        <h3>选号确认</h3>
        <p class="disclaimer-text">
          历史走势仅用于回顾过往开奖号码分布，不构成任何选号建议。
        </p>
        <label class="disclaimer-check"
        >
          <input v-model="agreed" type="checkbox" />
          <span>我知道历史走势不影响中奖概率，仅基于个人意愿自选</span>
        </label>

        <div class="drawer-actions">
          <button type="button" class="secondary" @click="cancel">取消</button>
          <button
            type="button"
            class="primary"
            :disabled="!agreed"
            @click="confirm"
          >
            开始选号
          </button>
        </div>
      </div>
    </aside>
  </transition>
</template>

<style scoped>
.drawer {
  position: fixed;
  top: 0;
  right: 0;
  bottom: 0;
  width: min(380px, 90vw);
  background: var(--surface);
  box-shadow: -2px 0 12px rgba(0, 0, 0, 0.1);
  z-index: 100;
  display: flex;
  flex-direction: column;
}

.drawer-content {
  padding: 24px;
  flex: 1;
  overflow-y: auto;
}

h3 {
  font-size: var(--text-xl);
  font-weight: 600;
  margin-bottom: 16px;
}

.disclaimer-text {
  color: var(--muted);
  font-size: var(--text-md);
  line-height: 1.6;
  margin-bottom: 20px;
}

.disclaimer-check {
  display: flex;
  gap: 10px;
  align-items: flex-start;
  padding: 14px;
  background: var(--surface-2);
  border-radius: var(--radius);
  font-size: var(--text-md);
  cursor: pointer;
}

.disclaimer-check input {
  margin-top: 3px;
  width: 18px;
  height: 18px;
  flex-shrink: 0;
}

.drawer-actions {
  margin-top: 24px;
  display: flex;
  gap: 10px;
}

.primary,
.secondary {
  flex: 1;
  padding: 12px;
  border-radius: var(--radius);
  font-size: var(--text-md);
  font-weight: 600;
  cursor: pointer;
  min-height: 44px;
}

.primary {
  border: none;
  background: var(--accent);
  color: #fff;
}

.primary:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.secondary {
  border: 1px solid var(--border);
  background: var(--surface);
  color: var(--fg);
}

.slide-enter-active,
.slide-leave-active {
  transition: transform var(--dur);
}

.slide-enter-from,
.slide-leave-to {
  transform: translateX(100%);
}
</style>
