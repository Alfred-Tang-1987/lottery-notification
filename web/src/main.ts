import { createApp } from 'vue';
import { createPinia } from 'pinia';
import App from './App.vue';
import router from './router'; // wired up with vue-router
import './styles/tokens.css';
import 'virtual:uno.css';

// 主题偏好：localStorage 优先，否则跟随系统
const savedTheme = localStorage.getItem('theme');
if (
  savedTheme === 'dark' ||
  (!savedTheme && matchMedia('(prefers-color-scheme: dark)').matches)
) {
  document.documentElement.classList.add('dark');
}

createApp(App).use(createPinia()).use(router).mount('#app');
