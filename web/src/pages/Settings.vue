<script setup lang="ts">
import { ref, onMounted } from 'vue';
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

const channels = ref<Channel[] | null>(null);
const loading = ref(false);
const error = ref('');
const saving = ref(false);
const forms = ref<Record<string, string>>({});

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
      <section class="card">
        <div class="card-header">
          <h2 class="card-title">推送渠道</h2>
        </div>
        <div class="card-body">
          <div v-if="channels && channels.length > 0" class="channel-list">
            <div
              v-for="ch in channels"
              :key="ch.id"
              class="channel-item"
            >
              <div class="channel-type">{{ CHANNELS.find((c) => c.type === ch.type)?.name || ch.type }}</div>
              <div class="channel-status">{{ ch.enabled ? '已启用' : '已停用' }}</div>
            </div>
          </div>
          <div v-else class="empty-tip">暂无渠道配置</div>
        </div>
      </section>

      <section
        v-for="channel in CHANNELS"
        :key="channel.type"
        class="card"
      >
        <div class="card-header">
          <h2 class="card-title">添加 {{ channel.name }}</h2>
          <p class="card-subtitle">{{ channel.desc }}</p>
        </div>
        <div class="card-body">
          <form @submit.prevent="addChannel(channel.type)">
            <label
              v-for="field in channel.fields"
              :key="field.key"
              class="field"
            >
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
</style>
