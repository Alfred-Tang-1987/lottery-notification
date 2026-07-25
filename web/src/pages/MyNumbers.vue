<script setup lang="ts">
import { ref, onMounted, computed } from 'vue';
import { apiGet, apiPost, apiPatch, apiDelete } from '../api/client';
import {
  LOTTERIES,
  lotteryName,
  getLotteryRange,
  getPlayTypes,
  PLAY_TYPE_LABELS,
  randomPick,
  parseCsvLine,
  calculateCost,
} from '../lib/lotteries';
import State from '../components/State.vue';
import NumberPad from '../components/NumberPad.vue';

interface Ticket {
  id: number;
  lottery_code: string;
  play_type: string;
  numbers_json: string;
  tuo_json: string | null;
  label: string | null;
  multiplier: number;
  append: boolean;
  cost: number;
  enabled: boolean;
}

const tickets = ref<Ticket[] | null>(null);
const loading = ref(false);
const error = ref('');
const saving = ref(false);
const showForm = ref(false);
const editingId = ref<number | null>(null); // 非 null = 编辑模式
const deletingId = ref<number | null>(null); // 删除二次确认
const form = ref({
  lottery_code: 'ssq',
  play_type: 'single',
  numbers_json: '',
  label: '',
  multiplier: 1,
  dlt_append: false,
});
// Number pad state (arrays for v-model binding with NumberPad)
const padFront = ref<number[]>([]);
const padBack = ref<number[]>([]);
const csvText = ref('');
const csvError = ref('');
const csvImported = ref(0);

const grouped = computed(() => {
  const map: Record<string, Ticket[]> = {};
  for (const t of tickets.value || []) {
    if (!map[t.lottery_code]) map[t.lottery_code] = [];
    map[t.lottery_code].push(t);
  }
  return map;
});

/** 当前选中彩种的号码区间（查 lib/lotteries.ts） */
const currentRange = computed(() => {
  const code = form.value.lottery_code;
  const r = getLotteryRange(code);
  return r ?? null;
});

/** 自动计算投入金额（分）。
 * 公式：n_combos × price_per_bet × (append?1.5:1) × multiplier
 * 号码不足时返回 0（用户选号过程中显示 0 元，避免抛错打断输入）。
 */
const computedCost = computed<number>(() => {
  const code = form.value.lottery_code;
  const playType = form.value.play_type;
  const multiplier = form.value.multiplier;
  if (!Number.isInteger(multiplier) || multiplier < 1) return 0;
  try {
    const cost = calculateCost({
      code,
      playType,
      front: padFront.value,
      back: padBack.value.length > 0 ? padBack.value : undefined,
      multiplier,
      append: code === 'dlt' && form.value.dlt_append,
    });
    return cost;
  } catch {
    return 0;
  }
});

/** 投入金额展示（元），2 位小数 */
const costYuan = computed(() => (computedCost.value / 100).toFixed(2));

function onRandomPick() {
  const result = randomPick(form.value.lottery_code, {
    playType: form.value.play_type,
  });
  padFront.value = result.front;
  padBack.value = result.back ?? [];
  syncPadToJson();
}

function syncPadToJson() {
  const front = [...padFront.value].sort((a, b) => a - b);
  const r = getLotteryRange(form.value.lottery_code);
  if (r?.back) {
    const back = [...padBack.value].sort((a, b) => a - b);
    form.value.numbers_json = JSON.stringify({ front, back });
  } else {
    form.value.numbers_json = JSON.stringify({ front });
  }
}

function clearPad() {
  padFront.value = [];
  padBack.value = [];
  form.value.numbers_json = '';
}

