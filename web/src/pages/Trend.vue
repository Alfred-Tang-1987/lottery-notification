<script setup lang="ts">
import { ref, onMounted, computed, watch } from 'vue';
import { apiGet } from '../api/client';
import { LOTTERIES } from '../lib/lotteries';
import State from '../components/State.vue';
import BallNumber from '../components/BallNumber.vue';
import TrendSelectDrawer from '../components/TrendSelectDrawer.vue';

interface Draw {
  id: number;
  lottery_code: string;
  lottery_name: string;
  draw_no: string;
  draw_date: string;
  numbers_json: string;
}

const selected = ref('ssq');
const draws = ref<Draw[] | null>(null);
const loading = ref(false);
const error = ref('');
const selectUnlocked = ref(false);
const drawer = ref<InstanceType<typeof TrendSelectDrawer> | null>(null);
const limit = ref(30);

// Known number ranges per lottery type (from lottery-rules.md)
const LOTTERY_RANGES: Record<string, { front: { min: number; max: number }; back: { min: number; max: number } | null }> = {
  ssq: { front: { min: 1, max: 33 }, back: { min: 1, max: 16 } },
  dlt: { front: { min: 1, max: 35 }, back: { min: 1, max: 12 } },
  qlc: { front: { min: 1, max: 30 }, back: null },
  fc3d: { front: { min: 0, max: 9 }, back: null },
  pl3: { front: { min: 0, max: 9 }, back: null },
  pl5: { front: { min: 0, max: 9 }, back: null },
  qxc: { front: { min: 0, max: 9 }, back: { min: 0, max: 14 } },
};

async function load() {
  loading.value = true;
  error.value = '';
  try {
    draws.value = await apiGet<Draw[]>(
      `/api/draws?lottery_code=${selected.value}&limit=${limit.value}`
    );
  } catch (err) {
    error.value = err instanceof Error ? err.message : '加载失败';
  } finally {
    loading.value = false;
  }
}

function openSelect() {
  drawer.value?.start();
}

function frequencyMap(values: number[]): Record<number, number> {
  const map: Record<number, number> = {};
  for (const v of values) {
    map[v] = (map[v] || 0) + 1;
  }
  return map;
}

const frontFreq = computed(() => {
  const values: number[] = [];
  for (const d of draws.value || []) {
    try {
      const parsed = JSON.parse(d.numbers_json) as { front: number[] };
      values.push(...(parsed.front || []));
    } catch (e) {
      // Intentional dev diagnostic: trend parse errors (not production logger)
      console.warn('trend frontFreq parse error', d.draw_no, e);
    }
  }
  return frequencyMap(values);
});

const backFreq = computed(() => {
  const values: number[] = [];
  for (const d of draws.value || []) {
    try {
      const parsed = JSON.parse(d.numbers_json) as { back?: number[] };
      values.push(...(parsed.back || []));
    } catch (e) {
      // Intentional dev diagnostic: trend parse errors (not production logger)
      console.warn('trend backFreq parse error', d.draw_no, e);
    }
  }
  return frequencyMap(values);
});

function sortedKeys(freq: Record<number, number>): number[] {
  return Object.keys(freq)
    .map((k) => Number(k))
    .sort((a, b) => a - b);
}

// ─── 综合分布图 matrix ───

const range = computed(() => LOTTERY_RANGES[selected.value]);

const frontNumbers = computed(() => {
  const r = range.value?.front;
  if (!r) return [] as number[];
  const nums: number[] = [];
  for (let n = r.min; n <= r.max; n++) nums.push(n);
  return nums;
});

const backNumbers = computed(() => {
  const r = range.value?.back;
  if (!r) return [] as number[];
  const nums: number[] = [];
  for (let n = r.min; n <= r.max; n++) nums.push(n);
  return nums;
});

/** Draws in chronological order (oldest first = 从远到近) */
const chronologicalDraws = computed(() => {
  if (!draws.value) return [];
  return [...draws.value].reverse();
});

/** {draw_index: {front: Set<number>, back: Set<number>}} */
const drawNumberSets = computed(() => {
  const result: Map<number, { front: Set<number>; back: Set<number> }> = new Map();
  (chronologicalDraws.value || []).forEach((d, idx) => {
    try {
      const parsed = JSON.parse(d.numbers_json) as { front: number[]; back?: number[] };
      result.set(idx, {
        front: new Set(parsed.front || []),
        back: new Set(parsed.back || []),
      });
    } catch {
      result.set(idx, { front: new Set(), back: new Set() });
    }
  });
  return result;
});

/** Miss count per number: consecutive draws from top (recent) to bottom (old) without that number appearing */
const missCounts = computed(() => {
  const n = chronologicalDraws.value.length;
  const frontMiss: number[] = new Array(frontNumbers.value.length).fill(0);
  const backMiss: number[] = new Array(backNumbers.value.length).fill(0);

  const sets = drawNumberSets.value;
  // Walk from most recent (idx n-1) to oldest (idx 0)
  for (let idx = n - 1; idx >= 0; idx--) {
    const s = sets.get(idx);
    if (!s) continue;
    for (let col = 0; col < frontNumbers.value.length; col++) {
      if (frontMiss[col] === 0 && !s.front.has(frontNumbers.value[col])) {
        frontMiss[col] = n - idx;
      }
    }
    for (let col = 0; col < backNumbers.value.length; col++) {
      if (backMiss[col] === 0 && !s.back.has(backNumbers.value[col])) {
        backMiss[col] = n - idx;
      }
    }
  }
  return { front: frontMiss, back: backMiss };
});

