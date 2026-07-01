<script setup lang="ts">
import { ref, onMounted, computed } from 'vue';
import { apiGet, apiPost } from '../api/client';
import State from '../components/State.vue';

interface Channel {
  id: number;
  type: 'bark' | 'feishu' | 'email';
  config: Record<string, string>;
  enabled: boolean;
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
const loading = ref(false);
const error = ref('');
const saving = ref(false);
const forms = ref<Record<string, string>>({});

// DND form
const dndStart = ref('22:00');
const dndEnd = ref('07:00');
const dndEnabled = ref(false);

async function load() {
  loading.value = true;
  error.value = '';
  try {
    channels.value = await apiGet<Channel[]>('/channels');
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

      <!-- 推送策略（每彩种） -->
      <section class="card">
        <div class="card-header">
          <h2 class="card-title">推送策略</h2>
          <p class="card-subtitle">按彩种设置推送规则（every / win_only）</p>
        </div>
        <div class="card-body">
          <p class="placeholder-note">推送策略配置即将上线。目前默认对所有启用彩种进行每期推送。</p>
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
            <input v-model="dndEnabled" type="checkbox" />
          </label>
          <div v-if="dndEnabled" class="dnd-row">
            <label class="field-inline">
              <span class="field-label">开始</span>
              <input v-model="dndStart" type="time" />
            </label>
            <span class="dnd-sep">—</span>
            <label class="field-inline">
              <span class="field-label">结束</span>
              <input v-model="dndEnd" type="time" />
            </label>
          </div>
          <p class="hint-text">免打扰时段内暂停所有推送，次日汇总照常发送。</p>
          <p class="placeholder-note">DND 服务端持久化即将上线。</p>
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
              @click="applyTheme('light')"
            >
              <span class="theme-icon">&#9728;</span>
              <span>浅色</span>
            </button>
            <button
              type="button"
              class="theme-btn"
              :class="{ active: theme === 'dark' }"
              @click="applyTheme('dark')"
            >
              <span class="theme-icon">&#9790;</span>
              <span>深色</span>
            </button>
            <button
              type="button"
              class="theme-btn"
              :class="{ active: theme === 'auto' }"
              @click="applyTheme('auto')"
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

input {
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

.theme-icon {
  font-size: 24px;
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

.placeholder-note {
  color: var(--warning);
  font-size: var(--text-sm);
  padding: 8px 0;
}
</style>
