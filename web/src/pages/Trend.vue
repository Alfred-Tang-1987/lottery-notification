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
    } catch {
      // ignore
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
    } catch {
      // ignore
    }
  }
  return frequencyMap(values);
});

function sortedKeys(freq: Record<number, number>): number[] {
  return Object.keys(freq)
    .map((k) => Number(k))
    .sort((a, b) => a - b);
}

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
          <h2 class="card-title">近{{ limit }}期开奖</h2>
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

          <table class="trend-table">
            <thead>
              <tr>
                <th>期号</th>
                <th>开奖号码</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="draw in draws" :key="draw.id">
                <td>{{ draw.draw_no }}</td>
                <td>
                  <BallNumber
                    :numbers-json="draw.numbers_json"
                    :lottery-code="draw.lottery_code"
                  />
                </td>
              </tr>
            </tbody>
          </table>
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
