<script setup lang="ts">
import { ref } from 'vue';
import { useRouter } from 'vue-router';
import { apiPost } from '../api/client';

const router = useRouter();
const tab = ref<'login' | 'register'>('login');
const form = ref({
  username: '',
  password: '',
  invite_code: '',
});
const err = ref('');
const loading = ref(false);

async function submit() {
  err.value = '';
  loading.value = true;
  try {
    if (tab.value === 'login') {
      await apiPost('/auth/login', {
        username: form.value.username,
        password: form.value.password,
      });
    } else {
      await apiPost('/auth/register', {
        username: form.value.username,
        password: form.value.password,
        invite_code: form.value.invite_code,
      });
    }
    await router.push('/');
  } catch (e) {
    err.value = e instanceof Error ? e.message : '请求失败';
  } finally {
    loading.value = false;
  }
}
</script>

<template>
  <main class="login" role="main">
    <div class="login-card">
      <h1>兑奖了吗？</h1>
      <p class="tagline">开奖自动核对与中奖推送</p>

      <div class="tabs" role="tablist">
        <button
          type="button"
          role="tab"
          :aria-selected="tab === 'login'"
          class="tab"
          :class="{ active: tab === 'login' }"
          @click="tab = 'login'"
        >
          登录
        </button>
        <button
          type="button"
          role="tab"
          :aria-selected="tab === 'register'"
          class="tab"
          :class="{ active: tab === 'register' }"
          @click="tab = 'register'"
        >
          注册
        </button>
      </div>

      <form @submit.prevent="submit">
        <label class="field"
        >
          <span class="field-label">用户名</span>
          <input
            v-model="form.username"
            type="text"
            placeholder="用户名"
            required
            minlength="3"
            autocomplete="username"
          />
        </label>

        <label class="field"
        >
          <span class="field-label">密码</span>
          <input
            v-model="form.password"
            type="password"
            placeholder="密码（≥8位）"
            required
            minlength="8"
            autocomplete="current-password"
          />
        </label>

        <label v-if="tab === 'register'" class="field"
        >
          <span class="field-label">邀请码</span>
          <input
            v-model="form.invite_code"
            type="text"
            placeholder="6位邀请码"
            required
            minlength="6"
            maxlength="6"
            pattern="\d{6}"
            autocomplete="off"
          />
        </label>

        <p v-if="err" class="error" role="alert">{{ err }}</p>

        <button type="submit" class="submit" :disabled="loading">
          {{ loading ? '请稍候…' : (tab === 'login' ? '登录' : '注册') }}
        </button>
      </form>

      <p class="hint">理性购彩 量力而行 · 彩票为独立随机事件，历史不代表未来</p>
    </div>
  </main>
</template>

<style scoped>
.login {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: calc(100vh - 56px);
  padding: 20px;
}

.login-card {
  width: 100%;
  max-width: 380px;
  background: var(--surface);
  border-radius: 18px;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.04);
  padding: 32px;
}

h1 {
  font-size: var(--text-2xl);
  font-weight: 600;
  text-align: center;
  letter-spacing: -0.021em;
}

.tagline {
  text-align: center;
  color: var(--muted);
  font-size: var(--text-md);
  margin-top: 4px;
  margin-bottom: 24px;
}

.tabs {
  display: flex;
  gap: 8px;
  margin-bottom: 20px;
  background: var(--surface-2);
  padding: 4px;
  border-radius: 12px;
}

.tab {
  flex: 1;
  padding: 10px;
  border: none;
  background: transparent;
  color: var(--muted);
  font-size: var(--text-md);
  font-weight: 500;
  border-radius: 10px;
  cursor: pointer;
}

.tab.active {
  background: var(--surface);
  color: var(--fg);
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.06);
}

.field {
  display: block;
  margin-bottom: 16px;
}

.field-label {
  display: block;
  font-size: var(--text-sm);
  color: var(--muted);
  margin-bottom: 6px;
}

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

input:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}

.error {
  color: var(--danger);
  font-size: var(--text-sm);
  margin-bottom: 16px;
  padding: 10px 12px;
  background: #fef2f2;
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

.submit:disabled {
  opacity: 0.7;
  cursor: not-allowed;
}

.hint {
  text-align: center;
  font-size: var(--text-xs);
  color: var(--muted);
  margin-top: 20px;
}
</style>
