<script setup lang="ts">
import { ref, onMounted, computed } from 'vue';
import { apiGet, apiPost, apiPut } from '../api/client';
import State from '../components/State.vue';
import { LOTTERIES } from '../lib/lotteries';

interface Channel {
  id: number;
  type: 'bark' | 'feishu' | 'email';
  config: Record<string, string>;
  enabled: boolean;
}

interface NotificationRule {
  id: number;
  lottery_code: string;
  strategy: 'every' | 'win_only';
}

interface NotificationSettings {
  master_enable: boolean;
  path_a_enable: boolean;
  summary_time: string | null;
  new_numbers_default_enabled: boolean;
}

interface Preferences {
  theme: 'light' | 'dark' | 'auto';
}

interface TemplatePreview {
  path_a: { title: string; body: string };
  path_b: { title: string; body: string };
}

const CHANNELS = [
  {
    type: 'bark' as const,
    name: 'Bark',
    desc: 'iOS 原生推送，填写 key（可选自定义 URL）',
    fields: [
      { key: 'key', label: 'Bark Key' },
      { key: 'url', label: 'Bark URL（可选）' },
    ],
  },
  {
    type: 'feishu' as const,
    name: '飞书',
    desc: '群机器人 webhook',
    fields: [{ key: 'webhook', label: 'Webhook 地址' }],
  },
  {
    type: 'email' as const,
    name: '邮箱',
    desc: '系统统一发件，只需填写收件地址',
    fields: [{ key: 'address', label: '收件邮箱' }],
  },
];

const DEFAULT_SUMMARY_TIME = '07:00';
const DEFAULT_DND_START = '22:00';
const DEFAULT_DND_END = '07:00';

const theme = ref<'light' | 'dark' | 'auto'>(getSavedTheme());

function getSavedTheme(): 'light' | 'dark' | 'auto' {
  const saved = localStorage.getItem('theme');
  if (saved === 'dark' || saved === 'light' || saved === 'auto') return saved;
  return 'auto';
}

function applyTheme(t: 'light' | 'dark' | 'auto') {
  theme.value = t;
  localStorage.setItem('theme', t);
  const preferDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
  const isDark = t === 'dark' || (t === 'auto' && preferDark);
  document.documentElement.classList.toggle('dark', isDark);
}

const channels = ref<Channel[] | null>(null);
const rules = ref<NotificationRule[] | null>(null);
const settings = ref<NotificationSettings | null>(null);
const templates = ref<TemplatePreview | null>(null);
const preferences = ref<Preferences | null>(null);
const loading = ref(false);
const error = ref('');
const saving = ref(false);
const savingRule = ref<string | null>(null);
const savingSettings = ref(false);
const savingDnd = ref(false);
const savingPrefs = ref(false);
const forms = ref<Record<string, string>>({});

// DND form
const dndStart = ref(DEFAULT_DND_START);
const dndEnd = ref(DEFAULT_DND_END);
const dndEnabled = ref(false);
const dndLoading = ref(false);
const dndError = ref('');

const strategyMap = computed(() => {
  const m = new Map<string, NotificationRule>();
  if (rules.value) {
    for (const r of rules.value) {
      m.set(r.lottery_code, r);
    }
  }
  return m;
});

const summaryTime = computed(() => {
  return settings.value?.summary_time ?? DEFAULT_SUMMARY_TIME;
});

async function load() {
  loading.value = true;
  error.value = '';
  try {
    const [ch, rl, set, tpl, dnd, prefs] = await Promise.all([
      apiGet<Channel[]>('/channels'),
      apiGet<NotificationRule[]>('/channels/rules'),
      apiGet<NotificationSettings>('/channels/settings').catch((e) => {
        error.value = e instanceof Error ? `通知设置加载失败：${e.message}` : '通知设置加载失败';
        return null;
      }),
      apiGet<TemplatePreview>('/channels/templates'),
      apiGet<{ enabled: boolean; start: string; end: string }>('/channels/dnd').catch(
        (e) => {
          error.value = e instanceof Error ? `DND 加载失败：${e.message}` : 'DND 加载失败';
          return null;
        }
      ),
      apiGet<Preferences>('/channels/preferences').catch((e) => {
        error.value =
          e instanceof Error ? `偏好加载失败：${e.message}` : '偏好加载失败';
        return null;
      }),
    ]);
    channels.value = ch;
    rules.value = rl;
    settings.value = set;
    templates.value = tpl;
    if (dnd) {
      dndEnabled.value = dnd.enabled;
      dndStart.value = dnd.start;
      dndEnd.value = dnd.end;
    }
    if (prefs) {
      preferences.value = prefs;
      applyTheme(prefs.theme);
    }
  } catch (err) {
    error.value = err instanceof Error ? err.message : '加载失败';
  } finally {
    loading.value = false;
  }
}

