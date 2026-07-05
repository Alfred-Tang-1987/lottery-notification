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
  win_rate: number;
  welfare_contribution: number;
}

interface CalendarItem {
  lottery_code: string;
  lottery_name: string;
  category: string;
  draw_days: number[];
  next_draw_date: string | null;
}

interface AgencyItem {
  name: string;
  address: string;
  category: string;
  lat: number;
  lng: number;
  distance_m: number | null;
}

interface Hit {
  id: number;
  lottery_name: string;
  draw_no: string;
  prize_tier: number | null;
  prize_amount: number | null;
  claim_status: string | null;
}

interface DashboardData {
  latest_draws: LatestDraw[];
  pending_claims: PendingClaim[];
  recent_hits: Hit[];
  summary: Summary;
}

const router = useRouter();
const data = ref<DashboardData | null>(null);
const calendar = ref<CalendarItem[] | null>(null);
const agencies = ref<AgencyItem[] | null>(null);
const loading = ref(false);
const error = ref('');

const hasClaims = computed(() => (data.value?.pending_claims?.length ?? 0) > 0);
const hasDraws = computed(() => (data.value?.latest_draws?.length ?? 0) > 0);
const hasCalendar = computed(() => (calendar.value?.length ?? 0) > 0);
const hasAgencies = computed(() => (agencies.value?.length ?? 0) > 0);

// Period filter for summary/card section (spec §12.2 row 2)
type PeriodType = 'month' | 'year' | 'all' | 'custom';
const filterPeriod = ref<PeriodType>('month');
const filterDateFrom = ref('');
const filterDateTo = ref('');

function buildDashboardQuery(): string {
  const params = new URLSearchParams();
  params.set('period', filterPeriod.value);
  if (filterPeriod.value === 'custom') {
    if (filterDateFrom.value) params.set('date_from', filterDateFrom.value);
    if (filterDateTo.value) params.set('date_to', filterDateTo.value);
  }
  return `/api/dashboard?${params.toString()}`;
}

async function load() {
  loading.value = true;
  error.value = '';
  try {
    const [dashResult, calResult, agResult] = await Promise.allSettled([
      apiGet<DashboardData>(buildDashboardQuery()),
      apiGet<CalendarItem[]>('/api/dashboard/calendar'),
      apiGet<AgencyItem[]>('/api/dashboard/agencies'),
    ]);

    // Always assign independent calendar/agency results before deciding dashboard fate
    // so a dashboard-only failure does not silently drop calendar and agencies.
    if (calResult.status === 'fulfilled') {
      calendar.value = calResult.value;
    } else if (calendar.value === null) {
      calendar.value = [];
    }

    if (agResult.status === 'fulfilled') {
      agencies.value = agResult.value;
    } else if (agencies.value === null) {
      agencies.value = [];
    }

    if (dashResult.status === 'fulfilled') {
      data.value = dashResult.value;
    } else {
      // Surface dashboard failures (including period-change failures when stale
      // data already exists) so the user knows the new period failed instead of
      // silently seeing stale data. Calendar/agencies render independently below
      // (outside the v-else error block) for partial degradation.
      if (data.value === null) {
        // Fresh load: provide a stable empty fallback so the template can still
        // render calendar/agencies; the error is surfaced via error.value.
        data.value = {
          latest_draws: [],
          pending_claims: [],
          recent_hits: [],
          summary: {
            total_cost: 0,
            total_prize: 0,
            pending_amount: 0,
            net: 0,
            win_count: 0,
            ticket_count: 0,
            win_rate: 0,
            welfare_contribution: 0,
          },
        };
      }
      throw dashResult.reason;
    }
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err || '加载失败');
  } finally {
    loading.value = false;
  }
}

async function applyPeriod() {
  await load();
}

function resetCustomDateOnPeriodChange() {
  if (filterPeriod.value !== 'custom') {
    filterDateFrom.value = '';
    filterDateTo.value = '';
    // Auto-refresh for non-custom periods; custom mode intentionally waits for the
    // 应用 button because the date range is empty until the user fills it.
    void load();
  }
}

async function gotoClaim(claimId: number) {
  try {
    await router.push(`/wins?claim=${claimId}`);
  } catch (err) {
    // Skip cancelled navigations (e.g. a navigation guard aborted the push) —
    // these are normal control flow, not genuine failures. Surfacing "跳转失败"
    // for a guard-aborted navigation would mislead the user.
    if (isNavigationCancelled(err)) return;
    // Router failure is a transient navigation issue, not a data-loading error;
    // surface it briefly so the user understands why nothing happened.
    const message = err instanceof Error ? err.message : String(err || '跳转失败');
    error.value = `跳转失败：${message}`;
    setTimeout(() => {
      if (error.value && error.value.startsWith('跳转失败')) error.value = '';
    }, 3000);
  }
}

