import { defineStore } from 'pinia';
import { ref, computed } from 'vue';
import { apiGet, apiPost, ApiError } from '../api/client';

export interface User {
  id: number;
  username: string;
  role: 'user' | 'admin';
}

export const useAuthStore = defineStore('auth', () => {
  const user = ref<User | null>(null);
  const loading = ref(false);
  const initialized = ref(false);

  const isAdmin = computed(() => user.value?.role === 'admin');
  const isLoggedIn = computed(() => user.value !== null);

  async function fetchMe() {
    loading.value = true;
    try {
      // skipAuthRedirect: fetchMe 是探测性请求（/login 路由也无条件调用），
      // 401 是合法响应（未登录态），不应在 client 层触发跳转，否则死循环。
      // 由本 catch 处理：401/403 → 置 user=null（UI 进入未登录态）。
      user.value = await apiGet('/auth/me', { skipAuthRedirect: true });
    } catch (err) {
      // 401/403 表示会话已失效/无权限，UI 应进入未登录态；
      // 其余错误（网络/5xx/超时）属于瞬态故障，不能静默把已登录用户刷成“未登录”。
      if (err instanceof ApiError && (err.status === 401 || err.status === 403)) {
        user.value = null;
      } else {
        throw err;
      }
    } finally {
      loading.value = false;
      initialized.value = true;
    }
  }

  async function logout() {
    await apiPost('/auth/logout');
    user.value = null;
    window.location.href = '/login';
  }

  return {
    user,
    loading,
    initialized,
    isAdmin,
    isLoggedIn,
    fetchMe,
    logout,
  };
});
