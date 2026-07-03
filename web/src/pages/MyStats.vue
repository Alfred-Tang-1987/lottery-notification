<script setup lang="ts">
import { ref, onMounted, onUnmounted, nextTick, watch } from 'vue';
import { apiGet } from '../api/client';
import { fmtMoney } from '../lib/format';
import State from '../components/State.vue';
import * as echarts from 'echarts';
import { aggregateByTier, aggregateMonthly, type WinRow, type MonthlyRow } from '../lib/stats';
import { LOTTERIES } from '../lib/lotteries';

interface Summary {
  total_cost: number;
  total_prize: number;
  pending_amount: number;
  net: number;
  win_count: number;
  ticket_count: number;
  win_rate: number;
  welfare_contribution: number;
}

const data = ref<{ summary: Summary } | null>(null);
const wins = ref<WinRow[]>([]);
const monthly = ref<MonthlyRow[]>([]);
const loading = ref(false);
const error = ref('');

// Filter state (spec §12.2 row 6: 全局筛选——时段本月/本年/全部/自定义·彩种，默认本月)
const period = ref<'month' | 'year' | 'all' | 'custom'>('month');
const lotteryCode = ref<string>('');
const dateFrom = ref<string>('');
const dateTo = ref<string>('');

const tierPieRef = ref<HTMLDivElement | null>(null);
const amountPieRef = ref<HTMLDivElement | null>(null);
const monthlyBarRef = ref<HTMLDivElement | null>(null);

// ECharts instances for cleanup
let tierPieChart: echarts.ECharts | null = null;
let amountPieChart: echarts.ECharts | null = null;
let monthlyBarChart: echarts.ECharts | null = null;
// Guard against async render after unmount
let disposed = false;

function buildQueryString(basePath: string): string {
  const params = new URLSearchParams();
  params.set('period', period.value);
  if (period.value === 'custom' && dateFrom.value) params.set('date_from', dateFrom.value);
  if (period.value === 'custom' && dateTo.value) params.set('date_to', dateTo.value);
  if (lotteryCode.value) {
    params.set('lottery_code', lotteryCode.value);
  }
  const qs = params.toString();
  return qs ? `${basePath}?${qs}` : basePath;
}

function buildComparisonsQueryString(): string {
  const params = new URLSearchParams();
  params.set('win_only', 'true');
  params.set('period', period.value);
  if (period.value === 'custom' && dateFrom.value) params.set('date_from', dateFrom.value);
  if (period.value === 'custom' && dateTo.value) params.set('date_to', dateTo.value);
  if (lotteryCode.value) {
    params.set('lottery_code', lotteryCode.value);
  }
  return `/api/comparisons?${params.toString()}`;
}

async function load() {
  loading.value = true;
  error.value = '';
  try {
    const [dash, comps, monthData] = await Promise.all([
      apiGet<{ summary: Summary }>(buildQueryString('/api/dashboard')),
      apiGet<WinRow[]>(buildComparisonsQueryString()),
      apiGet<MonthlyRow[]>('/api/dashboard/monthly'),
    ]);
    data.value = dash;
    wins.value = comps;
    monthly.value = monthData;
    await nextTick();
    if (!disposed) renderCharts();
  } catch (err) {
    error.value = err instanceof Error ? err.message : '加载失败';
  } finally {
    loading.value = false;
  }
}

function renderCharts() {
  // Dispose existing instances before re-init to prevent memory leaks
  // (echarts.init on an already-mounted DOM creates orphan instances)
  tierPieChart?.dispose();
  amountPieChart?.dispose();
  monthlyBarChart?.dispose();

  // Use pure aggregation functions
  const tierData = aggregateByTier(wins.value);
  const monthlyData = aggregateMonthly(monthly.value);

  // Tier pie chart (count view)
  if (tierPieRef.value) {
    tierPieChart = echarts.init(tierPieRef.value);
    tierPieChart.setOption({
      tooltip: { trigger: 'item', formatter: '{b}: {c} 笔 ({d}%)' },
      series: [{
        type: 'pie', radius: ['40%', '70%'],
        data: tierData.labels.map((name, i) => ({ name, value: tierData.counts[i] })),
        label: { formatter: '{b}\n{d}%' },
      }],
    });
  }

  // Amount pie chart (amount view)
  if (amountPieRef.value) {
    amountPieChart = echarts.init(amountPieRef.value);
    amountPieChart.setOption({
      tooltip: { trigger: 'item', formatter: (p: { name: string; value: number; percent: number }) => `${p.name}: ¥${(p.value / 100).toFixed(2)} (${p.percent}%)` },
      series: [{
        type: 'pie', radius: ['40%', '70%'],
        data: tierData.labels.map((name, i) => ({ name, value: tierData.amounts[i] })),
        label: { formatter: '{b}\n{d}%' },
      }],
    });
  }

  // Monthly bar chart
  if (monthlyBarRef.value) {
    monthlyBarChart = echarts.init(monthlyBarRef.value);
    monthlyBarChart.setOption({
      tooltip: {
        trigger: 'axis',
        formatter: (params: { seriesName: string; value: number }[]) => {
          const costVal = params.find((p) => p.seriesName === '投入')?.value ?? 0;
          const prizeVal = params.find((p) => p.seriesName === '中奖')?.value ?? 0;
          return `投入: ¥${costVal.toFixed(2)}<br/>中奖: ¥${prizeVal.toFixed(2)}`;
        },
      },
      legend: { data: ['投入', '中奖'], bottom: 0 },
      grid: { left: '8%', right: '8%', top: '10%', bottom: '14%' },
      xAxis: { type: 'category', data: monthlyData.months, axisLabel: { rotate: 45, fontSize: 10 } },
      yAxis: { type: 'value', axisLabel: { formatter: (v: number) => `¥${v}` } },
      series: [
        {
          name: '投入',
          type: 'bar',
          data: monthlyData.costs,
          itemStyle: { color: '#94a3b8' },
          label: { show: true, position: 'top', formatter: (p: { value: number }) => (p.value > 0 ? `¥${p.value}` : ''), fontSize: 10 },
        },
        {
          name: '中奖',
          type: 'bar',
          data: monthlyData.prizes,
          itemStyle: { color: '#34d399' },
          label: { show: true, position: 'top', formatter: (p: { value: number }) => (p.value > 0 ? `¥${p.value}` : ''), fontSize: 10 },
        },
      ],
    });
  }
}

