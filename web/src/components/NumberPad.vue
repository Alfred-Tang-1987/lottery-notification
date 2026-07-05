<script setup lang="ts">
/**
 * 通用号码盘组件——按 LotteryRange 渲染分区（红/蓝）或按位（0-9），
 * 点击选号 + 已选展示 + 越界校验。
 *
 * Props:
 * - numbers: 当前选中的号码数组（父组件控制）
 * - zone: "front" | "back"（决定颜色和校验）
 * - lotteryCode: 彩种代码（查区间）
 *
 * Emits:
 * - update:numbers: 选中号码变化（immutable update）
 */
import { computed } from "vue";
import { frontNumbers, backNumbers } from "../lib/lotteries";

const props = defineProps<{
  numbers: number[];
  zone: "front" | "back";
  lotteryCode: string;
}>();

const emit = defineEmits<{
  "update:numbers": [numbers: number[]];
}>();

const pool = computed(() => {
  if (props.zone === "front") return frontNumbers(props.lotteryCode);
  return backNumbers(props.lotteryCode);
});

const selectedSet = computed(() => new Set(props.numbers));

function toggle(n: number) {
  const next = new Set(props.numbers);
  if (next.has(n)) next.delete(n);
  else next.add(n);
  // 排序后 emit（immutable）
  const sorted = [...next].sort((a, b) => a - b);
  emit("update:numbers", sorted);
}

function formatNumber(n: number): string {
  return n < 10 ? `0${n}` : String(n);
}
</script>

<template>
  <div class="number-pad" :class="[zone]">
    <div class="num-grid">
      <button
        v-for="n in pool"
        :key="`np-${zone}-${n}`"
        type="button"
        class="num-btn"
        :class="{ selected: selectedSet.has(n) }"
        :aria-pressed="selectedSet.has(n)"
        @click="toggle(n)"
      >
        {{ formatNumber(n) }}
      </button>
    </div>
  </div>
</template>

<style scoped>
.number-pad {
  margin-bottom: 12px;
}

.num-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}

.num-btn {
  width: 36px;
  height: 36px;
  border: 1.5px solid var(--border);
  border-radius: 8px;
  background: var(--surface-2);
  color: var(--fg);
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 0;
  transition: background var(--dur), border-color var(--dur);
}

.num-btn:hover {
  border-color: var(--accent);
}

.num-btn.selected {
  color: #fff;
}

.front .num-btn.selected {
  background: var(--red-ball);
  border-color: var(--red-ball);
}

.back .num-btn.selected {
  background: var(--blue-ball);
  border-color: var(--blue-ball);
}

.num-btn:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}
</style>
