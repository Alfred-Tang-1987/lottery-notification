<script setup lang="ts">
import { ref, computed, onMounted } from 'vue';
import { apiGet, apiPost, apiPatch } from '../api/client';
import State from '../components/State.vue';
import { LOTTERIES, lotteryName } from '../lib/lotteries';

// ---------------------------------------------------------------------------
// 类型（对齐后端 admin_ext.py response models）
// ---------------------------------------------------------------------------

interface User {
  id: number;
  username: string;
  role: string;
  enabled: boolean;
  note: string;
}

interface HealthSource {
  source: string;
  status: string;
}

interface SmtpConfig {
  smtp_host: string | null;
  smtp_port: number;
  smtp_encryption: 'SSL/TLS' | 'STARTTLS' | 'none';
  smtp_user: string | null;
  smtp_from: string | null;
  configured: boolean;
}

interface SmtpTestResult {
  ok: boolean;
  message: string;
}

interface InviteCode {
  code: string;
  created_by: number;
  used_by: number | null;
  used_at: string | null;
  expires_at: string;
  attempts: number;
  locked_at: string | null;
  created_at: string;
}

interface LotteryToggle {
  code: string;
  enabled: boolean;
}

interface LotteryConfig {
  code: string;
  name: string;
  category: string;
  enabled: boolean;
  draw_days: number[];
}

interface PushLog {
  id: number;
  user_id: number;
  username: string | null;
  type: string;
  status: string;
  sent_at: string | null;
  error: string | null;
}

interface PushLogPage {
  total: number;
  page: number;
  page_size: number;
  items: PushLog[];
}

interface AuditLog {
  id: number;
  admin_id: number;
  admin_username: string | null;
  action: string;
  target_type: string;
  target_id: string | null;
  old_values: Record<string, unknown> | null;
  new_values: Record<string, unknown> | null;
  created_at: string;
}

interface AuditLogPage {
  total: number;
  page: number;
  page_size: number;
  items: AuditLog[];
}

// SMTP 服务商预设（与后端 SMTP_PROVIDERS 对齐：QQ/网易/Gmail/自定义）
// 自定义需手动填 host/port/encryption；其它三个自动填，前端只让用户填账号+授权码
const SMTP_PROVIDERS = [
  { value: 'qq', label: 'QQ 邮箱', host: 'smtp.qq.com', port: 465, encryption: 'SSL/TLS' as const },
  { value: 'netease', label: '网易 163', host: 'smtp.163.com', port: 465, encryption: 'SSL/TLS' as const },
  { value: 'gmail', label: 'Gmail', host: 'smtp.gmail.com', port: 587, encryption: 'STARTTLS' as const },
  { value: 'custom', label: '自定义', host: '', port: 465, encryption: 'SSL/TLS' as const },
];

const WEEKDAY_LABELS = ['一', '二', '三', '四', '五', '六', '日'];

/** 格式化 draw_days（Python 0-based weekday → 中文周X）。每日彩种显示「每日」。 */
function formatDrawDays(drawDays: number[]): string {
  if (!drawDays || drawDays.length === 0) return '—';
  if (drawDays.length === 7) return '每日';
  return drawDays
    .slice()
    .sort((a, b) => a - b)
    .map((d) => `周${WEEKDAY_LABELS[d] ?? d}`)
    .join('、');
}

// ---------------------------------------------------------------------------
// 状态
// ---------------------------------------------------------------------------

const loading = ref(false);
const error = ref('');

const users = ref<User[]>([]);
const health = ref<HealthSource[]>([]);
const smtp = ref<SmtpConfig | null>(null);
const invites = ref<InviteCode[]>([]);
const lotteries = ref<LotteryConfig[]>([]);
const audit = ref<AuditLog[]>([]);
const auditPage = ref(1);
const auditTotal = ref(0);

// 推送日志 + 6 维筛选
const pushLogs = ref<PushLog[]>([]);
const pushPage = ref(1);
const pushTotal = ref(0);
const PAGE_SIZE = 20;

// 6 维筛选（spec §12.2 row 9：日期/用户/彩种/渠道/类型/状态，按列序）
const filter = ref({
  date_from: '',
  date_to: '',
  user_id: '',
  lottery_code: '',
  channel: '',
  type: '',
  status: '',
});

