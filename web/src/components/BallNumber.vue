<script setup lang="ts">
import { computed } from 'vue';

const props = defineProps<{
  numbersJson: string;
  lotteryCode: string;
}>();

interface ParsedNumbers {
  front: number[];
  back?: number[];
}

const PARTITION_LOTTERIES = new Set(['ssq', 'dlt', 'qlc']);
const POSITIONAL_LOTTERIES = new Set(['fc3d', 'pl3', 'pl5']);

const data = computed<ParsedNumbers>(() => {
  try {
    const parsed = JSON.parse(props.numbersJson) as ParsedNumbers;
    return {
      front: Array.isArray(parsed.front) ? parsed.front : [],
      back: Array.isArray(parsed.back) ? parsed.back : undefined,
    };
  } catch {
    return { front: [] };
  }
});

const variant = computed(() => {
  if (PARTITION_LOTTERIES.has(props.lotteryCode)) return 'partition';
  if (POSITIONAL_LOTTERIES.has(props.lotteryCode)) return 'positional';
  if (props.lotteryCode === 'qxc') return 'hybrid';
  return 'positional';
});

function fmt(n: number): string {
  return n < 10 ? `0${n}` : String(n);
}
</script>

<template>
  <div class="ball-row" aria-label="开奖号码">
    <template v-if="variant === 'partition'">
      <span
        v-for="(n, idx) in data.front"
        :key="`f-${idx}`"
        class="ball red"
        >{{ fmt(n) }}</span
      >
      <span
        v-for="(n, idx) in data.back"
        :key="`b-${idx}`"
        class="ball blue"
        >{{ fmt(n) }}</span
      >
    </template>

    <template v-else-if="variant === 'hybrid'">
      <span
        v-for="(n, idx) in data.front"
        :key="`f-${idx}`"
        class="ball num"
        >{{ n }}</span
      >
      <span
        v-for="(n, idx) in data.back"
        :key="`b-${idx}`"
        class="ball blue"
        >{{ n }}</span
      >
    </template>

    <template v-else>
      <span
        v-for="(n, idx) in data.front"
        :key="`f-${idx}`"
        class="ball num"
        >{{ n }}</span
      >
    </template>
  </div>
</template>

<style scoped>
.ball-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.ball {
  width: 32px;
  height: 32px;
  border-radius: 50%;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 13px;
  font-weight: 600;
  color: #fff;
  flex-shrink: 0;
}

.ball.red {
  background: var(--red-ball);
}

.ball.blue {
  background: var(--blue-ball);
}

.ball.num {
  border-radius: 8px;
  background: var(--surface-2);
  color: var(--fg);
  border: 1.5px solid var(--border);
  font-family: var(--font-mono);
}
</style>