watch([selected, limit], () => {
  void load();
});

onMounted(() => {
  void load();
});
</script>

<template>
  <div class="trend">
    <header class="page-header">
      <h1>开奖走势</h1>
    </header>

    <div class="pills" role="tablist">
      <button
        v-for="l in LOTTERIES"
        :key="l.code"
        type="button"
        role="tab"
        class="pill"
        :class="{ active: selected === l.code }"
        :aria-selected="selected === l.code"
        @click="selected = l.code"
      >
        {{ l.name }}
      </button>
    </div>

    <div class="disclaimer">
      彩票为独立随机事件，历史不代表未来，走势仅供历史回顾，不构成任何选号建议。
    </div>

    <State v-if="loading" type="loading" title="加载走势数据…" />
    <State v-else-if="error" type="error" :title="error" @action="load" />
    <State v-else-if="!draws || draws.length === 0" type="empty" title="该彩种暂无历史走势" />

    <template v-else>
      <section class="card">
        <div class="card-header">
          <h2 class="card-title">综合分布图（近{{ chronologicalDraws.length }}期）</h2>
        </div>
        <div class="card-body">
          <div class="limit-picker">
            <label
            >期数:
              <select v-model.number="limit">
                <option :value="30">30 期</option>
                <option :value="50">50 期</option>
                <option :value="100">100 期</option>
              </select>
            </label>
          </div>

          <!-- 前区矩阵 -->
          <div v-if="frontNumbers.length > 0" class="matrix-section">
            <h3 class="matrix-label">前区 {{ frontNumbers[0] }}–{{ frontNumbers[frontNumbers.length - 1] }}</h3>
            <div class="matrix-wrapper">
              <table class="matrix-table">
                <thead>
                  <tr>
                    <th class="matrix-th draw-col">期号</th>
                    <th
                      v-for="n in frontNumbers"
                      :key="`fh-${n}`"
                      class="matrix-th num-col"
                    >{{ n }}</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="(draw, idx) in chronologicalDraws" :key="draw.id">
                    <td class="matrix-td draw-col">{{ draw.draw_no }}</td>
                    <td
                      v-for="n in frontNumbers"
                      :key="`f-${idx}-${n}`"
                      class="matrix-td num-col"
                      :class="{ hit: drawNumberSets.get(idx)?.front.has(n) ?? false }"
                    >
                      <span
                        v-if="drawNumberSets.get(idx)?.front.has(n)"
                        class="matrix-circle"
                      ></span>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>

          <!-- 后区矩阵 -->
          <div v-if="backNumbers.length > 0" class="matrix-section">
            <h3 class="matrix-label">
              后区 {{ backNumbers[0] }}–{{ backNumbers[backNumbers.length - 1] }}
              <span v-if="selected === 'qxc'" class="matrix-label-note">（0–14）</span>
            </h3>
            <div class="matrix-wrapper">
              <table class="matrix-table">
                <thead>
                  <tr>
                    <th class="matrix-th draw-col">期号</th>
                    <th
                      v-for="n in backNumbers"
                      :key="`bh-${n}`"
                      class="matrix-th num-col"
                    >{{ n }}</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="(draw, idx) in chronologicalDraws" :key="draw.id">
                    <td class="matrix-td draw-col">{{ draw.draw_no }}</td>
                    <td
                      v-for="n in backNumbers"
                      :key="`b-${idx}-${n}`"
                      class="matrix-td num-col"
                      :class="{ hit: drawNumberSets.get(idx)?.back.has(n) ?? false }"
                    >
                      <span
                        v-if="drawNumberSets.get(idx)?.back.has(n)"
                        class="matrix-circle back-circle"
                      ></span>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
        </div>
      </section>

      <section class="card">
        <div class="card-header">
          <h2 class="card-title">号码出现频次</h2>
        </div>
        <div class="card-body">
          <div class="freq-section">
            <h3>前区/号码</h3>
            <div class="freq-grid">
              <div
                v-for="n in sortedKeys(frontFreq)"
                :key="`f-${n}`"
                class="freq-cell"
              >
                <span class="freq-num">{{ n }}</span>
                <span class="freq-count">{{ frontFreq[n] }}</span>
              </div>
            </div>
          </div>

          <div v-if="Object.keys(backFreq).length > 0" class="freq-section">
            <h3>后区</h3>
            <div class="freq-grid">
              <div
                v-for="n in sortedKeys(backFreq)"
                :key="`b-${n}`"
                class="freq-cell"
              >
                <span class="freq-num">{{ n }}</span>
                <span class="freq-count">{{ backFreq[n] }}</span>
              </div>
            </div>
          </div>
        </div>
      </section>

      <section class="card">
        <div class="card-header">
          <h2 class="card-title">选号面板</h2>
        </div>
        <div class="card-body">
          <button
            v-if="!selectUnlocked"
            type="button"
            class="primary"
            @click="openSelect"
          >
            我要选号
          </button>
          <div v-else class="select-panel">
            <p>选号功能开发中，请通过「我的号码」页面手动添加注单。</p>
          </div>
        </div>
      </section>
    </template>

    <TrendSelectDrawer ref="drawer" @confirmed="selectUnlocked = true" />
  </div>