// SMTP 测试发送状态
const smtpTesting = ref(false);
const smtpTestResult = ref<SmtpTestResult | null>(null);

// SMTP 写入表单状态（spec §12.2 row 9：服务商下拉 + 账号 + 授权码 + 保存）
const smtpForm = ref({
  provider: 'qq',
  account: '',
  auth_code: '',
  // 自定义服务商时手动填
  host: '',
  port: 465,
  encryption: 'SSL/TLS' as 'SSL/TLS' | 'STARTTLS' | 'none',
  from_address: '',
});
const smtpSaving = ref(false);

// 邀请码生成状态
const inviteCreating = ref(false);

// ---------------------------------------------------------------------------
// 数据加载
// ---------------------------------------------------------------------------

async function loadCore() {
  const [u, h, s, i, l] = await Promise.all([
    apiGet<User[]>('/admin/users'),
    apiGet<{ sources: HealthSource[] }>('/admin/health'),
    apiGet<SmtpConfig>('/admin/smtp-config'),
    apiGet<InviteCode[]>('/admin/invite-codes'),
    apiGet<LotteryConfig[]>('/admin/lotteries'),
  ]);
  users.value = u;
  health.value = h.sources;
  smtp.value = s;
  invites.value = i;
  lotteries.value = l;
}

function buildPushLogQuery(page: number): string {
  const params = new URLSearchParams();
  params.set('page', String(page));
  params.set('page_size', String(PAGE_SIZE));
  const f = filter.value;
  if (f.date_from) params.set('date_from', f.date_from);
  if (f.date_to) params.set('date_to', f.date_to);
  if (f.user_id) params.set('user_id', f.user_id);
  if (f.lottery_code) params.set('lottery_code', f.lottery_code);
  // 注：channel 筛选已移除——NotificationLog 无 channel 列（Notifier 发送时不落库渠道），
  // 原后端 type == channel 是 no-op 假筛选（type 存通知标题，永不等于 bark/feishu/email）。
  // 加列需 DB 迁移，暂禁用，详见 app/api/admin_ext.py filtered_push_logs 注释。
  if (f.status) params.set('status', f.status);
  // 注：原 type 筛选（path_a/path_b）已移除——NotificationLog 无独立推送路径列，
  // 原后端 payload.contains(type) 子串匹配对 path_a/path_b 永不命中（误导性假筛选）。
  // 加列需 DB 迁移，暂禁用，详见 app/api/admin_ext.py filtered_push_logs 注释。
  return `/admin/push-logs?${params.toString()}`;
}

async function loadPushLogs(page: number = 1) {
  const res = await apiGet<PushLogPage>(buildPushLogQuery(page));
  // 校验响应结构：缺失关键字段时抛错而非静默回退到空（防 API 返回异常形状时显示无数据误导）
  if (!res || !Array.isArray(res.items) || typeof res.total !== 'number' || typeof res.page !== 'number') {
    throw new Error('推送日志响应结构异常：缺少 items/page/total 字段');
  }
  pushLogs.value = res.items;
  pushPage.value = res.page;
  pushTotal.value = res.total;
}

async function loadAudit(page: number = 1) {
  const params = new URLSearchParams();
  params.set('page', String(page));
  params.set('page_size', String(PAGE_SIZE));
  const res = await apiGet<AuditLogPage>(`/admin/audit-logs?${params.toString()}`);
  // 校验响应结构：缺失关键字段时抛错而非静默回退（hunter：防静默兜底掩盖 API 异常）
  if (!res || !Array.isArray(res.items) || typeof res.total !== 'number' || typeof res.page !== 'number') {
    throw new Error('审计日志响应结构异常：缺少 items/page/total 字段');
  }
  audit.value = res.items;
  auditPage.value = res.page;
  auditTotal.value = res.total ?? 0;
}

async function load() {
  loading.value = true;
  error.value = '';
  try {
    await Promise.all([loadCore(), loadPushLogs(1), loadAudit(1)]);
  } catch (err) {
    error.value = err instanceof Error ? err.message : '加载失败';
  } finally {
    loading.value = false;
  }
}

onMounted(() => {
  void load();
});

// ---------------------------------------------------------------------------
// 操作
// ---------------------------------------------------------------------------