async function addChannel(type: 'bark' | 'feishu' | 'email') {
  const config: Record<string, string> = {};
  for (const f of CHANNELS.find((c) => c.type === type)?.fields || []) {
    const value = forms.value[`${type}.${f.key}`];
    if (value) config[f.key] = value;
  }

  saving.value = true;
  try {
    await apiPost('/channels', { type, config });
    for (const f of CHANNELS.find((c) => c.type === type)?.fields || []) {
      forms.value[`${type}.${f.key}`] = '';
    }
    await load();
  } catch (err) {
    error.value = err instanceof Error ? err.message : '保存失败';
  } finally {
    saving.value = false;
  }
}

function ruleFor(code: string): NotificationRule | undefined {
  return strategyMap.value.get(code);
}

function strategyFor(code: string): 'every' | 'win_only' {
  return ruleFor(code)?.strategy ?? 'every';
}

async function onStrategyChange(code: string, strategy: 'every' | 'win_only') {
  await upsertRule(code, { strategy });
}

async function onSummaryTimeChange(value: string) {
  if (savingSettings.value) return;
  savingSettings.value = true;
  try {
    await apiPut('/channels/settings', {
      ...settings.value,
      summary_time: value,
    });
    await load();
  } catch (err) {
    error.value = err instanceof Error ? err.message : '保存汇总时间失败';
  } finally {
    savingSettings.value = false;
  }
}

async function onSettingsToggle(
  field: 'master_enable' | 'path_a_enable' | 'new_numbers_default_enabled',
  value: boolean
) {
  if (savingSettings.value || !settings.value) return;
  savingSettings.value = true;
  try {
    const next = { ...settings.value, [field]: value };
    await apiPut('/channels/settings', next);
    await load();
  } catch (err) {
    error.value = err instanceof Error ? err.message : '保存设置失败';
  } finally {
    savingSettings.value = false;
  }
}

async function upsertRule(
  code: string,
  patch: Partial<NotificationRule> & { strategy?: 'every' | 'win_only' }
) {
  if (savingRule.value) return;
  savingRule.value = code;
  try {
    const existing = ruleFor(code);
    await apiPut('/channels/rules', {
      lottery_code: code,
      strategy: patch.strategy ?? existing?.strategy ?? 'every',
    });
    await load();
  } catch (err) {
    error.value = err instanceof Error ? err.message : '保存规则失败';
  } finally {
    savingRule.value = null;
  }
}

async function saveDnd() {
  if (savingDnd.value) return;
  savingDnd.value = true;
  dndLoading.value = true;
  dndError.value = '';
  const previous = {
    enabled: dndEnabled.value,
    start: dndStart.value,
    end: dndEnd.value,
  };
  try {
    await apiPost('/channels/dnd', {
      enabled: dndEnabled.value,
      start: dndStart.value,
      end: dndEnd.value,
    });
  } catch (err) {
    dndError.value = err instanceof Error ? err.message : '保存失败';
    // Roll back to last known server state on failure so the UI stays in sync.
    dndEnabled.value = previous.enabled;
    dndStart.value = previous.start;
    dndEnd.value = previous.end;
  } finally {
    dndLoading.value = false;
    savingDnd.value = false;
  }
}

async function savePreferences(patch: Partial<Preferences>) {
  if (savingPrefs.value) return;
  savingPrefs.value = true;
  try {
    const next: Preferences = {
      theme: patch.theme ?? preferences.value?.theme ?? 'auto',
    };
    await apiPost('/channels/preferences', next);
    preferences.value = next;
    if (patch.theme) applyTheme(patch.theme);
  } catch (err) {
    error.value = err instanceof Error ? err.message : '保存偏好失败';
  } finally {
    savingPrefs.value = false;
  }
}

onMounted(() => {
  void load();
});
</script>

