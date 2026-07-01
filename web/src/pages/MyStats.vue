<script setup lang="ts">
import { ref, onMounted } from 'vue';
import { apiGet } from '../api/client';
import { fmtMoney } from '../lib/format';
import State from '../components/State.vue';

interface Summary {
  total_cost: number;
  total_prize: number;
  pending_amount: number;
  net: number;
  win_count: number;
  ticket_count: number;
}

interface DashboardData {
  summary: Summary;
}

const data = ref<DashboardData | null>(null);
const loading = ref(false);
const error = ref('');

async function load() {
  loading.value = true;
  error.value = '';
  try {
    data.value = await apiGet<DashboardData>('/api/dashboard');
  } catch (err) {
    error.value = err instanceof Error ? err.message : '加载失败';
  } finally {
    loading.value = false;
  }
}

onMounted(() => {
  void load();
});
</script>

<template>
  <div class="my-stats">
    <header class="page-header">
      <h1>我的统计</h1>
    </header>

    <State v-if="loading" type="loading" title="加载统计中…" />
    <State v-else-if="error" type="error" :title="error" @action="load" />
    <State
      v-else-if="!data"
      type="empty"
      title="暂无统计数据"
      @action="load"
    />

    <template v-else>
      <section class="card">
        <div class="card-header">
          <h2 class="card-title">盈亏总览</h2>
        </div>
        <div class="card-body">
          <div class="stats-grid">
            <div class="stat-card">
              <div class="stat-label">累计投入</div>
              <div class="stat-value">{{ fmtMoney(data.summary.total_cost) }}</div>
            </div>
            <div class="stat-card">
              <div class="stat-label">累计中奖</div>
              <div class="stat-value profit">{{ fmtMoney(data.summary.total_prize) }}</div>
            </div>
            <div class="stat-card">
              <div class="stat-label">待派奖</div>
              <div class="stat-value warning">{{ fmtMoney(data.summary.pending_amount) }}</div>
            </div>
            <div class="stat-card">
              <div class="stat-label">净盈亏</div>
              <div
                class="stat-value"
                :class="data.summary.net >= 0 ? 'profit' : 'loss'"
              >
                {{ fmtMoney(data.summary.net) }}
              </div>
            </div>
            <div class="stat-card">
              <div class="stat-label">中奖笔数</div>
              <div class="stat-value">{{ data.summary.win_count }}</div>
            </div>
            <div class="stat-card">
              <div class="stat-label">追注数量</div>
              <div class="stat-value">{{ data.summary.ticket_count }}</div>
            </div>
          </div>
        </div>
      </section>

      <section class="card">
        <div class="card-header">
          <h2 class="card-title">说明</h2>
        </div>
        <div class="card-body">
          <ul class="notes">
            <li>投入按号码池注单的真实成本计算（含倍投/追加）。</li>
            <li>浮动奖（一、二等奖）未回填官方金额时显示「待派奖」，不计入累计中奖。</li>
            <li>公益贡献按各彩种官方公益金比例单独统计，可在后续版本查看明细。</li>
          </ul>
        </div>
      </section>
    </template>
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

.stats-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 12px;
}

@media (min-width: 768px) {
  .stats-grid {
    grid-template-columns: repeat(3, 1fr);
  }
}

.stat-card {
  background: var(--surface-2);
  border-radius: 16px;
  padding: 18px;
  text-align: center;
}

.stat-label {
  font-size: var(--text-sm);
  color: var(--muted);
  margin-bottom: 8px;
}

.stat-value {
  font-size: var(--text-xl);
  font-weight: 600;
}

.stat-value.profit {
  color: var(--success);
}

.stat-value.loss {
  color: var(--danger);
}

.stat-value.warning {
  color: var(--warning);
}

.notes {
  padding-left: 20px;
  color: var(--muted);
  font-size: var(--text-md);
  line-height: 1.7;
}

.notes li {
  margin-bottom: 8px;
}
</style>