async function applyFilter() {
  await loadPushLogs(1);
}

async function nextPage() {
  await loadPushLogs(pushPage.value + 1);
}

async function prevPage() {
  if (pushPage.value > 1) await loadPushLogs(pushPage.value - 1);
}

const pushPageCount = computed(() => Math.max(1, Math.ceil(pushTotal.value / PAGE_SIZE)));

async function sendSmtpTest() {
  smtpTesting.value = true;
  smtpTestResult.value = null;
  try {
    smtpTestResult.value = await apiPost<SmtpTestResult>('/admin/smtp-test');
  } catch (err) {
    smtpTestResult.value = {
      ok: false,
      message: err instanceof Error ? err.message : '发送失败',
    };
  } finally {
    smtpTesting.value = false;
    // 测试后刷新审计（后端写审计）—— await + 错误处理（hunter：防 fire-and-forget 吞错）
    try {
      await loadAudit(1);
    } catch (err) {
      error.value = err instanceof Error ? err.message : '审计刷新失败';
    }
  }
}

async function saveSmtpConfig() {
  smtpSaving.value = true;
  error.value = '';
  try {
    const provider = SMTP_PROVIDERS.find((p) => p.value === smtpForm.value.provider)!;
    const isCustom = smtpForm.value.provider === 'custom';
    const body: Record<string, unknown> = {
      provider: smtpForm.value.provider,
      account: smtpForm.value.account,
      auth_code: smtpForm.value.auth_code,
    };
    if (isCustom) {
      body.host = smtpForm.value.host;
      body.port = smtpForm.value.port;
      body.encryption = smtpForm.value.encryption;
      body.from_address = smtpForm.value.from_address || smtpForm.value.account;
    } else {
      body.host = provider.host;
      body.port = provider.port;
      body.encryption = provider.encryption;
    }
    smtp.value = await apiPost<SmtpConfig>('/admin/smtp-config', body);
    // 保存成功后刷新审计（后端写审计）
    try {
      await loadAudit(1);
    } catch (err) {
      error.value = err instanceof Error ? err.message : '审计刷新失败';
    }
  } catch (err) {
    error.value = err instanceof Error ? err.message : 'SMTP 配置保存失败';
  } finally {
    smtpSaving.value = false;
  }
}

function onProviderChange() {
  // 选中预设服务商时自动填 host/port/encryption（spec §12.2 row 9：选中自动填）
  const provider = SMTP_PROVIDERS.find((p) => p.value === smtpForm.value.provider);
  if (provider && smtpForm.value.provider !== 'custom') {
    smtpForm.value.host = provider.host;
    smtpForm.value.port = provider.port;
    smtpForm.value.encryption = provider.encryption;
  }
}

async function createInvite() {
  inviteCreating.value = true;
  try {
    await apiPost<InviteCode>('/admin/invite-codes');
    invites.value = await apiGet<InviteCode[]>('/admin/invite-codes');
  } catch (err) {
    error.value = err instanceof Error ? err.message : '邀请码生成失败';
  } finally {
    inviteCreating.value = false;
  }
}

async function toggleLottery(code: string, enabled: boolean) {
  const prev = lotteries.value.find((l) => l.code === code);
  try {
    await apiPatch<LotteryToggle>(
      `/admin/lotteries/${code}/enabled?enabled=${enabled ? 'true' : 'false'}`,
    );
    // 乐观更新本地状态：成功后同步 checkbox 反映后端真实值
    const lt = lotteries.value.find((l) => l.code === code);
    if (lt) lt.enabled = enabled;
    // await + 错误处理（hunter：防 fire-and-forget 吞错）
    try {
      await loadAudit(1);
    } catch (err) {
      error.value = err instanceof Error ? err.message : '审计刷新失败';
    }
  } catch (err) {
    error.value = err instanceof Error ? err.message : '彩种切换失败';
    // 失败时回滚本地状态（hunter：失败时保持原状态，使 checkbox 反映后端真实值）
    if (prev) prev.enabled = !enabled;
  }
}

// ---------------------------------------------------------------------------
// 视图辅助
// ---------------------------------------------------------------------------

