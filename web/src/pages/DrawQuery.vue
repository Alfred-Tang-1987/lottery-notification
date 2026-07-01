<script setup lang="ts">
import { ref, watch, onMounted } from 'vue';
import { apiGet } from '../api/client';
import { LOTTERIES } from '../lib/lotteries';
import { fmtDate } from '../lib/format';
import State from '../components/State.vue';
import BallNumber from '../components/BallNumber.vue';

interface Draw {
  id: number;
  lottery_code: string;
  lottery_name: string;
  draw_no: string;
  draw_date: string;
  numbers_json: string;
  verified: boolean;
  single_source: boolean;
  version: number;
}

const selected = ref('ssq');
const draws = ref<Draw[] | null>(null);
const loading = ref(false);
const error = ref('');

async function load() {
  loading.value = true;
  error.value = '';
  try {
    draws.value = await apiGet<Draw[]>(`/api/draws?lottery_code=${selected.value}&limit=30`);
  } catch (err) {
    error.value = err instanceof Error ? err.message : '加载失败';
  } finally {
    loading.value = false;
  }
}

watch(selected, () => {
  void load();
});

onMounted(() => {
  void load();
});
</script>

<template>
  <div class="draw-query">
    <header class="page-header">
      <h1>开奖查询</h1>
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

    <State v-if="loading" type="loading" title="加载开奖结果…" />
    <State v-else-if="error" type="error" :title="error" @action="load" />
    <State v-else-if="!draws || draws.length === 0" type="empty" title="该彩种暂无开奖记录" />

    <table v-else class="draw-table">
      <thead>
        <tr>
          <th>期号</th>
          <th>开奖日期</th>
          <th>开奖号码</th>
          <th>来源</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="draw in draws" :key="draw.id">
          <td>{{ draw.draw_no }}</td>
          <td>{{ fmtDate(draw.draw_date) }}</td>
          <td>
            <BallNumber :numbers-json="draw.numbers_json" :lottery-code="draw.lottery_code" />
          </td>
          <td>
            <span v-if="draw.single_source" class="tag single">单源校验</span>
            <span v-else class="tag ok">双源校验</span>
          </td>
        </tr>
      </tbody>
    </table>
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
  margin-bottom: 20px;
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

.draw-table {
  width: 100%;
  border-collapse: collapse;
  background: var(--surface);
  border-radius: 18px;
  overflow: hidden;
  font-size: var(--text-md);
}

.draw-table th,
.draw-table td {
  padding: 14px 16px;
  text-align: left;
  border-bottom: 1px solid var(--border);
}

.draw-table th {
  color: var(--muted);
  font-weight: 600;
  font-size: var(--text-sm);
}

.draw-table tbody tr:last-child td {
  border-bottom: none;
}

.tag {
  display: inline-flex;
  padding: 3px 10px;
  border-radius: 20px;
  font-size: var(--text-xs);
  font-weight: 600;
}

.tag.ok {
  background: #dcfce7;
  color: #166534;
}

.tag.single {
  background: #fef3c7;
  color: #92400e;
}

@media (max-width: 640px) {
  .draw-table th,
  .draw-table td {
    padding: 10px 12px;
  }
}
</style>
