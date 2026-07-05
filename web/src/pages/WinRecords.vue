<script setup lang="ts">
import { ref, onMounted, computed } from 'vue';
import { apiGet, apiPost } from '../api/client';
import { fmtMoney } from '../lib/format';
import { LOTTERIES } from '../lib/lotteries';
import State from '../components/State.vue';

interface WinRecord {
  id: number;
  lottery_code: string;
  lottery_name: string;
  draw_no: string;
  draw_date: string;
  numbers_json: string;
  ticket_label: string | null;
  hits_json: string;
  prize_tier: number | null;
  prize_amount: number | null;
  is_win: boolean;
  created_at: string;
  claim_status: string | null;
  claim_id: number | null;
  deadline: string | null;
}

const records = ref<WinRecord[] | null>(null);
const error = ref('');
const filterStatus = ref<'all' | 'pending' | 'claimed'>('all');
const filterLottery = ref('');
const loadingRecords = ref(false);

// Period filter: month/year/all/custom (spec §12.2 row 5)
type PeriodType = 'month' | 'year' | 'all' | 'custom';
const filterPeriod = ref<PeriodType>('month');
const filterDateFrom = ref('');
const filterDateTo = ref('');

function buildQuery(): string {
  const params = new URLSearchParams();
  params.set('win_only', 'true');
  params.set('period', filterPeriod.value);
  if (filterPeriod.value === 'custom') {
    if (filterDateFrom.value) params.set('date_from', filterDateFrom.value);
    if (filterDateTo.value) params.set('date_to', filterDateTo.value);
  }
  return `/api/comparisons?${params.toString()}`;
}

async function load() {
  loadingRecords.value = true;
  error.value = '';
  try {
    records.value = await apiGet<WinRecord[]>(buildQuery());
  } catch (err) {
    error.value = err instanceof Error ? err.message : '加载失败';
  } finally {
    loadingRecords.value = false;
  }
}

async function applyPeriod() {
  await load();
}

function resetCustomDateOnPeriodChange() {
  if (filterPeriod.value !== 'custom') {
    filterDateFrom.value = '';
    filterDateTo.value = '';
    // Auto-refresh for non-custom periods to keep stats in sync with selection
    void load();
  }
}

const filtered = computed(() => {
  if (!records.value) return [];
  let list = records.value;
  if (filterStatus.value !== 'all') {
    list = list.filter((r) => r.claim_status === filterStatus.value);
  }
  if (filterLottery.value) {
    list = list.filter((r) => r.lottery_code === filterLottery.value);
  }
  return list.map((r) => ({
    ...r,
    _days: daysLeft(r.deadline),
  }));
});

const stats = computed(() => {
  const all = records.value || [];
  const pending = all.filter((r) => r.claim_status === 'pending');
  const claimed = all.filter((r) => r.claim_status === 'claimed');
  const expired = pending.filter((r) => r.deadline && new Date(r.deadline) < new Date());

  const sumPrize = (list: typeof all) =>
    list.reduce((sum, r) => sum + (r.prize_amount ?? 0), 0);

  return {
    total: all.length,
    totalAmount: sumPrize(all),
    pending: pending.length,
    pendingAmount: sumPrize(pending),
    claimed: claimed.length,
    claimedAmount: sumPrize(claimed),
    expired: expired.length,
    expiredAmount: sumPrize(expired),
  };
});

async function claim(record: typeof filtered.value[number]) {
  if (!record.claim_id) return;
  try {
    await apiPost(`/claims/${record.claim_id}/claim`);
    await load();
  } catch (err) {
    error.value = err instanceof Error ? err.message : '领取失败';
  }
}

function daysLeft(deadline: string | null): number | null {
  if (!deadline) return null;
  const diff = new Date(deadline).getTime() - Date.now();
  return Math.max(0, Math.floor(diff / (1000 * 60 * 60 * 24)));
}

function needsTaxHint(amount: number | null): boolean {
  return amount != null && amount >= 1_000_000; // >=1万元（10000*100分）
}

onMounted(() => {
  void load();
});
</script>