const CHANNEL_OPTIONS = ['bark', 'feishu', 'email'];
const TYPE_OPTIONS = ['path_a', 'path_b'];
const STATUS_OPTIONS = ['sent', 'failed', 'pending'];

function formatDate(s: string | null): string {
  if (!s) return '—';
  return s.replace('T', ' ').slice(0, 19);
}
</script>

<template>
  <div class="admin">
    <header class="page-header">
      <h1>后台管理</h1>
    </header>

    <State v-if="loading" type="loading" title="加载后台数据中…" />
    <State v-else-if="error && users.length === 0" type="error" :title="error" @action="load" />

    <template v-else>
      <!-- 全局错误提示区（独立于 users 列表：loadCore 成功后 loadPushLogs/loadAudit 失败也展示） -->
      <div v-if="error" class="global-error" role="alert">
        <span class="error-icon" aria-hidden="true">⚠</span>
        <span class="error-text">{{ error }}</span>
        <button class="error-dismiss" @click="error = ''" aria-label="关闭错误提示">×</button>
      </div>
      <!-- 用户管理 -->
      <section class="card">
        <div class="card-header">
          <h2 class="card-title">用户管理</h2>
          <p class="card-subtitle">邀请制注册 · 角色/启用状态 · 备注列（spec §12.2 row 9）</p>
        </div>
        <div class="card-body">
          <table v-if="users.length > 0" class="data-table">
            <thead>
              <tr>
                <th>ID</th>
                <th>用户名</th>
                <th>角色</th>
                <th>状态</th>
                <th>备注</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="u in users" :key="u.id">
                <td>{{ u.id }}</td>
                <td>{{ u.username }}</td>
                <td>{{ u.role }}</td>
                <td>
                  <span class="status-badge" :class="u.enabled ? 'sent' : 'failed'">
                    {{ u.enabled ? '启用' : '停用' }}
                  </span>
                </td>
                <td class="note-cell">{{ u.note || '—' }}</td>
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
          <p class="card-subtitle">生成新邀请码供新用户注册（6 位 · 30 天有效）</p>
        </div>
        <div class="card-body">
          <button
            type="button"
            class="invite-create-btn primary-btn"
            :disabled="inviteCreating"
            @click="createInvite"
          >
            {{ inviteCreating ? '生成中…' : '生成邀请码' }}
          </button>
          <table v-if="invites.length > 0" class="data-table">
            <thead>
              <tr>
                <th>邀请码</th>
                <th>创建人</th>
                <th>使用人</th>
                <th>过期时间</th>
                <th>尝试次数</th>
                <th>状态</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="ic in invites" :key="ic.code">
                <td><code>{{ ic.code }}</code></td>
                <td>{{ ic.created_by }}</td>
                <td>{{ ic.used_by ?? '—' }}</td>
                <td>{{ formatDate(ic.expires_at) }}</td>
                <td>{{ ic.attempts }}</td>
                <td>
                  <span class="status-badge" :class="ic.used_by ? 'pending' : 'sent'">
                    {{ ic.used_by ? '已使用' : '可用' }}
                  </span>
                </td>
              </tr>
            </tbody>
          </table>
          <div v-else class="empty-tip">暂无邀请码，点击上方按钮生成</div>
        </div>
      </section>

      <!-- SMTP 发件配置（spec §12.2 row 9：服务商下拉 + 自动填 + 只填账号+授权码 + 保存） -->
      <section class="card">
        <div class="card-header">
          <h2 class="card-title">SMTP 发件配置</h2>
          <p class="card-subtitle">服务商下拉自动填服务器/端口/加密 · 只填账号+授权码 · 保存后即时生效</p>
        </div>
        <div class="card-body">
          <!-- 当前配置只读概览 -->
          <dl v-if="smtp" class="smtp-info">
            <div><dt>当前服务器</dt><dd>{{ smtp.smtp_host || '—' }}</dd></div>
            <div><dt>端口</dt><dd>{{ smtp.smtp_port }}</dd></div>
            <div><dt>加密</dt><dd>{{ smtp.smtp_encryption }}</dd></div>
            <div><dt>当前账号</dt><dd>{{ smtp.smtp_user || '—' }}</dd></div>
            <div><dt>状态</dt>
              <dd>
                <span class="status-badge" :class="smtp.configured ? 'sent' : 'failed'">
                  {{ smtp.configured ? '已配置' : '未完整' }}
                </span>
              </dd>
            </div>
          </dl>

          <!-- 写入表单：服务商 + 账号 + 授权码 + 保存 -->
          <div class="smtp-form">
            <div class="form-row">
              <label for="smtp-provider" class="form-label">服务商</label>
              <select
                id="smtp-provider"
                class="smtp-provider form-input"
                v-model="smtpForm.provider"
                @change="onProviderChange"
              >
                <option v-for="p in SMTP_PROVIDERS" :key="p.value" :value="p.value">{{ p.label }}</option>
              </select>
            </div>

            <div v-if="smtpForm.provider === 'custom'" class="form-row">
              <label for="smtp-host" class="form-label">SMTP 服务器</label>
              <input
                id="smtp-host"
                class="smtp-host form-input"
                v-model="smtpForm.host"
                placeholder="mail.example.com"
              />
            </div>
            <div v-if="smtpForm.provider === 'custom'" class="form-row">
              <label for="smtp-port" class="form-label">端口</label>
              <input
                id="smtp-port"
                class="smtp-port form-input"
                type="number"
                v-model.number="smtpForm.port"
              />
            </div>
            <div v-if="smtpForm.provider === 'custom'" class="form-row">
              <label for="smtp-encryption" class="form-label">加密</label>
              <select
                id="smtp-encryption"
                class="smtp-encryption form-input"
                v-model="smtpForm.encryption"
              >
                <option value="SSL/TLS">SSL/TLS</option>
                <option value="STARTTLS">STARTTLS</option>
                <option value="none">none</option>
              </select>
            </div>

            <div class="form-row">
              <label for="smtp-account" class="form-label">发件账号</label>
              <input
                id="smtp-account"
                class="smtp-account form-input"
                v-model="smtpForm.account"
                placeholder="your@qq.com"
              />
            </div>
            <div class="form-row">
              <label for="smtp-auth-code" class="form-label">授权码</label>
              <input
                id="smtp-auth-code"
                class="smtp-auth-code form-input"
                type="password"
                v-model="smtpForm.auth_code"
                placeholder="SMTP 授权码（非邮箱登录密码）"
              />
            </div>
            <div v-if="smtpForm.provider === 'custom'" class="form-row">
              <label for="smtp-from" class="form-label">发件地址</label>
              <input
                id="smtp-from"
                class="smtp-from form-input"
                v-model="smtpForm.from_address"
                placeholder="留空则等于发件账号"
              />
            </div>

            <div class="form-actions">
              <button
                type="button"
                class="smtp-save-btn primary-btn"
                :disabled="smtpSaving || !smtpForm.account || !smtpForm.auth_code"
                @click="saveSmtpConfig"
              >
                {{ smtpSaving ? '保存中…' : '保存配置' }}
              </button>
              <button
                type="button"
                class="smtp-test-btn secondary-btn"
                :disabled="smtpTesting || !smtp?.configured"
                @click="sendSmtpTest"
              >
                {{ smtpTesting ? '发送中…' : '发送测试邮件' }}
              </button>
            </div>
          </div>

          <p
            v-if="smtpTestResult"
            class="smtp-test-result"
            :class="smtpTestResult.ok ? 'ok' : 'err'"
            role="status"
          >
            {{ smtpTestResult.ok ? '✓ ' : '✗ ' }}{{ smtpTestResult.message }}
          </p>
        </div>
      </section>

      <!-- 彩种配置（spec §12.2 row 9：启用/开奖日/双源三要素） -->
      <section class="card">
        <div class="card-header">
          <h2 class="card-title">彩种管理</h2>
          <p class="card-subtitle">启用 / 开奖日 / 分类 — checkbox 反映后端真实状态</p>
        </div>
        <div class="card-body">
          <ul class="lottery-list">
            <li v-for="l in lotteries" :key="l.code" class="lottery-row">
              <span class="lottery-name">{{ l.name }}</span>
              <span class="lottery-meta">
                <span class="lottery-draw-days">开奖日：{{ formatDrawDays(l.draw_days) }}</span>
                <span class="lottery-source">分类：{{ l.category === 'welfare' ? '福彩' : '体彩' }}</span>
              </span>
              <label class="toggle-label">
                <input
                  type="checkbox"
                  class="lottery-toggle"
                  :data-lottery="l.code"
                  :checked="l.enabled"
                  @change="(e) => toggleLottery(l.code, (e.target as HTMLInputElement).checked)"
                />
                <span>启用</span>
              </label>
            </li>
          </ul>
          <p class="lottery-note">所有彩种均为双源容灾（MXNZP 主 + 聚合数据备，spec §4.2）</p>
        </div>
      </section>

      <!-- 数据源健康 -->
      <section class="card">
        <div class="card-header">
          <h2 class="card-title">数据源健康</h2>
          <p class="card-subtitle">双源容灾状态</p>
        </div>
        <div class="card-body">
          <div v-if="health.length > 0" class="source-list">
            <div v-for="s in health" :key="s.source" class="source-item">
              <span class="source-name">{{ s.source }}</span>
              <span class="source-status" :class="s.status">{{ s.status }}</span>
            </div>
          </div>
          <div v-else class="empty-tip">暂无健康数据</div>
        </div>
      </section>

      <!-- 推送日志（6 维筛选 + 分页） -->
      <section class="card">
        <div class="card-header">
          <h2 class="card-title">推送日志</h2>
          <p class="card-subtitle">6 维筛选：日期 / 用户 / 彩种 / 渠道 / 类型 / 状态</p>
        </div>
        <div class="card-body">
          <div class="filter-bar">
            <label class="filter-field">
              <span>日期从</span>
              <input type="date" class="filter-date-from" v-model="filter.date_from" />
            </label>
            <label class="filter-field">
              <span>日期到</span>
              <input type="date" class="filter-date-to" v-model="filter.date_to" />
            </label>
            <label class="filter-field">
              <span>用户 ID</span>
              <input type="number" class="filter-user-id" v-model="filter.user_id" placeholder="用户 ID" />
            </label>
            <label class="filter-field">
              <span>彩种</span>
              <select class="filter-lottery" v-model="filter.lottery_code">
                <option value="">全部</option>
                <option v-for="l in LOTTERIES" :key="l.code" :value="l.code">{{ lotteryName(l.code) }}</option>
              </select>
            </label>
            <label class="filter-field">
              <span>渠道<abbr title="NotificationLog 无 channel 列，需 DB 迁移，暂不可用">*</abbr></span>
              <select class="filter-channel" v-model="filter.channel" disabled>
                <option value="">全部</option>
                <option v-for="c in CHANNEL_OPTIONS" :key="c" :value="c">{{ c }}</option>
              </select>
            </label>
            <label class="filter-field">
              <span>类型<abbr title="推送路径筛选需 DB 加列，暂不可用">*</abbr></span>
              <select class="filter-type" v-model="filter.type" disabled>
                <option value="">全部</option>
                <option v-for="t in TYPE_OPTIONS" :key="t" :value="t">{{ t }}</option>
              </select>
            </label>
            <label class="filter-field">
              <span>状态</span>
              <select class="filter-status" v-model="filter.status">
                <option value="">全部</option>
                <option v-for="st in STATUS_OPTIONS" :key="st" :value="st">{{ st }}</option>
              </select>
            </label>
            <button type="button" class="filter-apply-btn primary-btn" @click="applyFilter">应用筛选</button>
          </div>

          <table v-if="pushLogs.length > 0" class="data-table">
            <thead>
              <tr>
                <th>ID</th>
                <th>用户</th>
                <th>类型</th>
                <th>状态</th>
                <th>发送时间</th>
                <th>错误</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="log in pushLogs" :key="log.id">
                <td>{{ log.id }}</td>
                <td>{{ log.username ?? log.user_id }}</td>
                <td>{{ log.type }}</td>
                <td>
                  <span class="status-badge" :class="log.status">{{ log.status }}</span>
                </td>
                <td>{{ formatDate(log.sent_at) }}</td>
                <td>{{ log.error || '—' }}</td>
              </tr>
            </tbody>
          </table>
          <div v-else class="empty-tip">暂无推送日志</div>

          <div class="pager">
            <button type="button" class="pager-prev" :disabled="pushPage <= 1" @click="prevPage">上一页</button>
            <span class="pager-info">{{ pushPage }} / {{ pushPageCount }}</span>
            <button type="button" class="pager-next" :disabled="pushPage >= pushPageCount" @click="nextPage">下一页</button>
            <span class="pager-total">共 {{ pushTotal }} 条</span>
          </div>
        </div>
      </section>

      <!-- 操作审计 -->
      <section class="card">
        <div class="card-header">
          <h2 class="card-title">操作审计</h2>
          <p class="card-subtitle">管理员操作记录</p>
        </div>
        <div class="card-body">
          <table v-if="audit.length > 0" class="data-table">
            <thead>
              <tr>
                <th>ID</th>
                <th>管理员</th>
                <th>操作</th>
                <th>目标类型</th>
                <th>目标 ID</th>
                <th>时间</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="a in audit" :key="a.id">
                <td>{{ a.id }}</td>
                <td>{{ a.admin_username ?? a.admin_id }}</td>
                <td>{{ a.action }}</td>
                <td>{{ a.target_type }}</td>
                <td>{{ a.target_id || '—' }}</td>
                <td>{{ formatDate(a.created_at) }}</td>
              </tr>
            </tbody>
          </table>
          <div v-else class="empty-tip">暂无审计日志</div>

          <!-- 审计日志分页（spec §12.2 row 9 + plan T6f Step 3） -->
          <div v-if="auditTotal > 0" class="audit-pager pager">
            <button
              type="button"
              class="pager-prev"
              :disabled="auditPage <= 1"
              @click="loadAudit(auditPage - 1)"
            >
              上一页
            </button>
            <span class="pager-info">第 {{ auditPage }} / {{ Math.ceil(auditTotal / PAGE_SIZE) }} 页（共 {{ auditTotal }} 条）</span>
            <button
              type="button"
              class="pager-next"
              :disabled="auditPage >= Math.ceil(auditTotal / PAGE_SIZE)"
              @click="loadAudit(auditPage + 1)"
            >
              下一页
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