async function csvImport() {
  const raw = csvText.value.trim();
  if (!raw) return;
  const lines = raw.split(/\r?\n/);
  let imported = 0;
  const errors: string[] = [];
  saving.value = true;
  csvError.value = '';
  csvImported.value = 0;
  try {
    for (let i = 0; i < lines.length; i++) {
      const line = lines[i];
      const result = parseCsvLine(line, i + 1);
      if (!result.ok) {
        errors.push(`行 ${result.line ?? i + 1}: ${result.error}`);
        continue;
      }
      const { code, front, back, draw_no } = result.data;
      const numbersJson = JSON.stringify(
        back ? { front, back } : { front },
      );
      // CSV 导入固定 single 1 倍不追加：cost = 1 注 × 200 分 × 1 = 200 分。
      // 复杂玩法/倍投需通过图形界面录入。calculateCost 抛错时回退 200（保守）。
      let rowCost = 200;
      try {
        rowCost = calculateCost({
          code,
          playType: 'single',
          front,
          back: back,
          multiplier: 1,
        });
      } catch {
        // 未知彩种或异常回退默认单注价
      }
      // Per-row isolation: a single API failure (e.g. duplicate, validation
      // error) must NOT abort the whole batch. Catch, record, and continue so
      // the user sees which rows succeeded vs failed. Without this, one bad
      // row silently drops all subsequent rows (silent partial-failure).
      try {
        await apiPost('/tickets', {
          lottery_code: code,
          play_type: 'single',
          numbers_json: numbersJson,
          multiplier: 1,
          cost: rowCost,
          // plan Step 3 CSV 期号字段：Ticket 模型无 draw_no 列，作为 label 记录（DrawQuery 页才真正用期号查开奖）
          ...(draw_no ? { label: draw_no } : {}),
        });
        imported++;
      } catch (rowErr) {
        const msg = rowErr instanceof Error ? rowErr.message : '未知错误';
        errors.push(`行 ${i + 1}: 导入失败 - ${msg}`);
      }
    }
    csvImported.value = imported;
    if (errors.length > 0) {
      csvError.value = errors.join('\n');
    }
    // Only clear the textarea when every row succeeded. On partial failure,
    // keep the original input so the user can edit out the succeeded rows and
    // retry the failed ones (errors are surfaced with line numbers above).
    if (errors.length === 0) {
      csvText.value = '';
    }
    await load();
  } catch (err) {
    // Only catastrophic failures (e.g. parseCsvLine throwing unexpectedly,
    // load() failing) land here. Per-row API failures are isolated above.
    // Preserve any per-row errors already collected so a late catastrophic
    // failure (e.g. load() after the loop) doesn't discard them.
    const msg = err instanceof Error ? err.message : '未知错误';
    const catastrophic = `导入 ${imported} 条后失败: ${msg}`;
    csvError.value = errors.length > 0 ? `${catastrophic}\n${errors.join('\n')}` : catastrophic;
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
    dlt_append: false,
  };
  clearPad();
}

async function createTicket(continueAfter: boolean = false) {
  saving.value = true;
  try {
    // lottery-rules.md §倍投: 倍投 1–99 倍（1× = 单倍投注，不倍投；合法场景）
    if (!Number.isInteger(form.value.multiplier) || form.value.multiplier < 1 || form.value.multiplier > 99) {
      error.value = '倍投必须是 1–99 之间的整数';
      saving.value = false;
      return;
    }
    // 号码必填（padFront 不能为空——至少需要前区号码）
    if (padFront.value.length === 0) {
      error.value = '请先选择前区号码';
      saving.value = false;
      return;
    }
    const body: Record<string, unknown> = {
      lottery_code: form.value.lottery_code,
      play_type: form.value.play_type,
      numbers_json: form.value.numbers_json,
      label: form.value.label || undefined,
      multiplier: form.value.multiplier,
      cost: computedCost.value,
      append: form.value.lottery_code === 'dlt' && form.value.dlt_append,
    };
    if (editingId.value !== null) {
      // 编辑模式：PATCH /tickets/{id}
      await apiPatch(`/tickets/${editingId.value}`, body);
      editingId.value = null;
      resetForm();
      showForm.value = false;
    } else {
      // 新建模式
      await apiPost('/tickets', body);
      if (continueAfter) {
        // 保存并继续：清空号码盘，保留彩种/玩法/倍投设置，不关 modal
        clearPad();
      } else {
        resetForm();
        showForm.value = false;
      }
    }
    await load();
  } catch (err) {
    error.value = err instanceof Error ? err.message : '保存失败';
  } finally {
    saving.value = false;
  }
}