// vue-router throws a NavigationFailure on aborted/redirected/duplicated
// navigations; these are normal control flow, not errors. Returns true for
// failures that should be silently ignored.
function isNavigationCancelled(err: unknown): boolean {
  if (err == null || typeof err !== 'object') return false;
  // NavigationFailure exposes a `type` property (aborted|cancelled|duplicated|noRoute).
  const type = (err as { type?: unknown }).type;
  // noRoute is a genuine failure and stays surfaced; the others are control flow.
  return type === 'aborted' || type === 'cancelled' || type === 'duplicated';
}

function openMapUrl(lat: number, lng: number, name: string): string {
  // High-level map intent compatible with iOS Safari and desktop; user can choose preferred app.
  // The q parameter is the human-readable agency name (URL-encoded) so the map
  // pin label is meaningful, not raw "lat,lng" coordinates.
  return `https://maps.apple.com/?ll=${lat},${lng}&q=${encodeURIComponent(name)}`;
}

function openMap(agency: AgencyItem) {
  const url = openMapUrl(agency.lat, agency.lng, agency.name);
  const opened = window.open(url, '_blank');
  if (!opened) {
    window.location.href = url;
  }
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

      <!-- 我的命中（D5 第二优先级） -->
      <section v-if="data.recent_hits && data.recent_hits.length > 0" class="card" aria-labelledby="hits-title">
        <div class="card-header">
          <h2 id="hits-title" class="card-title">我的命中</h2>
        </div>
        <div class="card-body">
          <div class="hits-grid">
            <article v-for="hit in data.recent_hits" :key="hit.id" class="hit-card">
              <div class="hit-header">
                <span class="hit-lottery">{{ hit.lottery_name }}</span>
                <span class="hit-draw">第{{ hit.draw_no }}期</span>
              </div>
              <div class="hit-tier" v-if="hit.prize_tier">{{ hit.prize_tier }}等奖</div>
              <div class="hit-amount">{{ fmtMoney(hit.prize_amount) }}</div>
              <div class="hit-status">
                <span v-if="hit.claim_status === 'claimed'" class="tag claimed">已领取</span>
                <span v-else-if="hit.claim_status === 'pending'" class="tag pending">待兑奖</span>
                <span v-else class="tag none">无兑奖</span>
              </div>
            </article>
          </div>
        </div>
      </section>

      <!-- 盈亏速览（D5 第三优先级，含公益贡献卡） -->
      <section class="card" aria-labelledby="summary-title">
        <div class="card-header">
          <h2 id="summary-title" class="card-title">盈亏速览</h2>
          <div class="filter-group" role="group" aria-label="时段筛选">
            <select
              v-model="filterPeriod"
              class="lottery-select"
              data-testid="dashboard-period"
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
                data-testid="dashboard-date-from"
                aria-label="开始日期"
              />
              <input
                v-model="filterDateTo"
                type="date"
                class="lottery-select"
                data-testid="dashboard-date-to"
                aria-label="结束日期"
              />
              <button type="button" class="filter-btn" data-testid="dashboard-apply-period" @click="applyPeriod">应用</button>
            </template>
          </div>
        </div>
        <div class="card-body">
          <div class="stats-row">
            <div class="stat-card">
              <div class="stat-label">累计投入</div>
              <div class="stat-value">{{ fmtMoney(data.summary?.total_cost) }}</div>
            </div>
            <div class="stat-card">
              <div class="stat-label">累计中奖</div>
              <div class="stat-value profit">{{ fmtMoney(data.summary?.total_prize) }}</div>
            </div>
            <div class="stat-card">
              <div class="stat-label">净盈亏</div>
              <div class="stat-value" :class="(data.summary?.net ?? 0) >= 0 ? 'profit' : 'loss'">
                {{ fmtMoney(data.summary?.net) }}
              </div>
            </div>
            <div class="stat-card">
              <div class="stat-label">追注数量</div>
              <div class="stat-value">{{ data.summary?.ticket_count }}</div>
            </div>
          </div>
          <div v-if="data.summary?.welfare_contribution != null" class="welfare-card">
            <div>
              <div class="welfare-label">公益贡献</div>
              <div class="welfare-hint">按各彩种公益金比例累计</div>
            </div>
            <div class="welfare-value">{{ fmtMoney(data.summary?.welfare_contribution) }}</div>
          </div>
        </div>
      </section>

      <!-- 开奖概览（D5 第四优先级） -->
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

    <!-- 开奖日历（D5 次屏）— rendered outside the dashboard v-else chain so a
         dashboard-only failure still surfaces calendar data (partial
         degradation, spec §12.4 PARTIAL state). -->
    <section v-if="hasCalendar" class="card" aria-labelledby="calendar-title">
      <div class="card-header">
        <div>
          <h2 id="calendar-title" class="card-title">开奖日历</h2>
          <p class="card-subtitle">按已启用彩种过滤 · 下一期预告</p>
        </div>
      </div>
      <div class="card-body">
        <ul class="calendar-list" role="list">
          <li v-for="item in calendar" :key="item.lottery_code" class="calendar-item">
            <div class="calendar-main">
              <span class="calendar-name">{{ item.lottery_name }}</span>
              <span class="calendar-category" :class="item.category">
                {{ item.category === 'welfare' ? '福彩' : '体彩' }}
              </span>
            </div>
            <div class="calendar-next">
              <span v-if="item.next_draw_date">{{ fmtShortDate(item.next_draw_date) }}</span>
              <span v-else class="calendar-none">—</span>
            </div>
          </li>
        </ul>
      </div>
    </section>

    <!-- 附近代销点（D5 次屏，MVP mock）— rendered outside the dashboard v-else
         chain for the same partial-degradation reason as the calendar above. -->
    <section v-if="hasAgencies" class="card" aria-labelledby="agencies-title">
      <div class="card-header">
        <div>
          <h2 id="agencies-title" class="card-title">附近代销点</h2>
          <p class="card-subtitle">便民查询 · 点击打开地图导航</p>
        </div>
      </div>
      <div class="card-body">
        <ul class="agency-list" role="list">
          <li
            v-for="agency in agencies"
            :key="agency.name"
            class="agency-item"
          >
            <button type="button" class="agency-card" @click="openMap(agency)">
              <div class="agency-main">
                <span class="agency-name">{{ agency.name }}</span>
                <span class="agency-category" :class="agency.category">
                  {{ agency.category === 'welfare' ? '福彩' : '体彩' }}
                </span>
              </div>
              <div class="agency-address">{{ agency.address }}</div>
              <div v-if="agency.distance_m != null" class="agency-distance">{{ agency.distance_m }} 米</div>
            </button>
          </li>
        </ul>
      </div>
    </section>
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

.welfare-card {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 16px;
  padding: 16px 18px;
  background: var(--surface-2);
  border-radius: 16px;
}

.welfare-label {
  font-size: var(--text-sm);
  color: var(--muted);
}

.welfare-hint {
  font-size: var(--text-xs);
  color: var(--muted);
  margin-top: 2px;
}

.welfare-value {
  font-size: var(--text-xl);
  font-weight: 600;
  color: var(--success);
}

.hits-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
  gap: 12px;
}

