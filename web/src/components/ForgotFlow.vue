<!-- web/src/components/ForgotFlow.vue -->
<script setup lang="ts">
import { onUnmounted, ref } from 'vue';
import { apiPost } from '../api/client';

const emit = defineEmits<{ done: [] }>();

const step = ref<1 | 2>(1);
const username = ref('');
const code = ref('');
const newPassword = ref('');
const confirmPassword = ref('');
const info = ref('');
const err = ref('');
const loading = ref(false);
const countdown = ref(0);
let timer: ReturnType<typeof setInterval> | null = null;

function startCountdown() {
  countdown.value = 60;
  timer = setInterval(() => {
    countdown.value -= 1;
    if (countdown.value <= 0 && timer) {
      clearInterval(timer);
      timer = null;
    }
  }, 1000);
}

onUnmounted(() => {
  if (timer) clearInterval(timer);
});

async function sendCode() {
  err.value = '';
  info.value = '';
  loading.value = true;
  try {
    const r = await apiPost<{ ok: boolean; message: string }>('/auth/forgot-password', {
      username: username.value,
    });
    info.value = r.message;
    step.value = 2;
    startCountdown();
  } catch (e) {
    err.value = e instanceof Error ? e.message : '请求失败';
  } finally {
    loading.value = false;
  }
}

async function submitReset() {
  err.value = '';
  if (!/^\d{6}$/.test(code.value)) {
    err.value = '验证码为 6 位数字';
    return;
  }
  if (newPassword.value.length < 8) {
    err.value = '新密码至少 8 位';
    return;
  }
  if (newPassword.value !== confirmPassword.value) {
    err.value = '两次输入的密码不一致';
    return;
  }
  loading.value = true;
  try {
    await apiPost('/auth/reset-password', {
      username: username.value,
      code: code.value,
      new_password: newPassword.value,
    });
    emit('done');
  } catch (e) {
    err.value = e instanceof Error ? e.message : '请求失败';
  } finally {
    loading.value = false;
  }
}
</script>

<template>
  <div class="forgot-flow">
    <ol class="steps" aria-label="重置进度">
      <li :aria-current="step === 1 ? 'step' : undefined" :class="{ active: step === 1 }">1 发送验证码</li>
      <li :aria-current="step === 2 ? 'step' : undefined" :class="{ active: step === 2 }">2 设置新密码</li>
    </ol>

    <form v-show="step === 1" data-test="forgot-step1" @submit.prevent="sendCode">
      <label class="field">
        <span class="field-label">用户名</span>
        <input
          v-model="username"
          data-test="forgot-username"
          type="text"
          placeholder="用户名"
          required
          autocomplete="username"
        />
      </label>
      <p v-if="err" class="error" role="alert">{{ err }}</p>
      <button
        type="submit"
        class="submit"
        data-test="forgot-send"
        :disabled="loading || countdown > 0"
      >
        {{ countdown > 0 ? `重新发送（${countdown}s）` : (loading ? '请稍候…' : '发送验证码') }}
      </button>
      <p class="hint">验证码将发送至你配置的邮箱；未配置邮箱请联系管理员重置</p>
    </form>

    <form v-show="step === 2" data-test="forgot-step2" @submit.prevent="submitReset">
      <p v-if="info" class="info" role="status">{{ info }}</p>
      <label class="field">
        <span class="field-label">验证码</span>
        <input
          v-model="code"
          data-test="forgot-code"
          type="text"
          placeholder="6 位验证码"
          required
          minlength="6"
          maxlength="6"
          pattern="\d{6}"
          autocomplete="one-time-code"
          inputmode="numeric"
        />
      </label>
      <label class="field">
        <span class="field-label">新密码</span>
        <input
          v-model="newPassword"
          data-test="forgot-newpass"
          type="password"
          placeholder="新密码（≥8位）"
          required
          minlength="8"
          autocomplete="new-password"
        />
      </label>
      <label class="field">
        <span class="field-label">确认新密码</span>
        <input
          v-model="confirmPassword"
          data-test="forgot-confirm"
          type="password"
          placeholder="再次输入新密码"
          required
          minlength="8"
          autocomplete="new-password"
        />
      </label>
      <p v-if="err" class="error" role="alert">{{ err }}</p>
      <button type="submit" class="submit" data-test="forgot-submit" :disabled="loading">
        {{ loading ? '请稍候…' : '重置密码' }}
      </button>
      <button
        type="button"
        class="resend"
        data-test="forgot-resend"
        :disabled="countdown > 0"
        @click="sendCode"
      >
        {{ countdown > 0 ? `重新发送（${countdown}s）` : '重新发送验证码' }}
      </button>
    </form>
  </div>
</template>

<style scoped>
.steps {
  display: flex;
  gap: 12px;
  list-style: none;
  padding: 0;
  margin: 0 0 20px;
  font-size: var(--text-sm);
  color: var(--muted);
}
.steps li.active {
  color: var(--fg);
  font-weight: 600;
}
.field { display: block; margin-bottom: 16px; }
.field-label { display: block; font-size: var(--text-sm); color: var(--muted); margin-bottom: 6px; }
input {
  width: 100%;
  padding: 12px 14px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--surface);
  color: var(--fg);
  font-size: var(--text-md);
  min-height: 44px;
}
input:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.error {
  color: var(--danger);
  font-size: var(--text-sm);
  margin-bottom: 16px;
  padding: 10px 12px;
  background: #fef2f2;
  border-radius: var(--radius);
}
.info {
  color: var(--fg);
  font-size: var(--text-sm);
  margin-bottom: 16px;
  padding: 10px 12px;
  background: var(--surface-2);
  border-radius: var(--radius);
}
.submit {
  width: 100%;
  padding: 12px;
  border: none;
  border-radius: var(--radius);
  background: var(--accent);
  color: #fff;
  font-size: var(--text-md);
  font-weight: 600;
  cursor: pointer;
  min-height: 44px;
}
.submit:disabled { opacity: 0.7; cursor: not-allowed; }
.resend {
  width: 100%;
  margin-top: 10px;
  padding: 10px;
  border: none;
  background: transparent;
  color: var(--accent);
  font-size: var(--text-sm);
  cursor: pointer;
}
.resend:disabled { color: var(--muted); cursor: not-allowed; }
.hint { text-align: center; font-size: var(--text-xs); color: var(--muted); margin-top: 12px; }
</style>