.primary-btn {
  background: var(--accent);
  color: #fff;
  border: none;
  border-radius: 10px;
  padding: 8px 16px;
  font-size: var(--text-md);
  font-weight: 600;
  cursor: pointer;
  margin-bottom: 12px;
  transition: opacity 0.15s;
}

.primary-btn:hover:not(:disabled) {
  opacity: 0.9;
}

.primary-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.smtp-info {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 12px 24px;
  margin: 0 0 16px;
}

.smtp-info > div {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.smtp-info dt {
  font-size: var(--text-sm);
  color: var(--muted);
}

.smtp-info dd {
  margin: 0;
  font-size: var(--text-md);
  font-weight: 500;
}

.smtp-test-result {
  margin-top: 8px;
  font-size: var(--text-sm);
}

.smtp-test-result.ok {
  color: #166534;
}

.smtp-test-result.err {
  color: #991b1b;
}

.lottery-list {
  list-style: none;
  padding: 0;
  margin: 0;
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: 10px;
}

.lottery-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 14px;
  background: var(--surface-2);
  border-radius: 12px;
}

.lottery-name {
  font-weight: 600;
}

.toggle-label {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: var(--text-sm);
  cursor: pointer;
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

.filter-bar {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
  gap: 10px;
  align-items: end;
  margin-bottom: 16px;
  padding: 12px;
  background: var(--surface-2);
  border-radius: 12px;
}

.filter-field {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: var(--text-sm);
}

.filter-field span {
  color: var(--muted);
  font-size: var(--text-xs);
}

.filter-field input,
.filter-field select {
  padding: 6px 8px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--surface);
  font-size: var(--text-sm);
}

.filter-apply-btn {
  margin-bottom: 0;
  align-self: end;
}

.pager {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-top: 16px;
  font-size: var(--text-sm);
  color: var(--muted);
}

.pager button {
  padding: 6px 12px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--surface);
  cursor: pointer;
  font-size: var(--text-sm);
}

.pager button:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.pager-info {
  font-weight: 600;
}

.pager-total {
  margin-left: auto;
}

@media (max-width: 768px) {
  .smtp-info {
    grid-template-columns: 1fr;
  }

  .filter-bar {
    grid-template-columns: 1fr;
  }
}
</style>
