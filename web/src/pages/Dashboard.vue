<script setup lang="ts">
import { ref, computed, onMounted } from 'vue';
import { useRouter } from 'vue-router';
import { apiGet } from '../api/client';
import { fmtMoney, fmtShortDate } from '../lib/format';
import State from '../components/State.vue';
import BallNumber from '../components/BallNumber.vue';

interface LatestDraw {
  lottery_code: string;
  lottery_name: string;
  draw_no: string;
  draw_date: string;
  numbers_json: string;
  verified: boolean;
  single_source: boolean;
}

interface PendingClaim {
  id: number;
  comparison_id: number;
  lottery_code: string;
  lottery_name: string;
  draw_no: string;
  prize_tier: number | null;
  prize_amount: number | null;
  deadline: string;
  status: string;
  days_left: number;
}

interface Summary {
  total_cost: number;
  total_prize: number;
  pending_amount: number;
  net: number;
  win_count: number;
  ticket_count: number;
}

interface DashboardData {
  latest_draws: LatestDraw[];
  pending_claims: PendingClaim[];
  recent_hits: unknown[];
  summary: Summary;
}

const router = useRouter();
const data = ref<DashboardData | null>(null);
const loading = ref(false);
const error = ref('');

const hasClaims = computed(() => (data.value?.pending_claims.length ?? 0) > 0);
const hasDraws = computed(() => (data.value?.latest_draws.length ?? 0) > 0);

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

async function gotoClaim(claimId: number) {
  await router.push(`/wins?claim=${claimId}`);
}

onMounted(() => {
  void load();
});
</script>

<template>
  <div class="dashboard">
    <header class="page-header">
      <h1>仪表盘</h1>
      <p class="subtitle">开奖自动核对 · 以官方开奖为准</p>
    </header>

    <State v-if="loading" type="loading" title="加载仪表盘中…" />
    <State
      v-else-if="error"
      type="error"
      :title="error"
      @action="load"
    />
    <State
      v-else-if="!data"
      type="empty"
      title="暂无数据"
      @action="load"
    />

    <template v-else>
      <!-- 待兑奖（首屏最高优先级） -->
      <section v-if="hasClaims" class="card" aria-labelledby="claims-title">
        <div class="card-header">
          <div>
            <h2 id="claims-title" class="card-title">待兑奖</h2>
            <p class="card-subtitle">中奖后 60 天内有效 · 临近过期标红</p>
          </div>
        </div>
        <div class="card-body">
          <ul class="claim-list" role="list">
            <li
              v-for="claim in data.pending_claims"
              :key="claim.id"
              class="claim-item"
              :class="{ urgent: claim.days_left <= 15 }"
            >
              <div class="claim-info">
                <div class="claim-amount">{{ fmtMoney(claim.prize_amount) }}</div>
                <div class="claim-detail">
                  {{ claim.lottery_name }} 第{{ claim.draw_no }}期
                  <span v-if="claim.prize_tier">· {{ claim.prize_tier }}等奖</span>
                </div>
              </div>
              <div class="claim-actions">
                <div class="claim-countdown">
                  <div class="countdown-value" :class="{ urgent: claim.days_left <= 15 }">
                    {{ claim.days_left }}
                  </div>
                  <div class="countdown-label">天后过期</div>
                </div>
                <button
                  type="button"
                  class="claim-btn"
                  @click="gotoClaim(claim.id)"
                >
                  已领取
                </button>
              </div>
            </li>
          </ul>
        </div>
      </section>

      <!-- 盈亏速览 -->
      <section class="card" aria-labelledby="summary-title">
        <div class="card-header">
          <h2 id="summary-title" class="card-title">盈亏速览</h2>
        </div>
        <div class="card-body">
          <div class="stats-row">
            <div class="stat-card">
              <div class="stat-label">累计投入</div>
              <div class="stat-value">{{ fmtMoney(data.summary.total_cost) }}</div>
            </div>
            <div class="stat-card">
              <div class="stat-label">累计中奖</div>
              <div class="stat-value profit">{{ fmtMoney(data.summary.total_prize) }}</div>
            </div>
            <div class="stat-card">
              <div class="stat-label">净盈亏</div>
              <div class="stat-value" :class="data.summary.net >= 0 ? 'profit' : 'loss'">
                {{ fmtMoney(data.summary.net) }}
              </div>
            </div>
            <div class="stat-card">
              <div class="stat-label">追注数量</div>
              <div class="stat-value">{{ data.summary.ticket_count }}</div>
            </div>
          </div>
        </div>
      </section>

      <!-- 开奖概览 -->
      <section v-if="hasDraws" class="card" aria-labelledby="draws-title">
        <div class="card-header">
          <div>
            <h2 id="draws-title" class="card-title">近期开奖概览</h2>
            <p class="card-subtitle">最近一期开奖号码 · 以官方开奖为准</p>
          </div>
        </div>
        <div class="card-body">
          <div class="lottery-grid">
            <article
              v-for="draw in data.latest_draws"
              :key="draw.lottery_code"
              class="lottery-card"
            >
              <div class="lottery-card-header">
                <div>
                  <div class="lottery-name">{{ draw.lottery_name }}</div>
                  <div class="lottery-official">第{{ draw.draw_no }}期</div>
                </div>
                <div class="lottery-date">{{ fmtShortDate(draw.draw_date) }}</div>
              </div>
              <BallNumber
                :numbers-json="draw.numbers_json"
                :lottery-code="draw.lottery_code"
              />
              <div v-if="!draw.verified" class="source-tag warn">校验中</div>
              <div v-else-if="draw.single_source" class="source-tag single">单源校验</div>
              <div v-else class="source-tag ok">双源校验</div>
            </article>
          </div>
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
  letter-spacing: -0.021em;
}

