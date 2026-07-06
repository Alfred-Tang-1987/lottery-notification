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
