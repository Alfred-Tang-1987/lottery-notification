<script setup lang="ts">
import { ref, onMounted, computed } from 'vue';
import { apiGet, apiPost } from '../api/client';
import { LOTTERIES, lotteryName } from '../lib/lotteries';
import State from '../components/State.vue';

// Per lottery-rules.md: partition lotteries → 单式/复式/胆拖; positional → 单选/直选/组选3/组选6
const PLAY_TYPES: Record<string, string[]> = {
  ssq: ['single', 'compound', 'dantuo'],
  dlt: ['single', 'compound', 'dantuo'],
  qlc: ['single', 'compound', 'dantuo'],
  qxc: ['single', 'compound', 'dantuo'],
  fc3d: ['danxuan', 'zuxuan3', 'zuxuan6'],
  pl3: ['zhixuan', 'zuxuan3', 'zuxuan6'],
  pl5: ['zhixuan'],
};

const PLAY_TYPE_LABELS: Record<string, string> = {
  single: '单式',
  compound: '复式',
  dantuo: '胆拖',
  danxuan: '单选',
  zhixuan: '直选',
  zuxuan3: '组选3',
  zuxuan6: '组选6',
};

// Known number ranges and default counts per lottery type
const LOTTERY_RANGES: Record<string, { front: { min: number; max: number; count: number }; back: { min: number; max: number; count: number } | null }> = {
  ssq: { front: { min: 1, max: 33, count: 6 }, back: { min: 1, max: 16, count: 1 } },
  dlt: { front: { min: 1, max: 35, count: 5 }, back: { min: 1, max: 12, count: 2 } },
  qlc: { front: { min: 1, max: 30, count: 7 }, back: null },
  fc3d: { front: { min: 0, max: 9, count: 3 }, back: null },
  pl3: { front: { min: 0, max: 9, count: 3 }, back: null },
  pl5: { front: { min: 0, max: 9, count: 5 }, back: null },
  qxc: { front: { min: 0, max: 9, count: 6 }, back: { min: 0, max: 14, count: 1 } },
};

interface Ticket {
  id: number;
  lottery_code: string;
  play_type: string;
  label: string | null;
  multiplier: number;
  cost: number;
  enabled: boolean;
}

const tickets = ref<Ticket[] | null>(null);
const loading = ref(false);
const error = ref('');
const saving = ref(false);
const showForm = ref(false);
const form = ref({
  lottery_code: 'ssq',
  play_type: 'single',
  numbers_json: '',
  label: '',
  multiplier: 1,
  cost: 200,
  dlt_append: false,
});
// Number pad state
const padFront = ref<Set<number>>(new Set());
const padBack = ref<Set<number>>(new Set());
const csvText = ref('');

const grouped = computed(() => {
  const map: Record<string, Ticket[]> = {};
  for (const t of tickets.value || []) {
    if (!map[t.lottery_code]) map[t.lottery_code] = [];
    map[t.lottery_code].push(t);
  }
  return map;
});

const currentRange = computed(() => LOTTERY_RANGES[form.value.lottery_code]);

const frontNumbers = computed(() => {
  const r = currentRange.value?.front;
  if (!r) return [] as number[];
  return Array.from({ length: r.max - r.min + 1 }, (_, i) => r.min + i);
});

const backNumbers = computed(() => {
  const r = currentRange.value?.back;
  if (!r) return [] as number[];
  return Array.from({ length: r.max - r.min + 1 }, (_, i) => r.min + i);
});

function toggleFront(n: number) {
  const next = new Set(padFront.value);
  if (next.has(n)) next.delete(n);
  else next.add(n);
  padFront.value = next;
}

function toggleBack(n: number) {
  const next = new Set(padBack.value);
  if (next.has(n)) next.delete(n);
  else next.add(n);
  padBack.value = next;
}

