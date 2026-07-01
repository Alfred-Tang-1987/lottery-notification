import { defineStore } from 'pinia';
import { ref, computed } from 'vue';
import { apiGet, apiPost } from '../api/client';

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
      user.value = await apiGet('/auth/me');
    } catch {
      user.value = null;
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