<template>
  <div class="win-records">
    <header class="page-header">
      <h1>中奖记录</h1>
    </header>

    <State v-if="loadingRecords" type="loading" title="加载中奖记录…" />
    <State v-else-if="error" type="error" :title="error" @action="load" />
    <State
      v-else-if="!records || records.length === 0"
      type="empty"
      title="暂无中奖记录"
      @action="load"
    />

    <template v-else>
      <!-- 统计概览 4 卡（金额与笔数，随筛选联动） -->
      <div class="stats-row">
        <div class="stat-mini">
          <div class="stat-mini-label">累计中奖</div>
          <div class="stat-mini-value">{{ fmtMoney(stats.totalAmount) }}</div>
          <div class="stat-mini-sub">{{ stats.total }} 笔</div>
        </div>
        <div class="stat-mini stat-mini--pending">
          <div class="stat-mini-label">待兑奖</div>
          <div class="stat-mini-value">{{ fmtMoney(stats.pendingAmount) }}</div>
          <div class="stat-mini-sub">{{ stats.pending }} 笔</div>
        </div>
        <div class="stat-mini stat-mini--claimed">
          <div class="stat-mini-label">已领取</div>
          <div class="stat-mini-value">{{ fmtMoney(stats.claimedAmount) }}</div>
          <div class="stat-mini-sub">{{ stats.claimed }} 笔</div>
        </div>
        <div class="stat-mini stat-mini--expired">
          <div class="stat-mini-label">已过期</div>
          <div class="stat-mini-value">{{ fmtMoney(stats.expiredAmount) }}</div>
          <div class="stat-mini-sub">{{ stats.expired }} 笔</div>
        </div>
      </div>

      <!-- 筛选条 -->
      <div class="filters">
        <div class="filter-group" role="group" aria-label="时段筛选">
          <select
            v-model="filterPeriod"
            class="lottery-select"
            data-testid="filter-period"
            aria-label="时段筛选"
            @change="resetCustomDateOnPeriodChange"
          >
            <option value="month">本月</option>
            <option value="year">本年</option>
            <option value="all">全部</option>
            <option value="custom">自定义</option>
          </select>
          <template v-if="filterPeriod === 'custom'">
            <input
              v-model="filterDateFrom"
              type="date"
              class="lottery-select"
              data-testid="filter-date-from"
              aria-label="开始日期"
            />
            <input
              v-model="filterDateTo"
              type="date"
              class="lottery-select"
              data-testid="filter-date-to"
              aria-label="结束日期"
            />
          </template>
          <button v-if="filterPeriod === 'custom'" type="button" class="filter-btn" data-testid="apply-period" @click="applyPeriod">应用</button>
        </div>

        <div class="filter-group" role="group" aria-label="兑奖状态筛选">
          <button type="button" class="filter-btn" :class="{ active: filterStatus === 'all' }" data-testid="filter-status-all" @click="filterStatus = 'all'">全部</button>
          <button type="button" class="filter-btn" :class="{ active: filterStatus === 'pending' }" data-testid="filter-status-pending" @click="filterStatus = 'pending'">待兑奖</button>
          <button type="button" class="filter-btn" :class="{ active: filterStatus === 'claimed' }" data-testid="filter-status-claimed" @click="filterStatus = 'claimed'">已领取</button>
        </div>

        <select v-model="filterLottery" class="lottery-select" aria-label="彩种筛选">
          <option value="">全部彩种</option>
          <option v-for="l in LOTTERIES" :key="l.code" :value="l.code">{{ l.name }}</option>
        </select>
      </div>

      <ul class="record-list" role="list">
        <li
          v-for="record in filtered"
          :key="record.id"
          class="record-card"
          :class="{ urgent: record.claim_status === 'pending' && record._days !== null && record._days <= 15 }"
        >
          <div class="record-main">
            <div class="record-title">
              {{ record.lottery_name }} 第{{ record.draw_no }}期
              <span v-if="record.prize_tier" class="tier">{{ record.prize_tier }}等奖</span>
            </div>
            <div class="record-amount">{{ fmtMoney(record.prize_amount) }}</div>
            <div v-if="needsTaxHint(record.prize_amount)" class="tax-hint">
              <span class="tax-icon">&#9432;</span>
              单笔中奖超 1 万元需缴纳 20% 偶然所得税，实得约 {{ fmtMoney((record.prize_amount ?? 0) * 0.8) }}
            </div>
            <div class="record-detail">我的号码: {{ record.numbers_json }}</div>
            <div class="record-detail">命中: {{ record.hits_json }}</div>
          </div>
          <div class="record-side">
            <span class="status-badge" :class="record.claim_status || 'unknown'">
              {{ record.claim_status === 'pending' ? '待兑奖' : record.claim_status === 'claimed' ? '已领取' : '无兑奖' }}
            </span>
            <div
              v-if="record.claim_status === 'pending' && record._days !== null"
              class="countdown"
              :class="{ urgent: record._days! <= 15 }"
            >
              剩余 {{ record._days }} 天
            </div>
            <button
              v-if="record.claim_status === 'pending' && record.claim_id"
              type="button"
              class="claim-btn"
              @click="claim(record)"
            >
              已领取
            </button>
          </div>
        </li>
      </ul>

      <!-- 兑奖须知 -->
      <footer class="claim-footer">
        <h3>兑奖须知</h3>
        <ul>
          <li>开奖之日起 60 个自然日内兑奖，逾期视为弃奖。</li>
          <li>单注中奖金额 ≤ 1 万元：站点通兑；＞ 1 万元：须至彩票中心兑奖。</li>
          <li>单注中奖金额超过 1 万元的部分缴纳 20% 偶然所得税。</li>
          <li>请妥善保管原始彩票或购买凭证，兑奖时需出示。</li>
        </ul>
      </footer>
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