/** 删除号码（二次确认后调用 DELETE API）。删除后投入自动减少（dashboard SUM 聚合）。 */
async function deleteTicket(id: number) {
  try {
    await apiDelete(`/tickets/${id}`);
    deletingId.value = null;
    await load();
  } catch (err) {
    error.value = err instanceof Error ? err.message : '删除失败';
  }
}

/** 进入编辑模式：预填表单数据 + 打开 modal。 */
function startEdit(t: Ticket) {
  editingId.value = t.id;
  form.value = {
    lottery_code: t.lottery_code,
    play_type: t.play_type,
    numbers_json: t.numbers_json,
    label: t.label || '',
    multiplier: t.multiplier,
    dlt_append: t.append,
  };
  // 解析 numbers_json 填充号码盘
  try {
    const parsed = JSON.parse(t.numbers_json) as { front: number[]; back?: number[] };
    padFront.value = parsed.front || [];
    padBack.value = parsed.back || [];
  } catch {
    clearPad();
  }
  error.value = '';
  showForm.value = true;
}

/** 取消编辑/新建：重置一切。 */
function cancelForm() {
  editingId.value = null;
  resetForm();
  showForm.value = false;
}

/** 格式化号码展示（如 "01 02 03 04 05 06 + 07"）。 */
function formatNumbers(t: Ticket): string {
  try {
    const parsed = JSON.parse(t.numbers_json) as { front: number[]; back?: number[] };
    // 所有彩种号码均 2 位补零（ssq 01-33+01-16, fc3d 0-9 也补零保持对齐）
    const pad = (n: number) => String(n).padStart(2, '0');
    const frontStr = (parsed.front || []).map(pad).join(' ');
    const backStr = parsed.back && parsed.back.length > 0
      ? ' + ' + parsed.back.map(pad).join(' ')
      : '';
    return frontStr + backStr;
  } catch {
    return t.numbers_json;
  }
}

