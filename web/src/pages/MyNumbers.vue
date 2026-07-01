<script setup lang="ts">
import { ref, onMounted, computed } from 'vue';
import { apiGet, apiPost } from '../api/client';
import { LOTTERIES, lotteryName } from '../lib/lotteries';
import State from '../components/State.vue';

interface Ticket {
  id: number;
  lottery_code: string;
  play_type: string;
  label: string | null;
  multiplier: number;
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
});

const grouped = computed(() => {
  const map: Record<string, Ticket[]> = {};
  for (const t of tickets.value || []) {
    if (!map[t.lottery_code]) map[t.lottery_code] = [];
    map[t.lottery_code].push(t);
  }
  return map;
});

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
  };
}

async function createTicket() {
  saving.value = true;
  try {
    await apiPost('/tickets', {
      lottery_code: form.value.lottery_code,
      play_type: form.value.play_type,
      numbers_json: form.value.numbers_json,
      label: form.value.label || undefined,
      multiplier: form.value.multiplier,
      cost: form.value.cost,
    });
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
      <div class="modal-card">
        <h2>添加号码</h2>
        <form @submit.prevent="createTicket">
          <label class="field">
            <span class="field-label">彩种</span>
            <select v-model="form.lottery_code">
              <option v-for="l in LOTTERIES" :key="l.code" :value="l.code">
                {{ l.name }}
              </option>
            </select>
          </label>

          <label class="field">
            <span class="field-label">玩法</span>
            <select v-model="form.play_type">
              <option value="single">单式</option>
              <option value="zhixuan">直选</option>
              <option value="danxuan">单选</option>
            </select>
          </label>

          <label class="field">
            <span class="field-label">号码 JSON</span>
            <input
              v-model="form.numbers_json"
              type="text"
              placeholder='{"front":[1,2,3,4,5,6],"back":[7]}'
              required
            />
          </label>

          <label class="field">
            <span class="field-label">倍投</span>
            <input v-model.number="form.multiplier" type="number" min="1" max="99" />
          </label>

          <label class="field">
            <span class="field-label">投入（分）</span>
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
}

.modal-card h2 {
  margin-bottom: 16px;
  font-size: var(--text-xl);
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