.subtitle {
  font-size: var(--text-md);
  color: var(--muted);
  margin-top: 4px;
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
  letter-spacing: -0.021em;
}

.card-subtitle {
  font-size: var(--text-md);
  color: var(--muted);
  margin-top: 2px;
}

.card-body {
  padding: 16px 20px 20px;
}

.lottery-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 16px;
}

.lottery-card {
  background: var(--surface-2);
  border-radius: 16px;
  padding: 16px;
}

.lottery-card-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  margin-bottom: 12px;
}

.lottery-name {
  font-size: var(--text-lg);
  font-weight: 600;
}

.lottery-official,
.lottery-date {
  font-size: var(--text-sm);
  color: var(--muted);
}

.source-tag {
  display: inline-flex;
  margin-top: 10px;
  padding: 3px 10px;
  border-radius: 20px;
  font-size: var(--text-xs);
  font-weight: 600;
}

.source-tag.ok {
  background: #dcfce7;
  color: #166534;
}

.source-tag.single {
  background: #fef3c7;
  color: #92400e;
}

.source-tag.warn {
  background: #fee2e2;
  color: #991b1b;
}

.claim-list {
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 12px;
  margin: 0;
  padding: 0;
}

.claim-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 18px;
  background: var(--surface-2);
  border-radius: 14px;
  border: 1px solid transparent;
}

.claim-item.urgent {
  border-color: #fecaca;
  background: #fef2f2;
}

.claim-amount {
  font-size: var(--text-xl);
  font-weight: 600;
}

.claim-detail {
  font-size: var(--text-sm);
  color: var(--muted);
  margin-top: 2px;
}

.claim-actions {
  display: flex;
  align-items: center;
  gap: 14px;
}

.claim-countdown {
  text-align: right;
}

.countdown-value {
  font-size: var(--text-lg);
  font-weight: 600;
}

.countdown-value.urgent {
  color: var(--danger);
}

.countdown-label {
  font-size: var(--text-xs);
  color: var(--muted);
}

.claim-btn {
  padding: 8px 16px;
  border-radius: var(--radius);
  border: none;
  background: var(--success);
  color: #fff;
  font-size: var(--text-sm);
  font-weight: 600;
  cursor: pointer;
  min-height: 44px;
}

.stats-row {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 12px;
}

@media (min-width: 768px) {
  .stats-row {
    grid-template-columns: repeat(4, 1fr);
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
  letter-spacing: -0.021em;
}

.stat-value.profit {
  color: var(--success);
}

.stat-value.loss {
  color: var(--danger);
}
</style>