/** 投入展示（元）。 */
function formatCost(cost: number): string {
  return (cost / 100).toFixed(2) + ' 元';
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
                <div class="ticket-numbers">{{ formatNumbers(ticket) }}</div>
                <div class="ticket-detail">
                  <span class="badge" :class="ticket.enabled ? 'badge-on' : 'badge-off'">
                    {{ ticket.enabled ? '启用' : '停用' }}
                  </span>
                  {{ PLAY_TYPE_LABELS[ticket.play_type] || ticket.play_type }} ·
                  {{ ticket.multiplier }}倍 ·
                  {{ formatCost(ticket.cost) }}
                  <span v-if="ticket.label" class="ticket-label-tag">· {{ ticket.label }}</span>
                </div>
              </div>
              <div class="ticket-actions">
                <button type="button" class="secondary small" @click="startEdit(ticket)">编辑</button>
                <button
                  v-if="deletingId !== ticket.id"
                  type="button"
                  class="secondary small danger"
                  @click="deletingId = ticket.id"
                >删除</button>
                <template v-else>
                  <span class="confirm-text">确认删除？</span>
                  <button
                    type="button"
                    class="danger small"
                    :disabled="saving"
                    @click="deleteTicket(ticket.id)"
                  >确认</button>
                  <button
                    type="button"
                    class="secondary small"
                    @click="deletingId = null"
                  >取消</button>
                </template>
              </div>
            </li>
          </ul>
        </div>
      </section>
    </template>

    <!-- 添加/编辑号码弹窗 -->
    <div v-if="showForm" class="modal" role="dialog" :aria-label="editingId !== null ? '编辑号码' : '添加号码'">
      <button
        type="button"
        class="modal-backdrop"
        aria-label="关闭弹窗"
        @click="cancelForm"
      />
      <div class="modal-card modal-card-wide">
        <h2>{{ editingId !== null ? '编辑号码' : '添加号码' }}</h2>
        <form @submit.prevent="() => createTicket(false)">
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
                v-for="pt in getPlayTypes(form.lottery_code)"
                :key="pt"
                :value="pt"
              >
                {{ PLAY_TYPE_LABELS[pt] || pt }}
              </option>
            </select>
          </label>

          <!-- 号码盘（NumberPad 组件：点击选号） -->
          <div class="field">
            <span class="field-label">前区号码（点选）</span>
            <NumberPad
              :numbers="padFront"
              zone="front"
              :lottery-code="form.lottery_code"
              @update:numbers="(nums) => { padFront = nums; syncPadToJson(); }"
            />
          </div>

          <div v-if="currentRange?.back" class="field">
            <span class="field-label">后区号码（点选）</span>
            <NumberPad
              :numbers="padBack"
              zone="back"
              :lottery-code="form.lottery_code"
              @update:numbers="(nums) => { padBack = nums; syncPadToJson(); }"
            />
          </div>

          <div class="field row">
            <button type="button" class="secondary small" @click="onRandomPick">机选一注</button>
            <button type="button" class="secondary small danger" @click="clearPad">清空</button>
          </div>

          <!-- numbers_json 隐藏字段：选号自动同步，不再展示给用户（避免手动输入出错） -->
          <input v-model="form.numbers_json" type="hidden" />

          <!-- CSV 批量导入 -->
          <details class="field csv-details">
            <summary class="csv-summary">批量导入（CSV）</summary>
            <textarea
              v-model="csvText"
              rows="4"
              placeholder="每行：彩种代码,[期号,]号码...（期号可选）&#10;例: ssq,2024090,1,2,3,4,5,6,7&#10;    ssq,1,2,3,4,5,6,7&#10;    dlt,5,10,15,20,25,3,8"
              class="csv-area"
            />
            <button type="button" class="secondary small" style="margin-top:8px" @click="csvImport()" :disabled="saving">
              {{ saving ? '导入中…' : '导入 CSV' }}
            </button>
            <p v-if="csvError" class="csv-error" style="color: var(--danger); font-size: 0.875rem; margin-top: 8px; white-space: pre-wrap;">{{ csvError }}</p>
            <p v-if="csvImported > 0" class="csv-success" style="color: var(--success); font-size: 0.875rem; margin-top: 8px;">✓ 成功导入 {{ csvImported }} 条</p>
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

          <div class="field">
            <span class="field-label">
              投入
              <span v-if="form.dlt_append">（含追加）</span>
            </span>
            <div class="cost-display">{{ costYuan }} 元</div>
          </div>

          <label class="field">
            <span class="field-label">备注</span>
            <input v-model="form.label" type="text" maxlength="32" />
          </label>

          <div class="modal-actions">
            <button type="button" class="secondary" @click="cancelForm">取消</button>
            <button
              v-if="editingId === null"
              type="button"
              class="secondary"
              :disabled="saving"
              @click="createTicket(true)"
            >
              {{ saving ? '保存中…' : '保存并继续' }}
            </button>
            <button type="submit" class="primary" :disabled="saving">
              {{ saving ? '保存中…' : (editingId !== null ? '保存修改' : '保存') }}
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
  /* button reset: backdrop is a <button> for A11y (spec §12.4) */
  border: none;
  padding: 0;
  margin: 0;
  cursor: default;
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

.cost-display {
  padding: 8px 12px;
  background: var(--surface-2);
  border-radius: 8px;
  font-size: var(--text-lg);
  font-weight: 600;
  color: var(--accent);
}

.ticket-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.ticket-numbers {
  font-family: var(--font-mono, monospace);
  font-size: var(--text-lg);
  font-weight: 600;
  letter-spacing: 1px;
  color: var(--text);
}

.ticket-actions {
  display: flex;
  gap: 6px;
  align-items: center;
}

.confirm-text {
  color: var(--danger);
  font-size: var(--text-sm);
}

.badge {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 4px;
  font-size: var(--text-sm);
  font-weight: 500;
}
.badge-on {
  background: var(--success-bg, #e8f5e9);
  color: var(--success, #2e7d32);
}
.badge-off {
  background: var(--surface-2);
  color: var(--text-muted);
}

.ticket-label-tag {
  color: var(--text-muted);
}

button.small {
  padding: 4px 10px;
  font-size: var(--text-sm);
}
</style>