function randomPick() {
  const r = currentRange.value;
  if (!r) return;
  const pick = (pool: number[], count: number): number[] => {
    const shuffled = [...pool].sort(() => Math.random() - 0.5);
    return shuffled.slice(0, count).sort((a, b) => a - b);
  };
  padFront.value = new Set(pick(frontNumbers.value, r.front.count));
  if (r.back) {
    padBack.value = new Set(pick(backNumbers.value, r.back.count));
  }
  syncPadToJson();
}

function syncPadToJson() {
  const front = [...padFront.value].sort((a, b) => a - b);
  const r = currentRange.value;
  if (r?.back) {
    const back = [...padBack.value].sort((a, b) => a - b);
    form.value.numbers_json = JSON.stringify({ front, back });
  } else {
    form.value.numbers_json = JSON.stringify({ front: front.length > 0 ? front : [] });
  }
}

function clearPad() {
  padFront.value = new Set();
  padBack.value = new Set();
  form.value.numbers_json = '';
}

async function csvImport() {
  if (!csvText.value.trim()) return;
  const lines = csvText.value.trim().split(/\r?\n/);
  let imported = 0;
  saving.value = true;
  try {
    for (const line of lines) {
      const parts = line.split(',').map((s) => s.trim()).filter(Boolean);
      if (parts.length < 1) continue;
      const code = parts[0] || form.value.lottery_code;
      const nums = parts.slice(1).map(Number);
      const r = LOTTERY_RANGES[code];
      if (!r) continue;
      const frontCount = r.front.count;
      const front = nums.slice(0, frontCount).sort((a, b) => a - b);
      const back = r.back ? nums.slice(frontCount, frontCount + r.back.count).sort((a, b) => a - b) : [];
      const numbersJson = JSON.stringify(r.back
        ? { front: front.length === frontCount ? front : [], back: back.length === r.back.count ? back : [] }
        : { front: front.length === frontCount ? front : [] }
      );
      await apiPost('/tickets', {
        lottery_code: code,
        play_type: 'single',
        numbers_json: numbersJson,
        multiplier: 1,
        cost: 200,
      });
      imported++;
    }
    csvText.value = '';
    await load();
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
  } catch (err: any) {
    error.value = `导入 ${imported} 条后失败: ${err?.message || '未知错误'}`;
  } finally {
    saving.value = false;
  }
}

async function load() {
  loading.value = true;
  error.value = '';
  try {
    tickets.value = await apiGet<Ticket[]>('/tickets');
  } catch (err) {
    error.value = err instanceof Error ? err.message : '加载失败';
  } finally {
    loading.value = false;
  }
}

function resetForm() {
  form.value = {
    lottery_code: 'ssq',
    play_type: 'single',
    numbers_json: '',
    label: '',
    multiplier: 1,
    cost: 200,
    dlt_append: false,
  };
  clearPad();
}

async function createTicket() {
  saving.value = true;
  try {
    const body: Record<string, unknown> = {
      lottery_code: form.value.lottery_code,
      play_type: form.value.play_type,
      numbers_json: form.value.numbers_json,
      label: form.value.label || undefined,
      multiplier: form.value.multiplier,
      cost: form.value.cost,
    };
    // DLT append flag
    if (form.value.lottery_code === 'dlt' && form.value.dlt_append) {
      body.dlt_append = true;
      // Append increases cost: +1元 per note (100分)
      body.cost = (form.value.cost || 200) + 100;
    }
    await apiPost('/tickets', body);
    resetForm();
    showForm.value = false;
    await load();
  } catch (err) {
    error.value = err instanceof Error ? err.message : '保存失败';
  } finally {
    saving.value = false;
  }
}

onMounted(() => {
  void load();
});
</script>