<template>
  <div class="settings">
    <header class="page-header">
      <h1>设置</h1>
    </header>

    <State v-if="loading" type="loading" title="加载设置中…" />
    <State v-else-if="error" type="error" :title="error" @action="load" />

    <template v-else>
      <!-- 推送渠道 -->
      <section class="card">
        <div class="card-header">
          <h2 class="card-title">推送渠道</h2>
        </div>
        <div class="card-body">
          <div v-if="channels && channels.length > 0" class="channel-list">
            <div v-for="ch in channels" :key="ch.id" class="channel-item">
              <div class="channel-type">{{ CHANNELS.find((c) => c.type === ch.type)?.name || ch.type }}</div>
              <div class="channel-status">{{ ch.enabled ? '已启用' : '已停用' }}</div>
            </div>
          </div>
          <div v-else class="empty-tip">暂无渠道配置</div>
        </div>
      </section>

      <section v-for="channel in CHANNELS" :key="channel.type" class="card">
        <div class="card-header">
          <h2 class="card-title">添加 {{ channel.name }}</h2>
          <p class="card-subtitle">{{ channel.desc }}</p>
        </div>
        <div class="card-body">
          <form @submit.prevent="addChannel(channel.type)">
            <label v-for="field in channel.fields" :key="field.key" class="field">
              <span class="field-label">{{ field.label }}</span>
              <input
                v-model="forms[`${channel.type}.${field.key}`]"
                type="text"
                :placeholder="field.label"
                :required="field.key !== 'url'"
              />
            </label>
            <button type="submit" class="primary" :disabled="saving">
              {{ saving ? '保存中…' : '添加' }}
            </button>
          </form>
        </div>
      </section>

      <!-- 推送时机 -->
      <section class="card">
        <div class="card-header">
          <h2 class="card-title">推送时机</h2>
          <p class="card-subtitle">总开关、大奖即时简讯、次日汇总时间、免打扰时段</p>
        </div>
        <div class="card-body">
          <div class="timing-row">
            <label class="toggle-row">
              <span>总开关</span>
              <input
                type="checkbox"
                :checked="settings?.master_enable ?? true"
                :disabled="savingSettings"
                @change="onSettingsToggle('master_enable', ($event.target as HTMLInputElement).checked)"
              />
            </label>
            <label class="toggle-row">
              <span>大奖即时简讯</span>
              <input
                type="checkbox"
                :checked="settings?.path_a_enable ?? true"
                :disabled="savingSettings"
                @change="onSettingsToggle('path_a_enable', ($event.target as HTMLInputElement).checked)"
              />
            </label>
          </div>

          <div class="timing-row">
            <label class="field-inline">
              <span class="field-label">次日汇总时间</span>
              <input
                type="time"
                :value="summaryTime"
                :disabled="savingSettings"
                @change="onSummaryTimeChange(($event.target as HTMLInputElement).value)"
              />
            </label>
            <p class="hint-text">所有彩种统一在次日该时间发送详情汇总。</p>
          </div>

          <div class="rule-list">
            <div
              v-for="lottery in LOTTERIES"
              :key="lottery.code"
              class="rule-row"
              :data-lottery="lottery.code"
            >
              <div class="rule-name">{{ lottery.name }}</div>
              <label class="rule-field">
                <span class="field-label">策略</span>
                <select
                  :value="strategyFor(lottery.code)"
                  :disabled="savingRule === lottery.code"
                  @change="onStrategyChange(lottery.code, ($event.target as HTMLSelectElement).value as 'every' | 'win_only')"
                >
                  <option value="every">每期推送</option>
                  <option value="win_only">仅中奖</option>
                </select>
              </label>
            </div>
          </div>
        </div>
      </section>

      <!-- 模板预览 -->
      <section class="card">
        <div class="card-header">
          <h2 class="card-title">推送模板预览</h2>
          <p class="card-subtitle">大奖即时简讯 / 次日汇总</p>
        </div>
        <div class="card-body">
          <div v-if="templates" class="template-list">
            <div class="template-card">
              <h4>路径 A：大奖即时简讯</h4>
              <p class="template-title">{{ templates.path_a.title }}</p>
              <pre class="template-body">{{ templates.path_a.body }}</pre>
            </div>
            <div class="template-card">
              <h4>路径 B：次日汇总</h4>
              <p class="template-title">{{ templates.path_b.title }}</p>
              <pre class="template-body">{{ templates.path_b.body }}</pre>
            </div>
          </div>
        </div>
      </section>

      <!-- 免打扰 DND -->
      <section class="card">
        <div class="card-header">
          <h2 class="card-title">免打扰时段</h2>
        </div>
        <div class="card-body">
          <label class="toggle-row">
            <span>开启免打扰</span>
            <input
              v-model="dndEnabled"
              class="dnd-toggle"
              type="checkbox"
              :disabled="savingDnd"
              @change="saveDnd"
            />
          </label>
          <div v-if="dndEnabled" class="dnd-row">
            <label class="field-inline">
              <span class="field-label">开始</span>
              <input v-model="dndStart" type="time" :disabled="savingDnd" @change="saveDnd" />
            </label>
            <span class="dnd-sep">—</span>
            <label class="field-inline">
              <span class="field-label">结束</span>
              <input v-model="dndEnd" type="time" :disabled="savingDnd" @change="saveDnd" />
            </label>
          </div>
          <p class="hint-text">免打扰时段内暂停次日汇总/周月报；大奖即时简讯可破例。</p>
          <p v-if="dndError" class="error" role="alert">{{ dndError }}</p>
          <p v-if="dndLoading" class="hint-text">保存中…</p>
        </div>
      </section>

      <!-- 偏好 -->
      <section class="card">
        <div class="card-header">
          <h2 class="card-title">偏好</h2>
        </div>
        <div class="card-body">
          <label class="toggle-row">
            <span>新号码默认启用</span>
            <input
              type="checkbox"
              :checked="settings?.new_numbers_default_enabled ?? true"
              :disabled="savingSettings"
              @change="onSettingsToggle('new_numbers_default_enabled', ($event.target as HTMLInputElement).checked)"
            />
          </label>
          <p class="hint-text">开启后，新添加的号码默认参与自动比对与推送。</p>
        </div>
      </section>

      <!-- 外观主题 -->
      <section class="card">
        <div class="card-header">
          <h2 class="card-title">外观</h2>
        </div>
        <div class="card-body">
          <div class="theme-row" role="group" aria-label="主题选择">
            <button
              type="button"
              class="theme-btn"
              :class="{ active: theme === 'light' }"
              :disabled="savingPrefs"
              @click="savePreferences({ theme: 'light' })"
            >
              <span class="theme-icon">&#9728;</span>
              <span>浅色</span>
            </button>
            <button
              type="button"
              class="theme-btn"
              :class="{ active: theme === 'dark' }"
              :disabled="savingPrefs"
              @click="savePreferences({ theme: 'dark' })"
            >
              <span class="theme-icon">&#9790;</span>
              <span>深色</span>
            </button>
            <button
              type="button"
              class="theme-btn"
              :class="{ active: theme === 'auto' }"
              :disabled="savingPrefs"
              @click="savePreferences({ theme: 'auto' })"
            >
              <span class="theme-icon">&#9788;</span>
              <span>跟随系统</span>
            </button>
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