.stats-row {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 10px;
  margin-bottom: 16px;
}

@media (max-width: 640px) {
  .stats-row {
    grid-template-columns: repeat(2, 1fr);
  }
}

.stat-mini {
  padding: 12px;
  background: var(--surface);
  border-radius: 14px;
  text-align: center;
  border: 1px solid var(--border);
}

.stat-mini--pending {
  border-color: #fbbf24;
}

.stat-mini--claimed {
  border-color: #34d399;
}

.stat-mini--expired {
  border-color: #f87171;
}

.stat-mini-label {
  font-size: var(--text-xs);
  color: var(--muted);
  margin-bottom: 4px;
}

.stat-mini-value {
  font-size: var(--text-lg);
  font-weight: 600;
}

.stat-mini-sub {
  font-size: var(--text-xs);
  color: var(--muted);
  margin-top: 2px;
}

.filters {
  display: flex;
  gap: 10px;
  align-items: center;
  margin-bottom: 20px;
  flex-wrap: wrap;
}

.filter-group {
  display: flex;
  gap: 8px;
}

.filter-btn {
  padding: 8px 16px;
  border: 1px solid var(--border);
  border-radius: 20px;
  background: var(--surface);
  color: var(--muted);
  font-size: var(--text-md);
  cursor: pointer;
  min-height: 44px;
}

.filter-btn.active {
  background: var(--accent);
  color: #fff;
  border-color: var(--accent);
}

.lottery-select {
  padding: 8px 12px;
  border: 1px solid var(--border);
  border-radius: 20px;
  background: var(--surface);
  color: var(--fg);
  font-size: var(--text-md);
  min-height: 44px;
}

.record-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.record-card {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 16px 20px;
  background: var(--surface);
  border-radius: 18px;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.04);
  border: 1px solid transparent;
}

.record-card.urgent {
  border-color: #fecaca;
  background: #fef2f2;
}

.record-title {
  font-weight: 600;
  font-size: var(--text-lg);
}

.tier {
  display: inline-flex;
  margin-left: 8px;
  padding: 2px 8px;
  background: #dbeafe;
  color: #1e40af;
  border-radius: 20px;
  font-size: var(--text-xs);
}

.record-amount {
  font-size: var(--text-xl);
  font-weight: 600;
  color: var(--success);
  margin-top: 4px;
}

.tax-hint {
  margin-top: 4px;
  padding: 6px 10px;
  background: #fef3c7;
  border-radius: 8px;
  color: #92400e;
  font-size: var(--text-xs);
  line-height: 1.4;
}

.tax-icon {
  margin-right: 4px;
}

.record-detail {
  font-size: var(--text-sm);
  color: var(--muted);
  margin-top: 4px;
}

.record-side {
  text-align: right;
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 8px;
}

.status-badge {
  display: inline-flex;
  padding: 3px 10px;
  border-radius: 20px;
  font-size: var(--text-xs);
  font-weight: 600;
}

.status-badge.pending {
  background: #fef3c7;
  color: #92400e;
}

.status-badge.claimed {
  background: #dcfce7;
  color: #166534;
}

.status-badge.unknown {
  background: #f3f4f6;
  color: var(--muted);
}

.countdown {
  font-size: var(--text-sm);
  color: var(--muted);
}

.countdown.urgent {
  color: var(--danger);
  font-weight: 600;
}

.claim-btn {
  padding: 8px 16px;
  border: none;
  border-radius: var(--radius);
  background: var(--success);
  color: #fff;
  font-size: var(--text-sm);
  font-weight: 600;
  cursor: pointer;
  min-height: 44px;
}

.claim-footer {
  margin-top: 24px;
  padding: 20px;
  background: var(--surface);
  border-radius: 18px;
  font-size: var(--text-sm);
}

.claim-footer h3 {
  font-size: var(--text-md);
  font-weight: 600;
  margin-bottom: 10px;
}

.claim-footer ul {
  padding-left: 18px;
  color: var(--muted);
  line-height: 1.8;
}

.claim-footer li {
  margin-bottom: 4px;
}

@media (max-width: 640px) {
  .record-card {
    flex-direction: column;
    align-items: flex-start;
  }

  .record-side {
    align-items: flex-start;
    text-align: left;
    width: 100%;
    flex-direction: row;
    justify-content: space-between;
  }
}
</style>
