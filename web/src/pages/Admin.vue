<script setup lang="ts">
import { ref, onMounted } from 'vue';
import { apiGet } from '../api/client';
import State from '../components/State.vue';

interface User {
  id: number;
  username: string;
  role: string;
  enabled: boolean;
}

interface HealthSource {
  source: string;
  status: string;
}

interface PushLog {
  id: number;
  user_id: number;
  type: string;
  status: string;
  error: string | null;
}

const users = ref<User[] | null>(null);
const health = ref<{ sources: HealthSource[] } | null>(null);
const logs = ref<PushLog[] | null>(null);
const loading = ref(false);
const error = ref('');

async function load() {
  loading.value = true;
  error.value = '';
  try {
    const [u, h, l] = await Promise.all([
      apiGet<User[]>('/admin/users'),
      apiGet<{ sources: HealthSource[] }>('/admin/health'),
      apiGet<PushLog[]>('/admin/push-logs?limit=20'),
    ]);
    users.value = u;
    health.value = h;
    logs.value = l;
  } catch (err) {
    error.value = err instanceof Error ? err.message : '加载失败';
  } finally {
    loading.value = false;
  }
}

onMounted(() => {
  void load();
});
</script>

<template>
  <div class="admin">
    <header class="page-header">
      <h1>后台管理</h1>
    </header>

    <State v-if="loading" type="loading" title="加载后台数据中…" />
    <State v-else-if="error" type="error" :title="error" @action="load" />

    <template v-else>
      <section class="card">
        <div class="card-header">
          <h2 class="card-title">用户管理</h2>
        </div>
        <div class="card-body">
          <table v-if="users && users.length > 0" class="data-table">
            <thead>
              <tr>
                <th>ID</th>
                <th>用户名</th>
                <th>角色</th>
                <th>状态</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="u in users" :key="u.id">
                <td>{{ u.id }}</td>
                <td>{{ u.username }}</td>
                <td>{{ u.role }}</td>
                <td>{{ u.enabled ? '启用' : '停用' }}</td>
              </tr>
            </tbody>
          </table>
          <div v-else class="empty-tip">暂无用户</div>
        </div>
      </section>

      <section class="card">
        <div class="card-header">
          <h2 class="card-title">数据源健康</h2>
        </div>
        <div class="card-body">
          <div v-if="health && health.sources.length > 0" class="source-list">
            <div
              v-for="s in health.sources"
              :key="s.source"
              class="source-item"
            >
              <span class="source-name">{{ s.source }}</span>
              <span class="source-status" :class="s.status">{{ s.status }}</span>
            </div>
          </div>
          <div v-else class="empty-tip">暂无健康数据</div>
        </div>
      </section>

      <section class="card">
        <div class="card-header">
          <h2 class="card-title">推送日志</h2>
        </div>
        <div class="card-body">
          <table v-if="logs && logs.length > 0" class="data-table">
            <thead>
              <tr>
                <th>ID</th>
                <th>用户</th>
                <th>类型</th>
                <th>状态</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="log in logs" :key="log.id">
                <td>{{ log.id }}</td>
                <td>{{ log.user_id }}</td>
                <td>{{ log.type }}</td>
                <td>
                  <span class="status-badge" :class="log.status">{{ log.status }}</span>
                </td>
              </tr>
            </tbody>
          </table>
          <div v-else class="empty-tip">暂无推送日志</div>
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

.card-body {
  padding: 16px 20px 20px;
}

.data-table {
  width: 100%;
  border-collapse: collapse;
  font-size: var(--text-md);
}

.data-table th,
.data-table td {
  padding: 12px 14px;
  text-align: left;
  border-bottom: 1px solid var(--border);
}

.data-table th {
  color: var(--muted);
  font-weight: 600;
  font-size: var(--text-sm);
}

.empty-tip {
  color: var(--muted);
  text-align: center;
  padding: 20px;
}

.source-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.source-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px;
  background: var(--surface-2);
  border-radius: 12px;
}

.source-name {
  font-weight: 600;
  text-transform: uppercase;
}

.source-status {
  padding: 3px 10px;
  border-radius: 20px;
  font-size: var(--text-xs);
  font-weight: 600;
}

.source-status.ok {
  background: #dcfce7;
  color: #166534;
}

.source-status.error,
.source-status.failed {
  background: #fee2e2;
  color: #991b1b;
}

.status-badge {
  display: inline-flex;
  padding: 3px 10px;
  border-radius: 20px;
  font-size: var(--text-xs);
  font-weight: 600;
}

.status-badge.sent {
  background: #dcfce7;
  color: #166534;
}

.status-badge.failed {
  background: #fee2e2;
  color: #991b1b;
}

.status-badge.pending {
  background: #fef3c7;
  color: #92400e;
}
</style>