.card-subtitle {
  font-size: var(--text-md);
  color: var(--muted);
  margin-top: 2px;
}

.card-body {
  padding: 16px 20px 20px;
}

.channel-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.channel-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px;
  background: var(--surface-2);
  border-radius: 12px;
}

.channel-type {
  font-weight: 600;
}

.channel-status {
  font-size: var(--text-sm);
  color: var(--muted);
}

.empty-tip {
  color: var(--muted);
  text-align: center;
  padding: 20px;
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

.primary:disabled {
  opacity: 0.7;
  cursor: not-allowed;
}

.timing-row {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-bottom: 20px;
}

.rule-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.rule-row {
  display: grid;
  grid-template-columns: 1fr 120px;
  gap: 12px;
  align-items: end;
  padding: 12px;
  background: var(--surface-2);
  border-radius: 12px;
}

.rule-name {
  font-weight: 600;
  padding-bottom: 8px;
}

.rule-field .field-label {
  font-size: 11px;
  margin-bottom: 4px;
}

.template-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.template-card {
  padding: 12px;
  background: var(--surface-2);
  border-radius: 12px;
}

.template-card h4 {
  margin: 0 0 8px;
  font-size: var(--text-md);
}

.template-title {
  font-weight: 600;
  margin: 0 0 8px;
}

.template-body {
  white-space: pre-wrap;
  margin: 0;
  font-size: var(--text-sm);
  color: var(--muted);
}

.theme-row {
  display: flex;
  gap: 12px;
}

.theme-btn {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  padding: 16px 8px;
  border: 2px solid var(--border);
  border-radius: 16px;
  background: var(--surface);
  color: var(--fg);
  font-size: var(--text-md);
  cursor: pointer;
  min-height: 44px;
  transition: border-color var(--dur);
}

.theme-btn.active {
  border-color: var(--accent);
  background: color-mix(in srgb, var(--accent) 8%, var(--surface));
}

.theme-btn:disabled {
  opacity: 0.7;
  cursor: not-allowed;
}

.theme-icon {
  font-size: 24px;
}

.toggle-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 0;
}

.toggle-row input[type='checkbox'] {
  width: auto;
  min-height: auto;
  transform: scale(1.3);
}

.dnd-row {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-top: 12px;
}

.field-inline {
  flex: 1;
}

.field-inline .field-label {
  display: block;
  font-size: var(--text-sm);
  color: var(--muted);
  margin-bottom: 4px;
}

.dnd-sep {
  padding-top: 22px;
  color: var(--muted);
}

.hint-text {
  margin-top: 8px;
  font-size: var(--text-sm);
  color: var(--muted);
}

.error {
  color: var(--danger);
  font-size: var(--text-sm);
  margin-top: 8px;
}

@media (max-width: 640px) {
  .rule-row {
    grid-template-columns: 1fr;
    align-items: stretch;
  }

  .rule-name {
    padding-bottom: 0;
  }
}
</style>
