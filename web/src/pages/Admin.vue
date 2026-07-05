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

interface PushLogPage {
  total: number;
  page: number;
  page_size: number;
  items: PushLog[];
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
      apiGet<PushLogPage>('/admin/push-logs?page=1&page_size=20'),
    ]);
    users.value = u;
    health.value = h;
    logs.value = l.items;
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
      <!-- 用户管理 -->
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

      <!-- 邀请码管理 -->
      <section class="card">
        <div class="card-header">
          <h2 class="card-title">邀请码</h2>
          <p class="card-subtitle">生成新邀请码供新用户注册</p>
        </div>
        <div class="card-body">
          <p class="placeholder-note">邀请码管理 API 即将上线（Plan 07）。目前可通过 CLI 生成邀请码。</p>
        </div>
      </section>

      <!-- SMTP 发件配置 -->
      <section class="card">
        <div class="card-header">
          <h2 class="card-title">SMTP 发件配置</h2>
          <p class="card-subtitle">系统通知邮件的统一发件服务器</p>
        </div>
        <div class="card-body">
          <p class="placeholder-note">SMTP 配置目前通过 .env 文件管理，管理面板界面即将上线。</p>
        </div>
      </section>

      <!-- 彩种配置 -->
      <section class="card">
        <div class="card-header">
          <h2 class="card-title">彩种管理</h2>
          <p class="card-subtitle">启用/停用彩种、修改开奖日</p>
        </div>
        <div class="card-body">
          <p class="placeholder-note">彩种配置管理 API 即将上线。当前通过 DB 种子（seeds/）初始化。</p>
        </div>
      </section>

      <!-- 数据源健康 -->
      <section class="card">
        <div class="card-header">
          <h2 class="card-title">数据源健康</h2>
        </div>
        <div class="card-body">
          <div v-if="health && health.sources.length > 0" class="source-list">
            <div v-for="s in health.sources" :key="s.source" class="source-item">
              <span class="source-name">{{ s.source }}</span>
              <span class="source-status" :class="s.status">{{ s.status }}</span>
            </div>
          </div>
          <div v-else class="empty-tip">暂无健康数据</div>
        </div>
      </section>

      <!-- 推送日志 -->
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

      <!-- 操作审计 -->
      <section class="card">
        <div class="card-header">
          <h2 class="card-title">操作审计</h2>
          <p class="card-subtitle">管理员操作记录</p>
        </div>
        <div class="card-body">
          <p class="placeholder-note">审计日志查询 API 即将上线。后端 audit_service 已记录所有管理员操作，界面待开放。</p>
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

.placeholder-note {
  color: var(--warning);
  font-size: var(--text-sm);
  padding: 8px 0;
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