.hit-card {
  background: var(--surface-2);
  border-radius: 14px;
  padding: 14px 16px;
}

.hit-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 6px;
}

.hit-lottery {
  font-weight: 600;
  font-size: var(--text-md);
}

.hit-draw {
  font-size: var(--text-xs);
  color: var(--muted);
}

.hit-tier {
  display: inline-flex;
  padding: 2px 8px;
  background: #dbeafe;
  color: #1e40af;
  border-radius: 20px;
  font-size: var(--text-xs);
  margin-bottom: 4px;
}

.hit-amount {
  font-size: var(--text-xl);
  font-weight: 600;
  color: var(--success);
}

.hit-status {
  margin-top: 4px;
}

.tag {
  display: inline-flex;
  padding: 2px 8px;
  border-radius: 20px;
  font-size: var(--text-xs);
  font-weight: 600;
}

.tag.claimed {
  background: #dcfce7;
  color: #166534;
}

.tag.pending {
  background: #fef3c7;
  color: #92400e;
}

.tag.none {
  background: #f3f4f6;
  color: var(--muted);
}

.calendar-list,
.agency-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.calendar-item,
.agency-card {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 16px;
  background: var(--surface-2);
  border-radius: 14px;
  width: 100%;
  text-align: left;
  border: none;
  cursor: pointer;
}

.calendar-main,
.agency-main {
  display: flex;
  align-items: center;
  gap: 8px;
}

.calendar-name,
.agency-name {
  font-weight: 600;
}

.calendar-category,
.agency-category {
  font-size: var(--text-xs);
  padding: 2px 8px;
  border-radius: 20px;
  font-weight: 600;
}

.calendar-category.welfare,
.agency-category.welfare {
  background: #fee2e2;
  color: #991b1b;
}

.calendar-category.sport,
.agency-category.sport {
  background: #dbeafe;
  color: #1e40af;
}

.calendar-next {
  font-size: var(--text-sm);
  color: var(--fg);
  font-weight: 600;
}

.calendar-none {
  color: var(--muted);
}

.agency-address {
  font-size: var(--text-sm);
  color: var(--muted);
  margin-top: 4px;
}

.agency-distance {
  font-size: var(--text-xs);
  color: var(--muted);
  margin-top: 2px;
}
</style>
