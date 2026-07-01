<script setup lang="ts">
import { ref, onMounted, computed } from 'vue';
import { apiGet, apiPost } from '../api/client';
import { fmtMoney } from '../lib/format';
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
const loading = ref(false);
const error = ref('');
const filter = ref<'all' | 'pending' | 'claimed'>('all');

async function load() {
  loading.value = true;
  error.value = '';
  try {
    records.value = await apiGet<WinRecord[]>('/api/comparisons?win_only=true');
  } catch (err) {
    error.value = err instanceof Error ? err.message : '加载失败';
  } finally {
    loading.value = false;
  }
}

const filtered = computed(() => {
  if (!records.value) return [];
  if (filter.value === 'all') return records.value;
  return records.value.filter((r) => r.claim_status === filter.value);
});

async function claim(record: WinRecord) {
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

onMounted(() => {
  void load();
});
</script>

<template>
  <div class="win-records">
    <header class="page-header">
      <h1>中奖记录</h1>
    </header>

    <div class="filters" role="group" aria-label="兑奖状态筛选">
      <button
        type="button"
        class="filter-btn"
        :class="{ active: filter === 'all' }"
        @click="filter = 'all'"
      >
        全部
      </button>
      <button
        type="button"
        class="filter-btn"
        :class="{ active: filter === 'pending' }"
        @click="filter = 'pending'"
      >
        待兑奖
      </button>
      <button
        type="button"
        class="filter-btn"
        :class="{ active: filter === 'claimed' }"
        @click="filter = 'claimed'"
      >
        已领取
      </button>
    </div>

    <State v-if="loading" type="loading" title="加载中奖记录…" />
    <State v-else-if="error" type="error" :title="error" @action="load" />
    <State
      v-else-if="filtered.length === 0"
      type="empty"
      :title="filter === 'all' ? '本期暂无中奖，理性购彩' : '该筛选下暂无记录'"
      @action="load"
    />

    <ul v-else class="record-list" role="list">
      <li
        v-for="record in filtered"
        :key="record.id"
        class="record-card"
        :class="{ urgent: record.claim_status === 'pending' && daysLeft(record.deadline) !== null && daysLeft(record.deadline)! <= 15 }"
      >
        <div class="record-main">
          <div class="record-title">
            {{ record.lottery_name }} 第{{ record.draw_no }}期
            <span class="tier" v-if="record.prize_tier">{{ record.prize_tier }}等奖</span>
          </div>
          <div class="record-amount">{{ fmtMoney(record.prize_amount) }}</div>
          <div class="record-detail">
            我的号码: {{ record.numbers_json }}
          </div>
          <div class="record-detail">
            命中: {{ record.hits_json }}
          </div>
        </div>
        <div class="record-side">
          <span
            class="status-badge"
            :class="record.claim_status || 'unknown'"
          >
            {{ record.claim_status === 'pending' ? '待兑奖' : record.claim_status === 'claimed' ? '已领取' : '无兑奖' }}
          </span>
          <div
            v-if="record.claim_status === 'pending' && record.deadline"
            class="countdown"
            :class="{ urgent: daysLeft(record.deadline)! <= 15 }"
          >
            剩余 {{ daysLeft(record.deadline) }} 天
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

.filters {
  display: flex;
  gap: 8px;
  margin-bottom: 20px;
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