<template>
  <div class="my-numbers">
    <header class="page-header">
      <h1>我的号码</h1>
      <button type="button" class="primary" @click="showForm = true">+ 添加号码</button>
    </header>

    <State v-if="loading" type="loading" title="加载号码池中…" />
    <State v-else-if="error" type="error" :title="error" @action="load" />
    <State
      v-else-if="!tickets || tickets.length === 0"
      type="empty"
      title="号码池为空"
      cta="去选一注"
      @action="showForm = true"
    />

    <template v-else>
      <section
        v-for="(list, code) in grouped"
        :key="code"
        class="card"
        :aria-labelledby="`lottery-${code}`"
      >
        <div class="card-header">
          <h2 :id="`lottery-${code}`" class="card-title">{{ lotteryName(code) }}</h2>
        </div>
        <div class="card-body">
          <ul class="ticket-list" role="list">
            <li v-for="ticket in list" :key="ticket.id" class="ticket-item">
              <div class="ticket-meta">
                <div class="ticket-label">{{ ticket.label || '未命名注单' }}</div>
                <div class="ticket-detail">
                  {{ ticket.play_type }} · {{ ticket.multiplier }}倍 ·
                  {{ ticket.enabled ? '启用' : '停用' }}
                </div>
              </div>
            </li>
          </ul>
        </div>
      </section>
    </template>

    <!-- 添加号码弹窗 -->
    <div v-if="showForm" class="modal" role="dialog" aria-label="添加号码">
      <div class="modal-backdrop" @click="showForm = false" />
      <div class="modal-card modal-card-wide">
        <h2>添加号码</h2>
        <form @submit.prevent="createTicket">
          <label class="field">
            <span class="field-label">彩种</span>
            <select v-model="form.lottery_code" @change="clearPad()">
              <option v-for="l in LOTTERIES" :key="l.code" :value="l.code">
                {{ l.name }}
              </option>
            </select>
          </label>

          <label class="field">
            <span class="field-label">玩法</span>
            <select v-model="form.play_type">
              <option
                v-for="pt in PLAY_TYPES[form.lottery_code] || PLAY_TYPES['ssq']"
                :key="pt"
                :value="pt"
              >
                {{ PLAY_TYPE_LABELS[pt] || pt }}
              </option>
            </select>
          </label>

          <!-- 号码盘（点击选号） -->
          <div class="field">
            <span class="field-label">前区号码（点选）</span>
            <div class="num-pad">
              <button
                v-for="n in frontNumbers"
                :key="`fp-${n}`"
                type="button"
                class="num-btn"
                :class="{ selected: padFront.has(n) }"
                @click="toggleFront(n)"
              >{{ n < 10 ? String(n).padStart(2, '0') : n }}</button>
            </div>
          </div>

          <div v-if="backNumbers.length > 0" class="field">
            <span class="field-label">后区号码（点选）</span>
            <div class="num-pad">
              <button
                v-for="n in backNumbers"
                :key="`bp-${n}`"
                type="button"
                class="num-btn back-btn"
                :class="{ selected: padBack.has(n) }"
                @click="toggleBack(n)"
              >{{ n < 10 ? String(n).padStart(2, '0') : n }}</button>
            </div>
          </div>

          <div class="field row">
            <button type="button" class="secondary small" @click="randomPick">机选一注</button>
            <button type="button" class="secondary small" @click="syncPadToJson">确认选号</button>
            <button type="button" class="secondary small danger" @click="clearPad">清空</button>
          </div>

          <label class="field">
            <span class="field-label">号码 JSON（选号自动生成，或手动输入）</span>
            <input
              v-model="form.numbers_json"
              type="text"
              placeholder='{"front":[1,2,3,4,5,6],"back":[7]}'
              :required="!csvText"
            />
          </label>

          <!-- CSV 批量导入 -->
          <details class="field csv-details">
            <summary class="csv-summary">批量导入（CSV）</summary>
            <textarea
              v-model="csvText"
              rows="4"
              placeholder="每行：彩种代码,前1,前2,...,后1,后2...&#10;例: ssq,1,2,3,4,5,6,7&#10;    dlt,5,10,15,20,25,3,8"
              class="csv-area"
            />
            <button type="button" class="secondary small" style="margin-top:8px" @click="csvImport()" :disabled="saving">
              {{ saving ? '导入中…' : '导入 CSV' }}
            </button>
          </details>

          <!-- DLT 追加 -->
          <label v-if="form.lottery_code === 'dlt'" class="field toggle-row">
            <span>追加投注（+1元/注，追加仅参与一、二等奖 80%）</span>
            <input v-model="form.dlt_append" type="checkbox" />
          </label>

          <label class="field">
            <span class="field-label">倍投</span>
            <input v-model.number="form.multiplier" type="number" min="1" max="99" />
          </label>

          <label class="field">
            <span class="field-label">
              投入（分）
              <span v-if="form.dlt_append">（含追加）</span>
            </span>
            <input v-model.number="form.cost" type="number" min="0" />
          </label>

          <label class="field">
            <span class="field-label">备注</span>
            <input v-model="form.label" type="text" maxlength="32" />
          </label>

          <div class="modal-actions">
            <button type="button" class="secondary" @click="showForm = false">取消</button>
            <button type="submit" class="primary" :disabled="saving">
              {{ saving ? '保存中…' : '保存' }}
            </button>
          </div>
        </form>
      </div>
    </div>
  </div>