</template>

<style scoped>
.page-header {
  margin-bottom: 20px;
}

.page-header h1 {
  font-size: var(--text-2xl);
  font-weight: 600;
}

.pills {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 12px;
}

.pill {
  padding: 8px 16px;
  border: 1px solid var(--border);
  border-radius: 20px;
  background: var(--surface);
  color: var(--muted);
  font-size: var(--text-md);
  cursor: pointer;
  min-height: 44px;
}

.pill.active {
  background: var(--accent);
  color: #fff;
  border-color: var(--accent);
}

.disclaimer {
  padding: 12px 16px;
  background: #fefce8;
  border: 1px solid #fbbf24;
  border-radius: var(--radius);
  color: #92400e;
  font-size: var(--text-sm);
  margin-bottom: 20px;
}

.card {
  background: var(--surface);
  border-radius: 18px;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.04);
  margin-bottom: 20px;
  overflow: hidden;
}

.card-header {
  padding: 18px 20px 0;
}

.card-title {
  font-size: var(--text-xl);
  font-weight: 600;
}

.card-body {
  padding: 16px 20px 20px;
}

.limit-picker {
  margin-bottom: 12px;
}

.limit-picker select {
  padding: 6px 10px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--surface);
  color: var(--fg);
}

.trend-table {
  width: 100%;
  border-collapse: collapse;
  font-size: var(--text-md);
}

.trend-table th,
.trend-table td {
  padding: 12px 14px;
  text-align: left;
  border-bottom: 1px solid var(--border);
}

.trend-table th {
  color: var(--muted);
  font-weight: 600;
  font-size: var(--text-sm);
}

/* ─── 综合分布图 matrix ─── */
.matrix-section {
  margin-bottom: 24px;
}

.matrix-label {
  font-size: var(--text-md);
  color: var(--muted);
  margin-bottom: 8px;
}

.matrix-label-note {
  font-weight: 400;
  font-size: var(--text-xs);
}

.matrix-wrapper {
  overflow-x: auto;
  max-height: 60vh;
  overflow-y: auto;
  border: 1px solid var(--border);
  border-radius: var(--radius);
}

.matrix-table {
  border-collapse: collapse;
  font-size: var(--text-xs);
  white-space: nowrap;
  min-width: max-content;
}

.matrix-table thead {
  position: sticky;
  top: 0;
  z-index: 1;
}

.matrix-th {
  background: var(--surface-2);
  font-weight: 600;
  text-align: center;
  padding: 6px 3px;
  border-bottom: 2px solid var(--border);
  position: sticky;
  top: 0;
}

.matrix-th.draw-col {
  position: sticky;
  left: 0;
  z-index: 2;
  background: var(--surface);
  min-width: 70px;
  text-align: left;
  padding-left: 8px;
}

.matrix-th.num-col {
  min-width: 22px;
  max-width: 24px;
}

.matrix-td {
  text-align: center;
  padding: 3px 3px;
  border-bottom: 1px solid var(--border);
}

.matrix-td.draw-col {
  position: sticky;
  left: 0;
  z-index: 1;
  background: var(--surface);
  text-align: left;
  padding-left: 8px;
  font-size: var(--text-xs);
  color: var(--muted);
}

.matrix-td.num-col {
  min-width: 22px;
  max-width: 24px;
}

.matrix-td.hit {
  background: color-mix(in srgb, var(--accent) 8%, transparent);
}

.matrix-circle {
  display: inline-block;
  width: 14px;
  height: 14px;
  border-radius: 50%;
  background: var(--red-ball);
}

.matrix-circle.back-circle {
  background: var(--blue-ball);
}

.freq-section {
  margin-bottom: 20px;
}

.freq-section h3 {
  font-size: var(--text-md);
  color: var(--muted);
  margin-bottom: 10px;
}

.freq-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.freq-cell {
  display: flex;
  flex-direction: column;
  align-items: center;
  width: 44px;
  padding: 8px 4px;
  background: var(--surface-2);
  border-radius: 10px;
}

.freq-num {
  font-weight: 600;
  font-size: var(--text-md);
}

.freq-count {
  font-size: var(--text-xs);
  color: var(--muted);
}

.primary {
  padding: 10px 16px;
  border: none;
  border-radius: var(--radius);
  background: var(--accent);
  color: #fff;
  font-size: var(--text-md);
  font-weight: 600;
  cursor: pointer;
  min-height: 44px;
}

.select-panel {
  padding: 20px;
  background: var(--surface-2);
  border-radius: var(--radius);
  color: var(--muted);
}
</style>