onMounted(() => {
  void load();
});

onUnmounted(() => {
  disposed = true;
  // Dispose ECharts instances to prevent memory leaks
  tierPieChart?.dispose();
  amountPieChart?.dispose();
  monthlyBarChart?.dispose();
  tierPieChart = null;
  amountPieChart = null;
  monthlyBarChart = null;
});

// 监听筛选条件变化，重新加载数据（spec §12.2 row 6: 盈亏总览随筛选联动）
watch([period, lotteryCode, dateFrom, dateTo], () => {
  void load();
});
</script>

<template>
  <div class="my-stats">
    <header class="page-header">
      <h1>我的统计</h1>
    </header>

    <!-- 筛选栏（spec §12.2 row 6: 全局筛选——时段·彩种） -->
    <section class="card filter-bar">
      <div class="filter-group">
        <span class="filter-label">时段</span>
        <div class="pill-group" role="radiogroup" aria-label="时段筛选">
          <button
            type="button"
            class="pill"
            :class="{ active: period === 'month' }"
            role="radio"
            :aria-checked="period === 'month'"
            @click="period = 'month'"
          >
            本月
          </button>
          <button
            type="button"
            class="pill"
            :class="{ active: period === 'year' }"
            role="radio"
            :aria-checked="period === 'year'"
            @click="period = 'year'"
          >
            本年
          </button>
          <button
            type="button"
            class="pill"
            :class="{ active: period === 'all' }"
            role="radio"
            :aria-checked="period === 'all'"
            @click="period = 'all'"
          >
            全部
          </button>
          <button
            type="button"
            class="pill"
            :class="{ active: period === 'custom' }"
            role="radio"
            :aria-checked="period === 'custom'"
            @click="period = 'custom'"
          >
            自定义
          </button>
        </div>
      </div>
      <div v-if="period === 'custom'" class="filter-group">
        <label class="filter-label">日期范围</label>
        <input
          type="date"
          v-model="dateFrom"
          class="date-input"
          placeholder="开始日期"
        />
        <span class="date-separator">至</span>
        <input
          type="date"
          v-model="dateTo"
          class="date-input"
          placeholder="结束日期"
        />
      </div>
      <div class="filter-group">
        <label for="lottery-filter" class="filter-label">彩种</label>
        <select id="lottery-filter" v-model="lotteryCode" class="lottery-select">
          <option value="">全部彩种</option>
          <option v-for="l in LOTTERIES" :key="l.code" :value="l.code">
            {{ l.name }}
          </option>
        </select>
      </div>
    </section>

    <State v-if="loading" type="loading" title="加载统计中…" />
    <State v-else-if="error" type="error" :title="error" @action="load" />
    <State v-else-if="!data" type="empty" title="暂无统计数据" @action="load" />

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
              <div class="stat-value warning">{{ data.summary.pending_amount }} 笔</div>
            </div>
            <div class="stat-card">
              <div class="stat-label">净盈亏</div>
              <div class="stat-value" :class="data.summary.net >= 0 ? 'profit' : 'loss'">
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
            <div class="stat-card">
              <div class="stat-label">中奖率</div>
              <div class="stat-value">{{ (data.summary.win_rate * 100).toFixed(1) }}%</div>
            </div>
            <div class="stat-card">
              <div class="stat-label">公益贡献</div>
              <div class="stat-value">{{ fmtMoney(data.summary.welfare_contribution) }}</div>
            </div>
          </div>
        </div>
      </section>

      <section class="card">
        <div class="card-header">
          <h2 class="card-title">中奖等级分布</h2>
        </div>
        <div class="card-body">
          <div v-if="wins.length === 0" class="empty-tip">暂无中奖记录</div>
          <div v-else class="charts-row">
            <div class="chart-panel">
              <h3>按笔数</h3>
              <div ref="tierPieRef" class="chart-box" />
            </div>
            <div class="chart-panel">
              <h3>按金额占比</h3>
              <div ref="amountPieRef" class="chart-box" />
            </div>
          </div>
        </div>
      </section>

      <section class="card">
        <div class="card-header">
          <h2 class="card-title">月度投入 / 中奖</h2>
        </div>
        <div class="card-body">
          <div ref="monthlyBarRef" class="chart-box chart-box-lg" />
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

.charts-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
}

@media (max-width: 640px) {
  .charts-row {
    grid-template-columns: 1fr;
  }
}

.chart-panel h3 {
  font-size: var(--text-md);
  color: var(--muted);
  text-align: center;
  margin-bottom: 8px;
}

.chart-box {
  width: 100%;
  height: 280px;
}

.chart-box-lg {
  height: 320px;
}

.empty-tip {
  color: var(--muted);
  text-align: center;
  padding: 20px;
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