</template>

<style scoped>
.my-numbers {
  padding-bottom: 20px;
}

.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
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
  margin-bottom: 16px;
  overflow: hidden;
}

.card-header {
  padding: 16px 20px 0;
}

.card-title {
  font-size: var(--text-xl);
  font-weight: 600;
}

.card-body {
  padding: 12px 20px 20px;
}

.ticket-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.ticket-item {
  padding: 12px;
  background: var(--surface-2);
  border-radius: 12px;
}

.ticket-label {
  font-weight: 600;
}

.ticket-detail {
  font-size: var(--text-sm);
  color: var(--muted);
  margin-top: 2px;
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

.secondary {
  padding: 10px 16px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--surface);
  color: var(--fg);
  font-size: var(--text-md);
  cursor: pointer;
  min-height: 44px;
}

.modal {
  position: fixed;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 200;
  padding: 16px;
}

.modal-backdrop {
  position: absolute;
  inset: 0;
  background: rgba(0, 0, 0, 0.4);
}

.modal-card {
  position: relative;
  width: 100%;
  max-width: 420px;
  background: var(--surface);
  border-radius: 18px;
  padding: 24px;
  z-index: 1;
  max-height: 90vh;
  overflow-y: auto;
}

.modal-card-wide {
  max-width: 520px;
}

.modal-card h2 {
  margin-bottom: 16px;
  font-size: var(--text-xl);
}

/* Number pad */
.num-pad {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}

.num-btn {
  width: 36px;
  height: 36px;
  border: 1.5px solid var(--border);
  border-radius: 8px;
  background: var(--surface-2);
  color: var(--fg);
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-height: unset;
  padding: 0;
  transition: background var(--dur), border-color var(--dur);
}

.num-btn:hover {
  border-color: var(--accent);
}

.num-btn.selected {
  background: var(--red-ball);
  color: #fff;
  border-color: var(--red-ball);
}

.back-btn.selected {
  background: var(--blue-ball);
  border-color: var(--blue-ball);
}

.row {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.small {
  padding: 6px 12px !important;
  font-size: var(--text-sm);
  min-height: 36px;
}

.danger {
  color: var(--danger) !important;
  border-color: var(--danger) !important;
}

.csv-details {
  margin-bottom: 14px;
}

.csv-summary {
  cursor: pointer;
  color: var(--accent);
  font-size: var(--text-sm);
  padding: 4px 0;
}

.csv-area {
  width: 100%;
  padding: 8px 10px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--surface-2);
  color: var(--fg);
  font-family: var(--font-mono);
  font-size: var(--text-xs);
  margin-top: 6px;
  resize: vertical;
}

.toggle-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 0;
}

.toggle-row input[type="checkbox"] {
  width: auto;
  min-height: auto;
  transform: scale(1.3);
}

.field {
  display: block;
  margin-bottom: 14px;
}

.field-label {
  display: block;
  font-size: var(--text-sm);
  color: var(--muted);
  margin-bottom: 6px;
}

input,
select {
  width: 100%;
  padding: 10px 12px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--surface);
  color: var(--fg);
  font-size: var(--text-md);
  min-height: 44px;
}

.modal-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  margin-top: 20px;
}
</style>
